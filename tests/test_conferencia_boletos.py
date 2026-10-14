"""Smoke test da tela de boletos (abas e endpoints de consulta) e sync de pagamentos."""
from datetime import date, timedelta

import pytest

import routers.sicoob as sicoob
from models import ContaReceber, StatusConta
from tests.conftest import criar_cliente_teste


@pytest.mark.asyncio
async def test_tela_boletos_renderiza_aba_conferencia(authenticated_client, db_session, test_empresa):
    resp = await authenticated_client.get("/sicoob/boletos")

    assert resp.status_code == 200
    assert "Conferência (abertos no Sicoob)" in resp.text
    assert "conferirAbertos()" in resp.text


@pytest.mark.asyncio
async def test_conferencia_exige_periodo(authenticated_client, db_session, test_empresa):
    resp = await authenticated_client.get("/sicoob/api/conferencia-abertos")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "período" in data["error"]


def test_data_liquidacao_usa_movimento_tipo_5():
    """Liquidacao e o movimento 5; a baixa (6) nao pode virar data de recebimento."""
    boleto = {
        "dataEmissao": "2026-09-01",
        "listaHistorico": [
            {"dataHistorico": "2026-09-02", "tipoHistorico": "4", "descricaoHistorico": "VENCIDO"},
            {"dataHistorico": "2026-09-05", "tipoHistorico": "6", "descricaoHistorico": "BAIXA"},
        ],
    }
    assert sicoob.extrair_data_liquidacao(boleto) is None

    boleto["listaHistorico"].append(
        {"dataHistorico": "2026-09-10", "tipoHistorico": "5", "descricaoHistorico": "LIQUIDACAO"}
    )
    assert sicoob.extrair_data_liquidacao(boleto) == "2026-09-10"


def test_data_liquidacao_ignora_tarifa_e_desconto():
    boleto = {
        "dataEmissao": "2026-09-01",
        "listaHistorico": [
            {"dataHistorico": "2026-09-03", "tipoHistorico": "1", "descricaoHistorico": "TARIFA - R$ 0,75"},
            {"dataHistorico": "2026-09-04", "tipoHistorico": "1", "descricaoHistorico": "DESCONTO"},
            {"dataHistorico": "2026-09-07", "tipoHistorico": "5", "descricaoHistorico": "LIQUIDACAO"},
        ],
    }
    assert sicoob.extrair_data_liquidacao(boleto) == "2026-09-07"


def test_data_liquidacao_descarta_datas_impossiveis():
    """Data futura ou anterior a emissao nao podem ser gravadas."""
    amanha = (date.today() + timedelta(days=1)).isoformat()
    boleto = {"dataEmissao": "2026-09-20", "listaHistorico": [
        {"dataHistorico": amanha, "tipoHistorico": "5", "descricaoHistorico": "LIQUIDACAO"}
    ]}
    assert sicoob.extrair_data_liquidacao(boleto) is None

    boleto = {"dataEmissao": "2026-09-20", "listaHistorico": [
        {"dataHistorico": "2026-09-01", "tipoHistorico": "5", "descricaoHistorico": "LIQUIDACAO"}
    ]}
    assert sicoob.extrair_data_liquidacao(boleto) is None


def test_sync_pagamentos_retorna_detalhe_e_aviso_sem_data(
    db_session, test_user, monkeypatch
):
    """O sync deve dizer quais boletos mudaram e avisar os sem data de liquidacao."""
    cliente = criar_cliente_teste(db_session)
    conta = ContaReceber(
        cliente_id=cliente.id, descricao="Cobranca sync", valor=100,
        data_vencimento=date.today(), status=StatusConta.PENDENTE,
        boleto_emitido=True, nosso_numero="77", api_nosso_numero="50888",
    )
    sem_data = ContaReceber(
        cliente_id=cliente.id, descricao="Cobranca sync 2", valor=50,
        data_vencimento=date.today(), status=StatusConta.PENDENTE,
        boleto_emitido=True, nosso_numero="78", api_nosso_numero="50889",
    )
    db_session.add_all([conta, sem_data])
    db_session.commit()

    respostas = {
        "50888": {
            "nossoNumero": "50888",
            "situacaoBoleto": "Liquidado",
            "dataEmissao": (date.today() - timedelta(days=10)).isoformat(),
            "listaHistorico": [
                {"dataHistorico": (date.today() - timedelta(days=2)).isoformat(),
                 "tipoHistorico": "5", "descricaoHistorico": "LIQUIDACAO"}
            ],
        },
        "50889": {"nossoNumero": "50889", "situacaoBoleto": "Liquidado"},
    }

    monkeypatch.setattr(sicoob, "get_token_or_error", lambda db, scope="boletos_consulta": ("tok", None))
    monkeypatch.setattr(sicoob, "get_empresa", lambda db: type("E", (), {"sicoob_beneficiario": "91820"})())
    monkeypatch.setattr(sicoob, "get_cert_config", lambda db: None)

    class FakeResp:
        def __init__(self, payload, status_code=200):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return {"resultado": self._payload}

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            payload = respostas.get(str((params or {}).get("nossoNumero")))
            if payload is None:
                return FakeResp({})
            return FakeResp(payload)

    monkeypatch.setattr(sicoob.httpx, "Client", FakeClient)

    class FakeRequest:
        session = {"user_id": test_user.id}
        client = None

    resp = sicoob.sync_pagamentos(FakeRequest(), db_session)

    assert resp["success"] is True
    assert resp["consultados"] == 2
    assert resp["atualizados"] == 2
    por_numero = {i["nosso_numero"]: i for i in resp["itens"]}
    assert por_numero["50888"]["origem_data"] == "sicoob"
    assert por_numero["50888"]["data_recebimento"] == (date.today() - timedelta(days=2)).isoformat()
    assert por_numero["50889"]["origem_data"] == "estimada"
    assert resp["sem_data"] == ["50889"]

    db_session.expire_all()
    contas = {c.api_nosso_numero: c for c in db_session.query(ContaReceber).all()}
    assert contas["50888"].status == StatusConta.PAGO
    assert contas["50889"].data_recebimento == date.today()

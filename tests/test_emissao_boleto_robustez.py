"""Robustez da emissao de boleto: colisao de nosso_numero e sessao invalida.

Dois problemas reais de producao cobertos aqui:

1. `nosso_numero` tem indice unico. Uma cobranca cancelada ficava com o numero
   do documento e a nova cobranca do mesmo documento estourava IntegrityError
   no commit -- depois de o boleto ja ter sido criado no Sicoob.
2. Depois de um erro de banco a sessao fica em PendingRollback. O handler antigo
   lia `conta.numero_parcela` ao montar a mensagem, o que disparava um refresh e
   transformava a falha do boleto em HTTP 500.
"""
from datetime import date

import pytest
from sqlalchemy import text

import routers.sicoob as sicoob
from models import ContaReceber, StatusConta
from services.parcelamento import emitir_boletos_contas
from tests.conftest import criar_cliente_teste


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise sicoob.httpx.HTTPStatusError(
                "erro", request=None, response=self
            )


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse(
            200,
            {
                "resultado": {
                    "nossoNumero": "123456789",
                    "seuNumero": kwargs["json"]["seuNumero"],
                    "codigoBarras": "00000.00000 00000.000000 00000.000000 0 00000000000000",
                }
            },
        )


@pytest.fixture
def sicoob_falso(monkeypatch):
    from types import SimpleNamespace

    empresa = SimpleNamespace(
        sicoob_beneficiario="91820",
        sicoob_client_id="client",
        sicoob_conta_corrente="110558",
    )
    monkeypatch.setattr(sicoob, "get_empresa", lambda db: empresa)
    monkeypatch.setattr(sicoob, "get_cert_config", lambda db: None)
    monkeypatch.setattr(
        sicoob, "refresh_sicoob_token",
        lambda db, scope="boletos_consulta": "token-teste",
    )
    monkeypatch.setattr(sicoob.httpx, "Client", FakeClient)
    return empresa


def _conta(db_session, cliente, **kwargs):
    dados = dict(
        cliente_id=cliente.id,
        descricao="Cobranca teste",
        valor=100,
        data_vencimento=date(2030, 1, 10),
        status=StatusConta.PENDENTE,
    )
    dados.update(kwargs)
    conta = ContaReceber(**dados)
    db_session.add(conta)
    db_session.commit()
    db_session.refresh(conta)
    return conta


def test_boleto_nao_colide_com_nosso_numero_de_conta_cancelada(
    sicoob_falso, db_session
):
    """Conta cancelada nao bloqueia a emissao; o numero antigo e preservado."""
    cliente = criar_cliente_teste(db_session)
    antiga = _conta(
        db_session, cliente,
        descricao="Cobranca cancelada", nosso_numero="4495",
        numero_documento="4495", status=StatusConta.CANCELADO,
    )
    nova = _conta(
        db_session, cliente,
        descricao="Cobranca nova", numero_documento="4495",
    )

    resultado = sicoob.emitir_boleto(db_session, nova)

    assert resultado["success"] is True, resultado
    db_session.expire_all()
    assert db_session.get(ContaReceber, antiga.id).nosso_numero == "4495"
    salva = db_session.get(ContaReceber, nova.id)
    assert salva.boleto_emitido is True
    assert salva.nosso_numero == f"4495-{nova.id}"
    assert db_session.is_active


def test_boleto_usa_sufixo_quando_numero_esta_ativo_em_outra_conta(
    sicoob_falso, db_session
):
    """Conta ativa com o mesmo numero nao e sobrescrita; a nova recebe sufixo."""
    cliente = criar_cliente_teste(db_session)
    ativa = _conta(
        db_session, cliente,
        descricao="Cobranca ativa", nosso_numero="4495",
        numero_documento="4495", boleto_emitido=True,
    )
    nova = _conta(
        db_session, cliente,
        descricao="Cobranca nova", numero_documento="4495",
    )

    resultado = sicoob.emitir_boleto(db_session, nova)

    assert resultado["success"] is True, resultado
    db_session.expire_all()
    assert db_session.get(ContaReceber, ativa.id).nosso_numero == "4495"
    assert db_session.get(ContaReceber, nova.id).nosso_numero == f"4495-{nova.id}"


def test_emitir_boletos_contas_reverte_sessao_apos_erro(db_session, monkeypatch):
    """Erro de banco no boleto nao deixa a sessao invalida para as proximas contas."""
    cliente = criar_cliente_teste(db_session)
    conta = _conta(db_session, cliente, descricao="Cobranca com erro")

    def _falha(db, conta):
        # Simula um erro de banco que marca a sessao como PendingRollback.
        db.execute(text("SELECT * FROM tabela_que_nao_existe"))
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(sicoob, "emitir_boleto", _falha)

    ok, erros = emitir_boletos_contas(db_session, [conta])

    assert ok == 0
    assert len(erros) == 1
    assert db_session.is_active, "a sessao precisa de rollback apos o erro"
    # A sessao volta a funcionar.
    assert db_session.query(ContaReceber).count() == 1

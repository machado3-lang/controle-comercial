"""Testes da baixa de boleto no Sicoob (idempotencia real + exclusao de cobranca)."""
from datetime import date

import pytest

import routers.sicoob as sicoob
from models import ContaReceber, StatusConta
from routers.contas import excluir_conta_receber
from tests.conftest import criar_cliente_teste


class FakeResponse:
    def __init__(self, status_code=204, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeClient:
    """Cliente httpx falso: registra as chamadas e devolve respostas fixas."""

    def __init__(self, *args, **kwargs):
        self.gets = []
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return FakeResponse(200, {"resultado": {"boletos": []}})

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return getattr(self, "_resposta_post", FakeResponse(204))


class FakeFactory:
    """Guarda os clientes criados e a resposta que o POST deve devolver."""

    def __init__(self):
        self.clients = []
        self.resposta_post = FakeResponse(204)

    def __call__(self, *args, **kwargs):
        c = FakeClient(*args, **kwargs)
        c._resposta_post = self.resposta_post
        self.clients.append(c)
        return c


@pytest.fixture
def conta_com_boleto(db_session):
    cliente = criar_cliente_teste(db_session)
    conta = ContaReceber(
        cliente_id=cliente.id,
        descricao="Cobranca teste",
        valor=100,
        data_vencimento=date(2030, 1, 10),
        status=StatusConta.PENDENTE,
        boleto_emitido=True,
        nosso_numero="123",
        api_nosso_numero="99001",
    )
    db_session.add(conta)
    db_session.commit()
    db_session.refresh(conta)
    return conta


@pytest.fixture
def sicoob_falso(monkeypatch, db_session):
    """Substitui empresa/token/cert e o cliente HTTP do modulo sicoob."""
    from types import SimpleNamespace

    empresa = SimpleNamespace(sicoob_beneficiario="91820", sicoob_client_id="x")
    monkeypatch.setattr(sicoob, "get_empresa", lambda db: empresa)
    monkeypatch.setattr(sicoob, "get_cert_config", lambda db: None)
    monkeypatch.setattr(sicoob, "refresh_sicoob_token", lambda db, scope="boletos_consulta": "token-teste")

    clients = []

    def _client(*args, **kwargs):
        c = FakeClient(*args, **kwargs)
        clients.append(c)
        return c

    factory = FakeFactory()

    def _client(*args, **kwargs):
        return factory(*args, **kwargs)

    monkeypatch.setattr(sicoob.httpx, "Client", _client)
    return factory


def test_baixa_de_conta_excluida_com_boleto_aberto(sicoob_falso, db_session, conta_com_boleto, monkeypatch):
    """Conta EXCLUIDA com boleto ainda aberto no Sicoob: a baixa e comandada."""
    conta_com_boleto.status = StatusConta.EXCLUIDO
    db_session.commit()
    monkeypatch.setattr(
        sicoob, "consultar_situacao_boleto_sicoob", lambda db, conta: (sicoob.SITUACAO_ABERTA, None)
    )

    resultado = sicoob.baixar_boleto_sicoob(db_session, conta_com_boleto, motivo="teste")

    assert resultado["success"] is True
    assert any("/baixar" in url for url, _ in sicoob_falso.clients[-1].posts), "a baixa nao foi comandada no Sicoob"
    db_session.expire_all()
    assert db_session.get(ContaReceber, conta_com_boleto.id).status == StatusConta.EXCLUIDO


def test_baixa_idempotente_quando_sicoob_ja_baixou(sicoob_falso, db_session, conta_com_boleto, monkeypatch):
    """Se o Sicoob informa BAIXADO, nao comanda nova baixa."""
    conta_com_boleto.status = StatusConta.CANCELADO
    db_session.commit()
    monkeypatch.setattr(
        sicoob, "consultar_situacao_boleto_sicoob", lambda db, conta: (sicoob.SITUACAO_BAIXADA, None)
    )

    resultado = sicoob.baixar_boleto_sicoob(db_session, conta_com_boleto)

    assert resultado["success"] is True
    assert resultado.get("ja_baixado") is True
    assert sicoob_falso.clients == [], "nao deveria comandar baixa de boleto ja baixado"


def test_falha_na_baixa_restaura_status(sicoob_falso, db_session, conta_com_boleto):
    """Falha na baixa devolve a conta ao status original (nao fica BAIXA_SOLICITADA)."""
    sicoob_falso.resposta_post = FakeResponse(400, text="rejeitado")

    resultado = sicoob.baixar_boleto_sicoob(db_session, conta_com_boleto)

    assert resultado["success"] is False
    db_session.expire_all()
    assert db_session.get(ContaReceber, conta_com_boleto.id).status == StatusConta.PENDENTE


def test_exclusao_bloqueia_conta_recebida(db_session, test_user, conta_com_boleto):
    """Excluir conta ja recebida apagaria o recebimento do financeiro."""
    conta_com_boleto.status = StatusConta.PAGO
    db_session.commit()

    class FakeRequest:
        session = {"user_id": test_user.id}
        client = None

    resp = excluir_conta_receber(FakeRequest(), conta_com_boleto.id, db_session, senha="test123")

    assert resp.status_code == 400
    db_session.expire_all()
    assert db_session.get(ContaReceber, conta_com_boleto.id).status == StatusConta.PAGO


def test_exclusao_bloqueia_quando_boleto_esta_liquidado(
    db_session, test_user, conta_com_boleto, monkeypatch
):
    """Boleto liquidado no Sicoob: a exclusao e barrada."""
    import routers.contas as contas

    monkeypatch.setattr(
        "routers.sicoob.baixar_boleto_sicoob",
        lambda db, conta, motivo="": {"success": True, "liquidado": True, "message": "liquidado"},
    )

    class FakeRequest:
        session = {"user_id": test_user.id}
        client = None

    resp = excluir_conta_receber(FakeRequest(), conta_com_boleto.id, db_session, senha="test123")

    assert resp.status_code == 400
    db_session.expire_all()
    assert db_session.get(ContaReceber, conta_com_boleto.id).status == StatusConta.PENDENTE

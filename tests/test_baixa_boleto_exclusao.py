"""Testes da baixa de boleto no Sicoob (idempotencia real + exclusao de cobranca)."""
from datetime import date

import pytest

import routers.sicoob as sicoob
from fastapi.responses import RedirectResponse
from models import ContaReceber, NFe, StatusConta
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


def test_recibo_sem_nota_continua_gerando_contas(db_session, test_empresa):
    """Excecao a regra: 'Gerar recibo (sem NFs)' gera a conta no faturamento.

    Nao existe nota nesse fluxo, entao a cobranca nasce ali mesmo — inclusive
    com a flag "Gerar Cobranca" desligada.
    """
    from models import PedidoVenda
    from routers.pedidos import finalizar_pedido

    cliente = criar_cliente_teste(db_session, cpf_cnpj="55566677788")
    pedido = PedidoVenda(
        cliente_id=cliente.id, numero="9901", data=date.today(), total=100,
        status="APROVADO", forma_pagamento="aprazo", gerar_cobranca=False,
        num_parcelas=1, intervalo_dias=30, primeiro_vencimento=date(2030, 1, 10),
    )
    db_session.add(pedido)
    db_session.commit()

    class FakeRequest:
        session = {}
        client = None

    finalizar_pedido(
        FakeRequest(), pedido.id, db_session,
        tipo_pedido="venda", forma_pagamento="aprazo", gerar_boleto=False,
        terminos_boleto="", gerar_cobranca=False, acao="recibo",
        num_parcelas=1, primeiro_vencimento="2030-01-10", intervalo_dias=30,
    )

    db_session.expire_all()
    contas = db_session.query(ContaReceber).filter(ContaReceber.pedido_id == pedido.id).all()
    assert len(contas) == 1
    assert contas[0].status == StatusConta.PENDENTE


def test_gerar_contas_para_nota_aceita_pedido_id(db_session, conta_com_boleto):
    """A cobranca de uma nota pode ser vinculada ao pedido de origem."""
    from services.parcelamento import gerar_contas_receber_para_nota as gerar

    contas = gerar(
        db_session, nfe_id=None, cliente_id=conta_com_boleto.cliente_id,
        descricao="Pedido 89 - NFe", valor_total=50,
        primeiro_vencimento=date(2030, 1, 10), num_parcelas=1,
        forma_pagamento="aprazo", pedido_id=999,
    )
    db_session.commit()

    assert len(contas) == 1
    assert contas[0].pedido_id == 999


def test_rascunho_nfe_de_pedido_nao_gera_contas(db_session, test_empresa):
    """Rascunho de NFe a partir de pedido: NFe criada e ZERO contas a receber.

    A cobranca so nasce na transmissao (a nota ainda nao existe como documento
    autorizado). Antes este trecho quebrava com
    "gerar_contas_receber_para_nota() got an unexpected keyword argument 'pedido_id'".
    """
    import routers.nfe as nfe_router
    from models import Cliente, Produto, PedidoVenda, PedidoVendaItem

    test_empresa.notaas_api_key = "chave-teste"
    test_empresa.serie_nfe = 1
    db_session.commit()

    cliente = criar_cliente_teste(db_session, cpf_cnpj="11122233344")
    cliente.isento_ie = True
    db_session.commit()
    produto = Produto(nome="Produto NFe", preco=10, tipo="produto", ncm="12345678", unidade="UN")
    db_session.add(produto)
    db_session.commit()

    pedido = PedidoVenda(
        cliente_id=cliente.id, numero="9900", data=date.today(), total=10,
        status="FATURADO", forma_pagamento="aprazo", gerar_cobranca=True,
        num_parcelas=2, intervalo_dias=30, primeiro_vencimento=date(2030, 1, 10),
    )
    db_session.add(pedido)
    db_session.commit()
    db_session.add(PedidoVendaItem(
        pedido_id=pedido.id, produto_id=produto.id, descricao="Produto NFe",
        quantidade=1, preco_unitario=10, total=10,
    ))
    db_session.commit()

    class FakeRequest:
        session = {}
        client = None

    req = FakeRequest()
    resp = nfe_router.emitir_pedido_submit(
        req, pedido.id, db_session,
        natureza_operacao="Venda de mercadoria", cfop="5102",
    )

    assert isinstance(resp, RedirectResponse), req.session
    db_session.expire_all()
    nfe = db_session.query(NFe).filter(NFe.pedido_id == pedido.id).first()
    assert nfe is not None, "o rascunho da NFe deveria ter sido criado"
    assert nfe.status == "rascunho"
    assert db_session.query(ContaReceber).filter(ContaReceber.pedido_id == pedido.id).count() == 0
    assert db_session.query(ContaReceber).filter(ContaReceber.nfe_id == nfe.id).count() == 0


def _request_fake(user_id):
    class FakeRequest:
        session = {"user_id": user_id}
        client = None

    return FakeRequest()


def test_conferencia_abertos_aponta_boletos_sem_cobranca_ativa(
    db_session, test_user, conta_com_boleto, monkeypatch
):
    """Conferencia inversa: boleto aberto no Sicoob sem cobranca ativa local."""
    conta_com_boleto.status = StatusConta.EXCLUIDO
    db_session.commit()

    boletos = [
        {"nossoNumero": "99001", "seuNumero": "123", "valor": 100.0, "dataVencimento": "2030-01-10"},
        {"nossoNumero": "99099", "seuNumero": "999", "valor": 50.0, "dataVencimento": "2030-01-10"},
        {"nossoNumero": "99002", "seuNumero": "124", "valor": 10.0, "dataVencimento": "2030-01-10"},
    ]
    monkeypatch.setattr(
        sicoob, "_buscar_boletos_por_pagador",
        lambda db, cpf, di=None, df=None, codigo_situacao=None: (boletos, None),
    )

    # 99002 pertence a uma conta ativa -> nao e divergencia
    from tests.conftest import criar_cliente_teste
    cliente = criar_cliente_teste(db_session, cpf_cnpj="99988877000166")
    ativa = ContaReceber(
        cliente_id=cliente.id, descricao="Ativa", valor=10,
        data_vencimento=date(2030, 1, 10), status=StatusConta.PENDENTE,
        nosso_numero="99002", api_nosso_numero="99002",
    )
    db_session.add(ativa)
    db_session.commit()

    resp = sicoob.conferencia_boletos_abertos(
        _request_fake(test_user.id), db_session,
        data_inicio="2026-01-01", data_fim="2030-12-31", offset=0, limite=25,
    )

    tipos = {d["nossoNumero"]: d["tipo"] for d in resp["divergencias"]}
    assert tipos.get("99001") == "cancelada_local"
    assert tipos.get("99099") == "sem_cobranca_local"
    assert "99002" not in tipos, "boleto com cobranca ativa nao deve ser listado"


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

"""Testes da classificacao contabil automatica de contas a receber.

Cobre a resolucao por origem (NF-e, NFS-e, pedido, OS), o fallback da empresa,
o backfill e o DRE com rollup hierarquico. Roda em SQLite isolado (fixture
`db_classificacao`), sem tocar no banco de producao.
"""
import os
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from database import Base  # noqa: E402
from models import (  # noqa: E402
    Cliente,
    ContaPagar,
    ContaReceber,
    Empresa,
    OrdemServico,
    PedidoVenda,
    PedidoVendaItem,
    PlanoDeContas,
    Produto,
    StatusConta,
    StatusPedido,
)
from models_nfe import NFe, NFeItem, NFSe, NFSeItem  # noqa: E402
from models_estoque import OSPeca  # noqa: E402
import routers.contas as contas_router  # noqa: E402
from services.classificacao_contabil import (  # noqa: E402
    resolver_conta_boleto,
    resolver_conta_receita,
)
from services.parcelamento import gerar_contas_receber  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest_asyncio.fixture
def db_classificacao():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Sessao = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Sessao()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def cliente(db_classificacao):
    """Cliente de apoio: `contas_receber`, `NFe` e `PedidoVenda` o exigem."""
    c = Cliente(nome="Cliente Teste", cpf_cnpj="11111111111")
    db_classificacao.add(c)
    db_classificacao.commit()
    return c


@pytest.fixture
def plano(db_classificacao):
    """Plano de contas 3/3.1/3.2 + 4/4.1, realista para os testes."""
    c3 = PlanoDeContas(codigo="3", nome="RECEITAS", tipo="receita", nivel=1)
    c31 = PlanoDeContas(codigo="3.1", nome="Venda de Produtos", tipo="receita", nivel=2)
    c32 = PlanoDeContas(codigo="3.2", nome="Prestacao de Servicos", tipo="receita", nivel=2)
    c4 = PlanoDeContas(codigo="4", nome="DESPESAS", tipo="despesa", nivel=1)
    c41 = PlanoDeContas(codigo="4.1", nome="Custos Operacionais", tipo="despesa", nivel=2)
    for c in (c3, c31, c32, c4, c41):
        db_classificacao.add(c)
    db_classificacao.flush()
    c31.parent_id, c32.parent_id = c3.id, c3.id
    c41.parent_id = c4.id
    db_classificacao.commit()
    return {
        "receitas": c3, "produtos": c31, "servicos": c32,
        "despesas": c4, "custos": c41,
    }


@pytest.fixture
def empresa_padrao(db_classificacao, plano):
    e = Empresa(
        razao_social="Teste Ltda",
        nome_fantasia="Teste",
        cnpj="12345678000195",
        conta_receita_padrao_id=plano["produtos"].id,
    )
    db_classificacao.add(e)
    db_classificacao.commit()
    return e


def _produto(db, nome, tipo, conta_id):
    p = Produto(codigo=f"P{nome}", nome=nome, tipo=tipo, preco=Decimal("10"),
                situacao="A", conta_receita_id=conta_id)
    db.add(p)
    db.commit()
    return p


# --------------------------------------------------------------------------
# Cascata de resolucao
# --------------------------------------------------------------------------

def test_resolve_pelo_item_da_nfe(db_classificacao, plano, empresa_padrao, cliente):
    produto = _produto(db_classificacao, "Cabo de rede", "produto", plano["servicos"].id)
    nfe = NFe(cliente_id=cliente.id, numero="100", valor_total=Decimal("100"), status="issued")
    db_classificacao.add(nfe)
    db_classificacao.flush()
    db_classificacao.add(NFeItem(nfe_id=nfe.id, produto_id=produto.id,
                                 descricao="Cabo", quantidade=Decimal("1"),
                                 preco_unitario=Decimal("100"), total=Decimal("100")))
    db_classificacao.commit()

    conta_id, revisar = resolver_conta_receita(db_classificacao, nfe_id=nfe.id)
    assert conta_id == plano["servicos"].id
    assert revisar is False, "veio da origem, nao precisa de revisao"


def test_resolve_pelo_item_da_nfse(db_classificacao, plano, empresa_padrao, cliente):
    produto = _produto(db_classificacao, "Manutencao preventiva", "servico",
                       plano["servicos"].id)
    nfse = NFSe(cliente_id=cliente.id, valor_total=Decimal("250"), status="autorizada")
    db_classificacao.add(nfse)
    db_classificacao.flush()
    db_classificacao.add(NFSeItem(nfse_id=nfse.id, produto_id=produto.id,
                                  descricao="Manutencao", quantidade=Decimal("1"),
                                  valor_unitario=Decimal("250"),
                                  valor_total=Decimal("250")))
    db_classificacao.commit()

    conta_id, revisar = resolver_conta_receita(db_classificacao, nfse_id=nfse.id)
    assert conta_id == plano["servicos"].id
    assert revisar is False


def test_resolve_pelo_pedido(db_classificacao, plano, empresa_padrao, cliente):
    produto = _produto(db_classificacao, "Impressora", "produto", plano["produtos"].id)
    pedido = PedidoVenda(cliente_id=cliente.id, numero=1, status=StatusPedido.PENDENTE)
    db_classificacao.add(pedido)
    db_classificacao.flush()
    db_classificacao.add(PedidoVendaItem(pedido_id=pedido.id, produto_id=produto.id,
                                         descricao=produto.nome,
                                         quantidade=Decimal("1"),
                                         preco_unitario=Decimal("50"),
                                         total=Decimal("50")))
    db_classificacao.commit()

    conta_id, _ = resolver_conta_receita(db_classificacao, pedido_id=pedido.id)
    assert conta_id == plano["produtos"].id


def test_resolve_pela_os(db_classificacao, plano, empresa_padrao, cliente):
    peca = _produto(db_classificacao, "Fonte 12V", "produto", plano["produtos"].id)
    os = OrdemServico(cliente_id=cliente.id, equipamento="Impressora",
                   status="concluida", valor_total=Decimal("80"))
    db_classificacao.add(os)
    db_classificacao.flush()
    db_classificacao.add(OSPeca(os_id=os.id, produto_id=peca.id,
                                quantidade=Decimal("1")))
    db_classificacao.commit()

    conta_id, _ = resolver_conta_receita(db_classificacao, os_id=os.id)
    assert conta_id == plano["produtos"].id


def test_nota_mista_cai_no_fallback_e_marca_revisao(db_classificacao, plano, empresa_padrao, cliente):
    """Itens com contas diferentes nao podem escolher a 'maior': distorceria o DRE."""
    prod = _produto(db_classificacao, "Cabo", "produto", plano["produtos"].id)
    serv = _produto(db_classificacao, "Instalacao", "servico", plano["servicos"].id)
    nfe = NFe(cliente_id=cliente.id, numero="300", valor_total=Decimal("300"), status="issued")
    db_classificacao.add(nfe)
    db_classificacao.flush()
    db_classificacao.add(NFeItem(nfe_id=nfe.id, produto_id=prod.id, descricao="Cabo",
                                 quantidade=Decimal("1"), preco_unitario=Decimal("100"),
                                 total=Decimal("100")))
    db_classificacao.add(NFeItem(nfe_id=nfe.id, produto_id=serv.id, descricao="Instalacao",
                                 quantidade=Decimal("1"), preco_unitario=Decimal("200"),
                                 total=Decimal("200")))
    db_classificacao.commit()

    conta_id, revisar = resolver_conta_receita(db_classificacao, nfe_id=nfe.id)
    assert conta_id == plano["produtos"].id, "cai na conta padrao da empresa"
    assert revisar is True, "nota mista precisa de conferencia"


def test_conta_explicita_tem_precedencia(db_classificacao, plano, empresa_padrao):
    produto = _produto(db_classificacao, "Servico X", "servico", plano["servicos"].id)
    conta_id, revisar = resolver_conta_receita(
        db_classificacao, plano_conta_id=plano["produtos"].id
    )
    assert conta_id == plano["produtos"].id
    assert revisar is False
    assert produto.conta_receita_id == plano["servicos"].id


def test_conta_inativa_nao_e_usada(db_classificacao, plano, empresa_padrao):
    """Conta inativa sumiria do DRE (filtro ativo==True); classificar nela
    esconderia o valor do relatorio."""
    plano["servicos"].ativo = False
    db_classificacao.commit()

    conta_id, revisar = resolver_conta_receita(
        db_classificacao, plano_conta_id=plano["servicos"].id
    )
    assert conta_id == plano["produtos"].id, "cai no fallback, nao na conta inativa"
    assert revisar is True


def test_sem_origem_usa_padrao_da_empresa(db_classificacao, plano, empresa_padrao):
    conta_id, revisar = resolver_conta_receita(db_classificacao)
    assert conta_id == plano["produtos"].id
    assert revisar is True


def test_sem_nenhuma_conta_cadastrada(db_classificacao):
    """Base sem plano de contas: devolve None sem levantar excecao, para nao
    derrubar o faturamento."""
    conta_id, revisar = resolver_conta_receita(db_classificacao)
    assert conta_id is None
    assert revisar is True


def test_boleto_importado_usa_padrao(db_classificacao, plano, empresa_padrao):
    conta_id, revisar = resolver_conta_boleto(db_classificacao)
    assert conta_id == plano["produtos"].id
    assert revisar is True


# --------------------------------------------------------------------------
# Integracao: gerar_contas_receber classifica sozinho
# --------------------------------------------------------------------------

def test_gerar_contas_receber_classifica_pela_nfe(db_classificacao, plano, empresa_padrao, cliente):
    produto = _produto(db_classificacao, "Camera IP", "produto", plano["produtos"].id)
    nfe = NFe(cliente_id=cliente.id, numero="400", valor_total=Decimal("400"), status="issued")
    db_classificacao.add(nfe)
    db_classificacao.flush()
    db_classificacao.add(NFeItem(nfe_id=nfe.id, produto_id=produto.id, descricao="Camera",
                                 quantidade=Decimal("1"), preco_unitario=Decimal("400"),
                                 total=Decimal("400")))
    db_classificacao.commit()

    # NENHUM plano_conta_id informado: e o caminho dos 14 fluxos automaticos.
    contas = gerar_contas_receber(
        db_classificacao, cliente_id=cliente.id, descricao="NFe 1", valor_total=Decimal("400"),
        primeiro_vencimento=date.today(), nfe_id=nfe.id,
    )
    db_classificacao.commit()

    assert len(contas) == 1
    assert contas[0].plano_conta_id == plano["produtos"].id, "classificado pela origem"
    assert contas[0].classificacao_revisar is False


def test_gerar_contas_receber_parcelado_propaga_conta(db_classificacao, plano, empresa_padrao, cliente):
    produto = _produto(db_classificacao, "Nobreak", "produto", plano["produtos"].id)
    os = OrdemServico(cliente_id=cliente.id, equipamento="Nobreak",
                   status="concluida", valor_total=Decimal("300"))
    db_classificacao.add(os)
    db_classificacao.flush()
    db_classificacao.add(OSPeca(os_id=os.id, produto_id=produto.id, quantidade=Decimal("1")))
    db_classificacao.commit()

    contas = gerar_contas_receber(
        db_classificacao, cliente_id=cliente.id, descricao="OS", valor_total=Decimal("300"),
        primeiro_vencimento=date.today(), num_parcelas=3, os_id=os.id,
    )
    db_classificacao.commit()

    assert len(contas) == 3, "todas as parcelas recebem a conta"
    assert all(c.plano_conta_id == plano["produtos"].id for c in contas)
    assert len({c.parcelamento_grupo for c in contas}) == 1


def test_gerar_contas_receber_marca_revisao_no_fallback(db_classificacao, plano, empresa_padrao, cliente):
    """Sem origem e sem conta no item: gera classificada, mas sinalizada."""
    _produto(db_classificacao, "Servico avulso", "servico", None)
    contas = gerar_contas_receber(
        db_classificacao, cliente_id=cliente.id, descricao="Manual",
        valor_total=Decimal("100"), primeiro_vencimento=date.today(),
    )
    db_classificacao.commit()
    assert contas[0].plano_conta_id == plano["produtos"].id
    assert contas[0].classificacao_revisar is True


# --------------------------------------------------------------------------
# Backfill
# --------------------------------------------------------------------------

def test_backfill_classifica_receitas_existentes(db_classificacao, plano, empresa_padrao, cliente):
    produto = _produto(db_classificacao, "Patch panel", "produto", plano["produtos"].id)
    nfe = NFe(cliente_id=cliente.id, numero="60", valor_total=Decimal("60"), status="issued")
    db_classificacao.add(nfe)
    db_classificacao.flush()
    db_classificacao.add(NFeItem(nfe_id=nfe.id, produto_id=produto.id, descricao="Patch",
                                 quantidade=Decimal("1"), preco_unitario=Decimal("60"),
                                 total=Decimal("60")))
    db_classificacao.flush()

    antiga = ContaReceber(descricao="Antiga", cliente_id=cliente.id, valor=Decimal("60"),
                          data_vencimento=date.today(), status=StatusConta.PENDENTE,
                          nfe_id=nfe.id, plano_conta_id=None)
    sem_conta = ContaReceber(descricao="Boleto", cliente_id=cliente.id, valor=Decimal("20"),
                             data_vencimento=date.today(), status=StatusConta.PENDENTE,
                             plano_conta_id=None, observacao="Boleto Sicoob - 123")
    db_classificacao.add_all([antiga, sem_conta])
    db_classificacao.commit()

    from services.seed_classificacao import _backfill_classificacao
    _backfill_classificacao(db_classificacao)
    db_classificacao.refresh(antiga)
    db_classificacao.refresh(sem_conta)

    assert antiga.plano_conta_id == plano["produtos"].id
    assert antiga.classificacao_revisar is False, "veio da origem"
    assert sem_conta.plano_conta_id == plano["produtos"].id
    assert sem_conta.classificacao_revisar is True, "boleto nao tem origem: revisar"


def test_backfill_e_idempotente(db_classificacao, plano, empresa_padrao, cliente):
    c = ContaReceber(descricao="X", cliente_id=cliente.id, valor=Decimal("10"),
                     data_vencimento=date.today(), status=StatusConta.PENDENTE,
                     plano_conta_id=None)
    db_classificacao.add(c)
    db_classificacao.commit()

    from services.seed_classificacao import _backfill_classificacao
    _backfill_classificacao(db_classificacao)
    primeira = db_classificacao.query(ContaReceber).one().plano_conta_id
    _backfill_classificacao(db_classificacao)
    assert db_classificacao.query(ContaReceber).one().plano_conta_id == primeira


def test_seed_nao_sobrescreve_plano_existente(db_classificacao, plano, empresa_padrao):
    """O plano do usuario e preservado: o seed so age com a tabela vazia."""
    antes = db_classificacao.query(PlanoDeContas).count()
    from services.seed_classificacao import _semear_plano_minimo
    _semear_plano_minimo(db_classificacao)
    assert db_classificacao.query(PlanoDeContas).count() == antes


def test_conta_padrao_nao_sobrescreve_escolha(db_classificacao, plano):
    e = Empresa(razao_social="T", cnpj="1", conta_receita_padrao_id=plano["servicos"].id)
    db_classificacao.add(e)
    db_classificacao.commit()
    from services.seed_classificacao import _configurar_conta_padrao
    _configurar_conta_padrao(db_classificacao)
    db_classificacao.refresh(e)
    assert e.conta_receita_padrao_id == plano["servicos"].id


# --------------------------------------------------------------------------
# Select preserva conta inativa
# --------------------------------------------------------------------------

def test_select_inclui_conta_inativa_atual(db_classificacao, plano, empresa_padrao):
    """Se a conta atual sai do select, salvar o form zera o campo."""
    from routers.contas import _contas_receita_para_selecao

    plano["servicos"].ativo = False
    db_classificacao.commit()

    contas = _contas_receita_para_selecao(db_classificacao, plano["servicos"].id)
    ids = {c.id for c in contas}
    assert plano["servicos"].id in ids, "conta inativa ja vinculada precisa aparecer"
    assert plano["produtos"].id in ids


def test_select_sem_conta_atual_nao_traz_inativas(db_classificacao, plano, empresa_padrao):
    plano["servicos"].ativo = False
    db_classificacao.commit()
    from routers.contas import _contas_receita_para_selecao
    contas = _contas_receita_para_selecao(db_classificacao, None)
    assert plano["servicos"].id not in {c.id for c in contas}


# --------------------------------------------------------------------------
# DRE
# --------------------------------------------------------------------------

class _Req:
    """Request minimo: o DRE so usa .session e .app.state.templates."""

    def __init__(self, templates):
        self.session = {}
        self.headers = {}
        self.client = None
        self.app = type("A", (), {"state": type("S", (), {"templates": templates})()})()


class _TemplatesStub:
    """Captura o contexto do template sem renderizar.

    Renderizar `dre.html` exigiria o global `csrf_token` do app (definido em
    app/core/lifespan.py); aqui so interessa o contexto.
    """

    def __init__(self):
        self.ultimo = None

    def TemplateResponse(self, request, name, context=None, **kw):
        self.ultimo = context or {}
        return self.ultimo


@pytest.fixture
def templates_dre():
    """Stub no lugar do Jinja2Templates real."""
    return _TemplatesStub()


def test_dre_usa_valor_total_e_faz_rollup(db_classificacao, plano, empresa_padrao, cliente,
                                          templates_dre):
    """Tres garantias do DRE corrigidas:
    - soma valor_total (juros/desconto), nao valor
    - conta inativa continua no relatorio
    - grupos somam os filhos
    """
    c3, c31 = plano["receitas"], plano["produtos"]
    c4, c41 = plano["despesas"], plano["custos"]
    hoje = date.today()

    db_classificacao.add_all([
        # valor=100 mas valor_total=120 (10 de juros) -> DRE tem de dar 120
        ContaReceber(descricao="A", cliente_id=cliente.id, valor=Decimal("100.00"),
                     valor_total=Decimal("120.00"), data_vencimento=hoje,
                     data_recebimento=hoje, status=StatusConta.PAGO,
                     plano_conta_id=c31.id),
        ContaReceber(descricao="PENDENTE", cliente_id=cliente.id, valor=Decimal("777.00"),
                     data_vencimento=hoje, status=StatusConta.PENDENTE,
                     plano_conta_id=c31.id),
        ContaReceber(descricao="sem conta", cliente_id=cliente.id, valor=Decimal("999.00"),
                     data_vencimento=hoje, data_recebimento=hoje,
                     status=StatusConta.PAGO, plano_conta_id=None),
        ContaPagar(descricao="D", fornecedor_id=None, valor=Decimal("30.00"),
                   valor_total=Decimal("30.00"), data_vencimento=hoje,
                   data_pagamento=hoje, status=StatusConta.PAGO,
                   plano_conta_id=c41.id),
    ])
    db_classificacao.commit()

    # Grupo 3.1 recebe valor direto de um lancamento: o rollup tem de somar
    # direto + filhos, sem duplicar o que esta no grupo.
    db_classificacao.add(ContaReceber(descricao="G", cliente_id=cliente.id,
                                      valor=Decimal("5.00"),
                                      data_vencimento=hoje, data_recebimento=hoje,
                                      status=StatusConta.PAGO, plano_conta_id=c31.id))
    db_classificacao.commit()

    # Inativar a conta folha nao pode esconder o historico dela.
    plano["produtos"].ativo = False
    db_classificacao.commit()

    contas_router.dre(
        _Req(templates_dre), db=db_classificacao,
        data_inicio=(hoje - timedelta(days=1)).isoformat(),
        data_fim=(hoje + timedelta(days=1)).isoformat(),
    )
    receitas = {r.codigo: r.valor for r in templates_dre.ultimo["receitas"]}
    despesas = {d.codigo: d.valor for d in templates_dre.ultimo["despesas"]}

    assert float(receitas["3.1"]) == 125.00, "120 da folha + 5 direto no grupo"
    assert float(receitas["3"]) == 125.00, "raiz soma o grupo"
    assert float(receitas["--"]) == 999.00
    assert float(despesas["4.1"]) == 30.00
    assert float(despesas["4"]) == 30.00
    assert float(templates_dre.ultimo["total_receitas"]) == 125.00 + 999.00
    assert float(templates_dre.ultimo["saldo"]) == 125.00 + 999.00 - 30.00

    flags = {r.codigo: r.filhos for r in templates_dre.ultimo["receitas"]}
    assert flags["3"] is True, "3 RECEITAS e grupo (pai de 3.1 e 3.2)"
    assert flags["3.1"] is False, "3.1 nao tem filho, e folha"
    assert flags["--"] is False, "linha avulsa de sem classificacao"


def test_dre_mostra_conta_inativa_com_historico(db_classificacao, plano, empresa_padrao, cliente,
                                               templates_dre):
    """Regressao do bug: desativar uma conta sumia com ela do DRE."""
    hoje = date.today()
    plano["produtos"].ativo = False
    db_classificacao.add(ContaReceber(
        descricao="X", cliente_id=cliente.id, valor=Decimal("42.00"),
        data_vencimento=hoje, data_recebimento=hoje, status=StatusConta.PAGO,
        plano_conta_id=plano["produtos"].id))
    db_classificacao.commit()

    contas_router.dre(
        _Req(templates_dre), db=db_classificacao,
        data_inicio=(hoje - timedelta(days=1)).isoformat(),
        data_fim=(hoje + timedelta(days=1)).isoformat(),
    )
    receitas = {r.codigo: r.valor for r in templates_dre.ultimo["receitas"]}
    assert float(receitas["3.1"]) == 42.00


def test_dre_ignora_conta_nao_liquidada(db_classificacao, plano, empresa_padrao, cliente,
                                        templates_dre):
    """DRE e caixa: so entra o que foi efetivamente recebido/pago."""
    hoje = date.today()
    db_classificacao.add(ContaReceber(
        descricao="A", cliente_id=cliente.id, valor=Decimal("100.00"),
        data_vencimento=hoje, status=StatusConta.PENDENTE,
        plano_conta_id=plano["produtos"].id))
    db_classificacao.commit()

    contas_router.dre(
        _Req(templates_dre), db=db_classificacao,
        data_inicio=(hoje - timedelta(days=1)).isoformat(),
        data_fim=(hoje + timedelta(days=1)).isoformat(),
    )
    assert float(templates_dre.ultimo["total_receitas"]) == 0.0


def test_dre_tres_niveis_indentam_e_rollupam(db_classificacao, plano, empresa_padrao, cliente,
                                            templates_dre):
    hoje = date.today()
    folha = PlanoDeContas(codigo="3.1.1", nome="Cabos", tipo="receita", nivel=3,
                          parent_id=plano["produtos"].id)
    db_classificacao.add(folha)
    db_classificacao.flush()
    db_classificacao.add(ContaReceber(
        descricao="A", cliente_id=cliente.id, valor=Decimal("10.00"),
        data_vencimento=hoje, data_recebimento=hoje, status=StatusConta.PAGO,
        plano_conta_id=folha.id))
    db_classificacao.commit()

    contas_router.dre(
        _Req(templates_dre), db=db_classificacao,
        data_inicio=(hoje - timedelta(days=1)).isoformat(),
        data_fim=(hoje + timedelta(days=1)).isoformat(),
    )
    linhas = {r.codigo: r for r in templates_dre.ultimo["receitas"]}
    assert float(linhas["3.1.1"].valor) == 10.00
    assert float(linhas["3.1"].valor) == 10.00, "grupo recebe o do neto"
    assert float(linhas["3"].valor) == 10.00
    assert linhas["3.1.1"].nivel == 3, "o nivel habilita a indentacao no template"
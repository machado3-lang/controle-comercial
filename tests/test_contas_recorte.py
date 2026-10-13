"""Testes do recorte (filtro) de contas a receber e a pagar.

A listagem, a exportacao (CSV) e o PDF precisam cair no mesmo conjunto de
linhas e mostrar os totais desse conjunto. Antes:

- o valor total da tela vinha de uma query sem nenhum filtro (todas as contas
  em aberto), entao "Vencidos" mostrava 20 contas com o total geral;
- "Total Filtrado" contava a pagina atual (`contas|length`), nao o filtro todo;
- a exportacao definia "vencido" como `status == VENCIDO`, mas nenhum fluxo
  grava esse status (so e lido) — o arquivo saia com cabecalho e zero linhas;
- o PDF ignorava todos os parametros e imprimia sempre todas as contas em
  aberto, com o rotulo fixo.
"""
import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

import routers.contas as C
from models import ContaPagar, ContaReceber, StatusConta
from tests.conftest import criar_cliente_teste, criar_fornecedor_teste


class FakeRequest:
    def __init__(self):
        self.session = {}
        self.headers = {}
        self.app = SimpleNamespace(state=SimpleNamespace(templates=FakeTemplates()))


class FakeTemplates:
    def __init__(self):
        self.ctx = None

    def TemplateResponse(self, request, name, context=None, **kwargs):
        self.ctx = context
        return context


def criar_receber(db_session, cliente, *, valor, vencimento, status=StatusConta.PENDENTE, desc="Conta"):
    conta = ContaReceber(
        cliente_id=cliente.id, descricao=desc, valor=Decimal(str(valor)),
        data_vencimento=vencimento, status=status,
    )
    db_session.add(conta)
    db_session.commit()
    db_session.refresh(conta)
    return conta


def criar_pagar(db_session, fornecedor, *, valor, vencimento, status=StatusConta.PENDENTE):
    conta = ContaPagar(
        fornecedor_id=fornecedor.id, descricao="Conta", valor=Decimal(str(valor)),
        data_vencimento=vencimento, status=status,
    )
    db_session.add(conta)
    db_session.commit()
    db_session.refresh(conta)
    return conta


def listar(db_session, **kwargs):
    """Chama a rota de listagem e devolve o contexto enviado ao template."""
    req = FakeRequest()
    params = dict(
        busca="", status_filtro="", data_inicio="", data_fim="",
        page=1, per_page=20, sort="data_vencimento", ordem="asc", classificacao="",
    )
    params.update(kwargs)
    C.contas_receber(req, db_session, **params)
    return req.app.state.templates.ctx


def exportar(db_session, **kwargs):
    params = dict(
        busca="", status_filtro="", data_inicio="", data_fim="",
        sort="data_vencimento", ordem="asc", classificacao="",
    )
    params.update(kwargs)
    resp = C.exportar_contas_receber(FakeRequest(), db_session, **params)
    linhas = list(csv.reader(io.StringIO(resp.body.decode("utf-8-sig"))))
    return linhas[0], linhas[1:]


def test_valor_total_da_tela_respeita_o_filtro(db_session: Session, test_empresa):
    """Bug reportado: filtro Vencidos mostrava o total de todas as contas em aberto."""
    cliente = criar_cliente_teste(db_session)
    hoje = date.today()
    criar_receber(db_session, cliente, valor=100, vencimento=hoje - timedelta(days=10))
    criar_receber(db_session, cliente, valor=200, vencimento=hoje - timedelta(days=5))
    criar_receber(db_session, cliente, valor=900, vencimento=hoje + timedelta(days=30))
    criar_receber(db_session, cliente, valor=50, vencimento=hoje + timedelta(days=60),
                  status=StatusConta.PAGO)

    ctx = listar(db_session, status_filtro="vencido")

    assert Decimal(str(ctx["total_pendente"])) == Decimal("300.00")
    assert ctx["total_count"] == 2


def test_total_filtrado_conta_todas_as_paginas(db_session: Session, test_empresa):
    """'Total Filtrado' e do filtro inteiro, nao da pagina atual."""
    cliente = criar_cliente_teste(db_session)
    hoje = date.today()
    for i in range(5):
        criar_receber(db_session, cliente, valor=10, vencimento=hoje - timedelta(days=i + 1))

    ctx = listar(db_session, status_filtro="vencido", per_page=2, page=2)

    assert ctx["total_count"] == 5
    assert len(ctx["contas"]) == 2
    assert ctx["total_pages"] == 3


def test_filtro_vencido_deriva_do_vencimento(db_session: Session, test_empresa):
    """Nenhum fluxo grava StatusConta.VENCIDO: vencido e derivado do vencimento."""
    cliente = criar_cliente_teste(db_session)
    hoje = date.today()
    vencida = criar_receber(db_session, cliente, valor=10, vencimento=hoje - timedelta(days=1))
    assert vencida.status == StatusConta.PENDENTE

    ctx = listar(db_session, status_filtro="vencido")

    assert [c.id for c in ctx["contas"]] == [vencida.id]


def test_exportar_traz_as_mesmas_contas_da_listagem(db_session: Session, test_empresa):
    """Bug reportado: o 'Excel'/'CSV' de Vencidos saia so com o cabecalho."""
    cliente = criar_cliente_teste(db_session)
    hoje = date.today()
    for i in range(4):
        criar_receber(db_session, cliente, valor=10 * (i + 1), vencimento=hoje - timedelta(days=i + 1))
    criar_receber(db_session, cliente, valor=999, vencimento=hoje + timedelta(days=10))

    cabecalho, linhas = exportar(db_session, status_filtro="vencido")
    ctx = listar(db_session, status_filtro="vencido")

    assert len(linhas) == ctx["total_count"] == 4
    assert len(cabecalho) == 6
    assert "999,00" not in "\n".join(",".join(l) for l in linhas)


def test_exportar_respeita_periodo_e_busca(db_session: Session, test_empresa):
    cliente = criar_cliente_teste(db_session, nome="Empresa Alfa")
    outro = criar_cliente_teste(db_session, cpf_cnpj="98765432100", nome="Empresa Beta")
    hoje = date.today()
    criar_receber(db_session, cliente, valor=10, vencimento=hoje - timedelta(days=5), desc="Alfa A")
    criar_receber(db_session, cliente, valor=20, vencimento=hoje - timedelta(days=3), desc="Alfa B")
    criar_receber(db_session, outro, valor=30, vencimento=hoje - timedelta(days=2), desc="Beta C")

    _cabecalho, todas = exportar(db_session, status_filtro="vencido")
    assert len(todas) == 3

    _cabecalho, por_busca = exportar(db_session, status_filtro="vencido", busca="Alfa")
    assert len(por_busca) == 2

    _cabecalho, por_periodo = exportar(
        db_session, status_filtro="vencido", data_inicio=(hoje - timedelta(days=4)).isoformat()
    )
    assert len(por_periodo) == 2  # a de hoje-5 fica fora


def test_exportar_data_invalida_nao_quebra(db_session: Session, test_empresa):
    cliente = criar_cliente_teste(db_session)
    criar_receber(db_session, cliente, valor=10, vencimento=date.today())

    _cabecalho, linhas = exportar(db_session, data_inicio="31/12/2026")

    assert len(linhas) == 1


def test_pdf_respeita_o_filtro(db_session: Session, test_empresa, monkeypatch):
    """O PDF recebia os parametros e ignorava todos: imprimia as contas em aberto."""
    capturado = {}

    def fake_gerar_pdf_contas(contas, empresa, tipo="receber", filtros=None, **kwargs):
        capturado["ids"] = [c.id for c in contas]
        capturado["filtros"] = filtros
        return b"%PDF-1.4 fake"

    monkeypatch.setattr("services.nfse_pdf.gerar_pdf_contas", fake_gerar_pdf_contas)

    cliente = criar_cliente_teste(db_session)
    hoje = date.today()
    vencida = criar_receber(db_session, cliente, valor=10, vencimento=hoje - timedelta(days=2))
    em_aberto = criar_receber(db_session, cliente, valor=20, vencimento=hoje + timedelta(days=5))
    paga = criar_receber(db_session, cliente, valor=30, vencimento=hoje - timedelta(days=9),
                         status=StatusConta.PAGO)

    C.contas_receber_pdf(FakeRequest(), db_session, busca="", status_filtro="vencido",
                         data_inicio="", data_fim="", classificacao="")
    assert capturado["ids"] == [vencida.id]
    assert "Vencido" in capturado["filtros"]

    C.contas_receber_pdf(FakeRequest(), db_session, busca="", status_filtro="",
                         data_inicio="", data_fim="", classificacao="")
    assert set(capturado["ids"]) == {vencida.id, em_aberto.id}
    assert paga.id not in capturado["ids"]


def test_pagar_usa_o_mesmo_recorte(db_session: Session, test_empresa):
    """Contas a pagar tinham a mesma duplicacao de filtro."""
    fornecedor = criar_fornecedor_teste(db_session)
    hoje = date.today()
    criar_pagar(db_session, fornecedor, valor=100, vencimento=hoje - timedelta(days=4))
    criar_pagar(db_session, fornecedor, valor=700, vencimento=hoje + timedelta(days=4))

    resp = C.exportar_contas_pagar(
        FakeRequest(), db_session, busca="", status_filtro="vencido",
        data_inicio="", data_fim="", sort="data_vencimento", ordem="asc",
    )
    linhas = list(csv.reader(io.StringIO(resp.body.decode("utf-8-sig"))))

    assert len(linhas) - 1 == 1


def test_csv_tem_bom_utf8(db_session: Session, test_empresa):
    """Sem BOM o Excel abre o CSV com os acentos quebrados."""
    cliente = criar_cliente_teste(db_session, nome="Construtora São Paulo")
    criar_receber(db_session, cliente, valor=10, vencimento=date.today())

    resp = C.exportar_contas_receber(
        FakeRequest(), db_session, busca="", status_filtro="", data_inicio="", data_fim="",
        sort="data_vencimento", ordem="asc", classificacao="",
    )

    assert resp.body.startswith(b"\xef\xbb\xbf")
    assert "São Paulo" in resp.body.decode("utf-8-sig")


@pytest.mark.asyncio
async def test_tela_mostra_total_do_filtro(authenticated_client, db_session: Session, test_empresa):
    """A tela precisa exibir a contagem e o valor do recorte, nao da pagina atual."""
    cliente = criar_cliente_teste(db_session)
    hoje = date.today()
    for i in range(3):
        criar_receber(db_session, cliente, valor=10, vencimento=hoje - timedelta(days=i + 1))

    resp = await authenticated_client.get("/contas/receber?status_filtro=vencido&per_page=2")

    assert resp.status_code == 200
    assert "3 contas" in resp.text
    assert "30.00" in resp.text
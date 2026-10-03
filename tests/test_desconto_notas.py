"""Regressao do desconto: pedido -> NFe/NFSe -> cobranca.

Regra: o desconto nunca e "enterrado" no preco unitario quando o documento
tem vDesc (NFe). O valor gravado na nota e o LIQUIDO, e a cobrança gerada
depois usa esse mesmo valor (antes saia pelo bruto).
"""
import json
import re
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from models import (
    ContaReceber, NFe, NFSe, PedidoConsolidado, PedidoVenda, PedidoVendaItem,
    Produto, StatusPedido,
)
from services.desconto import (
    calcular_valores_com_desconto, distribuir_desconto, ratear_por_bruto,
    total_bruto,
)
from tests.conftest import criar_cliente_teste


# ---------------------------------------------------------------- unitario

def test_distribuir_desconto_sem_perder_centavos():
    itens = [
        {"preco_unitario": Decimal("100.00"), "quantidade": Decimal("1")},
        {"preco_unitario": Decimal("100.00"), "quantidade": Decimal("1")},
        {"preco_unitario": Decimal("100.00"), "quantidade": Decimal("1")},
    ]
    aplicado = distribuir_desconto(itens, Decimal("10.00"))
    assert aplicado == Decimal("10.00")
    assert sum(i["desconto"] for i in itens) == Decimal("10.00")
    # preco unitario intacto
    assert all(i["preco_unitario"] == Decimal("100.00") for i in itens)


def test_ratear_por_bruto_fecha_com_o_total():
    a, b = ratear_por_bruto(Decimal("300.00"), Decimal("100.00"), Decimal("40.00"))
    assert a == Decimal("30.00")
    assert b == Decimal("10.00")
    assert a + b == Decimal("40.00")


def test_desconto_maior_que_bruto_e_limitado():
    itens = [{"preco_unitario": Decimal("50.00"), "quantidade": Decimal("1")}]
    assert distribuir_desconto(itens, Decimal("999.00")) == Decimal("50.00")


def test_calcular_valores_com_desconto_nao_mutou_o_item():
    class _Item:
        quantidade = 2
        preco_unitario = Decimal("10.00")

    item = _Item()
    valores = calcular_valores_com_desconto([item], Decimal("5.00"))
    assert item.preco_unitario == Decimal("10.00")  # intacto
    assert valores[0][1] == Decimal("15.00")  # 20 - 5


def test_total_bruto_soma_qtd_vezes_preco():
    itens = [{"preco_unitario": Decimal("3.50"), "quantidade": Decimal("2")}]
    assert total_bruto(itens) == Decimal("7.00")


# ------------------------------------------------------------- integracao

async def _csrf(client: AsyncClient, url: str) -> str:
    resp = await client.get(url)
    assert resp.status_code == 200
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    return m.group(1) if m else "dummy"


_seq = {"n": 0}


def _criar_pre_venda(db_session, cliente_id, produto_id, total=20.0, desconto=5.0):
    _seq["n"] += 1
    pedido = PedidoVenda(
        cliente_id=cliente_id, numero=f"PV-{_seq['n']}-{produto_id}",
        status=StatusPedido.PRE_VENDA, total=total - desconto,
        valor_desconto=desconto, tipo_pedido="pre_venda",
    )
    db_session.add(pedido)
    db_session.flush()
    db_session.add(PedidoVendaItem(
        pedido_id=pedido.id, produto_id=produto_id, descricao="Servico A",
        quantidade=2, preco_unitario=total / 2, total=total,
    ))
    db_session.commit()
    return pedido.id


@pytest.mark.asyncio
async def test_consolidacao_herda_desconto_dos_pedidos(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """2 pre-vendas de 20 com 5 de desconto cada => consolidado 30 (40 - 10)."""
    cliente = criar_cliente_teste(db_session)
    produto = Produto(nome="Servico A", preco=10, tipo="servico")
    db_session.add(produto)
    db_session.commit()
    ids = [
        _criar_pre_venda(db_session, cliente.id, produto.id, total=20.0, desconto=5.0),
        _criar_pre_venda(db_session, cliente.id, produto.id, total=20.0, desconto=5.0),
    ]

    csrf = await _csrf(authenticated_client, "/consolidacoes/nova")
    resp = await authenticated_client.post("/consolidacoes/criar", data={
        "csrf_token": csrf,
        "pedido_ids": [str(i) for i in ids],
    })
    assert resp.status_code == 303
    cons_id = int(resp.headers["location"].rstrip("/").split("/")[-1])

    db_session.expire_all()
    cons = db_session.get(PedidoConsolidado, cons_id)
    assert float(cons.valor_desconto) == 10.0
    # total liquido (antes seria 40.0, o bruto)
    assert float(cons.total) == 30.0


@pytest.mark.asyncio
async def test_nfse_de_consolidacao_sai_com_desconto(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """A NFS-e da consolidacao sai pelo liquido (desconto no valor unitario)."""
    cliente = criar_cliente_teste(db_session)
    produto = Produto(nome="Servico A", preco=10, tipo="servico")
    db_session.add(produto)
    db_session.commit()
    ids = [_criar_pre_venda(db_session, cliente.id, produto.id, total=100.0, desconto=10.0)]

    csrf = await _csrf(authenticated_client, "/consolidacoes/nova")
    resp = await authenticated_client.post("/consolidacoes/criar", data={
        "csrf_token": csrf,
        "pedido_ids": [str(i) for i in ids],
    })
    cons_id = int(resp.headers["location"].rstrip("/").split("/")[-1])

    resp = await authenticated_client.post(
        f"/consolidacoes/{cons_id}/finalizar",
        data={"csrf_token": csrf, "forma_pagamento": "avista", "num_parcelas": "1",
              "intervalo_dias": "30", "gerar_cobranca": "on"},
    )
    assert resp.status_code == 303

    resp = await authenticated_client.post(
        f"/nfse/emitir/consolidacao/{cons_id}", data={"csrf_token": csrf}
    )
    assert resp.status_code == 303

    db_session.expire_all()
    nfse = db_session.query(NFSe).filter(NFSe.consolidacao_id == cons_id).first()
    assert nfse is not None
    # 100 de servicos - 10 de desconto
    assert float(nfse.valor_total) == 90.0
    assert float(sum(i.valor_total or 0 for i in nfse.itens)) == 90.0


@pytest.mark.asyncio
async def test_nfse_de_pedido_sai_com_o_desconto_do_pedido(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Pedido com desconto: a NFS-e emitida sai pelo liquido, sem alterar os
    itens do pedido."""
    cliente = criar_cliente_teste(db_session)
    servico = Produto(nome="Servico Pedido", preco=200, tipo="servico")
    db_session.add(servico)
    db_session.commit()

    pedido = PedidoVenda(
        cliente_id=cliente.id, numero="PV-DESC-1", status=StatusPedido.PENDENTE,
        total=180.0, valor_desconto=20.0, desconto_percentual=10.0,
        tipo_pedido="venda",
    )
    db_session.add(pedido)
    db_session.flush()
    item = PedidoVendaItem(
        pedido_id=pedido.id, produto_id=servico.id, descricao=servico.nome,
        quantidade=1, preco_unitario=200.0, total=200.0,
    )
    db_session.add(item)
    db_session.commit()

    csrf = await _csrf(authenticated_client, f"/pedidos/{pedido.id}")
    resp = await authenticated_client.post(
        f"/nfse/emitir/{pedido.id}", data={"csrf_token": csrf}
    )
    assert resp.status_code == 303

    db_session.expire_all()
    nfse = db_session.query(NFSe).filter(NFSe.pedido_id == pedido.id).first()
    assert nfse is not None
    assert float(nfse.valor_total) == 180.0
    # o item do PEDIDO continua com o preco original
    assert float(db_session.get(PedidoVendaItem, item.id).preco_unitario) == 200.0


@pytest.mark.asyncio
async def test_nfe_avulsa_grava_total_liquido_e_cobranca_liquida(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Desconto na NFe avulsa: valor_total liquido, vDesc nos itens e conta
    a receber pelo liquido (antes a conta saia pelo bruto)."""
    test_empresa.notaas_api_key = "test-key"
    db_session.commit()

    cliente = criar_cliente_teste(db_session, inscricao_estadual="123456")
    produto = Produto(nome="Produto NFe", preco=100, tipo="produto")
    db_session.add(produto)
    db_session.commit()

    itens = [{
        "produto_id": produto.id, "descricao": produto.nome, "ncm": "99999999",
        "unidade": "UN", "quantidade": 3, "preco_unitario": 100.0,
    }]
    csrf = await _csrf(authenticated_client, "/nfe/emitir/avulsa")
    resp = await authenticated_client.post("/nfe/emitir/avulsa", data={
        "csrf_token": csrf,
        "cliente_id": str(cliente.id),
        "itens_json": json.dumps(itens),
        "desconto": "30.00",
        "gerar_cobranca": "on",
        "num_parcelas": "1",
        "forma_pagamento": "pix",
    })
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.query(NFe).filter(NFe.cliente_id == cliente.id).first()
    assert nfe is not None
    assert float(nfe.valor_total) == 270.0            # 300 - 30
    assert float(sum(i.desconto or 0 for i in nfe.itens)) == 30.0
    assert float(nfe.itens[0].preco_unitario) == 100.0  # nao enterra no preco

    conta = db_session.query(ContaReceber).filter(ContaReceber.nfe_id == nfe.id).first()
    assert conta is not None
    assert float(conta.valor) == 270.0


@pytest.mark.asyncio
async def test_editar_nfe_mantem_desconto_como_vdesc(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Editar nao pode enterrar o desconto no preco unitario (criterio unico)."""
    test_empresa.notaas_api_key = "test-key"
    db_session.commit()

    cliente = criar_cliente_teste(db_session, inscricao_estadual="123456")
    produto = Produto(nome="Produto NFe 2", preco=50, tipo="produto")
    db_session.add(produto)
    db_session.commit()

    itens = [{
        "produto_id": produto.id, "descricao": produto.nome, "ncm": "99999999",
        "unidade": "UN", "quantidade": 2, "preco_unitario": 50.0,
    }]
    csrf = await _csrf(authenticated_client, "/nfe/emitir/avulsa")
    resp = await authenticated_client.post("/nfe/emitir/avulsa", data={
        "csrf_token": csrf, "cliente_id": str(cliente.id),
        "itens_json": json.dumps(itens), "desconto": "10.00",
    })
    assert resp.status_code == 303
    nfe = db_session.query(NFe).filter(NFe.cliente_id == cliente.id).first()

    # Reabre a tela de edicao (popula a sessao) e salva de novo
    resp = await authenticated_client.get(f"/nfe/{nfe.id}/editar")
    assert resp.status_code == 303
    resp = await authenticated_client.post(f"/nfe/{nfe.id}/editar", data={
        "csrf_token": csrf, "cliente_id": str(cliente.id),
        "itens_json": json.dumps(itens), "desconto": "10.00",
    })
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.get(NFe, nfe.id)
    assert float(nfe.valor_total) == 90.0                  # 100 - 10
    assert float(nfe.itens[0].preco_unitario) == 50.0      # preco intacto
    assert float(nfe.itens[0].desconto) == 10.0

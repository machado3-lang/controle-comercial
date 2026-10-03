import json
import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session
from models import PedidoVenda, Produto
from tests.conftest import criar_cliente_teste


async def _csrf(client: AsyncClient, url: str) -> str:
    import re
    resp = await client.get(url)
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    return m.group(1) if m else "dummy_csrf_token"


def _criar_produto(db_session: Session, nome: str, preco: float) -> Produto:
    p = Produto(nome=nome, preco=preco, tipo="produto")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


async def _salvar_pedido(client, csrf, cliente_id, itens, **desc):
    payload = {
        "csrf_token": csrf,
        "cliente_id": str(cliente_id),
        "data": "2026-10-01",
        "forma_pagamento": "avista",
        "itens": json.dumps(itens),
    }
    payload.update(desc)
    return await client.post("/pedidos/salvar", data=payload)


@pytest.mark.asyncio
async def test_salvar_pedido_com_desconto_em_reais(authenticated_client: AsyncClient, db_session: Session, test_empresa):
    cliente = criar_cliente_teste(db_session)
    prod = _criar_produto(db_session, "Produto Desc", 100.0)
    csrf = await _csrf(authenticated_client, "/pedidos/novo")

    itens = [{"item_id": prod.id, "variacao_id": None, "descricao": prod.nome, "quantidade": 2, "preco": 100.0}]
    # 2 x 100 = 200 de subtotal, desconto informado em R$ (12.75 com centavos)
    resp = await _salvar_pedido(authenticated_client, csrf, cliente.id, itens,
                                valor_desconto="12.75", desconto_percentual="6.38")
    assert resp.status_code == 303

    pedido = db_session.query(PedidoVenda).filter(PedidoVenda.cliente_id == cliente.id).first()
    assert pedido is not None
    assert float(pedido.subtotal) == 200.0
    assert float(pedido.valor_desconto) == 12.75
    assert float(pedido.desconto_percentual) == round(12.75 / 200 * 100, 2)
    # total liquido, e nao a soma bruta dos itens
    assert float(pedido.total) == 187.25


@pytest.mark.asyncio
async def test_salvar_pedido_com_desconto_percentual(authenticated_client: AsyncClient, db_session: Session, test_empresa):
    cliente = criar_cliente_teste(db_session)
    prod = _criar_produto(db_session, "Produto Pct", 50.0)
    csrf = await _csrf(authenticated_client, "/pedidos/novo")

    itens = [{"item_id": prod.id, "variacao_id": None, "descricao": prod.nome, "quantidade": 3, "preco": 50.0}]
    # 150 de subtotal, 10% => 15.00
    resp = await _salvar_pedido(authenticated_client, csrf, cliente.id, itens,
                                desconto_percentual="10", valor_desconto="15.00")
    assert resp.status_code == 303

    pedido = db_session.query(PedidoVenda).filter(PedidoVenda.cliente_id == cliente.id).first()
    assert float(pedido.valor_desconto) == 15.00
    assert float(pedido.total) == 135.00


@pytest.mark.asyncio
async def test_desconto_maior_que_subtotal_e_limitado(authenticated_client: AsyncClient, db_session: Session, test_empresa):
    cliente = criar_cliente_teste(db_session)
    prod = _criar_produto(db_session, "Produto Limite", 10.0)
    csrf = await _csrf(authenticated_client, "/pedidos/novo")

    itens = [{"item_id": prod.id, "variacao_id": None, "descricao": prod.nome, "quantidade": 1, "preco": 10.0}]
    resp = await _salvar_pedido(authenticated_client, csrf, cliente.id, itens,
                                valor_desconto="9999.99", desconto_percentual="100")
    assert resp.status_code == 303

    pedido = db_session.query(PedidoVenda).filter(PedidoVenda.cliente_id == cliente.id).first()
    assert float(pedido.valor_desconto) == 10.0
    assert float(pedido.total) == 0.0


@pytest.mark.asyncio
async def test_edicao_reabre_campos_de_desconto(authenticated_client: AsyncClient, db_session: Session, test_empresa):
    from models import PedidoVendaItem
    cliente = criar_cliente_teste(db_session)
    prod = _criar_produto(db_session, "Produto Edit", 80.0)

    pedido = PedidoVenda(
        cliente_id=cliente.id, numero="900", data=None, status="pendente",
        tipo_pedido="venda", total=72.0, valor_desconto=8.0, desconto_percentual=10.0,
    )
    db_session.add(pedido)
    db_session.flush()
    db_session.add(PedidoVendaItem(
        pedido_id=pedido.id, produto_id=prod.id, descricao=prod.nome,
        quantidade=1, preco_unitario=80.0, total=80.0,
    ))
    db_session.commit()

    resp = await authenticated_client.get(f"/pedidos/{pedido.id}/editar")
    assert resp.status_code == 200
    # Os campos de desconto voltam preenchidos (o resumo fica fora do <form>,
    # por isso viajam em hidden + value dos inputs % e R$).
    assert 'id="descontoHidden" value="8.00"' in resp.text
    assert 'id="descontoPercentualHidden" value="10.00"' in resp.text
    assert 'id="descontoValor" class=' in resp.text

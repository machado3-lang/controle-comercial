import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session
from models import PedidoVenda, PedidoVendaItem, StatusPedido, Cliente, Produto, FormaPagamento
from tests.conftest import criar_cliente_teste

@pytest.mark.asyncio
async def test_agrupamento_pre_venda(authenticated_client: AsyncClient, db_session: Session, test_empresa):
    # 1. Criar um cliente
    cliente = criar_cliente_teste(db_session)
    
    # 2. Criar produtos
    prod1 = Produto(nome="Produto 1", preco=10.0, tipo="produto")
    prod2 = Produto(nome="Produto 2", preco=20.0, tipo="produto")
    db_session.add(prod1)
    db_session.add(prod2)
    db_session.commit()
    db_session.refresh(prod1)
    db_session.refresh(prod2)

    # 3. Criar pré-vendas
    p1 = PedidoVenda(
        cliente_id=cliente.id,
        status=StatusPedido.PRE_VENDA,
        numero="PV1",
        total=30.0,
        tipo_pedido="pre_venda"
    )
    db_session.add(p1)
    db_session.flush()

    item1 = PedidoVendaItem(
        pedido_id=p1.id,
        produto_id=prod1.id,
        descricao=prod1.nome,
        quantidade=1.0,
        preco_unitario=10.0,
        total=10.0
    )
    item2 = PedidoVendaItem(
        pedido_id=p1.id,
        produto_id=prod2.id,
        descricao=prod2.nome,
        quantidade=1.0,
        preco_unitario=20.0,
        total=20.0
    )
    db_session.add_all([item1, item2])

    p2 = PedidoVenda(
        cliente_id=cliente.id,
        status=StatusPedido.PRE_VENDA,
        numero="PV2",
        total=10.0,
        tipo_pedido="pre_venda"
    )
    db_session.add(p2)
    db_session.flush()

    item3 = PedidoVendaItem(
        pedido_id=p2.id,
        produto_id=prod1.id,
        descricao=prod1.nome,
        quantidade=1.0,
        preco_unitario=10.0,
        total=10.0
    )
    db_session.add(item3)
    db_session.commit()

    # 4. Obter token CSRF da página de agrupamento
    resp = await authenticated_client.get("/pedidos/pre-venda/agrupar")
    assert resp.status_code == 200
    
    # Extrair token CSRF se houver
    import re
    token_match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    csrf_token = token_match.group(1) if token_match else "dummy_csrf_token"

    # Chamar endpoint de agrupamento
    response = await authenticated_client.post(
        "/pedidos/pre-venda/finalizar-grupo",
        data={
            "pedido_ids": [str(p1.id), str(p2.id)],
            "csrf_token": csrf_token
        }
    )
    
    # Deve redirecionar para os detalhes do novo pedido agrupado (status code 303)
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "/pedidos/" in location
    
    # 6. Carregar novo pedido e verificar se tem os itens agrupados
    novo_id = int(location.split("/")[-1])
    novo_pedido = db_session.query(PedidoVenda).filter(PedidoVenda.id == novo_id).first()
    assert novo_pedido is not None
    assert novo_pedido.total == 40.0
    assert len(novo_pedido.itens) == 3

    # Fazer GET na página de detalhes do novo pedido para testar o carregamento do relacionamento
    detalhe_resp = await authenticated_client.get(f"/pedidos/{novo_id}")
    assert detalhe_resp.status_code == 200

    # Recarregar pré-vendas originais do banco de dados
    db_session.expire_all()
    p1_db = db_session.query(PedidoVenda).filter(PedidoVenda.id == p1.id).first()
    p2_db = db_session.query(PedidoVenda).filter(PedidoVenda.id == p2.id).first()

    assert p1_db.status == StatusPedido.FATURADO
    assert p2_db.status == StatusPedido.FATURADO
    assert p1_db.pedido_agrupado_id == novo_id
    assert p2_db.pedido_agrupado_id == novo_id


@pytest.mark.asyncio
async def test_listar_pedidos(authenticated_client: AsyncClient):
    # Fazer GET na listagem de pedidos
    resp = await authenticated_client.get("/pedidos/")
    assert resp.status_code == 200
    assert "pedidos" in resp.text.lower()




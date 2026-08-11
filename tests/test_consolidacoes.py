"""Regressao do fluxo pre-venda -> consolidacao -> cobranca.

O bug original: o formulario de nova consolidacao enviava o campo `pedido_ids`
duas vezes (um hidden com JSON + um checkbox por pedido). Como a rota lia o
campo como `Form(str)` escalar, chegava apenas o ULTIMO valor (um id solto),
`json.loads` devolvia um int e `PedidoVenda.id.in_(int)` estourava
ArgumentError -> HTTP 500.
"""
import json
import re

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from models import (
    ContaReceber, PedidoConsolidado, PedidoVenda, PedidoVendaItem, Produto,
    StatusConsolidacao, StatusPedido,
)
from tests.conftest import criar_cliente_teste


async def _csrf(client: AsyncClient, url: str = "/consolidacoes/nova") -> str:
    resp = await client.get(url)
    assert resp.status_code == 200
    achado = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    return achado.group(1) if achado else "dummy"


def _criar_pre_vendas(db_session: Session, cliente_id: int, produto_id: int, quantos: int = 2):
    ids = []
    for i in range(quantos):
        pedido = PedidoVenda(
            cliente_id=cliente_id, numero=f"PV-{i}", status=StatusPedido.PRE_VENDA,
            total=20, tipo_pedido="pre_venda",
        )
        db_session.add(pedido)
        db_session.flush()
        db_session.add(PedidoVendaItem(
            pedido_id=pedido.id, produto_id=produto_id, descricao="Servico A",
            quantidade=2, preco_unitario=10, total=20,
        ))
        ids.append(pedido.id)
    db_session.commit()
    return ids


@pytest.mark.asyncio
async def test_criar_consolidacao_com_payload_do_navegador(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Payload real do form: hidden JSON + um checkbox por pedido marcado."""
    cliente = criar_cliente_teste(db_session)
    produto = Produto(nome="Servico A", preco=10, tipo="servico")
    db_session.add(produto)
    db_session.commit()
    ids = _criar_pre_vendas(db_session, cliente.id, produto.id, quantos=2)

    csrf = await _csrf(authenticated_client)
    resp = await authenticated_client.post("/consolidacoes/criar", data={
        "csrf_token": csrf,
        "pedido_ids_json": json.dumps([str(i) for i in ids]),
        "pedido_ids": [str(i) for i in ids],
        "observacao": "consolidacao de teste",
        "forma_pagamento": "aprazo",
    })

    assert resp.status_code == 303, "criar consolidacao nao pode retornar 500"
    destino = resp.headers.get("location", "")
    assert re.fullmatch(r"/consolidacoes/\d+", destino), destino

    db_session.expire_all()
    consolidacao = db_session.query(PedidoConsolidado).first()
    assert consolidacao is not None
    assert len(consolidacao.pedidos_origem) == 2
    assert float(consolidacao.total) == 40.0
    # Itens iguais sao agregados em uma linha, com rastreabilidade das origens
    assert len(consolidacao.itens) == 1
    assert len(consolidacao.itens[0].itens_origem) == 2
    assert all(p.status == StatusPedido.CONSOLIDADO for p in consolidacao.pedidos_origem)


@pytest.mark.asyncio
async def test_criar_consolidacao_aceita_apenas_json(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Compatibilidade: payload antigo, so com o hidden em JSON."""
    cliente = criar_cliente_teste(db_session)
    produto = Produto(nome="Servico A", preco=10, tipo="servico")
    db_session.add(produto)
    db_session.commit()
    ids = _criar_pre_vendas(db_session, cliente.id, produto.id, quantos=1)

    csrf = await _csrf(authenticated_client)
    resp = await authenticated_client.post("/consolidacoes/criar", data={
        "csrf_token": csrf,
        "pedido_ids": json.dumps([str(ids[0])]),
    })
    assert resp.status_code == 303
    assert resp.headers.get("location") != "/consolidacoes/nova"


@pytest.mark.asyncio
async def test_criar_consolidacao_recusa_pedido_invalido_sem_500(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Sem selecao / pedido inexistente / ja consolidado: erro amigavel, nunca 500."""
    csrf = await _csrf(authenticated_client)

    resp = await authenticated_client.post("/consolidacoes/criar", data={"csrf_token": csrf})
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/consolidacoes/nova"

    resp = await authenticated_client.post("/consolidacoes/criar", data={
        "csrf_token": csrf, "pedido_ids": "999999",
    })
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/consolidacoes/nova"


@pytest.mark.asyncio
async def test_rota_pedidos_disponiveis_nao_e_capturada_pela_rota_dinamica(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """/consolidacoes/pedidos-disponiveis precisa vir antes de /{consolidacao_id}."""
    resp = await authenticated_client.get("/consolidacoes/pedidos-disponiveis")
    assert resp.status_code == 200
    assert "clientes_agrupados" in resp.json()


@pytest.mark.asyncio
async def test_finalizar_consolidacao_gera_parcelas_sem_duplicar(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    produto = Produto(nome="Servico A", preco=10, tipo="servico")
    db_session.add(produto)
    db_session.commit()
    ids = _criar_pre_vendas(db_session, cliente.id, produto.id, quantos=2)

    csrf = await _csrf(authenticated_client)
    resp = await authenticated_client.post("/consolidacoes/criar", data={
        "csrf_token": csrf,
        "pedido_ids": [str(i) for i in ids],
    })
    consolidacao_id = int(resp.headers["location"].rstrip("/").split("/")[-1])

    dados = {
        "csrf_token": csrf, "forma_pagamento": "aprazo", "num_parcelas": "3",
        "primeiro_vencimento": "2030-01-10", "intervalo_dias": "30",
    }
    resp = await authenticated_client.post(
        f"/consolidacoes/{consolidacao_id}/finalizar", data=dados
    )
    assert resp.status_code == 303

    db_session.expire_all()
    contas = db_session.query(ContaReceber).filter(
        ContaReceber.consolidacao_id == consolidacao_id
    ).all()
    assert len(contas) == 3
    assert sum(float(c.valor) for c in contas) == 40.0
    consolidacao = db_session.get(PedidoConsolidado, consolidacao_id)
    assert consolidacao.status == StatusConsolidacao.CONCLUIDO

    # Uma segunda finalizacao e recusada e nao duplica as parcelas
    resp = await authenticated_client.post(
        f"/consolidacoes/{consolidacao_id}/finalizar", data=dados
    )
    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.query(ContaReceber).filter(
        ContaReceber.consolidacao_id == consolidacao_id
    ).count() == 3


@pytest.mark.asyncio
async def test_pedido_faturado_duas_vezes_nao_duplica_cobranca(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    produto = Produto(nome="Servico A", preco=10, tipo="servico")
    db_session.add(produto)
    db_session.commit()
    pedido_id = _criar_pre_vendas(db_session, cliente.id, produto.id, quantos=1)[0]

    csrf = await _csrf(authenticated_client, f"/pedidos/{pedido_id}")
    dados = {
        "csrf_token": csrf, "tipo_pedido": "venda", "forma_pagamento": "avista",
        "gerar_cobranca": "on", "num_parcelas": "2",
        "primeiro_vencimento": "2030-01-10", "intervalo_dias": "30",
    }
    for _ in range(2):
        resp = await authenticated_client.post(f"/pedidos/{pedido_id}/finalizar", data=dados)
        assert resp.status_code == 303

    db_session.expire_all()
    assert db_session.query(ContaReceber).filter(
        ContaReceber.pedido_id == pedido_id
    ).count() == 2


@pytest.mark.asyncio
async def test_novo_pedido_com_numero_nao_numerico(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Numero alfanumerico ja existente nao pode derrubar a tela de novo pedido."""
    cliente = criar_cliente_teste(db_session)
    db_session.add(PedidoVenda(
        cliente_id=cliente.id, numero="PV-ABC", status=StatusPedido.PRE_VENDA, total=0
    ))
    db_session.commit()

    resp = await authenticated_client.get("/pedidos/novo")
    assert resp.status_code == 200

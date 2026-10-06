"""Regressão: receita duplicada por faturamento de pedido agrupado/consolidado.

Um pedido que entra em agrupamento ou consolidação passa a ser faturado pelo
documento que o representa. Se o pedido de origem também for faturado, a mesma
venda entra duas vezes no financeiro.

Cobre a regra unica `services/guarda_faturamento.py` e, principalmente, o
caminho que gerava a duplicidade: `POST /pedidos/{id}/finalizar` com
`acao="recibo"`, a única das rotas de faturamento que criava cobrança sem
checar o agrupamento.
"""
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from models import (
    Cliente,
    ContaReceber,
    PedidoVenda,
    PedidoVendaItem,
    Produto,
    StatusPedido,
)
from services.guarda_faturamento import bloqueio_faturamento
from tests.conftest import criar_cliente_teste


@pytest.fixture
def pedido_base(db_session: Session, test_empresa):
    """Pedido PENDENTE com 1 item, pronto para ser finalizado."""
    cliente = criar_cliente_teste(db_session)
    produto = Produto(nome="Servico", preco=Decimal("100.00"),
                      tipo="produto", situacao="A")
    db_session.add(produto)
    db_session.flush()

    pedido = PedidoVenda(cliente_id=cliente.id, numero="9001",
                         status=StatusPedido.PENDENTE, total=Decimal("100.00"),
                         tipo_pedido="venda")
    db_session.add(pedido)
    db_session.flush()
    db_session.add(PedidoVendaItem(pedido_id=pedido.id, produto_id=produto.id,
                                   descricao="Servico", quantidade=Decimal("1"),
                                   preco_unitario=Decimal("100.00"),
                                   total=Decimal("100.00")))
    db_session.commit()
    db_session.refresh(pedido)
    return pedido


async def _csrf(client, pedido_id):
    """Token CSRF da sessao, lido do proprio form de finalizacao.

    Sem isso o middleware de CSRF barra o POST antes da rota rodar e os testes
    "deve bloquear" passariam por vazio — dariam verde sem exercitar a guarda.
    """
    import re
    r = await client.get(f"/pedidos/{pedido_id}")
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
    return m.group(1) if m else ""


async def _finalizar(client, pedido_id, acao="recibo", **extra):
    dados = {"tipo_pedido": "venda", "forma_pagamento": "PIX", "acao": acao}
    dados.update(extra)
    dados["csrf_token"] = await _csrf(client, pedido_id)
    return await client.post(f"/pedidos/{pedido_id}/finalizar", data=dados,
                             follow_redirects=False)


# --------------------------------------------------------------------------
# Regra unica
# --------------------------------------------------------------------------

def test_pedido_pendente_pode_faturar(pedido_base):
    assert bloqueio_faturamento(pedido_base) is None


def test_pedido_agrupado_por_status_e_bloqueado(db_session, pedido_base):
    pedido_base.status = StatusPedido.AGRUPADO
    db_session.commit()
    b = bloqueio_faturamento(pedido_base)
    assert b is not None
    assert "agrupado" in b["completa"].lower()


def test_pedido_agrupado_por_vinculo_e_bloqueado(db_session, pedido_base):
    """So o vinculo, sem status consistente: o vinculo e a evidencia mais forte."""
    pedido_base.pedido_agrupado_id = 99999
    pedido_base.status = StatusPedido.PENDENTE
    db_session.commit()
    assert bloqueio_faturamento(pedido_base) is not None


def test_pedido_consolidado_por_vinculo_e_bloqueado(db_session, pedido_base):
    pedido_base.consolidacao_id = 1
    pedido_base.status = StatusPedido.PENDENTE
    db_session.commit()
    assert bloqueio_faturamento(pedido_base) is not None


def test_consolidado_tem_prioridade_sobre_agrupado(db_session, pedido_base):
    """Se um pedido tem os dois vinculos (estado inconsistente), a consolidacao
    manda: e ela que carrega o faturamento."""
    pedido_base.consolidacao_id = 1
    pedido_base.pedido_agrupado_id = 2
    db_session.commit()
    assert "consolidação" in bloqueio_faturamento(pedido_base)["completa"]


def test_status_em_string_do_banco_e_reconhecido(pedido_base):
    """`status` e coluna String: vem do banco como texto, nao como Enum.

    Regressão do risco `StatusPedido.CONSOLIDADO == "CONSOLIDADO"` -> False.
    """
    pedido_base.status = "AGRUPADO"
    assert bloqueio_faturamento(pedido_base) is not None, (
        "status lido do banco e string minuscula; a guarda nao pode passar batido")


def test_agrupado_e_cancelado_nao_sao_bloqueados(db_session, pedido_base):
    """Cancelar e reverter sao operacoes legitimas — nao vao por esta guarda."""
    pedido_base.status = StatusPedido.CANCELADO
    db_session.commit()
    assert bloqueio_faturamento(pedido_base) is None


# --------------------------------------------------------------------------
# Regressao: o caminho que gerava a duplicidade
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_finalizar_agrupado_com_recibo_nao_gera_cobranca(
    authenticated_client: AsyncClient, db_session: Session, pedido_base
):
    """O bug: pedido AGRUPADO finalizado com acao=recibo criava uma segunda
    conta a receber da mesma venda (o pedido agrupado ja tinha gerado a
    primeira)."""
    pedido_base.status = StatusPedido.AGRUPADO
    pedido_base.pedido_agrupado_id = 4242
    db_session.commit()

    r = await _finalizar(authenticated_client, pedido_base.id, acao="recibo")
    assert r.status_code in (302, 303)

    assert db_session.query(ContaReceber).filter(
        ContaReceber.pedido_id == pedido_base.id).count() == 0, (
        "pedido agrupado nao pode gerar cobranca: a venda ja esta no pedido agrupado")

    db_session.refresh(pedido_base)
    assert pedido_base.status == StatusPedido.AGRUPADO, (
        "o status do original nao pode virar FATURADO")


@pytest.mark.asyncio
async def test_finalizar_consolidado_com_recibo_nao_gera_cobranca(
    authenticated_client: AsyncClient, db_session: Session, pedido_base
):
    pedido_base.consolidacao_id = 7
    db_session.commit()

    await _finalizar(authenticated_client, pedido_base.id, acao="recibo")

    assert db_session.query(ContaReceber).filter(
        ContaReceber.pedido_id == pedido_base.id).count() == 0


@pytest.mark.asyncio
async def test_pedido_normal_com_recibo_ainda_gera_cobranca(
    authenticated_client: AsyncClient, db_session: Session, pedido_base
):
    """Contraprova: a guarda nao pode barrar o caminho legitimo."""
    r = await _finalizar(authenticated_client, pedido_base.id, acao="recibo")
    assert r.status_code in (302, 303)

    criadas = db_session.query(ContaReceber).filter(
        ContaReceber.pedido_id == pedido_base.id).all()
    assert criadas, "pedido normal com recibo deve gerar conta a receber"


@pytest.mark.asyncio
async def test_finalizar_agrupado_sem_itens_nao_gera_cobranca(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Pedido sem itens cai no outro ramo que gera cobranca ('fechado sem NFs')."""
    cliente = criar_cliente_teste(db_session)
    pedido = PedidoVenda(cliente_id=cliente.id, numero="9002",
                         status=StatusPedido.AGRUPADO, total=Decimal("50.00"),
                         pedido_agrupado_id=555)
    db_session.add(pedido)
    db_session.commit()
    db_session.refresh(pedido)

    await _finalizar(authenticated_client, pedido.id, acao="finalizar")

    assert db_session.query(ContaReceber).filter(
        ContaReceber.pedido_id == pedido.id).count() == 0
    db_session.refresh(pedido)
    assert pedido.status == StatusPedido.AGRUPADO
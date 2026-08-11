"""Testes da impressao da Consolicacao de Pedidos (A4 e Térmica 80mm)."""
from datetime import date

import pytest
from sqlalchemy.orm import Session

from models import PedidoConsolidado, PedidoConsolidadoItem, StatusConsolidacao
from tests.conftest import criar_cliente_teste


def _criar_consolidacao(db_session, cliente, numero="C001", total=100.0):
    cons = PedidoConsolidado(
        cliente_id=cliente.id, numero=numero, data=date.today(),
        status=StatusConsolidacao.CONCLUIDO, total=total, observacao="obs teste",
    )
    db_session.add(cons)
    db_session.commit()
    db_session.add(PedidoConsolidadoItem(
        consolidacao_id=cons.id, descricao="Serviço de Teste",
        quantidade=1, preco_unitario=total, total=total, unidade="UN",
    ))
    db_session.commit()
    return cons.id


@pytest.mark.asyncio
async def test_imprimir_consolidacao_a4_com_empresa_e_totais(
    authenticated_client, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    cid = _criar_consolidacao(db_session, cliente, "C001", 100.0)

    resp = await authenticated_client.get(f"/consolidacoes/{cid}/imprimir")
    assert resp.status_code == 200
    texto = resp.text
    assert test_empresa.nome_fantasia in texto
    assert cliente.nome in texto
    assert "C001" in texto
    assert "Serviço de Teste" in texto
    assert "Total Geral" in texto
    assert "Assinatura do Cliente" in texto


@pytest.mark.asyncio
async def test_imprimir_consolidacao_termica(
    authenticated_client, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    cid = _criar_consolidacao(db_session, cliente, "C002", 50.0)

    resp = await authenticated_client.get(f"/consolidacoes/{cid}/imprimir?termica=1")
    assert resp.status_code == 200
    assert "CONSOLIDAÇÃO DE PEDIDOS" in resp.text
    assert "C002" in resp.text
    assert "TOTAL GERAL" in resp.text

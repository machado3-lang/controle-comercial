"""Regressao do fluxo pre-venda -> consolidacao -> cobranca.

O bug original: o formulario de nova consolidacao enviava o campo `pedido_ids`
duas vezes (um hidden com JSON + um checkbox por pedido). Como a rota lia o
campo como `Form(str)` escalar, chegava apenas o ULTIMO valor (um id solto),
`json.loads` devolvia um int e `PedidoVenda.id.in_(int)` estourava
ArgumentError -> HTTP 500.
"""
import json
import re
from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from models import (
    ContaReceber, NFe, PedidoConsolidado, PedidoVenda, PedidoVendaItem, Produto,
    StatusConsolidacao, StatusConta, StatusPedido,
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


def _criar_pre_venda_mista(db_session, cliente_id, produto_id, servico_id):
    """Pedido de pre-venda com um produto (NFe) e um servico (NFSe)."""
    pedido = PedidoVenda(
        cliente_id=cliente_id, numero="PV-MISTA", status=StatusPedido.PRE_VENDA,
        total=40, tipo_pedido="pre_venda",
    )
    db_session.add(pedido)
    db_session.flush()
    db_session.add(PedidoVendaItem(
        pedido_id=pedido.id, produto_id=produto_id, descricao="Produto A",
        quantidade=1, preco_unitario=20, total=20,
    ))
    db_session.add(PedidoVendaItem(
        pedido_id=pedido.id, produto_id=servico_id, descricao="Servico A",
        quantidade=1, preco_unitario=20, total=20,
    ))
    db_session.commit()
    return pedido.id


@pytest.mark.asyncio
async def test_transmissao_nao_gera_cobranca_individual_na_consolidacao(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """A transmissao das notas da consolidacao NAO cobra por nota.

    O ponto unico de cobranca e a propria consolidacao (uma conta agrupada),
    como na OS. Cobrar em _garantir_cobranca_* duplicaria a conta/boleto.
    """
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
        "gerar_cobranca": "on",
    }
    resp = await authenticated_client.post(
        f"/consolidacoes/{consolidacao_id}/finalizar", data=dados
    )
    assert resp.status_code == 303

    # Emitir (rascunho) NAO cria cobranca: nenhuma conta nasce antes da nota.
    resp = await authenticated_client.post(
        f"/nfse/emitir/consolidacao/{consolidacao_id}", data={"csrf_token": csrf}
    )
    assert resp.status_code == 303

    db_session.expire_all()
    consolidacao = db_session.get(PedidoConsolidado, consolidacao_id)
    assert consolidacao.status == StatusConsolidacao.CONCLUIDO
    nfse = consolidacao.nfse
    assert nfse is not None

    # Transmissao respeita o ponto unico de cobranca: nada e gerado.
    from routers.nfse import _garantir_cobranca_nfse
    _garantir_cobranca_nfse(db_session, nfse)
    db_session.commit()
    db_session.expire_all()
    assert db_session.query(ContaReceber).filter(
        ContaReceber.consolidacao_id == consolidacao_id
    ).count() == 0


async def _finalizar_consolidacao_mista(authenticated_client, db_session, cliente):
    produto = Produto(nome="Produto A", preco=20, tipo="produto")
    servico = Produto(nome="Servico A", preco=20, tipo="servico")
    db_session.add_all([produto, servico])
    db_session.commit()
    pedido_id = _criar_pre_venda_mista(db_session, cliente.id, produto.id, servico.id)

    csrf = await _csrf(authenticated_client)
    resp = await authenticated_client.post("/consolidacoes/criar", data={
        "csrf_token": csrf, "pedido_ids": [str(pedido_id)],
    })
    consolidacao_id = int(resp.headers["location"].rstrip("/").split("/")[-1])
    resp = await authenticated_client.post(
        f"/consolidacoes/{consolidacao_id}/finalizar",
        data={
            "csrf_token": csrf, "forma_pagamento": "aprazo",
            "num_parcelas": "1", "primeiro_vencimento": "2030-01-10",
            "intervalo_dias": "30", "gerar_cobranca": "on",
        },
    )
    assert resp.status_code == 303
    db_session.expire_all()
    return consolidacao_id, csrf


def _notas_autorizadas(db_session, consolidacao_id, *, nfe=True, nfse=True):
    from models_nfe import NFe, NFSe
    notas = []
    if nfe:
        nota_nfe = NFe(
            consolidacao_id=consolidacao_id, numero=4495, serie=1, status="issued",
            origem="consolidacao", valor_total=180,
            data_emissao=datetime(2026, 11, 11, 9, 0),
        )
        db_session.add(nota_nfe)
        notas.append(nota_nfe)
    if nfse:
        nota_nfse = NFSe(
            consolidacao_id=consolidacao_id, numero="900", status="autorizada",
            origem="consolidacao", valor_total=100, aliquota_iss=5, iss_retido=True,
            data_emissao=datetime(2026, 11, 11, 10, 0),
        )
        db_session.add(nota_nfse)
        notas.append(nota_nfse)
    db_session.commit()
    return notas


@pytest.mark.asyncio
async def test_cobranca_agrupada_consolidacao_soma_nfe_e_nfse_liquido(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Uma unica ContaReceber: NFe 180 + NFSe liquida 95 (ISS 5% retido) = 275."""
    cliente = criar_cliente_teste(db_session)
    consolidacao_id, csrf = await _finalizar_consolidacao_mista(
        authenticated_client, db_session, cliente
    )
    _notas_autorizadas(db_session, consolidacao_id)

    resp = await authenticated_client.post(
        f"/consolidacoes/{consolidacao_id}/gerar-cobranca",
        data={
            "csrf_token": csrf, "nota": "auto", "forma_pagamento": "aprazo",
            "num_parcelas": "1", "primeiro_vencimento": "2030-01-10",
            "intervalo_dias": "30",
        },
    )
    assert resp.status_code == 303

    db_session.expire_all()
    contas = db_session.query(ContaReceber).filter(
        ContaReceber.consolidacao_id == consolidacao_id,
        ContaReceber.status.notin_([StatusConta.CANCELADO, StatusConta.EXCLUIDO]),
    ).all()
    assert len(contas) == 1
    conta = contas[0]
    assert conta.nfe_id is not None
    assert conta.nfse_id is not None
    assert abs(float(conta.valor) - 275.0) < 0.01

    # Gerar de novo nao duplica.
    resp = await authenticated_client.post(
        f"/consolidacoes/{consolidacao_id}/gerar-cobranca",
        data={"csrf_token": csrf, "nota": "auto", "forma_pagamento": "aprazo"},
    )
    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.query(ContaReceber).filter(
        ContaReceber.consolidacao_id == consolidacao_id
    ).count() == 1


@pytest.mark.asyncio
async def test_cobranca_agrupada_bloqueia_sem_as_duas_notas(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Sem a NFSe autorizada, a cobranca agrupada fica bloqueada."""
    cliente = criar_cliente_teste(db_session)
    consolidacao_id, csrf = await _finalizar_consolidacao_mista(
        authenticated_client, db_session, cliente
    )
    _notas_autorizadas(db_session, consolidacao_id, nfse=False)

    resp = await authenticated_client.post(
        f"/consolidacoes/{consolidacao_id}/gerar-cobranca",
        data={"csrf_token": csrf, "nota": "auto", "forma_pagamento": "aprazo"},
    )
    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.query(ContaReceber).filter(
        ContaReceber.consolidacao_id == consolidacao_id
    ).count() == 0


@pytest.mark.asyncio
async def test_cobranca_somente_nfe_quando_irma_nao_autorizada(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Caso de cancelamento da irma: cobra manualmente apenas a NF-e valida."""
    cliente = criar_cliente_teste(db_session)
    consolidacao_id, csrf = await _finalizar_consolidacao_mista(
        authenticated_client, db_session, cliente
    )
    _notas_autorizadas(db_session, consolidacao_id, nfse=False)

    resp = await authenticated_client.post(
        f"/consolidacoes/{consolidacao_id}/gerar-cobranca",
        data={
            "csrf_token": csrf, "nota": "nfe", "forma_pagamento": "aprazo",
            "primeiro_vencimento": "2030-01-10",
        },
    )
    assert resp.status_code == 303
    db_session.expire_all()
    contas = db_session.query(ContaReceber).filter(
        ContaReceber.consolidacao_id == consolidacao_id
    ).all()
    assert len(contas) == 1
    assert contas[0].nfe_id is not None
    assert contas[0].nfse_id is None
    assert abs(float(contas[0].valor) - 180.0) < 0.01


@pytest.mark.asyncio
async def test_detalhe_consolidacao_renderiza_cobranca_agrupada(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    consolidacao_id, csrf = await _finalizar_consolidacao_mista(
        authenticated_client, db_session, cliente
    )
    _notas_autorizadas(db_session, consolidacao_id)

    resp = await authenticated_client.get(f"/consolidacoes/{consolidacao_id}")
    assert resp.status_code == 200
    assert "Emitir Cobrança da Consolidação" in resp.text
    assert "275.00" in resp.text
    assert "NF-e da Consolidação" in resp.text


@pytest.mark.asyncio
async def test_cobranca_consolidacao_recusa_vencimento_antes_da_emissao(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    consolidacao_id, csrf = await _finalizar_consolidacao_mista(
        authenticated_client, db_session, cliente
    )
    _notas_autorizadas(db_session, consolidacao_id)

    resp = await authenticated_client.post(
        f"/consolidacoes/{consolidacao_id}/gerar-cobranca",
        data={
            "csrf_token": csrf, "nota": "auto", "forma_pagamento": "aprazo",
            "primeiro_vencimento": "2026-11-01",
        },
    )
    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.query(ContaReceber).filter(
        ContaReceber.consolidacao_id == consolidacao_id
    ).count() == 0


@pytest.mark.asyncio
async def test_finalizar_consolidacao_como_recibo_gera_contas(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """'Gerar recibo (sem NFs)' na consolidação gera a cobrança na finalização.

    Nao havera nota fiscal, entao as contas nascem ali mesmo (igual ao pedido
    avulso), em 3 parcelas a prazo.
    """
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

    resp = await authenticated_client.post(
        f"/consolidacoes/{consolidacao_id}/finalizar",
        data={
            "csrf_token": csrf, "forma_pagamento": "aprazo", "num_parcelas": "3",
            "primeiro_vencimento": "2030-01-10", "intervalo_dias": "30",
            "acao": "recibo",
        },
    )
    assert resp.status_code == 303

    db_session.expire_all()
    contas = db_session.query(ContaReceber).filter(
        ContaReceber.consolidacao_id == consolidacao_id
    ).all()
    assert len(contas) == 3
    assert all(c.status == StatusConta.PENDENTE for c in contas)
    assert all(c.nfe_id is None and c.nfse_id is None for c in contas)

    consolidacao = db_session.get(PedidoConsolidado, consolidacao_id)
    assert consolidacao.status == StatusConsolidacao.CONCLUIDO
    assert consolidacao.primeiro_vencimento.isoformat() == "2030-01-10"


@pytest.mark.asyncio
async def test_finalizar_consolidacao_sem_gerar_cobranca(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Flag 'Gerar Cobrança' desligada na consolidação: nenhuma conta nasce."""
    from routers.nfse import _garantir_cobranca_nfse

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

    # sem "gerar_cobranca" no form -> False
    resp = await authenticated_client.post(
        f"/consolidacoes/{consolidacao_id}/finalizar",
        data={"csrf_token": csrf, "forma_pagamento": "aprazo", "num_parcelas": "1"},
    )
    assert resp.status_code == 303

    db_session.expire_all()
    consolidacao = db_session.get(PedidoConsolidado, consolidacao_id)
    assert consolidacao.gerar_cobranca is False

    # emissao (rascunho) nao gera nada...
    resp = await authenticated_client.post(
        f"/nfse/emitir/consolidacao/{consolidacao_id}", data={"csrf_token": csrf}
    )
    assert resp.status_code == 303
    db_session.expire_all()
    consolidacao = db_session.get(PedidoConsolidado, consolidacao_id)
    assert consolidacao.nfse is not None
    assert db_session.query(ContaReceber).filter(
        ContaReceber.consolidacao_id == consolidacao_id
    ).count() == 0

    # ...e a transmissao tambem respeita a flag
    _garantir_cobranca_nfse(db_session, consolidacao.nfse)
    db_session.commit()
    db_session.expire_all()
    assert db_session.query(ContaReceber).filter(
        ContaReceber.consolidacao_id == consolidacao_id
    ).count() == 0


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
    # Pedido com itens: o faturamento NAO gera cobranca (a conta nasce da nota,
    # na transmissao), entao finalizar duas vezes nao duplica nada.
    assert db_session.query(ContaReceber).filter(
        ContaReceber.pedido_id == pedido_id
    ).count() == 0

    # A cobranca vem da NFe transmitida: 2 parcelas, sem duplicar na 2a chamada.
    from routers.nfe import _garantir_cobranca_nfe
    pedido = db_session.get(PedidoVenda, pedido_id)
    nfe = NFe(
        pedido_id=pedido_id, cliente_id=pedido.cliente_id, numero="9001",
        status="issued", valor_total=pedido.total or 0, forma_pagamento="avista",
    )
    db_session.add(nfe)
    db_session.commit()
    _garantir_cobranca_nfe(db_session, nfe)
    db_session.commit()
    _garantir_cobranca_nfe(db_session, nfe)
    db_session.commit()

    db_session.expire_all()
    contas = db_session.query(ContaReceber).filter(
        ContaReceber.pedido_id == pedido_id
    ).all()
    assert len(contas) == 2
    assert all(c.status == StatusConta.PAGO for c in contas)


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

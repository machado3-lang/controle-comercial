"""Testes das rotas de Ordem de Servico (OS).

Cobre o fluxo listar -> criar -> detalhar -> editar -> excluir e o caso
que originou o bug: ao excluir uma OS aberta o frontend (modal
`handleSubmitExclusao`) espera JSON, mas a rota devolvia RedirectResponse,
fazendo `r.json()` quebrar e exibir "Erro na requisicao".
"""
import json
import re

import pytest
from sqlalchemy.orm import Session

from models import OrdemServico, Cliente, StatusOS, NFe, Produto
from tests.conftest import criar_cliente_teste


async def _csrf(client, url="/ordens-servico/"):
    resp = await client.get(url)
    achado = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    return achado.group(1) if achado else "dummy"


@pytest.mark.asyncio
async def test_fluxo_os_listar_criar_detalhar(
    authenticated_client, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)

    resp = await authenticated_client.get("/ordens-servico/")
    assert resp.status_code == 200

    resp = await authenticated_client.get("/ordens-servico/nova")
    assert resp.status_code == 200

    csrf = await _csrf(authenticated_client, "/ordens-servico/nova")
    resp = await authenticated_client.post("/ordens-servico/nova", data={
        "csrf_token": csrf,
        "cliente_id": cliente.id, "equipamento": "Notebook Dell",
    })
    assert resp.status_code == 303

    db_session.expire_all()
    ordem = db_session.query(OrdemServico).first()
    assert ordem is not None
    assert ordem.equipamento == "Notebook Dell"
    assert ordem.status == StatusOS.ABERTA

    resp = await authenticated_client.get(f"/ordens-servico/{ordem.id}")
    assert resp.status_code == 200
    resp = await authenticated_client.get(f"/ordens-servico/{ordem.id}/editar")
    assert resp.status_code == 200
    resp = await authenticated_client.get(f"/ordens-servico/{ordem.id}/imprimir")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_editar_os_atualiza_valores(
    authenticated_client, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    ordem = OrdemServico(cliente_id=cliente.id, equipamento="Impressora", status=StatusOS.ABERTA)
    db_session.add(ordem)
    db_session.commit()
    oid = ordem.id

    csrf = await _csrf(authenticated_client, f"/ordens-servico/{oid}/editar")
    resp = await authenticated_client.post(f"/ordens-servico/{oid}/editar", data={
        "csrf_token": csrf,
        "cliente_id": cliente.id,
        "equipamento": "Impressora HP",
        "defeito_relatado": "nao liga",
        "valor_servico": "100",
        "valor_pecas": "50",
        "valor_total": "150",
        "status": "EM_ANDAMENTO",
    })
    assert resp.status_code == 303

    db_session.expire_all()
    atual = db_session.get(OrdemServico, oid)
    assert atual.equipamento == "Impressora HP"
    assert float(atual.valor_total) == 150.0
    assert atual.status == StatusOS.EM_ANDAMENTO


@pytest.mark.asyncio
async def test_excluir_os_aberta_remove_do_banco(
    authenticated_client, db_session: Session, test_empresa
):
    """Caso do bug: excluir OS aberta deve retornar JSON e remover o registro."""
    cliente = criar_cliente_teste(db_session)
    ordem = OrdemServico(cliente_id=cliente.id, equipamento="Celular", status=StatusOS.ABERTA)
    db_session.add(ordem)
    db_session.commit()
    oid = ordem.id

    csrf = await _csrf(authenticated_client)
    resp = await authenticated_client.post(f"/ordens-servico/{oid}/excluir", data={
        "csrf_token": csrf, "senha": "test123",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("redirect") == "/ordens-servico"

    db_session.expire_all()
    assert db_session.get(OrdemServico, oid) is None


@pytest.mark.asyncio
async def test_excluir_os_com_nfe_cancela_em_vez_de_apagar(
    authenticated_client, db_session: Session, test_empresa
):
    """NFe tem FK para a OS sem cascade: a exclusao definitiva quebraria,
    entao a rota deve CANCELAR a OS."""
    cliente = criar_cliente_teste(db_session)
    ordem = OrdemServico(cliente_id=cliente.id, equipamento="Tablet", status=StatusOS.ABERTA)
    db_session.add(ordem)
    db_session.commit()
    oid = ordem.id
    db_session.add(NFe(os_id=oid, numero=12345))
    db_session.commit()

    csrf = await _csrf(authenticated_client)
    resp = await authenticated_client.post(f"/ordens-servico/{oid}/excluir", data={
        "csrf_token": csrf, "senha": "test123",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True

    db_session.expire_all()
    atual = db_session.get(OrdemServico, oid)
    assert atual is not None
    assert atual.status == StatusOS.CANCELADA


@pytest.mark.asyncio
async def test_excluir_os_senha_invalida_retorna_403(
    authenticated_client, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    ordem = OrdemServico(cliente_id=cliente.id, equipamento="Radio", status=StatusOS.ABERTA)
    db_session.add(ordem)
    db_session.commit()
    oid = ordem.id

    csrf = await _csrf(authenticated_client)
    resp = await authenticated_client.post(f"/ordens-servico/{oid}/excluir", data={
        "csrf_token": csrf, "senha": "senha-errada",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_filtro_status_lista_apenas_do_status(
    authenticated_client, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    a = OrdemServico(cliente_id=cliente.id, equipamento="EQUIPABERTA123", status=StatusOS.ABERTA)
    f = OrdemServico(cliente_id=cliente.id, equipamento="EQUIPFINAL123", status=StatusOS.FINALIZADA)
    db_session.add_all([a, f])
    db_session.commit()

    resp = await authenticated_client.get("/ordens-servico/?status_filtro=aberta")
    assert resp.status_code == 200
    # O filtro usa o valor do enum ("aberta"); deve listar apenas a OS aberta.
    assert "EQUIPABERTA123" in resp.text
    assert "EQUIPFINAL123" not in resp.text


@pytest.mark.asyncio
async def test_imprimir_a4_contem_empresa_cliente_e_totais(
    authenticated_client, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    ordem = OrdemServico(
        cliente_id=cliente.id, equipamento="Notebook", status=StatusOS.ABERTA,
        defeito_relatado="Não liga", valor_servico=100, valor_pecas=50, valor_total=150,
        servicos_executados=json.dumps([{"nome": "Formatação", "qtd": 1, "preco": 100}]),
        pecas_utilizadas=json.dumps([{"nome": "HD 1TB", "qtd": 1, "preco": 50}]),
    )
    db_session.add(ordem)
    db_session.commit()
    oid = ordem.id

    resp = await authenticated_client.get(f"/ordens-servico/{oid}/imprimir?tipo=a4")
    assert resp.status_code == 200
    texto = resp.text
    assert test_empresa.nome_fantasia in texto
    assert cliente.nome in texto
    assert "Notebook" in texto
    assert "Formatação" in texto
    assert "HD 1TB" in texto
    assert "TOTAL" in texto or "Total Geral" in texto
    assert "Assinatura do Cliente" in texto


@pytest.mark.asyncio
async def test_imprimir_termica_e_matricial_renderizam(
    authenticated_client, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    ordem = OrdemServico(cliente_id=cliente.id, equipamento="Impressora", status=StatusOS.ABERTA)
    db_session.add(ordem)
    db_session.commit()
    oid = ordem.id

    # 'matricial' nao existe mais (80mm = termica); deve cair no A4 sem quebrar
    for tipo in ("termica", "matricial", "a4", "orcamento"):
        resp = await authenticated_client.get(f"/ordens-servico/{oid}/imprimir?tipo={tipo}")
        assert resp.status_code == 200
        assert f"Nº {oid}" in resp.text


@pytest.mark.asyncio
async def test_imprimir_orcamento_usa_titulo_orcamento(
    authenticated_client, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    ordem = OrdemServico(cliente_id=cliente.id, equipamento="Celular", status=StatusOS.ABERTA)
    db_session.add(ordem)
    db_session.commit()
    oid = ordem.id

    resp = await authenticated_client.get(f"/ordens-servico/{oid}/imprimir?tipo=orcamento")
    assert resp.status_code == 200
    # No formato orcamento o titulo nao deve ser "ORDEM DE SERVICO" (maiúsculas)
    assert "ORDEM DE SERVIÇO" not in resp.text
    assert "Nº" in resp.text


@pytest.mark.asyncio
async def test_imprimir_fallback_pecas_estoque(
    authenticated_client, db_session: Session, test_empresa
):
    """Quando o campo JSON de pecas esta vazio, usa as pecas do estoque (os_pecas)."""
    from models_estoque import OSPeca
    cliente = criar_cliente_teste(db_session)
    prod = __import__("models").Produto(nome="Fonte", preco=40, tipo="produto")
    db_session.add(prod)
    db_session.commit()
    ordem = OrdemServico(cliente_id=cliente.id, equipamento="PC", status=StatusOS.ABERTA)
    db_session.add(ordem)
    db_session.commit()
    db_session.add(OSPeca(os_id=ordem.id, produto_id=prod.id, quantidade=2, valor_unitario=40))
    db_session.commit()
    oid = ordem.id

    resp = await authenticated_client.get(f"/ordens-servico/{oid}/imprimir?tipo=a4")
    assert resp.status_code == 200
    assert "Fonte" in resp.text


@pytest.mark.asyncio
async def test_adicionar_peca_os_faz_baixa_de_estoque(
    authenticated_client, db_session: Session, test_empresa
):
    from models_estoque import OSPeca
    cliente = criar_cliente_teste(db_session)
    ordem = OrdemServico(cliente_id=cliente.id, equipamento="PC", status=StatusOS.ABERTA, valor_servico=0)
    db_session.add(ordem)
    db_session.commit()
    prod = Produto(nome="Fonte ATX", preco=50, preco_custo=30, tipo="produto", estoque=5)
    db_session.add(prod)
    db_session.commit()
    oid, pid = ordem.id, prod.id

    csrf = await _csrf(authenticated_client, f"/ordens-servico/{oid}")
    resp = await authenticated_client.post(f"/estoque/os/{oid}/peca/adicionar", data={
        "csrf_token": csrf, "produto_id": pid, "quantidade": 2,
    })
    assert resp.status_code == 303
    db_session.expire_all()
    assert float(db_session.get(Produto, pid).estoque) == 3.0
    os_at = db_session.get(OrdemServico, oid)
    assert float(os_at.valor_pecas) == 100.0
    assert float(os_at.valor_total) == 100.0
    assert db_session.query(OSPeca).filter(OSPeca.os_id == oid).count() == 1

    peca = db_session.query(OSPeca).filter(OSPeca.os_id == oid).first()
    csrf = await _csrf(authenticated_client, f"/ordens-servico/{oid}")
    resp = await authenticated_client.post(f"/estoque/os/{oid}/peca/{peca.id}/remover", data={"csrf_token": csrf})
    assert resp.status_code == 303
    db_session.expire_all()
    assert float(db_session.get(Produto, pid).estoque) == 5.0


@pytest.mark.asyncio
async def test_alterar_status_os_ciclo_de_vida(
    authenticated_client, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    ordem = OrdemServico(cliente_id=cliente.id, equipamento="TV", status=StatusOS.ABERTA)
    db_session.add(ordem)
    db_session.commit()
    oid = ordem.id

    for novo, esperado in [("em_andamento", StatusOS.EM_ANDAMENTO),
                            ("finalizada", StatusOS.FINALIZADA),
                            ("concluida", StatusOS.CONCLUIDA)]:
        csrf = await _csrf(authenticated_client, f"/ordens-servico/{oid}")
        resp = await authenticated_client.post(f"/ordens-servico/{oid}/status", data={
            "csrf_token": csrf, "novo_status": novo,
        })
        assert resp.status_code == 200
        assert resp.json().get("ok") is True
        db_session.expire_all()
        assert db_session.get(OrdemServico, oid).status == esperado

    csrf = await _csrf(authenticated_client, f"/ordens-servico/{oid}")
    resp = await authenticated_client.post(f"/ordens-servico/{oid}/status", data={
        "csrf_token": csrf, "novo_status": "status_inexistente",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_editar_os_com_pecas_faz_baixa(authenticated_client, db_session, test_empresa):
    """Pecas sao informadas uma unica vez na edicao da OS e ja fazem baixa de estoque."""
    from models_estoque import OSPeca
    cliente = criar_cliente_teste(db_session)
    ordem = OrdemServico(cliente_id=cliente.id, equipamento="Notebook", status=StatusOS.ABERTA)
    db_session.add(ordem)
    db_session.commit()
    prod = Produto(nome="SSD 1TB", preco=300, preco_custo=200, tipo="produto", estoque=4)
    db_session.add(prod)
    db_session.commit()
    oid, pid = ordem.id, prod.id

    csrf = await _csrf(authenticated_client, f"/ordens-servico/{oid}/editar")
    resp = await authenticated_client.post(f"/ordens-servico/{oid}/editar", data={
        "csrf_token": csrf, "cliente_id": cliente.id, "equipamento": "Notebook",
        "servicos_json_data": "[]",
        "pecas_json_data": json.dumps([{"id": pid, "nome": "SSD 1TB", "qtd": 2, "preco": 300}]),
        "valor_servico": "0", "valor_pecas": "600", "valor_total": "600", "status": "aberta",
    })
    assert resp.status_code == 303
    db_session.expire_all()
    assert float(db_session.get(Produto, pid).estoque) == 2.0
    assert db_session.query(OSPeca).filter(OSPeca.os_id == oid).count() == 1


@pytest.mark.asyncio
async def test_excluir_os_inexistente_retorna_404(
    authenticated_client, db_session: Session, test_empresa
):
    csrf = await _csrf(authenticated_client)
    resp = await authenticated_client.post("/ordens-servico/999999/excluir", data={
        "csrf_token": csrf, "senha": "test123",
    })
    assert resp.status_code == 404

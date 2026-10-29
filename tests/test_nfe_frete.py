"""Frete informado no rascunho da NFe.

Regras travadas aqui:
  - o frete entra no total da NFe, no vPag, no <cobr>/<dup> e na cobranca;
  - pedido/consolidacao continuam sem frete (o frete nasce so na NFe);
  - frete > 0 sem modalidade assume CIF (0 - Remetente);
  - frete nao entra na base do valor aproximado dos tributos (IBPT);
  - operacao sem pagamento (tPag 90) nao aceita frete;
  - alterar o frete de um rascunho sincroniza a cobranca ja gerada, mas e
    bloqueado quando o boleto ja foi emitido.
"""
import json
import re

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from models import ContaReceber, NFe, Produto
from services.nfe_notaas import montar_payload_nfe
from tests.conftest import criar_cliente_teste


def _empresa_fake(ali_fed=0.0, ali_est=0.0):
    class _E:
        cfop_padrao = "5102"
        estado = "MS"
        serie_nfe = 1
        nfe_aliquota_federal = ali_fed
        nfe_aliquota_estadual = ali_est
    return _E()


class _ClienteFake:
    nome = "CLIENTE TESTE LTDA"
    cpf_cnpj = "01961119000193"
    tipo_pessoa = "juridica"
    inscricao_estadual = "282377476"
    indicador_ie = "contribuidor"
    endereco = "Avenida Teste"
    bairro = "Centro"
    cidade = "Campo Grande"
    estado = "MS"
    codigo_ibge = "5002704"
    cep = "79005320"
    email = "teste@teste.com"


_ITENS = [{
    "produto_id": 1,
    "descricao": "Produto",
    "ncm": "85437099",
    "unidade": "UN",
    "quantidade": 1,
    "preco_unitario": 1790.0,
    "origem": 0,
}]


# ------------------------------------------------------------------ unitario

def test_payload_soma_frete_no_pagamento_e_na_cobranca():
    payload = montar_payload_nfe(
        _empresa_fake(), _ClienteFake(), _ITENS,
        numero_nfe=1, forma_pagamento="boleto", frete_valor=100.0,
        modalidade_frete=0,
        duplicatas=[{"numero": 1, "vencimento": "2026-11-15", "valor": 1890.0}],
    )

    assert payload["valorFrete"] == 100.0
    assert payload["transporte"]["modalidadeFrete"] == 0
    # vPag = itens + frete (o vNF que a NotaAS monta)
    assert payload["pagamentos"][0]["valor"] == 1890.0
    assert payload["cobranca"]["fatura"]["valorLiquido"] == 1890.0
    assert payload["cobranca"]["parcelas"][0]["valor"] == 1890.0


def test_payload_soma_frete_depois_do_desconto():
    item = dict(_ITENS[0], desconto=90.0)
    payload = montar_payload_nfe(
        _empresa_fake(), _ClienteFake(), [item],
        numero_nfe=1, forma_pagamento="pix", frete_valor=50.0,
        modalidade_frete=1,
    )
    # 1790 - 90 + 50
    assert payload["pagamentos"][0]["valor"] == 1750.0
    assert payload["valorFrete"] == 50.0


def test_frete_sem_modalidade_vira_cif():
    payload = montar_payload_nfe(
        _empresa_fake(), _ClienteFake(), _ITENS,
        numero_nfe=1, forma_pagamento="pix", frete_valor=10.0,
        modalidade_frete=9,
    )
    assert payload["transporte"]["modalidadeFrete"] == 0


def test_frete_nao_entra_no_valor_aproximado_dos_tributos():
    payload = montar_payload_nfe(
        _empresa_fake(ali_fed=10.0), _ClienteFake(), _ITENS,
        numero_nfe=1, forma_pagamento="pix", frete_valor=100.0,
        modalidade_frete=0,
    )
    # 10% sobre 1790 = 179.00 (nao sobre 1890 = 189.00)
    assert "R$ 179.00" in payload["infCpl"]
    assert "R$ 189.00" not in payload["infCpl"]


# ---------------------------------------------------------------- integracao

async def _csrf(client: AsyncClient, url: str) -> str:
    resp = await client.get(url)
    assert resp.status_code == 200
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    return m.group(1) if m else "dummy"


def _itens(produto, preco=100.0, qtd=1):
    return [{
        "produto_id": produto.id, "descricao": produto.nome,
        "ncm": "99999999", "unidade": "UN",
        "quantidade": qtd, "preco_unitario": preco,
    }]


async def _criar_rascunho(client, db_session, test_empresa, *,
                          frete=0.0, modalidade=9, forma="pix",
                          gerar_cobranca=True):
    test_empresa.notaas_api_key = "test-key"
    db_session.commit()

    cliente = criar_cliente_teste(db_session, inscricao_estadual="123456")
    produto = Produto(nome="Produto Frete", preco=100, tipo="produto")
    db_session.add(produto)
    db_session.commit()

    csrf = await _csrf(client, "/nfe/emitir/avulsa")
    data = {
        "csrf_token": csrf,
        "cliente_id": str(cliente.id),
        "itens_json": json.dumps(_itens(produto)),
        "forma_pagamento": forma,
        "modalidade_frete": str(modalidade),
        "valor_frete": f"{frete:.2f}",
    }
    if gerar_cobranca:
        data["gerar_cobranca"] = "on"
    resp = await client.post("/nfe/emitir/avulsa", data=data)
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.query(NFe).filter(NFe.cliente_id == cliente.id).first()
    return cliente, produto, nfe


@pytest.mark.asyncio
async def test_avulsa_inclui_frete_no_total_e_na_cobranca(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    _, _, nfe = await _criar_rascunho(
        authenticated_client, db_session, test_empresa, frete=25.0,
    )
    assert nfe is not None
    assert float(nfe.valor_total) == 125.0
    # frete informado sem modalidade => CIF
    assert nfe.modalidade_frete == 0

    conta = db_session.query(ContaReceber).filter(ContaReceber.nfe_id == nfe.id).first()
    assert conta is not None
    assert float(conta.valor) == 125.0


@pytest.mark.asyncio
async def test_transporte_recalcula_total_e_sincroniza_cobranca(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    _, _, nfe = await _criar_rascunho(
        authenticated_client, db_session, test_empresa,
    )
    csrf = await _csrf(authenticated_client, f"/nfe/{nfe.id}/previa")

    resp = await authenticated_client.post(
        f"/nfe/{nfe.id}/transporte",
        data={"csrf_token": csrf, "valor_frete": "40.00"},
    )
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.get(NFe, nfe.id)
    assert float(nfe.valor_frete) == 40.0
    assert nfe.modalidade_frete == 0
    assert float(nfe.valor_total) == 140.0
    conta = db_session.query(ContaReceber).filter(ContaReceber.nfe_id == nfe.id).first()
    assert float(conta.valor) == 140.0


@pytest.mark.asyncio
async def test_transporte_bloqueia_quando_boleto_ja_foi_emitido(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    _, _, nfe = await _criar_rascunho(
        authenticated_client, db_session, test_empresa,
    )
    conta = db_session.query(ContaReceber).filter(ContaReceber.nfe_id == nfe.id).first()
    conta.boleto_emitido = True
    db_session.commit()

    csrf = await _csrf(authenticated_client, f"/nfe/{nfe.id}/previa")
    resp = await authenticated_client.post(
        f"/nfe/{nfe.id}/transporte",
        data={"csrf_token": csrf, "valor_frete": "40.00"},
    )
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.get(NFe, nfe.id)
    assert float(nfe.valor_frete) == 0.0
    assert float(nfe.valor_total) == 100.0
    conta = db_session.query(ContaReceber).filter(ContaReceber.nfe_id == nfe.id).first()
    assert float(conta.valor) == 100.0


@pytest.mark.asyncio
async def test_editar_preserva_frete_quando_campo_nao_e_enviado(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    cliente, produto, nfe = await _criar_rascunho(
        authenticated_client, db_session, test_empresa, frete=20.0,
    )
    assert float(nfe.valor_total) == 120.0

    # Reabre a edicao (popula a sessao) e salva sem os campos de frete.
    resp = await authenticated_client.get(f"/nfe/{nfe.id}/editar")
    assert resp.status_code == 303
    csrf = await _csrf(authenticated_client, "/nfe/emitir/avulsa")
    resp = await authenticated_client.post(f"/nfe/{nfe.id}/editar", data={
        "csrf_token": csrf,
        "cliente_id": str(cliente.id),
        "itens_json": json.dumps(_itens(produto)),
    })
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.get(NFe, nfe.id)
    assert float(nfe.valor_frete) == 20.0
    assert float(nfe.valor_total) == 120.0
    conta = db_session.query(ContaReceber).filter(ContaReceber.nfe_id == nfe.id).first()
    assert float(conta.valor) == 120.0


@pytest.mark.asyncio
async def test_editar_com_novo_frete_atualiza_total_e_cobranca(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    cliente, produto, nfe = await _criar_rascunho(
        authenticated_client, db_session, test_empresa,
    )
    resp = await authenticated_client.get(f"/nfe/{nfe.id}/editar")
    assert resp.status_code == 303
    csrf = await _csrf(authenticated_client, "/nfe/emitir/avulsa")
    resp = await authenticated_client.post(f"/nfe/{nfe.id}/editar", data={
        "csrf_token": csrf,
        "cliente_id": str(cliente.id),
        "itens_json": json.dumps(_itens(produto)),
        "valor_frete": "35.00",
        "modalidade_frete": "1",
        "transportadora_id": "",
    })
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.get(NFe, nfe.id)
    assert float(nfe.valor_frete) == 35.0
    assert nfe.modalidade_frete == 1
    assert float(nfe.valor_total) == 135.0
    conta = db_session.query(ContaReceber).filter(ContaReceber.nfe_id == nfe.id).first()
    assert float(conta.valor) == 135.0


@pytest.mark.asyncio
async def test_avulsa_sem_pagamento_rejeita_frete(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    test_empresa.notaas_api_key = "test-key"
    db_session.commit()
    cliente = criar_cliente_teste(db_session, inscricao_estadual="123456")
    produto = Produto(nome="Produto Remessa", preco=100, tipo="produto")
    db_session.add(produto)
    db_session.commit()

    csrf = await _csrf(authenticated_client, "/nfe/emitir/avulsa")
    resp = await authenticated_client.post("/nfe/emitir/avulsa", data={
        "csrf_token": csrf,
        "cliente_id": str(cliente.id),
        "itens_json": json.dumps(_itens(produto)),
        "forma_pagamento": "sem_pagamento",
        "valor_frete": "10.00",
    })
    assert resp.status_code == 303

    db_session.expire_all()
    assert db_session.query(NFe).filter(NFe.cliente_id == cliente.id).first() is None

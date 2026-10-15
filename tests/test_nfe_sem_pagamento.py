"""NF-e de operacao SEM PAGAMENTO (remessa para conserto, consignacao, bonificacao).

Contexto
--------
A NFe 4475 (remessa para conserto, CFOP 5915) foi emitida com tPag=15 (Boleto) e
R$ 1.790,00 de vPag porque a opcao "Outros" (tPag=99) era rejeitada pela SEFAZ.
O XML declarava um boleto que nao existia. nestas operacoes o meio correto e o
**tPag 90 - Sem Pagamento**, com vPag = 0.00.

O que este arquivo trava:
  - o mapa forma de pagamento -> tPag (90, nao 99);
  - vPag zerado no 90, e igual ao total da nota nas demais formas;
  - ausencia de <cobr>/<dup> (grupo so existe para tPag 15);
  - "sem pagamento" NAO e "a vista": nao gera conta a receber, mas tambem nao
    quita nada, porque nao houve recebimento.
"""
import json
import re

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from models import ContaReceber, NFe, Produto
from services.nfe_notaas import montar_payload_nfe
from services.parcelamento import (
    eh_pagamento_a_vista, eh_sem_pagamento, quitar_avista,
)
from tests.conftest import criar_cliente_teste


# ------------------------------------------------------------------ unitario

def test_eh_sem_pagamento_reconhece_as_variacoes():
    for forma in ("sem_pagamento", "Sem Pagamento", "SEM_PAGAMENTO", "sem_cobranca"):
        assert eh_sem_pagamento(forma) is True
    for forma in (None, "", "boleto", "pix", "avista", "outro"):
        assert eh_sem_pagamento(forma) is False


def test_sem_pagamento_nao_e_pagamento_a_vista():
    """Se fosse 'a vista', `quitar_avista` marcaria a conta como PAGO -- o que
    seria mentira: nao houve recebimento nenhum."""
    assert eh_pagamento_a_vista("sem_pagamento") is False


def test_quitar_avista_nao_quita_conta_de_sem_pagamento():
    class _Conta:
        status = None

    conta = _Conta()
    quitar_avista([conta], "sem_pagamento")
    assert conta.status is None


def _empresa_fake():
    class _E:
        cfop_padrao = "5102"
        estado = "MS"
        serie_nfe = 1
        nfe_aliquota_federal = 0.0
        nfe_aliquota_estadual = 0.0
    return _E()


class _ClienteFake:
    nome = "OLIMAC PONTO E ACESSO LTDA"
    cpf_cnpj = "01961119000193"
    tipo_pessoa = "juridica"
    inscricao_estadual = "282377476"
    indicador_ie = "contribuidor"
    endereco = "Avenida Joaquim Dornelas"
    bairro = "Amamba"
    cidade = "Campo Grande"
    estado = "MS"
    codigo_ibge = "5002704"
    cep = "79005320"
    email = "adm@olimacms.com.br"


_ITENS_REMISSA = [{
    "produto_id": 1,
    "descricao": "Controlador de Acesso - Control iD - iDFace Face ASK",
    "ncm": "85437099",
    "unidade": "UN",
    "quantidade": 1,
    "preco_unitario": 1790.0,
    "origem": 0,
}]


def test_payload_sem_pagamento_usa_tpag_90_com_vpag_zero():
    payload = montar_payload_nfe(
        _empresa_fake(), _ClienteFake(), _ITENS_REMISSA,
        numero_nfe=4476, cfop="5915",
        natureza_operacao="Remessa para conserto dentro do Estado",
        forma_pagamento="sem_pagamento",
    )
    pag = payload["pagamentos"][0]
    assert pag["tipoPagamento"] == "90"
    # vNF continua 1790 (o bem circulou) mas nao houve pagamento
    assert pag["valor"] == 0.0
    assert round(sum(i["valorTotal"] for i in payload["items"]), 2) == 1790.0


def test_payload_sem_pagamento_nao_monta_grupo_de_cobranca():
    payload = montar_payload_nfe(
        _empresa_fake(), _ClienteFake(), _ITENS_REMISSA,
        numero_nfe=4476, cfop="5915", forma_pagamento="sem_pagamento",
        duplicatas=[{"numero": 1, "vencimento": "2026-11-15", "valor": 1790.0}],
    )
    assert "cobranca" not in payload


def test_pagamento_a_vista_continua_com_vpag_total():
    """Nao pode regredir: nas formas com pagamento o vPag segue o total da nota."""
    payload = montar_payload_nfe(
        _empresa_fake(), _ClienteFake(), _ITENS_REMISSA,
        numero_nfe=4476, forma_pagamento="pix",
    )
    assert payload["pagamentos"][0]["tipoPagamento"] == "17"
    assert payload["pagamentos"][0]["valor"] == 1790.0


def test_boleto_mantem_grupo_de_cobranca():
    payload = montar_payload_nfe(
        _empresa_fake(), _ClienteFake(), _ITENS_REMISSA,
        numero_nfe=4476, forma_pagamento="boleto",
        duplicatas=[{"numero": 1, "vencimento": "2026-11-15", "valor": 1790.0}],
    )
    assert payload["pagamentos"][0]["tipoPagamento"] == "15"
    assert payload["cobranca"]["parcelas"][0]["valor"] == 1790.0


# ---------------------------------------------------------------- integracao

async def _csrf(client: AsyncClient, url: str) -> str:
    resp = await client.get(url)
    assert resp.status_code == 200
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    return m.group(1) if m else "dummy"


@pytest.mark.asyncio
async def test_rascunho_remessa_sem_pagamento_nao_gera_cobranca(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Mesmo com 'Gerar Cobranca' marcado e parcela preenchida, a remesa nao
    pode virar conta a receber: o valor do bem nao e receita."""
    test_empresa.notaas_api_key = "test-key"
    db_session.commit()

    cliente = criar_cliente_teste(db_session, inscricao_estadual="123456")
    produto = Produto(nome="Controlador iDFace", preco=1790, tipo="produto")
    db_session.add(produto)
    db_session.commit()

    itens = [{
        "produto_id": produto.id, "descricao": produto.nome, "ncm": "85437099",
        "unidade": "UN", "quantidade": 1, "preco_unitario": 1790.0,
    }]
    csrf = await _csrf(authenticated_client, "/nfe/emitir/avulsa")
    resp = await authenticated_client.post("/nfe/emitir/avulsa", data={
        "csrf_token": csrf,
        "cliente_id": str(cliente.id),
        "natureza_operacao": "Remessa para conserto dentro do Estado",
        "cfop": "5915",
        "itens_json": json.dumps(itens),
        "forma_pagamento": "sem_pagamento",
        "gerar_cobranca": "on",
        "num_parcelas": "2",
        "primeiro_vencimento": "2026-11-15",
    })
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.query(NFe).filter(NFe.cliente_id == cliente.id).first()
    assert nfe is not None
    assert nfe.forma_pagamento == "sem_pagamento"
    # o valor do bem continua na nota
    assert float(nfe.valor_total) == 1790.0
    # ... mas nada foi para o financeiro
    contas = db_session.query(ContaReceber).filter(ContaReceber.nfe_id == nfe.id).all()
    assert contas == []


@pytest.mark.asyncio
async def test_rascunho_venda_pix_ainda_gera_cobranca(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    """Contraprova: a opcao nova nao pode desligar a cobranca das vendas."""
    test_empresa.notaas_api_key = "test-key"
    db_session.commit()

    cliente = criar_cliente_teste(db_session, inscricao_estadual="123456")
    produto = Produto(nome="Produto Venda", preco=100, tipo="produto")
    db_session.add(produto)
    db_session.commit()

    itens = [{
        "produto_id": produto.id, "descricao": produto.nome, "ncm": "99999999",
        "unidade": "UN", "quantidade": 1, "preco_unitario": 100.0,
    }]
    csrf = await _csrf(authenticated_client, "/nfe/emitir/avulsa")
    resp = await authenticated_client.post("/nfe/emitir/avulsa", data={
        "csrf_token": csrf,
        "cliente_id": str(cliente.id),
        "itens_json": json.dumps(itens),
        "forma_pagamento": "pix",
        "gerar_cobranca": "on",
    })
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.query(NFe).filter(NFe.cliente_id == cliente.id).first()
    assert nfe is not None
    conta = db_session.query(ContaReceber).filter(ContaReceber.nfe_id == nfe.id).first()
    assert conta is not None
    assert float(conta.valor) == 100.0
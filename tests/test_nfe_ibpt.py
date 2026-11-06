"""IBPT (Lei 12.741/2012) nas NF-e.

Regra: o texto "Total aproximado de tributos" incide na VENDA. Em remessa
para conserto, retorno, consignacao, transferencia etc. ele e suprimido por
padrao (CFOP), com override por nota (`NFe.suprimir_ibpt`). Em venda o texto
continua saindo — a obrigacao e legal.

Tambem cobre a edicao das informacoes complementares (infCpl) na tela de
previa, sem texto padrao.
"""
import json
import re
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from models_nfe import NFe
from services.nfe_notaas import cfop_nao_venda, montar_payload_nfe
from tests.conftest import criar_cliente_teste


# ------------------------------------------------------------------ unitario

def test_cfop_de_remessa_nao_e_venda():
    for cfop in ("5915", "6915", "5901", "5902", "5910", "5911",
                 "5949", "5920", "6915"):
        assert cfop_nao_venda(cfop) is True, cfop


def test_cfop_de_venda_continua_com_ibpt():
    for cfop in ("5102", "6102", "5405", "5101", "6101", "5551", "6551"):
        assert cfop_nao_venda(cfop) is False, cfop


def test_cfop_de_transferencia_nao_e_venda():
    assert cfop_nao_venda("5151") is True
    assert cfop_nao_venda("5152") is True
    assert cfop_nao_venda("6151") is True


def test_cfop_invalido_nao_suprime():
    for cfop in (None, "", "51", "abc", "12345"):
        assert cfop_nao_venda(cfop) is False


class _EmpresaFake:
    cfop_padrao = "5102"
    estado = "MS"
    serie_nfe = 1
    nfe_aliquota_federal = 15.67
    nfe_aliquota_estadual = 17.00


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


_ITENS = [{
    "produto_id": 1,
    "descricao": "Controlador de Acesso",
    "ncm": "85437099",
    "unidade": "UN",
    "quantidade": 1,
    "preco_unitario": 1790.0,
    "origem": 0,
}]


def test_payload_remessa_suprime_texto_ibpt_mas_mantem_observacoes():
    payload = montar_payload_nfe(
        _EmpresaFake(), _ClienteFake(), _ITENS,
        numero_nfe=4476, cfop="5915",
        observacoes="Remessa para conserto em garantia, devendo retornar.",
        suprimir_ibpt=True,
    )
    assert payload["infCpl"] == "Remessa para conserto em garantia, devendo retornar."


def test_payload_venda_mantem_texto_ibpt():
    payload = montar_payload_nfe(
        _EmpresaFake(), _ClienteFake(), _ITENS,
        numero_nfe=4477, cfop="5102",
        observacoes="Pedido 123",
        suprimir_ibpt=False,
    )
    assert "Total aproximado de tributos" in payload["infCpl"]
    assert "Pedido 123" in payload["infCpl"]


# ---------------------------------------------------------------- integracao

async def _csrf(client: AsyncClient, url: str) -> str:
    resp = await client.get(url)
    assert resp.status_code == 200
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    return m.group(1) if m else "dummy"


@pytest.mark.asyncio
async def test_emissao_remessa_persiste_suprimir_ibpt(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    test_empresa.notaas_api_key = "test-key"
    db_session.commit()

    cliente = criar_cliente_teste(db_session, inscricao_estadual="123456")
    cliente.tipo_pessoa = "juridica"
    db_session.commit()

    itens = [{
        "produto_id": None, "descricao": "Controlador", "ncm": "85437099",
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
        "suprimir_ibpt": "1",
    })
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.query(NFe).filter(NFe.cliente_id == cliente.id).first()
    assert nfe is not None
    assert nfe.cfop == "5915"
    assert nfe.suprimir_ibpt is True


@pytest.mark.asyncio
async def test_editar_observacoes_do_rascunho(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    nfe = NFe(
        cliente_id=cliente.id, numero=9001, serie=1, status="rascunho",
        cfop="5915", natureza_operacao="Remessa para conserto",
        valor_total=Decimal("1790.00"),
    )
    db_session.add(nfe)
    db_session.commit()
    db_session.refresh(nfe)

    csrf = await _csrf(authenticated_client, f"/nfe/{nfe.id}/previa")
    resp = await authenticated_client.post(f"/nfe/{nfe.id}/observacoes", data={
        "csrf_token": csrf,
        "observacoes": "  Remessa para conserto. Devolver em 30 dias.  ",
        "suprimir_ibpt": "1",
    })
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.query(NFe).filter(NFe.id == nfe.id).first()
    assert nfe.observacoes == "Remessa para conserto. Devolver em 30 dias."
    assert nfe.suprimir_ibpt is True


@pytest.mark.asyncio
async def test_observacoes_nao_alteram_nota_emitida(
    authenticated_client: AsyncClient, db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    nfe = NFe(
        cliente_id=cliente.id, numero=9002, serie=1, status="issued",
        cfop="5102", natureza_operacao="Venda de mercadoria",
        valor_total=Decimal("100.00"), observacoes="Texto original",
    )
    db_session.add(nfe)
    db_session.commit()
    db_session.refresh(nfe)

    csrf = await _csrf(authenticated_client, "/nfe/emitir/avulsa")
    resp = await authenticated_client.post(f"/nfe/{nfe.id}/observacoes", data={
        "csrf_token": csrf,
        "observacoes": "Tentativa de alteracao",
        "suprimir_ibpt": "1",
    })
    assert resp.status_code == 303

    db_session.expire_all()
    nfe = db_session.query(NFe).filter(NFe.id == nfe.id).first()
    assert nfe.observacoes == "Texto original"
    assert nfe.suprimir_ibpt is False

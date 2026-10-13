"""Testes da cobranca de OS com NFe (pecas) + NFSe (servicos).

Cobre o ponto unico de cobranca da OS (routers/ordens_servico.py):
- agrupada (uma conta com NFe + NFSe, um boleto) ou separada (una por nota);
- a condicao escolhida na OS e persistida e herdada pelo rascunho de NFSe;
- as notas da OS nao geram cobranca na transmissao nem pelo botao da tela da nota,
  para nao duplicar a cobranca da OS.
"""
import json
import re
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from models import ContaReceber, OrdemServico, StatusConta
from models_nfe import NFe, NFSe
from routers.nfse import _garantir_cobranca_nfse, emitir_os_nfse_submit
from services.parcelamento import contas_receber_existentes_para
from tests.conftest import criar_cliente_teste


class FakeRequest:
    def __init__(self):
        self.session = {}
        self.headers = {}


async def _csrf(client, url):
    resp = await client.get(url)
    achado = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    return achado.group(1) if achado else "dummy"


def criar_os_com_notas(db_session, *, nfe_valor=270, nfse_valor=220, cliente=None):
    """OS com uma NFe de pecas transmitida e uma NFSe de servicos autorizada."""
    cliente = cliente or criar_cliente_teste(db_session)
    os_ = OrdemServico(
        cliente_id=cliente.id,
        equipamento="Notebook Dell",
        valor_servico=nfse_valor,
        valor_pecas=nfe_valor,
        valor_total=Decimal(str(nfe_valor + nfse_valor)),
        servicos_executados=json.dumps(
            [{"nome": "Assistencia tecnica", "qtd": 1.0, "preco": float(nfse_valor)}]
        ),
    )
    db_session.add(os_)
    db_session.commit()
    db_session.refresh(os_)

    nfe = NFe(os_id=os_.id, origem="os", cliente_id=cliente.id, numero=4470,
              status="issued", valor_total=nfe_valor, forma_pagamento="boleto")
    nfse = NFSe(os_id=os_.id, origem="os", cliente_id=cliente.id, numero="240",
                status="autorizada", valor_total=nfse_valor)
    db_session.add_all([nfe, nfse])
    db_session.commit()
    db_session.refresh(nfe)
    db_session.refresh(nfse)
    return os_, nfe, nfse


@pytest.mark.asyncio
async def test_cobranca_agrupada_soma_nfe_e_nfse(
    authenticated_client, db_session: Session, test_empresa
):
    """Sem a flag, uma unica conta com as duas notas e um unico boleto."""
    os_, nfe, nfse = criar_os_com_notas(db_session)

    resp = await authenticated_client.post(
        f"/ordens-servico/{os_.id}/gerar-cobranca",
        data={
            "csrf_token": await _csrf(authenticated_client, f"/ordens-servico/{os_.id}"),
            "forma_pagamento": "boleto", "num_parcelas": 1, "intervalo_dias": 30,
        },
    )
    assert resp.status_code == 303

    db_session.expire_all()
    contas = db_session.query(ContaReceber).all()
    assert len(contas) == 1
    conta = contas[0]
    assert Decimal(str(conta.valor)) == Decimal("490.00")
    assert conta.nfe_id == nfe.id
    assert conta.nfse_id == nfse.id
    assert conta.os_id == os_.id
    assert conta.forma_pagamento == "boleto"


@pytest.mark.asyncio
async def test_cobranca_separada_gera_uma_conta_por_nota(
    authenticated_client, db_session: Session, test_empresa
):
    """A flag 'Cobranca separada' continua gerando NFe e NFSe em contas distintas."""
    os_, nfe, nfse = criar_os_com_notas(db_session)

    resp = await authenticated_client.post(
        f"/ordens-servico/{os_.id}/gerar-cobranca",
        data={
            "csrf_token": await _csrf(authenticated_client, f"/ordens-servico/{os_.id}"),
            "forma_pagamento": "boleto", "num_parcelas": 1, "intervalo_dias": 30,
            "cobranca_separada": "1",
        },
    )
    assert resp.status_code == 303

    db_session.expire_all()
    contas = db_session.query(ContaReceber).order_by(ContaReceber.id).all()
    assert len(contas) == 2
    assert [c.nfe_id for c in contas] == [nfe.id, None]
    assert [c.nfse_id for c in contas] == [None, nfse.id]
    assert all(c.os_id == os_.id for c in contas)


@pytest.mark.asyncio
async def test_segunda_cobranca_da_os_e_bloqueada(
    authenticated_client, db_session: Session, test_empresa
):
    os_, nfe, nfse = criar_os_com_notas(db_session)
    dados = {
        "csrf_token": await _csrf(authenticated_client, f"/ordens-servico/{os_.id}"),
        "forma_pagamento": "boleto", "num_parcelas": 1, "intervalo_dias": 30,
    }
    await authenticated_client.post(f"/ordens-servico/{os_.id}/gerar-cobranca", data=dados)
    resp = await authenticated_client.post(f"/ordens-servico/{os_.id}/gerar-cobranca", data=dados)

    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.query(ContaReceber).count() == 1


@pytest.mark.asyncio
async def test_condicao_escolhida_na_os_e_persistida(
    authenticated_client, db_session: Session, test_empresa
):
    os_, nfe, nfse = criar_os_com_notas(db_session)

    await authenticated_client.post(
        f"/ordens-servico/{os_.id}/gerar-cobranca",
        data={
            "csrf_token": await _csrf(authenticated_client, f"/ordens-servico/{os_.id}"),
            "forma_pagamento": "boleto", "num_parcelas": 2, "intervalo_dias": 15,
            "primeiro_vencimento": "2026-11-10",
        },
    )

    db_session.expire_all()
    os_ = db_session.query(OrdemServico).get(os_.id)
    assert os_.forma_pagamento == "boleto"
    assert os_.num_parcelas == 2
    assert os_.intervalo_dias == 15
    assert os_.primeiro_vencimento is not None


@pytest.mark.asyncio
async def test_recibo_sem_nota_nao_sobrescreve_condicao_da_os(
    authenticated_client, db_session: Session, test_empresa
):
    """PIX pontual e' evento da venda, nao condicao da OS."""
    cliente = criar_cliente_teste(db_session)
    os_ = OrdemServico(
        cliente_id=cliente.id, equipamento="Monitor",
        valor_servico=100, valor_total=100,
        servicos_executados=json.dumps([{"nome": "Troca de tela", "qtd": 1.0, "preco": 100.0}]),
    )
    db_session.add(os_)
    db_session.commit()
    db_session.refresh(os_)
    os_.forma_pagamento = "boleto"
    db_session.commit()

    await authenticated_client.post(
        f"/ordens-servico/{os_.id}/gerar-cobranca",
        data={
            "csrf_token": await _csrf(authenticated_client, f"/ordens-servico/{os_.id}"),
            "forma_pagamento": "pix", "num_parcelas": 1, "intervalo_dias": 30,
        },
    )

    db_session.expire_all()
    os_ = db_session.query(OrdemServico).get(os_.id)
    assert os_.forma_pagamento == "boleto"


def test_rascunho_nfse_da_os_herda_condicao_da_os(db_session: Session, test_empresa):
    """O rascunho de NFSe nasce com a condicao da OS, e a tela pode escolher outra."""
    os_, _, _ = criar_os_com_notas(db_session)
    os_.forma_pagamento = "boleto"
    db_session.commit()

    emitir_os_nfse_submit(FakeRequest(), os_.id, db_session, forma_pagamento="")

    db_session.expire_all()
    rascunhos = (
        db_session.query(NFSe)
        .filter(NFSe.os_id == os_.id, NFSe.status == "rascunho")
        .order_by(NFSe.id)
        .all()
    )
    assert len(rascunhos) == 1
    assert rascunhos[0].forma_pagamento == "boleto"

    emitir_os_nfse_submit(FakeRequest(), os_.id, db_session, forma_pagamento="pix")

    db_session.expire_all()
    rascunhos = (
        db_session.query(NFSe)
        .filter(NFSe.os_id == os_.id, NFSe.status == "rascunho")
        .order_by(NFSe.id)
        .all()
    )
    assert [r.forma_pagamento for r in rascunhos] == ["boleto", "pix"]


def test_nfse_de_os_nao_gera_cobranca_na_transmissao(db_session: Session, test_empresa):
    """A cobranca da OS e gerada na tela da OS; a transmissao nao cria a segunda."""
    os_, _, nfse = criar_os_com_notas(db_session)

    _garantir_cobranca_nfse(db_session, nfse)

    db_session.expire_all()
    assert db_session.query(ContaReceber).count() == 0


def test_nfse_de_pedido_ainda_gera_cobranca_na_transmissao(db_session: Session, test_empresa):
    """Contraprova: fora da OS, a NFSe continua gerando a cobranca na transmissao."""
    cliente = criar_cliente_teste(db_session)
    nfse = NFSe(origem="avulsa", cliente_id=cliente.id, numero="241",
                status="autorizada", valor_total=220, forma_pagamento="pix")
    db_session.add(nfse)
    db_session.commit()
    db_session.refresh(nfse)

    _garantir_cobranca_nfse(db_session, nfse)

    db_session.expire_all()
    contas = db_session.query(ContaReceber).all()
    assert len(contas) == 1
    assert contas[0].nfse_id == nfse.id
    assert contas[0].status == StatusConta.PAGO  # pix e' a vista


def _cobranca_mao(cliente_id, **kwargs):
    return ContaReceber(
        cliente_id=cliente_id,
        descricao="NFe #4470",
        valor=Decimal("270.00"),
        data_vencimento=date.today(),
        forma_pagamento="boleto",
        status=StatusConta.PENDENTE,
        **kwargs,
    )


def test_anti_duplicidade_enxerga_a_nota_irma_da_os(db_session: Session, test_empresa):
    """A cobranca feita para a NFe aparece para a NFSe da mesma OS."""
    os_, nfe, nfse = criar_os_com_notas(db_session)
    db_session.add(_cobranca_mao(os_.cliente_id, nfe_id=nfe.id, os_id=os_.id))
    db_session.commit()

    assert contas_receber_existentes_para(db_session, nfse=nfse)
    assert contas_receber_existentes_para(db_session, nfe=nfe)


def test_nota_de_os_nao_ve_cobranca_de_outra_os(db_session: Session, test_empresa):
    """O vinculo por OS nao pode vazar entre OS diferentes."""
    os_, nfe, nfse = criar_os_com_notas(db_session)
    outro_cliente = criar_cliente_teste(db_session, cpf_cnpj="98765432100")
    outra_os, outra_nfe, _ = criar_os_com_notas(db_session, cliente=outro_cliente)
    db_session.add(_cobranca_mao(outra_os.cliente_id, nfe_id=outra_nfe.id, os_id=outra_os.id))
    db_session.commit()

    assert contas_receber_existentes_para(db_session, nfse=nfse) == []


def test_cobranca_cancelada_nao_bloqueia(db_session: Session, test_empresa):
    """Conta cancelada libera a OS para gerar a cobranca de novo."""
    os_, nfe, nfse = criar_os_com_notas(db_session)
    conta = _cobranca_mao(os_.cliente_id, nfe_id=nfe.id, os_id=os_.id)
    conta.status = StatusConta.CANCELADO
    db_session.add(conta)
    db_session.commit()

    assert contas_receber_existentes_para(db_session, nfse=nfse) == []
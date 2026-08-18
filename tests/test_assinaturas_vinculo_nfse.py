from datetime import date

import pytest

from models import Assinatura, ContaReceber, NFSe, StatusConta
from routers import assinaturas as router_assinaturas
from routers.assinaturas import (
    gerar_nfse_assinatura,
    proximo_vencimento_para_cobranca,
    _add_months,
)
from routers.nfse import gerar_cobranca_nfse
from tests.conftest import criar_cliente_teste


class _FakeRequest:
    def __init__(self):
        self.session = {}


@pytest.mark.asyncio
async def test_gerar_nfse_vincula_assinatura_e_cobranca_usa_vencimento(db_session, test_empresa):
    cliente = criar_cliente_teste(db_session, nome="VincNFSe Cli", cpf_cnpj="22222233333")
    a = Assinatura(
        cliente_id=cliente.id, periodicidade=1, descricao="Assinatura Vinc",
        valor=100, quantidade=1, data_inicio=date.today(), data_fim=date(2030, 1, 1),
        dia_vencimento=1, mes_vencimento=0, situacao=1, travar_cobranca=True,
    )
    db_session.add(a)
    db_session.commit()
    db_session.refresh(a)

    # 1) Gera a NFS-e a partir da assinatura (fluxo pos-trava)
    req = _FakeRequest()
    gerar_nfse_assinatura(req, a.id, db_session)

    db_session.expire_all()
    nfse = db_session.query(NFSe).filter(
        NFSe.cliente_id == cliente.id, NFSe.origem == "assinatura"
    ).order_by(NFSe.id.desc()).first()
    assert nfse is not None
    # O vinculo foi estabelecido
    assert nfse.assinatura_id == a.id

    # A rota de cobranca recusa NFSe em rascunho; simula a emissao (status apos transmissao)
    nfse.status = "autorizada"
    db_session.commit()

    # Vencimento esperado da PRIMEIRA cobranca (ainda nao ha conta gerada)
    esperado = proximo_vencimento_para_cobranca(db_session, a)

    # 2) Gerar cobranca a partir da NFS-e sem informar data -> usa vencimento da assinatura
    req2 = _FakeRequest()
    gerar_cobranca_nfse(req2, nfse.id, db_session, num_parcelas=1, primeiro_vencimento="", intervalo_dias=30)

    db_session.expire_all()
    conta = db_session.query(ContaReceber).filter(
        ContaReceber.observacao.like(f"%NFSe #{nfse.id}%")
    ).order_by(ContaReceber.id.desc()).first()
    assert conta is not None
    # Vencimento bate com o proximo da assinatura (primeira cobranca)
    assert conta.data_vencimento == esperado
    # Observacao permite rastrear a assinatura para renovacao futura
    assert f"assinatura #{a.id}" in conta.observacao

    # 3) Segunda renovacao: nova NFS-e do ciclo seguinte avanca o periodo
    req3 = _FakeRequest()
    gerar_nfse_assinatura(req3, a.id, db_session)
    db_session.expire_all()
    nfse2 = db_session.query(NFSe).filter(
        NFSe.cliente_id == cliente.id, NFSe.origem == "assinatura"
    ).order_by(NFSe.id.desc()).first()
    # Simula a emissao da segunda NFSe antes de gerar a cobranca
    nfse2.status = "autorizada"
    db_session.commit()
    req4 = _FakeRequest()
    gerar_cobranca_nfse(req4, nfse2.id, db_session, num_parcelas=1, primeiro_vencimento="", intervalo_dias=30)

    db_session.expire_all()
    contas = db_session.query(ContaReceber).filter(
        ContaReceber.observacao.like(f"%assinatura #{a.id}%")
    ).order_by(ContaReceber.data_vencimento.asc()).all()
    assert len(contas) == 2
    # segunda cobranca = primeira + 1 mes (periodicidade 1)
    assert contas[1].data_vencimento == _add_months(contas[0].data_vencimento, 1)


@pytest.mark.asyncio
async def test_proximo_vencimento_nao_mostra_data_passada(db_session, test_empresa):
    cliente = criar_cliente_teste(db_session, nome="PVCli", cpf_cnpj="22222333334")
    a = Assinatura(
        cliente_id=cliente.id, periodicidade=1, descricao="PV",
        valor=100, quantidade=1, data_inicio=date.today(), data_fim=date(2030, 1, 1),
        dia_vencimento=1, mes_vencimento=0, situacao=1, travar_cobranca=False,
    )
    db_session.add(a)
    db_session.commit()
    db_session.refresh(a)

    prox = router_assinaturas._proximo_vencimento(a)
    assert prox is not None
    assert prox >= date.today()

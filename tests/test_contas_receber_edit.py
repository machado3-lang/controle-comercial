from datetime import date

import pytest

from models import Cliente, ContaReceber, StatusConta
from routers.contas import atualizar_conta_receber
from tests.conftest import criar_cliente_teste


class _FakeRequest:
    def __init__(self):
        self.session = {}


@pytest.mark.asyncio
async def test_editar_conta_salva_data_recebimento(db_session):
    cliente = criar_cliente_teste(db_session, nome="CtaEdit", cpf_cnpj="44444555556")
    conta = ContaReceber(
        cliente_id=cliente.id, descricao="Mensal", valor=100,
        data_vencimento=date(2026, 8, 1), status=StatusConta.PENDENTE,
    )
    db_session.add(conta)
    db_session.commit()
    db_session.refresh(conta)
    assert conta.data_recebimento is None

    req = _FakeRequest()
    atualizar_conta_receber(
        req, conta.id, db_session,
        descricao="Mensal", valor="100", data_vencimento=date(2026, 8, 1),
        cliente_id=str(cliente.id), status=StatusConta.PAGO,
        numero_documento=None, tipo_documento_id=None, plano_conta_id=None,
        forma_pagamento="pix", observacao=None, data_recebimento="2026-07-20", emitir_boletos=False,
    )

    db_session.expire_all()
    c = db_session.get(ContaReceber, conta.id)
    # Bug corrigido: data_recebimento agora e salva
    assert c.data_recebimento == date(2026, 7, 20)
    assert c.status == StatusConta.PAGO


@pytest.mark.asyncio
async def test_editar_conta_paga_sem_data_usa_hoje(db_session):
    cliente = criar_cliente_teste(db_session, nome="CtaEdit2", cpf_cnpj="44444666667")
    conta = ContaReceber(
        cliente_id=cliente.id, descricao="Mensal", valor=100,
        data_vencimento=date(2026, 8, 1), status=StatusConta.PENDENTE,
    )
    db_session.add(conta)
    db_session.commit()
    db_session.refresh(conta)

    req = _FakeRequest()
    atualizar_conta_receber(
        req, conta.id, db_session,
        descricao="Mensal", valor="100", data_vencimento=date(2026, 8, 1),
        cliente_id=str(cliente.id), status=StatusConta.PAGO,
        numero_documento=None, tipo_documento_id=None, plano_conta_id=None,
        forma_pagamento="pix", observacao=None, data_recebimento="", emitir_boletos=False,
    )

    db_session.expire_all()
    c = db_session.get(ContaReceber, conta.id)
    # Sem data informada e status pago -> assume hoje
    assert c.data_recebimento == date.today()

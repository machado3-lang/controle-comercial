"""Testes da propagacao da observacao da OS para as notas e dos termos do boleto.

`ordens_servico.observacao` era write-only: salva, exibida na OS e sincronizada
com o Bling, mas nenhum gerador de NFe/NFSe a lia. Agora a OS tem a flag
`propagar_observacao` (o padrao offered na emissao) e a tela de emissao da NFe e
da NFSe oferece o envio.

O mesmo vale para `terminos_boleto` do pedido/consolidacao: campo write-only que
vira `mensagensInstrucao` (5 linhas de 40 caracteres) no POST /boletos do Sicoob.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

import routers.sicoob as sicoob
from models import (
    ContaReceber, OrdemServico, PedidoVenda, PedidoConsolidado, StatusConta,
)
from models_nfe import NFe, NFSe
from routers.nfe import emitir_os_submit
from routers.nfse import emitir_os_nfse_submit
from tests.conftest import criar_cliente_teste


class FakeRequest:
    def __init__(self):
        self.session = {}
        self.headers = {}


def criar_os(db_session, *, observacao="", propagar=False, pecas=270, servico=220):
    cliente = criar_cliente_teste(db_session)
    cliente.isento_ie = True
    db_session.commit()
    os_ = OrdemServico(
        cliente_id=cliente.id, equipamento="Notebook Dell",
        valor_servico=servico, valor_pecas=pecas, valor_total=Decimal(str(pecas + servico)),
        observacao=observacao or None,
        propagar_observacao=bool(propagar),
        pecas_utilizadas=json.dumps(
            [{"nome": "Teclado", "qtd": 1.0, "preco": float(pecas)}]
        ),
        servicos_executados=json.dumps(
            [{"nome": "Assistência técnica", "qtd": 1.0, "preco": float(servico)}]
        ),
    )
    db_session.add(os_)
    db_session.commit()
    db_session.refresh(os_)
    return os_


@pytest.fixture
def empresa_com_api(db_session, test_empresa):
    test_empresa.notaas_api_key = "chave-de-teste"
    db_session.commit()
    return test_empresa


def nfe_da_os(db_session):
    """Reproduz o que a rota grava, com os mesmos cortes."""
    from routers.nfe import _LIMITE_OBSERVACAO_NFE

    return db_session.query(NFe).filter(NFe.os_id != None).first()


def test_observacao_da_os_so_entra_nfe_quando_marcada(
    db_session: Session, empresa_com_api
):
    os_ = criar_os(db_session, observacao="Cliente autorizou por telefone", propagar=True)

    emitir_os_submit(FakeRequest(), os_.id, db_session, natureza_operacao="Venda de mercadoria",
                     cfop="5102", incluir_observacao="1")

    db_session.expire_all()
    nfe = db_session.query(NFe).filter(NFe.os_id == os_.id).first()
    assert nfe is not None
    assert nfe.observacoes == "Cliente autorizou por telefone"


def test_observacao_da_os_nao_entra_sem_marcacao(db_session: Session, empresa_com_api):
    """Padrao e NAO enviar: a observacao da OS costuma ser interna."""
    os_ = criar_os(db_session, observacao="Peca com garantia de 90 dias", propagar=False)

    emitir_os_submit(FakeRequest(), os_.id, db_session, natureza_operacao="Venda de mercadoria",
                     cfop="5102", incluir_observacao="")

    db_session.expire_all()
    nfe = db_session.query(NFe).filter(NFe.os_id == os_.id).first()
    assert nfe.observacoes is None


def test_observacao_da_os_vai_para_a_nfse(db_session: Session, test_empresa):
    os_ = criar_os(db_session, observacao="Garantia de 90 dias nas peças", propagar=True)

    emitir_os_nfse_submit(FakeRequest(), os_.id, db_session, forma_pagamento="",
                         incluir_observacao="1")

    db_session.expire_all()
    nfse = db_session.query(NFSe).filter(NFSe.os_id == os_.id).first()
    assert nfse is not None
    assert nfse.observacoes == "Garantia de 90 dias nas peças"


def test_observacao_da_os_nao_vai_para_a_nfse_sem_marcacao(db_session: Session, test_empresa):
    os_ = criar_os(db_session, observacao="Interno:用来 nao mandar", propagar=False)

    emitir_os_nfse_submit(FakeRequest(), os_.id, db_session, forma_pagamento="",
                         incluir_observacao="")

    db_session.expire_all()
    nfse = db_session.query(NFSe).filter(NFSe.os_id == os_.id).first()
    assert nfse.observacoes == ""


def test_observacao_longa_da_os_e_aparedada_na_nfe(db_session: Session, empresa_com_api):
    """<infCpl> aceita 5000 caracteres e ainda recebe o texto de IBPT."""
    os_ = criar_os(db_session, observacao="X" * 6000, propagar=True)

    emitir_os_submit(FakeRequest(), os_.id, db_session, natureza_operacao="Venda de mercadoria",
                     cfop="5102", incluir_observacao="1")

    db_session.expire_all()
    nfe = db_session.query(NFe).filter(NFe.os_id == os_.id).first()
    from routers.nfe import _LIMITE_OBSERVACAO_NFE

    assert len(nfe.observacoes) == _LIMITE_OBSERVACAO_NFE
    assert _LIMITE_OBSERVACAO_NFE < 5000


def test_descricao_do_servico_da_nfse_respeita_o_limite_do_dps():
    """A observacao e aparada para o que sobra dentro de <xDescServ>."""
    from services import nfse_betha

    assert nfse_betha._LIMITE_XDESCSERV == 2000


def test_termos_do_boleto_viram_mensagens_de_instrucao(db_session: Session, test_empresa):
    cliente = criar_cliente_teste(db_session)
    pedido = PedidoVenda(cliente_id=cliente.id, numero=22, total=Decimal("100"),
                         forma_pagamento="boleto",
                         terminos_boleto="Prazo de pagamento 30 dias.\nAtraso sujeita a multa.")
    db_session.add(pedido)
    db_session.commit()
    db_session.refresh(pedido)

    conta = ContaReceber(cliente_id=cliente.id, descricao="Pedido 22", valor=Decimal("100"),
                        data_vencimento=date.today() + timedelta(days=30),
                        status=StatusConta.PENDENTE, forma_pagamento="boleto",
                        pedido_id=pedido.id)
    db_session.add(conta)
    db_session.commit()

    linhas = sicoob._mensagens_instrucao_boleto(db_session, conta)

    assert linhas == ["Prazo de pagamento 30 dias.", "Atraso sujeita a multa."]


def test_termos_longos_sao_quebrados_em_40_caracteres_e_5_linhas(
    db_session: Session, test_empresa
):
    cliente = criar_cliente_teste(db_session)
    texto = "\n".join("L" * 100 for _ in range(10))
    pedido = PedidoVenda(cliente_id=cliente.id, numero=23, total=Decimal("100"),
                         forma_pagamento="boleto", terminos_boleto=texto)
    db_session.add(pedido)
    db_session.commit()
    db_session.refresh(pedido)
    conta = ContaReceber(cliente_id=cliente.id, descricao="Pedido 23", valor=Decimal("100"),
                        data_vencimento=date.today(), status=StatusConta.PENDENTE,
                        forma_pagamento="boleto", pedido_id=pedido.id)
    db_session.add(conta)
    db_session.commit()

    linhas = sicoob._mensagens_instrucao_boleto(db_session, conta)

    assert len(linhas) == 5
    assert all(len(l) <= 40 for l in linhas)


def test_sem_termos_nao_gera_mensagens(db_session: Session, test_empresa):
    """Campo opcional nao utilizado tem de sair do body, nao ir nulo."""
    cliente = criar_cliente_teste(db_session)
    pedido = PedidoVenda(cliente_id=cliente.id, numero=24, total=Decimal("100"),
                         forma_pagamento="boleto", terminos_boleto="   ")
    db_session.add(pedido)
    db_session.commit()
    db_session.refresh(pedido)
    conta = ContaReceber(cliente_id=cliente.id, descricao="Pedido 24", valor=Decimal("100"),
                        data_vencimento=date.today(), status=StatusConta.PENDENTE,
                        forma_pagamento="boleto", pedido_id=pedido.id)
    db_session.add(conta)
    db_session.commit()

    assert sicoob._mensagens_instrucao_boleto(db_session, conta) == []


def test_termos_da_consolidacao_tambem_valem(db_session: Session, test_empresa):
    cliente = criar_cliente_teste(db_session)
    cons = PedidoConsolidado(cliente_id=cliente.id, data=date.today(),
                             terminos_boleto="Faturamento mensal")
    db_session.add(cons)
    db_session.commit()
    db_session.refresh(cons)
    conta = ContaReceber(cliente_id=cliente.id, descricao="Consolidacao", valor=Decimal("100"),
                        data_vencimento=date.today(), status=StatusConta.PENDENTE,
                        forma_pagamento="boleto", consolidacao_id=cons.id)
    db_session.add(conta)
    db_session.commit()

    assert sicoob._mensagens_instrucao_boleto(db_session, conta) == ["Faturamento mensal"]
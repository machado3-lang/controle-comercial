"""Numeracao da NFe reconciliada pela chave de acesso (fonte de verdade SEFAZ).

A chave embute cUF(2) AAMM(4) CNPJ(14) mod(2) serie(3) nNF(9) tpEmis(1) cNF(8) DV(1).
"""
from datetime import date, datetime

import pytest

from models import Empresa, NFe, PedidoConsolidado, StatusConsolidacao
from routers.nfe import _duplicatas_previstas_nfe
from services.numeracao import (
    numero_da_chave,
    proximo_numero_nfe,
    reconciliar_numero_nfe,
    reverter_numero_nfe,
    serie_da_chave,
)
from tests.conftest import criar_cliente_teste


def montar_chave(numero, serie=1, cnpj="13133714000110"):
    return (
        "50" + "2611" + cnpj + "55" + f"{serie:03d}" + f"{numero:09d}"
        + "1" + "12345678" + "0"
    )


def criar_nfe(db, numero, chave=None, serie=1, status="issued", **kwargs):
    nfe = NFe(
        numero=numero,
        serie=serie,
        status=status,
        origem="avulsa",
        chave_acesso=chave,
        **kwargs,
    )
    db.add(nfe)
    db.commit()
    db.refresh(nfe)
    return nfe


def test_le_numero_e_serie_da_chave():
    chave = montar_chave(4490, serie=1)
    assert len(chave) == 44
    assert numero_da_chave(chave) == 4490
    assert serie_da_chave(chave) == 1
    assert numero_da_chave("123") is None
    assert serie_da_chave(None) is None


def test_reconcilia_numero_divergente_com_a_chave(db_session, test_empresa):
    test_empresa.ultimo_numero_nfe = 4492
    db_session.commit()
    nfe = criar_nfe(db_session, 4491, chave=montar_chave(4490))

    alterou = reconciliar_numero_nfe(db_session, test_empresa, nfe)
    db_session.commit()

    assert alterou is True
    assert nfe.numero == 4490
    # Contador nunca desce (4492 ja estava a frente).
    assert test_empresa.ultimo_numero_nfe == 4492


def test_reconcilia_sobe_contador_quando_esta_atras(db_session, test_empresa):
    test_empresa.ultimo_numero_nfe = 100
    db_session.commit()
    nfe = criar_nfe(db_session, 100, chave=montar_chave(4490))

    reconciliar_numero_nfe(db_session, test_empresa, nfe)
    db_session.commit()

    assert nfe.numero == 4490
    assert test_empresa.ultimo_numero_nfe == 4490


def test_reconciliacao_nao_cria_numero_duplicado(db_session, test_empresa):
    # B ja ocupa 4490; corrigir A para 4490 criaria duplicata -> deve recusar.
    criar_nfe(db_session, 4490, chave=montar_chave(4491))
    a = criar_nfe(db_session, 4491, chave=montar_chave(4490))

    alterou = reconciliar_numero_nfe(db_session, test_empresa, a)
    db_session.commit()

    assert alterou is False
    assert a.numero == 4491


def test_reconciliacao_e_idempotente(db_session, test_empresa):
    nfe = criar_nfe(db_session, 4490, chave=montar_chave(4490))
    assert reconciliar_numero_nfe(db_session, test_empresa, nfe) is False
    assert nfe.numero == 4490


def test_proximo_numero_pula_numero_ja_usado(db_session, test_empresa):
    test_empresa.ultimo_numero_nfe = 10
    db_session.commit()
    criar_nfe(db_session, 11, serie=1, status="rascunho")

    numero = proximo_numero_nfe(db_session, test_empresa)
    db_session.refresh(test_empresa)

    assert numero == 12
    assert test_empresa.ultimo_numero_nfe == 12


def test_proximo_numero_sem_commit_mantem_transacao_do_chamador(db_session, test_empresa):
    test_empresa.ultimo_numero_nfe = 20
    db_session.commit()

    numero = proximo_numero_nfe(db_session, test_empresa, commit=False)

    assert numero == 21
    assert test_empresa.ultimo_numero_nfe == 21
    db_session.rollback()
    db_session.refresh(test_empresa)
    assert test_empresa.ultimo_numero_nfe == 20


def test_rejeitada_nao_bloqueia_reconciliacao_da_autorizada(db_session, test_empresa):
    """Caso real 4494/4495: a tentativa rejeitada nao ocupa o numero na SEFAZ.

    A NFe rejeitada tem invoice/chave da tentativa (mas sem protocolo); a nota
    autorizada ficou com numero local 4495 enquanto a chave confirma 4494.
    A reconciliacao deve rebaixar a autorizada para 4494 sem recusar por causa
    da rejeitada.
    """
    test_empresa.ultimo_numero_nfe = 4494
    db_session.commit()
    rejeitada = criar_nfe(db_session, 4494, status="error", chave=montar_chave(4494))
    rejeitada.invoice_id = "5db3d4c3-2d20-4022-b993-5fabc2f63622"
    autorizada = criar_nfe(
        db_session, 4495, chave=montar_chave(4494), protocolo="150260051801410",
    )
    db_session.commit()

    alterou = reconciliar_numero_nfe(db_session, test_empresa, autorizada)
    db_session.commit()

    assert alterou is True
    assert autorizada.numero == 4494
    assert test_empresa.ultimo_numero_nfe == 4494
    # A proxima emissao continua em 4495 (numero que a SEFAZ espera).
    assert proximo_numero_nfe(db_session, test_empresa) == 4495


def test_reverter_numero_libera_rejeitada_mesmo_com_chave(db_session, test_empresa):
    """Excluir a tentativa rejeitada devolve o numero ao contador."""
    criar_nfe(db_session, 4493, status="issued")
    rejeitada = criar_nfe(db_session, 4494, status="error", chave=montar_chave(4494))
    rejeitada.invoice_id = "abc"
    test_empresa.ultimo_numero_nfe = 4494
    db_session.commit()

    assert reverter_numero_nfe(db_session, test_empresa, rejeitada) == 4493


def test_reverter_numero_nao_libera_autorizada(db_session, test_empresa):
    """Nota autorizada (com protocolo) ocupa o numero e nunca volta."""
    autorizada = criar_nfe(
        db_session, 4494, status="issued", chave=montar_chave(4494),
        protocolo="150260051801410",
    )
    test_empresa.ultimo_numero_nfe = 4494
    db_session.commit()

    assert reverter_numero_nfe(db_session, test_empresa, autorizada) is None
    assert test_empresa.ultimo_numero_nfe == 4494


def _consolidacao_com_vencimento(db_session, vencimento):
    cliente = criar_cliente_teste(db_session)
    cons = PedidoConsolidado(
        cliente_id=cliente.id, numero=f"CONS-{vencimento.isoformat()}",
        total=100, status=StatusConsolidacao.CONCLUIDO,
        primeiro_vencimento=vencimento, num_parcelas=1, intervalo_dias=30,
        gerar_cobranca=True,
    )
    db_session.add(cons)
    db_session.commit()
    nfe = NFe(
        consolidacao_id=cons.id, numero=4495, serie=1, status="rascunho",
        origem="consolidacao", valor_total=100,
        data_emissao=datetime(2026, 11, 11, 9, 0),
    )
    db_session.add(nfe)
    db_session.commit()
    return nfe


def test_duplicata_da_nfe_recusa_vencimento_anterior_a_emissao(db_session, test_empresa):
    """cStat 900 / nOcor 001: <dVenc> >= <dhEmi>. A validacao deve barrar antes."""
    nfe = _consolidacao_com_vencimento(db_session, date(2026, 11, 9))

    with pytest.raises(ValueError):
        _duplicatas_previstas_nfe(db_session, nfe, "boleto")


def test_duplicata_da_nfe_usa_o_vencimento_da_consolidacao(db_session, test_empresa):
    nfe = _consolidacao_com_vencimento(db_session, date(2026, 11, 11))

    duplicatas = _duplicatas_previstas_nfe(db_session, nfe, "boleto")

    assert duplicatas is not None
    assert duplicatas[0]["vencimento"] == "2026-11-11"


def test_duplicata_aceita_vencimento_posterior_a_emissao(db_session, test_empresa):
    nfe = _consolidacao_com_vencimento(db_session, date(2026, 12, 10))

    duplicatas = _duplicatas_previstas_nfe(db_session, nfe, "boleto")

    assert duplicatas[0]["vencimento"] == "2026-12-10"

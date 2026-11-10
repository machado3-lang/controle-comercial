"""Numeracao da NFe reconciliada pela chave de acesso (fonte de verdade SEFAZ).

A chave embute cUF(2) AAMM(4) CNPJ(14) mod(2) serie(3) nNF(9) tpEmis(1) cNF(8) DV(1).
"""
from models import Empresa, NFe
from services.numeracao import (
    numero_da_chave,
    proximo_numero_nfe,
    reconciliar_numero_nfe,
    serie_da_chave,
)


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

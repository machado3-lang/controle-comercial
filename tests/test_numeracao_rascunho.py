"""Testes da devolucao da numeracao ao excluir um rascunho de NFe / NFSe."""
import pytest

from models import Empresa, NFe, NFSe
from routers.nfe import excluir_nfe
from routers.nfse import excluir_nfse
from services.numeracao import reverter_numero_nfe


class FakeRequest:
    def __init__(self):
        self.session = {}


def criar_nfe(db, numero, status="rascunho", **kwargs):
    nfe = NFe(numero=numero, status=status, origem="avulsa", **kwargs)
    db.add(nfe)
    db.commit()
    db.refresh(nfe)
    return nfe


def criar_nfse(db, numero, status="rascunho", **kwargs):
    nfse = NFSe(numero=str(numero), status=status, origem="avulsa", **kwargs)
    db.add(nfse)
    db.commit()
    db.refresh(nfse)
    return nfse


def contador_nfe(db):
    db.expire_all()
    return db.query(Empresa).first().ultimo_numero_nfe


def contador_nfse(db):
    db.expire_all()
    return db.query(Empresa).first().ultimo_numero_nfse


def test_excluir_rascunho_nfe_devolve_numeracao(db_session, test_empresa):
    """Cenario do bug: rascunho 4471 criado e apagado deve devolver 4470 ao config."""
    criar_nfe(db_session, 4470, status="issued")
    rascunho = criar_nfe(db_session, 4471)
    test_empresa.ultimo_numero_nfe = 4471
    db_session.commit()

    excluir_nfe(FakeRequest(), rascunho.id, db_session)

    assert contador_nfe(db_session) == 4470


def test_excluir_rascunho_nfe_avisa_numero_liberado(db_session, test_empresa):
    criar_nfe(db_session, 4470, status="issued")
    rascunho = criar_nfe(db_session, 4471)
    test_empresa.ultimo_numero_nfe = 4471
    db_session.commit()

    request = FakeRequest()
    excluir_nfe(request, rascunho.id, db_session)

    assert contador_nfe(db_session) == 4470
    assert "Numeração liberada" in request.session["message"]


def test_nfe_rascunho_com_nota_posterior_nao_regressa(db_session, test_empresa):
    """Ha rascunho seguinte na sequencia: o contador nao pode voltar, senao colide."""
    criar_nfe(db_session, 4470, status="issued")
    primeiro = criar_nfe(db_session, 4471)
    criar_nfe(db_session, 4472)
    test_empresa.ultimo_numero_nfe = 4472
    db_session.commit()

    excluir_nfe(FakeRequest(), primeiro.id, db_session)

    assert contador_nfe(db_session) == 4472


def test_nfe_rascunho_ja_submetido_nao_devolve(db_session, test_empresa):
    """invoice_id preenchido = nota ja foi para a SEFAZ; o numero nao volta."""
    criar_nfe(db_session, 4470, status="issued")
    rascunho = criar_nfe(db_session, 4471, invoice_id="abc-123")
    test_empresa.ultimo_numero_nfe = 4471
    db_session.commit()

    excluir_nfe(FakeRequest(), rascunho.id, db_session)

    assert contador_nfe(db_session) == 4471


def test_nfe_sem_notas_restantes_devolve_numeracao(db_session, test_empresa):
    """Sem nenhuma nota restante a devolucao segue valendo: restaura o config anterior."""
    rascunho = criar_nfe(db_session, 4471)
    test_empresa.ultimo_numero_nfe = 4471
    db_session.commit()

    excluir_nfe(FakeRequest(), rascunho.id, db_session)

    assert contador_nfe(db_session) == 4470


def test_nfe_com_contador_a_frente_nao_regressa(db_session, test_empresa):
    """Contador adiantado nao e rebaixado pelo uso de um rascunho antigo."""
    criar_nfe(db_session, 4469, status="issued")
    rascunho = criar_nfe(db_session, 4450)
    test_empresa.ultimo_numero_nfe = 4471
    db_session.commit()

    excluir_nfe(FakeRequest(), rascunho.id, db_session)

    assert contador_nfe(db_session) == 4471


def test_nfe_nao_cria_duplicata(db_session, test_empresa):
    """Se o numero ja existe em outra nota, devolver o contador geraria duplicidade."""
    criar_nfe(db_session, 4470, status="issued")
    outra = criar_nfe(db_session, 4471, status="issued")
    rascunho = criar_nfe(db_session, 4471)
    test_empresa.ultimo_numero_nfe = 4471
    db_session.commit()

    excluir_nfe(FakeRequest(), rascunho.id, db_session)

    assert contador_nfe(db_session) == 4471
    assert db_session.query(NFe).filter(NFe.id == outra.id).first() is not None


def test_excluir_rascunho_nfse_devolve_numeracao(db_session, test_empresa):
    criar_nfse(db_session, 239, status="emitida")
    rascunho = criar_nfse(db_session, 240)
    test_empresa.ultimo_numero_nfse = 240
    db_session.commit()

    request = FakeRequest()
    excluir_nfse(request, rascunho.id, db_session)

    assert contador_nfse(db_session) == 239
    assert "Numeração liberada" in request.session["message"]


def test_nfse_ignora_numero_nao_numerico(db_session, test_empresa):
    """numero textual ('Pendente') nao pode estourar o calculo do maior numero."""
    criar_nfse(db_session, 239, status="emitida")
    criar_nfse(db_session, "Pendente")
    rascunho = criar_nfse(db_session, 240)
    test_empresa.ultimo_numero_nfse = 240
    db_session.commit()

    excluir_nfse(FakeRequest(), rascunho.id, db_session)

    assert contador_nfse(db_session) == 239


def test_nfse_com_dps_nao_devolve(db_session, test_empresa):
    """DPS alocada = a prefeitura ja recebeu a nota; o numero nao volta."""
    criar_nfse(db_session, 239, status="emitida")
    rascunho = criar_nfse(db_session, 240, numero_dps=15)
    test_empresa.ultimo_numero_nfse = 240
    db_session.commit()

    excluir_nfse(FakeRequest(), rascunho.id, db_session)

    assert contador_nfse(db_session) == 240


def test_regerar_consolidacao_devolve_sequencia_inteira(db_session, test_empresa):
    """
    Regenerar apaga todos os rascunhos da consolidacao de uma vez. Processando do maior
    numero para o menor (como em routers/nfse.py), o contador volta todos os passos e a
    nota regenerada reaproveita a mesma numeracao.
    """
    criar_nfe(db_session, 4470, status="issued")
    primeiro = criar_nfe(db_session, 4471)
    segundo = criar_nfe(db_session, 4472)
    test_empresa.ultimo_numero_nfe = 4472
    db_session.commit()

    empresa = db_session.query(Empresa).first()
    for nota in sorted([primeiro, segundo], key=lambda n: n.numero, reverse=True):
        reverter_numero_nfe(db_session, empresa, nota)
        db_session.delete(nota)
    db_session.commit()

    assert contador_nfe(db_session) == 4470
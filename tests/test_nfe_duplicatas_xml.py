"""Parcelas (grupo <cobr>/<dup>) da NFe de pedido a prazo.

Contexto
--------
A NFe de pedido/consolidacao e transmitida antes de existirem ContaReceber (elas
so nascem na autorizacao). Por isso o payload era montado sem `duplicatas` e o
XML/DANFE saia sem as parcelas da venda a prazo, mesmo com forma = boleto e
"Gerar Boleto" marcado. `_duplicatas_previstas_nfe` calcula as parcelas em
memoria (espelhando gerar_contas_receber) apenas para o XML.

Tambem trava a normalizacao de FormaPagamento (str-Enum): em Python 3.11+
``str(FormaPagamento.BOLETO)`` devolve ``'FormaPagamento.BOLETO'``, o que
quebrava a deteccao de a vista e a emissao de boleto.
"""
from datetime import date, datetime

from models import FormaPagamento
from services.nfe_notaas import montar_payload_nfe
from services.parcelamento import eh_pagamento_a_vista, forma_pagamento_str
from routers.nfe import _duplicatas_previstas_nfe


class _Origem:
    gerar_cobranca = True
    num_parcelas = 2
    intervalo_dias = 30
    primeiro_vencimento = date(2030, 1, 10)
    data = date(2030, 1, 1)


class _OrigemSemCobranca(_Origem):
    gerar_cobranca = False


class _NFeFake:
    def __init__(self, origem=None, consolidacao_id=None, valor_total=100.0,
                 data_emissao=None):
        self.pedido = origem
        self.consolidacao_id = consolidacao_id
        self.valor_total = valor_total
        self.data_emissao = data_emissao or datetime(2030, 1, 1, 9, 0)


# ------------------------------------------------------------ normalizacao enum

def test_forma_pagamento_str_usa_o_valor_do_enum():
    assert forma_pagamento_str(FormaPagamento.BOLETO) == "boleto"
    assert forma_pagamento_str(FormaPagamento.APRAZO) == "aprazo"
    assert forma_pagamento_str("boleto") == "boleto"
    assert forma_pagamento_str(None) is None


def test_eh_pagamento_a_vista_reconhece_enum():
    """Regressao: o membro do enum AVISTA precisa ser detectado como a vista."""
    assert eh_pagamento_a_vista(FormaPagamento.AVISTA) is True
    assert eh_pagamento_a_vista(FormaPagamento.CARTAO_DEBITO) is True
    assert eh_pagamento_a_vista(FormaPagamento.BOLETO) is False
    assert eh_pagamento_a_vista(FormaPagamento.APRAZO) is False


# ------------------------------------------------------- duplicatas previstas

def test_duplicatas_previstas_boleto_2x():
    nfe = _NFeFake(origem=_Origem(), valor_total=100.0)
    duplicatas = _duplicatas_previstas_nfe(None, nfe, "boleto")

    assert duplicatas is not None and len(duplicatas) == 2
    assert duplicatas[0]["numero"] == "001"
    assert duplicatas[0]["vencimento"] == "2030-01-10"
    assert duplicatas[0]["valor"] == 50.0
    assert duplicatas[1]["vencimento"] == "2030-02-09"
    assert round(sum(d["valor"] for d in duplicatas), 2) == 100.0


def test_duplicatas_previstas_none_para_a_vista():
    nfe = _NFeFake(origem=_Origem(), valor_total=100.0)
    assert _duplicatas_previstas_nfe(None, nfe, "avista") is None
    assert _duplicatas_previstas_nfe(None, nfe, "cartao_credito") is None
    assert _duplicatas_previstas_nfe(None, nfe, "sem_pagamento") is None


def test_duplicatas_previstas_none_com_gerar_cobranca_desligado():
    nfe = _NFeFake(origem=_OrigemSemCobranca(), valor_total=100.0)
    assert _duplicatas_previstas_nfe(None, nfe, "boleto") is None


def test_duplicatas_previstas_none_sem_origem():
    nfe = _NFeFake(origem=None, valor_total=100.0)
    assert _duplicatas_previstas_nfe(None, nfe, "boleto") is None


def test_duplicatas_previstas_none_com_valor_zero():
    nfe = _NFeFake(origem=_Origem(), valor_total=0.0)
    assert _duplicatas_previstas_nfe(None, nfe, "boleto") is None


# --------------------------------------------------------- integracao payload

def _empresa_fake():
    class _E:
        cfop_padrao = "5102"
        estado = "MS"
        serie_nfe = 1
        nfe_aliquota_federal = 0.0
        nfe_aliquota_estadual = 0.0
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


def test_xml_recebe_parcelas_do_pedido_a_prazo():
    """O <cobr>/<dup> deve ir no XML mesmo sem ContaReceber previa."""
    nfe = _NFeFake(origem=_Origem(), valor_total=1790.0)
    duplicatas = _duplicatas_previstas_nfe(None, nfe, "boleto")

    payload = montar_payload_nfe(
        _empresa_fake(), _ClienteFake(), _ITENS,
        numero_nfe=1, forma_pagamento="boleto", duplicatas=duplicatas,
    )

    assert payload["pagamentos"][0]["tipoPagamento"] == "15"
    assert "cobranca" in payload
    assert len(payload["cobranca"]["parcelas"]) == 2
    assert round(sum(p["valor"] for p in payload["cobranca"]["parcelas"]), 2) == 1790.0

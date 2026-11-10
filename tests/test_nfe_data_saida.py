"""dhSaiEnt nunca pode ser anterior a dhEmi (Rejeicao 506 da SEFAZ).

A coluna `nfe.data_saida` era DATE no PostgreSQL: a hora era truncada para
00:00 e o XML saia com `dhSaiEnt` do mesmo dia anterior ao `dhEmi`.
"""
from datetime import datetime

from routers.nfe import _ajustar_data_saida
from services.nfe_notaas import montar_payload_nfe, normalizar_datas_nfe


class _EmpresaFake:
    cfop_padrao = "5102"
    estado = "MS"
    serie_nfe = 1
    nfe_aliquota_federal = 0.0
    nfe_aliquota_estadual = 0.0


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
    "preco_unitario": 100.0,
    "origem": 0,
}]


def test_saida_meia_noite_do_mesmo_dia_assume_horario_da_emissao():
    emissao, saida = normalizar_datas_nfe(
        "2026-11-10T08:07:28", "2026-11-10T00:00:00"
    )

    assert emissao == "2026-11-10T08:07:28"
    assert saida == "2026-11-10T08:07:28"


def test_saida_em_data_posterior_e_preservada():
    _, saida = normalizar_datas_nfe(
        "2026-11-10T08:07:28", "2026-11-11T00:00:00"
    )

    assert saida == "2026-11-11T00:00:00"


def test_sem_saida_nao_altera_nada():
    assert normalizar_datas_nfe("2026-11-10T08:07:28", None) == (
        "2026-11-10T08:07:28", None
    )
    assert normalizar_datas_nfe(None, None) == (None, None)


def test_normaliza_datetimes():
    emissao = datetime(2026, 11, 10, 8, 7, 28)
    saida = datetime(2026, 11, 10, 0, 0)
    _, saida_norm = normalizar_datas_nfe(emissao, saida)
    assert saida_norm == emissao


def test_payload_nao_leva_dhsaient_anterior_ao_dhEmi():
    payload = montar_payload_nfe(
        _EmpresaFake(), _ClienteFake(), _ITENS,
        numero_nfe=4493,
        data_emissao="2026-11-10T08:07:28",
        data_saida="2026-11-10T00:00:00",
    )

    assert payload["dataEmissao"] == "2026-11-10T08:07:28"
    assert payload["dataSaida"] == "2026-11-10T08:07:28"


def test_payload_sem_saida_nao_envia_dhsaient():
    payload = montar_payload_nfe(
        _EmpresaFake(), _ClienteFake(), _ITENS,
        numero_nfe=4493,
        data_emissao="2026-11-10T08:07:28",
        data_saida=None,
    )

    assert "dataSaida" not in payload


def test_ajustar_data_saida_corrige_meia_noite_e_nao_atrasa_emissao():
    emissao = datetime(2026, 11, 10, 8, 7, 28)
    assert _ajustar_data_saida(emissao, datetime(2026, 11, 10, 0, 0)) == emissao
    posterior = datetime(2026, 11, 11, 9, 0)
    assert _ajustar_data_saida(emissao, posterior) == posterior
    assert _ajustar_data_saida(emissao, None) is None

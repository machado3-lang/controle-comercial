"""Testes das mudanças do ADN (cnpjConsulta) e da importação de NFSe recebida."""

from services.nfse_betha import parametros_consulta_adn, mensagem_rejeicao_adn
from routers.nfse import _extrair_dados_nfse_recebida

CHAVE = "12345678901234567890123456789012345678901234567890"

XML_NACIONAL = f"""<?xml version="1.0" encoding="UTF-8"?>
<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.00">
  <infNFSe Id="NFS{CHAVE}">
    <nNFSe>987</nNFSe>
    <emit><CNPJ>63554067000198</CNPJ><xNome>PRESTADOR TESTE LTDA</xNome></emit>
    <valores><vLiq>150.50</vLiq></valores>
    <DPS><infDPS>
      <dhEmi>2026-11-05T10:20:30-03:00</dhEmi>
      <toma><CNPJ>13133714000110</CNPJ><xNome>EMPRESA TESTE</xNome></toma>
      <valores><vServPrest><vServ>150.50</vServ></vServPrest></valores>
    </infDPS></DPS>
  </infNFSe>
</NFSe>
"""


def test_parametros_consulta_adn_com_cnpj_formatado():
    params = parametros_consulta_adn("13.133.714/0001-10")
    assert params == {"lote": "true", "cnpjConsulta": "13133714000110"}


def test_parametros_consulta_adn_com_cnpj_alfanumerico():
    # IN RFB 2.229/2024: 12 posições alfanuméricas + 2 dígitos verificadores.
    params = parametros_consulta_adn("12.ABC.345/01DE-35")
    assert params == {"lote": "true", "cnpjConsulta": "12ABC34501DE35"}


def test_parametros_consulta_adn_com_cpf():
    params = parametros_consulta_adn("123.456.789-01")
    assert params == {"lote": "true", "cpfConsulta": "12345678901"}


def test_parametros_consulta_adn_sem_inscricao():
    assert parametros_consulta_adn("") == {"lote": "true"}


def test_mensagem_rejeicao_adn_com_erros():
    data = {"StatusProcessamento": "REJEICAO",
            "Erros": [{"Codigo": "E2245", "Descricao": "Não há procuração."}]}
    assert mensagem_rejeicao_adn(data) == "E2245: Não há procuração."


def test_mensagem_rejeicao_adn_sem_erros_da_dica():
    data = {"StatusProcessamento": "REJEICAO", "Erros": [],
            "TipoConsulta": "InscricaoCertificado"}
    msg = mensagem_rejeicao_adn(data)
    assert "REJEICAO" in msg and "procuração" in msg


def test_extrai_dados_da_nfse_nacional():
    dados = _extrair_dados_nfse_recebida(XML_NACIONAL)
    assert dados["chave_acesso"] == CHAVE
    assert dados["numero"] == "987"
    assert dados["emitente_cnpj"] == "63554067000198"
    assert dados["emitente_nome"] == "PRESTADOR TESTE LTDA"
    assert dados["tomador_cnpj"] == "13133714000110"
    assert str(dados["valor_total"]) == "150.50"
    assert dados["data_emissao"].strftime("%Y-%m-%d") == "2026-11-05"
    assert dados["cancelada"] is False


def test_xml_de_evento_nao_e_importado_como_nfse():
    evento = (
        '<evento xmlns="http://www.sped.fazenda.gov.br/nfse">'
        '<infEvento><chNFSe>' + CHAVE + '</chNFSe>'
        '<tpEvento>101101</tpEvento></infEvento></evento>'
    )
    assert _extrair_dados_nfse_recebida(evento) == {}

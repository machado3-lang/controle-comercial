"""
NFSe Service - Emissão Nota Fiscal de Serviço (DPS Nacional)
Padrão Nacional + Betha Fly Notas para Dourados-MS
"""
from lxml import etree
from typing import Dict, Optional, Any
import os


# Namespaces DPS Nacional
NS_DPS = {
    'ds': 'http://www.w3.org/2000/09/xmldsig#',
    'tipos': 'http://www.ginfes.com.br/tipos',
    'nfse': 'http://www.abrasf.org.br/nfcd/v100',
    'dps': 'http://www.gov.br/nfe/v1.0/dps'
}

# XPath para namespace padrão
NSMAP = {
    None: 'http://www.gov.br/nfe/v1.0/dps',
    'ds': 'http://www.w3.org/2000/09/xmldsig#',
    'tipos': 'http://www.ginfes.com.br/tipos',
    'nfse': 'http://www.abrasf.org.br/nfcd/v100'
}


def montar_dps(pedido: Dict, empresa: Dict, cliente: Dict) -> Dict[str, Any]:
    """
    Monta estrutura básica da DPS (Declaração Prestação Serviço)
    
    Campos obrigatórios:
    - Prestador: CNPJ, Inscrição Municipal
    - Tomador: CNPJ/CPF, nome, endereço
    - Serviço: LC116/03, tributação municipal, valor, alíquota
    """
    dps = {
        'versao': '1.00',
        'prestador': {
            'cnpj': empresa.get('cnpj', '').replace('.', '').replace('/', '').replace('-', ''),
            'inscricao_municipal': empresa.get('inscricao_municipal', ''),
            'codigo_municipio': os.getenv('MUNICIPIO_CODIGO', '5003402'),  # Dourados-MS
        },
        'tomador': {
            'cnpj': cliente.get('cnpj', '').replace('.', '').replace('/', '').replace('-', '') if len(cliente.get('cnpj', '')) > 11 else None,
            'cpf': cliente.get('cpf_cnpj', '').replace('.', '').replace('-', '') if len(cliente.get('cpf_cnpj', '')) <= 11 else None,
            'nome': cliente.get('nome', ''),
            'inscricao_municipal': cliente.get('inscricao_municipal', ''),
            'telefone': cliente.get('telefone', ''),
            'email': cliente.get('email', ''),
            'endereco': {
                'logradouro': cliente.get('endereco', ''),
                'numero': '',
                'complemento': '',
                'bairro': cliente.get('bairro', ''),
                'codigo_municipio': '',
                'uf': cliente.get('estado', ''),
                'cep': cliente.get('cep', ''),
            }
        },
        'servico': {
            'lc116': '1101',  # Análise ou desenvolvimento de sistemas de informática
            'tributacao_municipal': '51',  # Exemplo: Tributação fora do município (consultar tabela de Dourados)
            'descricao': pedido.get('descricao', 'Serviço de desenvolvimento de software'),
            'valor_servicos': float(pedido.get('valor', 0)),
            'valor_deducoes': 0,
            'valor_pissu': 0,  # PIS
            'valor_cofins': 0,
            'valor_inss': 0,
            'valor_ir': 0,
            'valor_csll': 0,
            'aliquota_simples': 0,  # Ver na empresa
            'aliquota': 0,  # Alíquota do ISS
        }
    }
    return dps


def validar_xml_against_xsd(xml_string: str, xsd_path: str) -> bool:
    """
    Valida XML usando XSD (schema) fornecido
    
    Args:
        xml_string: XML em formato string
        xsd_path: caminho para arquivo .xsd de validação
        
    Returns:
        True se válido, lança exceção se inválido
    """
    # Carrega XSD
    if not os.path.exists(xsd_path):
        raise FileNotFoundError(f"XSD não encontrado: {xsd_path}")
    
    with open(xsd_path, 'rb') as f:
        xsd_doc = etree.XML(f.read())
    
    schema = etree.XMLSchema(etree.parse(xsd_path))
    parser = etree.XMLParser(schema=schema)
    
    try:
        xml_doc = etree.fromstring(xml_string.encode('utf-8'), parser)
        return True
    except etree.DocumentInvalid as e:
        raise ValueError(f"XML inválido contra XSD: {e}")


def gerar_xml_dps(dps: Dict, assinatura: Optional[str] = None) -> str:
    """
    Gera XML DPS a partir do dicionário
    
    Args:
        dps: estrutura montada por montar_dps()
        assinatura: XML Signature (opcional, adicionado após assinatura)
    """
    root = etree.Element('DPS', nsmap=NSMAP)
    
    # infDPS - Informações da DPS
    inf_dps = etree.SubElement(root, 'infDPS')
    inf_dps.set('{http://www.w3.org/2000/xmlns/xmlns}Id', 'DPS1')
    
    # Prestador
    prest = etree.SubElement(inf_dps, 'prest')
    if dps['prestador']['cnpj']:
        etree.SubElement(prest, 'CNPJ').text = dps['prestador']['cnpj']
    if dps['prestador']['inscricao_municipal']:
        etree.SubElement(prest, 'IM').text = dps['prestador']['inscricao_municipal']
    
    # Tomador
    tom = etree.SubElement(inf_dps, 'tom')
    if dps['tomador']['cnpj']:
        etree.SubElement(tom, 'CNPJ').text = dps['tomador']['cnpj']
    elif dps['tomador']['cpf']:
        etree.SubElement(tom, 'CPF').text = dps['tomador']['cpf']
    
    etree.SubElement(tom, 'xNome').text = dps['tomador']['nome']
    
    end = etree.SubElement(tom, 'end')
    etree.SubElement(end, 'xLog').text = dps['tomador']['endereco']['logradouro']
    etree.SubElement(end, 'nro').text = dps['tomador']['endereco']['numero']
    etree.SubElement(end, 'xBairro').text = dps['tomador']['endereco']['bairro']
    
    # Serviço
    serv = etree.SubElement(inf_dps, 'serv')
    etree.SubElement(serv, 'xLCServ').text = dps['servico']['lc116']  # Código LC 116/03
    etree.SubElement(serv, 'xClaTrib').text = dps['servico']['tributacao_municipal']  # Tributação municipal
    etree.SubElement(serv, 'xServ').text = dps['servico']['descricao']
    etree.SubElement(serv, 'vServPrest').text = f"{dps['servico']['valor_servicos']:.2f}"
    
    # Adicionar assinatura se fornecida
    if assinatura:
        sig_root = etree.fromstring(assinatura)
        root.append(sig_root)
    
    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding='UTF-8', standalone=False).decode('utf-8')


# Exemplo de uso (remover em produção)
if __name__ == '__main__':
    # Dados de teste
    dps_test = montar_dps(
        pedido={'descricao': 'Desenvolvimento de software', 'valor': 1000.00},
        empresa={'cnpj': '00.000.000/0001-91', 'inscricao_municipal': '000.000-0'},
        cliente={'nome': 'Cliente Teste Ltda', 'cnpj': '00.000.000/0001-91'}
    )
    xml = gerar_xml_dps(dps_test)
    print("XML gerado:")
    print(xml[:500])  # primeiro 500 chars
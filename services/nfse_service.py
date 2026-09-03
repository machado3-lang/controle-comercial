"""
NFSe Service - Emissão Nota Fiscal de Serviço (DPS Nacional)
Padrão Nacional + Betha Fly Notas para Dourados-MS
"""
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
    from lxml import etree
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
    from lxml import etree
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


def extrair_fatura_nfse(xml: str) -> list:
    """Extrai fatura/duplicatas e respectivos vencimentos do XML da NFSe recebida.

    O layout da NFSe varia bastante entre municípios (ABRASF, Ginfes, Betha,
    Padrão Nacional, etc.) e nem sempre traz vencimento. Esta função faz uma
    varredura tolerante (namespace-agnóstica) em busca de blocos de fatura/
    duplicata/parcela e das tags de vencimento/valor, retornando o que encontrar.

    Retorna lista de dicts: {"numero": str, "vencimento": date|None,
    "valor": Decimal|None}. Lista vazia = sem informação de vencimento no XML
    (o usuário informará manualmente no popup).
    """
    from datetime import datetime as _dt
    from decimal import Decimal, InvalidOperation
    import re
    import xml.etree.ElementTree as ET

    if not xml:
        return []

    try:
        root = ET.fromstring(xml)
    except Exception:
        return []

    def local(tag: str) -> str:
        return tag.split('}')[-1].lower()

    def parse_data(s):
        s = (s or '').strip()[:10]
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
            try:
                return _dt.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def parse_valor(s):
        if not s or not s.strip():
            return None
        raw = s.strip()
        raw = raw.replace(".", "").replace(",", ".") if "," in raw else raw
        try:
            return Decimal(raw)
        except (InvalidOperation, ValueError):
            return None

    def coletar(elem) -> dict:
        campos = {}
        for e in elem.iter():
            txt = (e.text or '').strip()
            if not txt:
                continue
            campos.setdefault(local(e.tag), txt)
        return campos

    parcelas = []
    blocos_tags = ("fat", "fatura", "dup", "duplicata", "parcela", "cobr")
    for elem in root.iter():
        if local(elem.tag) in blocos_tags:
            c = coletar(elem)
            # numero: chave com num/fat/dup/parcela
            numero = None
            for k in c:
                if re.search(r'num|fat|dup|parcela', k) and 'venc' not in k:
                    numero = c[k]
                    break
            # vencimento: chave contendo venc
            venc_raw = next((c[k] for k in c if 'venc' in k), None)
            # valor: chave contendo val (mas não venc), ou padrões vDup/vFat/vParcela
            val_raw = next((
                c[k] for k in c
                if (('val' in k) or (k[:2] in ('vd', 'vf', 'vp') and 'venc' not in k))
                and 'venc' not in k
            ), None)
            parcelas.append({
                "numero": (numero or str(len(parcelas) + 1)).strip(),
                "vencimento": parse_data(venc_raw),
                "valor": parse_valor(val_raw),
            })

    # Fallback: sem blocos, mas há uma tag de vencimento em nível de documento
    if not parcelas:
        for elem in root.iter():
            if 'venc' in local(elem.tag):
                txt = (elem.text or '').strip()
                if txt:
                    parcelas.append({
                        "numero": "1",
                        "vencimento": parse_data(txt),
                        "valor": None,
                    })
                    break

    # Ordena por vencimento (quando houver) e remove duplicatas idênticas
    parcelas.sort(key=lambda d: (d["vencimento"] or _dt.max.date(), d["numero"]))
    unicas = []
    vistos = set()
    for p in parcelas:
        chave = (p["numero"], str(p["vencimento"]), str(p["valor"]))
        if chave in vistos:
            continue
        vistos.add(chave)
        unicas.append(p)
    return unicas


def limpar_mensagem_erro(texto: str, limite: int = 400) -> str:
    """
    Remove tags XML/HTML, entidades e espaços excessivos de uma mensagem de erro
    crua retornada pela prefeitura/SEFIN/Betha, deixando apenas o texto legível.
    Trunca mensagens muito longas (o 'erro grande' que assusta o usuário).
    """
    import re
    import html

    t = texto or ""
    # Remove blocos de CDATA e tags XML/HTML
    t = re.sub(r"<!\[CDATA\[.*?\]\]>", " ", t, flags=re.DOTALL)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        t = "Erro não especificado retornado pelo webservice."
    if len(t) > limite:
        t = t[:limite].rstrip() + "..."
    return t


# Padrões que indicam erros de REGRA DE NEGÓCIO / VALIDAÇÃO (devem virar "aviso",
# não "erro" assustador): prazo de cancelamento, CEP, IBGE, campos obrigatórios etc.
_PADROES_AVISO = (
    "prazo", "vencido", "vencida", "fora do prazo", "não pode ser cancelada",
    "nao pode ser cancelada", "cancelamento", "cancelar", "cep", "ibge",
    "codigo do municipio", "código do município", "codigomunicipio",
    "municipio", "município", "rejeitad", "não autorizado", "nao autorizado",
    "inconsist", "inválido", "inválida", "invalido", "invalida",
    "obrigatório", "obrigatoria", "obrigatório", "obrigatória",
    "preencha", "não informado", "nao informado", "inexistente",
)


def classificar_erro_nfse(texto: str) -> str:
    """
    Classifica uma mensagem de erro da NFSe.

    Retorna:
        'warning' -> erro de validação/regra de negócio (ex.: fora do prazo de
                     cancelamento, CEP inválido, falta do código IBGE). Deve ser
                     exibido como um AVISO amigável, não como erro fatal.
        'danger'  -> erro de sistema/comunicação (ex.: falha de conexão, certificado,
                     timeout, resposta não reconhecida).
    """
    t = (texto or "").lower()
    for padrao in _PADROES_AVISO:
        if padrao in t:
            return "warning"
    return "danger"


def formatar_aviso_nfse(texto: str, acao: str = "realizar a operação") -> dict:
    """
    Gera um dicionário de mensagem {'tipo': 'warning'|'danger', 'texto': ...}
    limpo e amigável a partir de uma mensagem crua de erro da NFSe.
    """
    limpo = limpar_mensagem_erro(texto)
    tipo = classificar_erro_nfse(limpo)
    if tipo == "warning":
        texto_aviso = f"Não foi possível {acao}. {limpo}"
    else:
        texto_aviso = f"Erro ao {acao}: {limpo}"
    return {"tipo": tipo, "texto": texto_aviso}


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
"""
NFSe PDF Generator - Gera espelho da NFS-e a partir do XML autorizado
Extrai dados e gera PDF com WeasyPrint
"""
import os
from datetime import datetime


# Template HTML NFS-e modera e estilizado
NFSe_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
    @page { margin: 1.5cm; size: A4; }
    body { font-family: 'Helvetica', sans-serif; font-size: 9pt; color: #333; }
    .header { border-bottom: 2px solid #005CA9; padding-bottom: 10px; margin-bottom: 15px; }
    .header h1 { color: #005CA9; font-size: 16pt; margin: 0; }
    .header .nota-info { float: right; text-align: right; }
    .quadro { border: 1px solid #ccc; border-radius: 5px; padding: 12px; margin-bottom: 12px; }
    .quadro-titulo { background: #005CA9; color: white; padding: 6px 10px; margin: -12px -12px 8px -12px; font-weight: bold; }
    .row { display: flex; flex-wrap: wrap; }
    .col { flex: 1; min-width: 200px; margin-right: 10px; }
    .col:last-child { margin-right: 0; }
    .field { margin-bottom: 6px; }
    .field-label { font-weight: bold; color: #555; font-size: 8pt; }
    .field-value { color: #000; }
    .total-box { background: #f0f8ff; border: 2px solid #005CA9; border-radius: 8px; padding: 15px; text-align: center; margin-top: 15px; }
    .total-label { font-size: 12pt; color: #005CA9; }
    .total-value { font-size: 18pt; font-weight: bold; color: #005CA9; }
    .simples { text-align: center; color: #666; font-style: italic; margin-top: 10px; }
</style>
</head>
<body>
    <div class="header">
        <div style="float: left;"><strong>NOTA FISCAL DE SERVIÇO ELETRÔNICA</strong><br>
        <small>{{ municipio }} - MS</small></div>
        <div class="nota-info">
            <div>Nº NFS-e: <strong>{{ numero }}</strong></div>
            <div>Chave Acesso: <small>{{ chave }}</small></div>
        </div>
        <div style="clear: both;"></div>
    </div>

    <div class="quadro">
        <div class="quadro-titulo">PRESTADOR DO SERVIÇO</div>
        <div class="row">
            <div class="col">
                <div class="field"><span class="field-label">Razão Social:</span> <span class="field-value">{{ prestador.razao_social }}</span></div>
                <div class="field"><span class="field-label">CNPJ:</span> <span class="field-value">{{ prestador.cnpj }}</span></div>
            </div>
            <div class="col">
                <div class="field"><span class="field-label">Inscrição Municipal:</span> <span class="field-value">{{ prestador.inscricao_municipal }}</span></div>
                <div class="field"><span class="field-label">Endereço:</span> <span class="field-value">{{ prestador.endereco }}</span></div>
            </div>
        </div>
    </div>

    <div class="quadro">
        <div class="quadro-titulo">TOMADOR DO SERVIÇO</div>
        <div class="row">
            <div class="col">
                <div class="field"><span class="field-label">Razão Social:</span> <span class="field-value">{{ tomador.razao_social }}</span></div>
                <div class="field"><span class="field-label">CNPJ/CPF:</span> <span class="field-value">{{ tomador.cnpj_cpf }}</span></div>
            </div>
            <div class="col">
                <div class="field"><span class="field-label">Endereço:</span> <span class="field-value">{{ tomador.endereco }}</span></div>
                <div class="field"><span class="field-label">Município/UF:</span> <span class="field-value">{{ tomador.municipio }}/{{ tomador.uf }}</span></div>
            </div>
        </div>
    </div>

    <div class="quadro">
        <div class="quadro-titulo">DISCRIMINAÇÃO DOS SERVIÇOS</div>
        <div>{{ servicos.descricao }}</div>
        <table style="width: 100%; margin-top: 10px;">
            <tr style="background: #e6f2ff;">
                <th style="text-align: left; padding: 5px;">Código LC 116/03</th>
                <th style="text-align: right; padding: 5px;">Valor</th>
            </tr>
            {% for item in servicos.itens %}
            <tr>
                <td style="padding: 5px; border-bottom: 1px solid #ddd;">{{ item.codigo }} - {{ item.descricao }}</td>
                <td style="padding: 5px; border-bottom: 1px solid #ddd; text-align: right;">R$ {{ item.valor }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>

    <div class="total-box">
        <div class="total-label">VALOR TOTAL DA NOTA</div>
        <div class="total-value">R$ {{ valor_total }}</div>
    </div>

    <div class="simples">EMPRESA OPTANTE PELO SIMPLES NACIONAL<br>
    Código de Autenticação: {{ codigo_autenticacao }}
    </div>
</body>
</html>
"""


def extract_nfse_data(xml_string: str) -> dict:
    from lxml import etree
    """
    Extrai dados essenciais do XML de NFS-e autorizada
    Tags conforme padrão Nacional/Betha
    """
    root = etree.fromstring(xml_string.encode('utf-8'))
    
    # Remove namespace para simplificar busca
    namespaces = {'ns': 'http://www.gov.br/nfe/v1.0/nfse'}
    
    # Dados do cabeçalho
    numero = root.find('.//{*}Numero')
    chave = root.find('.//{*}ChaveAcesso')
    codigo_autenticacao = root.find('.//{*}CodigoAutenticacao')
    
    # Dados do prestador
    prestador = root.find('.//{*}Prestador')
    prestador_razao = root.find('.//{*}RazaoSocialPrestador') or root.find('.//{*}xNomePrestador')
    prestador_cnpj = root.find('.//{*}CNPJPrestador')
    prestador_im = root.find('.//{*}IMPrestador')
    
    # Dados do tomador
    tomador_razao = root.find('.//{*}RazaoSocialTomador') or root.find('.//{*}xNomeTomador')
    tomador_cnpj = root.find('.//{*}CNPJTomador') or root.find('.//{*}CPFTomador')
    tomador_end = root.find('.//{*}EnderecoTomador') or root.find('.//{*}xLogTomador')
    tomador_mun = root.find('.//{*}MunicipioTomador')
    tomador_uf = root.find('.//{*}UFTomador')
    
    # Serviços
    servicos_desc = root.find('.//{*}DiscriminacaoServicos')
    valor_total = root.find('.//{*}ValorTotalServicos')
    
    return {
        'numero': numero.text if numero is not None else '',
        'chave': chave.text if chave is not None else '',
        'codigo_autenticacao': codigo_autenticacao.text if codigo_autenticacao is not None else '',
        'prestador': {
            'razao_social': prestador_razao.text if prestador_razao is not None else '',
            'cnpj': format_cnpj(prestador_cnpj.text if prestador_cnpj is not None else ''),
            'inscricao_municipal': prestador_im.text if prestador_im is not None else '',
            'endereco': 'Rua Exemplo, 100 - Dourados/MS'
        },
        'tomador': {
            'razao_social': tomador_razao.text if tomador_razao is not None else '',
            'cnpj_cpf': format_cnpj(tomador_cnpj.text if tomador_cnpj is not None else ''),
            'endereco': tomador_end.text if tomador_end is not None else '',
            'municipio': tomador_mun.text if tomador_mun is not None else 'Dourados',
            'uf': tomador_uf.text if tomador_uf is not None else 'MS'
        },
        'servicos': {
            'descricao': servicos_desc.text if servicos_desc is not None else '',
            'itens': [{'codigo': '1101', 'descricao': 'Desenvolvimento de software', 'valor': valor_total.text if valor_total is not None else '0,00'}]
        },
        'valor_total': format_money(valor_total.text if valor_total is not None else '0,00')
    }


def format_cnpj(cnpj: str) -> str:
    """Formata CNPJ para exibição"""
    if not cnpj or len(cnpj) != 14:
        return cnpj or ''
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def format_money(value: str) -> str:
    """Formata valor monetário"""
    if not value:
        return '0,00'
    try:
        # Se vem como 1000.00, converte para 1.000,00
        num = float(value.replace(',', '.'))
        return f"{num:,.2f}".replace('.', '#').replace(',', '.').replace('#', ',')
    except:
        return value


def generate_pdf(xml_string: str, output_path: str) -> str:
    """
    Gera PDF da NFS-e a partir do XML autorizada
    
    Args:
        xml_string: XML da NFS-e autorizada
        output_path: caminho completo para salvar PDF
        
    Returns:
        caminho do arquivo PDF gerado
    """
    # Extrai dados
    data = extract_nfse_data(xml_string)
    
    # Renderiza HTML
    html_content = render_template(NFSe_TEMPLATE, data)
    
    # Gera PDF
    html = HTML(string=html_content)
    html.write_pdf(target=output_path)
    
    return output_path


def render_template(template: str, data: dict) -> str:
    """
    Renderiza template simples substituindo placeholders
    """
    result = template
    
    # Substituição simples de variáveis
    result = result.replace('{{ numero }}', data.get('numero', ''))
    result = result.replace('{{ chave }}', format_cnpj(data['chave']) if data.get('chave') else '')
    result = result.replace('{{ codigo_autenticacao }}', data.get('codigo_autenticacao', ''))
    result = result.replace('{{ valor_total }}', data.get('valor_total', '0,00'))
    result = result.replace('{{ municipio }}', 'Dourados')
    
    # Prestador
    p = data.get('prestador', {})
    result = result.replace('{{ prestador.razao_social }}', p.get('razao_social', ''))
    result = result.replace('{{ prestador.cnpj }}', p.get('cnpj', ''))
    result = result.replace('{{ prestador.inscricao_municipal }}', p.get('inscricao_municipal', ''))
    result = result.replace('{{ prestador.endereco }}', p.get('endereco', ''))
    
    # Tomador
    t = data.get('tomador', {})
    result = result.replace('{{ tomador.razao_social }}', t.get('razao_social', ''))
    result = result.replace('{{ tomador.cnpj_cpf }}', t.get('cnpj_cpf', ''))
    result = result.replace('{{ tomador.endereco }}', t.get('endereco', ''))
    result = result.replace('{{ tomador.municipio }}', t.get('municipio', ''))
    result = result.replace('{{ tomador.uf }}', t.get('uf', ''))
    
    # Serviços
    s = data.get('servicos', {})
    result = result.replace('{{ servicos.descricao }}', s.get('descricao', ''))
    
    # Itens - substituição simples
    itens_html = ''
    for item in s.get('itens', []):
        itens_html += f'<tr><td style="padding: 5px; border-bottom: 1px solid #ddd;">{item.get("codigo", "")} - {item.get("descricao", "")}</td><td style="padding: 5px; border-bottom: 1px solid #ddd; text-align: right;">R$ {item.get("valor", "")}</td></tr>'
    result = result.replace('{% for item in servicos.itens %}{{ item.codigo }} - {{ item.descricao }}</td><td style="padding: 5px; border-bottom: 1px solid #ddd; text-align: right;">R$ {{ item.valor }}</td></tr>{% endfor %}', itens_html)
    
    return result


# Teste básico
if __name__ == '__main__':
    # XML de teste (substitua com XML real da Betha)
    xml_test = '<?xml version="1.0"?><NFSe><Numero>12345</Numero><ChaveAcesso>00000000000000000000000000000000000000000000</ChaveAcesso><CodigoAutenticacao>ABC123</CodigoAutenticacao><DiscriminacaoServicos>Desenvolvimento de software</DiscriminacaoServicos><ValorTotalServicos>1000.00</ValorTotalServicos></NFSe>'
    
    print("NFSe PDF Generator carregado")
    data = extract_nfse_data(xml_test)
    print(f"Dados extraídos: {data['numero']}")
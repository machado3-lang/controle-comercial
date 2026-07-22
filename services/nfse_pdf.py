import os
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from fpdf import FPDF
from app.core.config import settings

UPLOAD_DIR = os.path.join(settings.UPLOAD_DIR, "nfse")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Namespace da NFS-e Nacional (Sefin Nacional / Portal ADN) esperado pelo
# leiaute padronizado do DANFSe (NT 008/2026).
NFSE_NACIONAL_NS = "http://www.sped.fazenda.gov.br/nfse"


def is_xml_nfse_nacional(xml_string: str) -> bool:
    """True se o XML e a NFS-e Nacional completa (grupos infNFSe + DPS).

    O DANFSe padronizado (brazilfiscalreport) exige o XML autorizado com
    NFSe/infNFSe e NFSe/infNFSe/DPS. A DPS enviada a prefeituras proprietarias
    (ex.: Betha) nao contem o infNFSe e usa outro namespace.
    """
    if not xml_string:
        return False
    try:
        root = ET.fromstring(xml_string)
    except Exception:
        return False
    return (
        root.find(f".//{{{NFSE_NACIONAL_NS}}}infNFSe") is not None
        and root.find(f".//{{{NFSE_NACIONAL_NS}}}DPS") is not None
    )


def gerar_danfse_pdf(xml_string: str, output_path: str, cancelada: bool = False) -> str:
    """Gera o DANFSe padronizado (NFS-e Nacional 2.0) a partir do XML completo.

    Usa o brazilfiscalreport (mesmo pacote do DANFE da NFe). Exige o XML
    autorizado no formato nacional; para notas canceladas, aplica a marca
    d'agua "CANCELADA".
    """
    from brazilfiscalreport.danfse import Danfse, DanfseConfig

    config = DanfseConfig(watermark_cancelled=bool(cancelada))
    danfse = Danfse(xml=xml_string, config=config)
    danfse.output(output_path)
    return output_path


def gerar_pdf_nfse(nfse, empresa, cliente, itens, status_labels) -> str:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)

    empresa_nome = (empresa.razao_social or empresa.nome_fantasia or "Empresa") if empresa else "Empresa"
    empresa_cnpj = empresa.cnpj or "" if empresa else ""
    empresa_end = empresa.endereco or "" if empresa else ""
    empresa_bairro = empresa.bairro or "" if empresa else ""
    empresa_cidade = empresa.cidade or "" if empresa else ""
    empresa_estado = empresa.estado or "" if empresa else ""
    empresa_cep = empresa.cep or "" if empresa else ""
    empresa_im = empresa.inscricao_municipal or "" if empresa else ""

    cliente_nome = cliente.nome if cliente else "-"
    cliente_cpf = cliente.cpf_cnpj if cliente else "-"
    cliente_end = cliente.endereco or "" if cliente else ""
    cliente_bairro = cliente.bairro or "" if cliente else ""
    cliente_cidade = cliente.cidade or "" if cliente else ""
    cliente_estado = cliente.estado or "" if cliente else ""
    cliente_cep = cliente.cep or "" if cliente else ""
    cliente_email = cliente.email or "" if cliente else ""

    status_label = status_labels.get(nfse.status, nfse.status) if status_labels else nfse.status

    pdf.add_page()

    # Cabeçalho
    pdf.set_fill_color(14, 165, 233)
    pdf.rect(0, 0, 210, 3, "F")
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(14, 165, 233)
    pdf.cell(0, 8, empresa_nome, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    if empresa_cnpj:
        pdf.cell(0, 4, f"CNPJ: {empresa_cnpj}", new_x="LMARGIN", new_y="NEXT")
    endereco_linha = ", ".join(filter(None, [empresa_end, empresa_bairro]))
    if endereco_linha:
        pdf.cell(0, 4, endereco_linha, new_x="LMARGIN", new_y="NEXT")
    cidade_linha = ", ".join(filter(None, [empresa_cidade, empresa_estado]))
    if cidade_linha:
        cidade_linha += f" - CEP: {empresa_cep}" if empresa_cep else ""
        pdf.cell(0, 4, cidade_linha, new_x="LMARGIN", new_y="NEXT")
    if empresa_im:
        pdf.cell(0, 4, f"Inscricao Municipal: {empresa_im}", new_x="LMARGIN", new_y="NEXT")

    # Número NFSe (lado direito)
    pdf.set_y(10)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(14, 165, 233)
    pdf.cell(0, 5, f"NFSe {nfse.numero or 'Pendente'}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(80, 80, 80)
    if nfse.data_emissao:
        pdf.cell(0, 4, f"Data: {nfse.data_emissao.strftime('%d/%m/%Y %H:%M')}", align="R", new_x="LMARGIN", new_y="NEXT")
    if nfse.codigo_verificacao:
        pdf.cell(0, 4, f"Codigo: {nfse.codigo_verificacao}", align="R", new_x="LMARGIN", new_y="NEXT")

    # Status
    cor_status = {"rascunho": (180, 140, 0), "pendente": (180, 140, 0),
                  "autorizada": (0, 150, 50), "erro": (180, 30, 30), "cancelada": (100, 100, 100)}
    sc = cor_status.get(nfse.status, (100, 100, 100))
    pdf.set_text_color(*sc)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 4, status_label.upper(), align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    # Linha separadora
    pdf.set_draw_color(14, 165, 233)
    pdf.set_line_width(0.5)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    # Título
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(14, 165, 233)
    pdf.cell(0, 8, "NOTA FISCAL DE SERVICO ELETRONICA", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 5, f"NFS-e {nfse.numero or ''}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # Tomador
    y_box = pdf.get_y()
    pdf.set_fill_color(245, 247, 250)
    pdf.set_draw_color(220, 220, 220)
    pdf.rect(10, y_box, 190, 30, "DF")
    pdf.set_xy(12, y_box + 2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(14, 165, 233)
    pdf.cell(0, 5, "TOMADOR / CLIENTE", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(12)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(40, 40, 40)
    pdf.cell(90, 5, f"Nome: {cliente_nome}")
    pdf.cell(0, 5, f"CPF/CNPJ: {cliente_cpf}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(12)
    endereco_completo = ", ".join(filter(None, [cliente_end, cliente_bairro]))
    pdf.cell(90, 5, f"Endereco: {endereco_completo}")
    cidade_uf = ", ".join(filter(None, [cliente_cidade, cliente_estado]))
    pdf.cell(0, 5, f"Cidade/UF: {cidade_uf}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(12)
    if cliente_cep:
        pdf.cell(90, 5, f"CEP: {cliente_cep}")
    if cliente_email:
        pdf.cell(0, 5, f"Email: {cliente_email}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(y_box + 33)

    # Informações da nota
    y_box = pdf.get_y()
    pdf.set_fill_color(245, 247, 250)
    pdf.rect(10, y_box, 190, 15, "DF")
    pdf.set_xy(12, y_box + 2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(14, 165, 233)
    pdf.cell(0, 5, "INFORMACOES DA NOTA", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(12)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(40, 40, 40)
    if nfse.natureza_operacao:
        pdf.cell(90, 5, f"Natureza: {nfse.natureza_operacao}")
    if nfse.municipio_nome:
        pdf.cell(0, 5, f"Municipio: {nfse.municipio_nome} ({nfse.municipio_codigo or ''})", new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(y_box + 18)

    # Tabela de itens
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(14, 165, 233)
    pdf.set_text_color(255, 255, 255)
    col_w = [80, 20, 25, 30, 35]
    headers = ["Descricao", "LC 116", "Qtd", "Vl. Unit.", "Total"]
    for i, h in enumerate(headers):
        x = 10 + sum(col_w[:i])
        pdf.set_xy(x, pdf.get_y())
        align = "L" if i < 2 else "C" if i == 2 else "R"
        pdf.cell(col_w[i], 7, h, border=0, align=align, fill=True)
    pdf.ln(7)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(40, 40, 40)
    for item in itens:
        desc = (item.descricao or "")[:60]
        cod = item.codigo_servico or "-"
        qtd = str(item.quantidade or 1)
        vlr = f"R$ {item.valor_unitario or 0:.2f}"
        tot = f"R$ {item.valor_total or 0:.2f}"
        row = [desc, cod, qtd, vlr, tot]
        y_start = pdf.get_y()
        pdf.set_fill_color(248, 249, 250)
        for i, val in enumerate(row):
            x = 10 + sum(col_w[:i])
            pdf.set_xy(x, y_start)
            align = "L" if i < 2 else "C" if i == 2 else "R"
            pdf.cell(col_w[i], 6, val, border=0, align=align)
            if i % 2 == 0:
                pdf.set_fill_color(255, 255, 255)
        pdf.set_y(y_start + 6)
        pdf.set_draw_color(230, 230, 230)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())

    # Tributos (Lei 12.741/2012)
    ali_fed = float(getattr(nfse, 'aliquota_federal', 0) or 0)
    ali_est = float(getattr(nfse, 'aliquota_estadual', 0) or 0)
    ali_mun = float(getattr(nfse, 'aliquota_municipal', 0) or 0)
    if ali_fed > 0 or ali_est > 0 or ali_mun > 0:
        pdf.ln(3)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(100, 100, 100)
        v_tot = float(nfse.valor_total or 0)
        v_fed = v_tot * ali_fed / 100
        v_est = v_tot * ali_est / 100
        v_mun = v_tot * ali_mun / 100
        pdf.set_x(12)
        pdf.cell(0, 4, f"Tributos aprox. (Lei 12.741/2012 - Fonte IBPT):", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(12)
        pdf.cell(0, 4, f"Federal: R$ {v_fed:.2f} ({ali_fed:.2f}%) | Estadual: R$ {v_est:.2f} ({ali_est:.2f}%) | Municipal: R$ {v_mun:.2f} ({ali_mun:.2f}%)", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # Total
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(14, 165, 233)
    pdf.set_x(145)
    pdf.cell(25, 8, "Valor Total:", align="R")
    pdf.cell(30, 8, f"R$ {float(nfse.valor_total or 0):.2f}", align="R")

    if getattr(nfse, 'iss_retido', False):
        ali_iss = getattr(nfse, 'aliquota_iss', 0) or 0
        vl_liquido = float(nfse.valor_total or 0) - float(nfse.valor_total or 0) * float(ali_iss) / 100
        pdf.ln(6)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(0, 150, 50)
        pdf.set_x(145)
        pdf.cell(25, 8, "Valor Líquido:", align="R")
        pdf.cell(30, 8, f"R$ {vl_liquido:.2f}", align="R")

    # Rodapé
    pdf.set_y(270)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(150, 150, 150)
    pdf.set_draw_color(220, 220, 220)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)
    data_str = nfse.data_emissao.strftime('%d/%m/%Y as %H:%M') if nfse.data_emissao else '-'
    pdf.cell(0, 3, f"Documento emitido em {data_str}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 3, f"NFSe {nfse.numero or ''} - Codigo de Verificacao: {nfse.codigo_verificacao or 'N/D'}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 3, "Este documento e uma representacao visual da NFS-e. Consulte o XML para validade juridica.", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf_filename = f"nfse_{nfse.numero or nfse.id}.pdf"
    pdf_path = os.path.join(UPLOAD_DIR, pdf_filename)
    pdf.output(pdf_path)
    return f"/static/uploads/nfse/{pdf_filename}"


def gerar_pdf_contas(contas, empresa, tipo="receber", filtros=None) -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    empresa_nome = (empresa.razao_social or empresa.nome_fantasia or "Empresa") if empresa else "Empresa"
    pdf.cell(0, 8, empresa_nome, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 12)
    titulo = "Contas a Receber" if tipo == "receber" else "Contas a Pagar"
    pdf.cell(0, 7, titulo, align="C", new_x="LMARGIN", new_y="NEXT")
    if filtros:
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(0, 5, filtros, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Cabeçalho da tabela
    pdf.set_font("Helvetica", "B", 8)
    col_w = [60, 28, 24, 24, 54]
    parte_label = "Cliente" if tipo == "receber" else "Fornecedor"
    headers = ["Descrição", "Valor", "Vencimento", "Status", parte_label]
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 6, h, border=1, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 7)
    linha_alt = 5
    total = Decimal("0")
    for c in contas:
        # Parte (cliente ou fornecedor) - duck typing seguro
        if tipo == "receber":
            parte = c.cliente.nome if getattr(c, "cliente", None) else "-"
        else:
            parte = c.fornecedor.nome if getattr(c, "fornecedor", None) else "-"
        status = c.status.name if hasattr(c.status, "name") else str(c.status)
        valor = c.valor or Decimal("0")
        total += valor
        descr = (c.descricao or "-")[:60]
        textos = [
            descr,
            f"R$ {float(valor):.2f}",
            c.data_vencimento.strftime('%d/%m/%Y') if c.data_vencimento else "-",
            status.lower(),
            parte[:30],
        ]
        if pdf.get_y() + linha_alt > pdf.h - pdf.b_margin:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 8)
            for i, h in enumerate(headers):
                pdf.cell(col_w[i], 6, h, border=1, align="C")
            pdf.ln()
            pdf.set_font("Helvetica", "", 7)
        x0 = pdf.get_x()
        y0 = pdf.get_y()
        for i, t in enumerate(textos):
            pdf.set_xy(x0 + sum(col_w[:i]), y0)
            pdf.cell(col_w[i], linha_alt, str(t), border=1, align="L" if i in (0, 4) else "C")

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, f"Total: R$ {float(total):.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, f"Emitido em {datetime.now().strftime('%d/%m/%Y %H:%M')}", align="C", new_x="LMARGIN", new_y="NEXT")
    out = pdf.output()
    return out if isinstance(out, bytes) else bytes(out)


def gerar_pdf_estoque(produtos, empresa, titulo="Posição de Estoque",
                      filtros=None, valor_total=0.0, valor_venda=0.0,
                      qtd_zerados=0, qtd_abaixo=0) -> bytes:
    """Gera PDF da posicao de estoque (tambem usado para abaixo do minimo)."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    empresa_nome = (empresa.razao_social or empresa.nome_fantasia or "Empresa") if empresa else "Empresa"
    pdf.cell(0, 8, empresa_nome, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, titulo, align="C", new_x="LMARGIN", new_y="NEXT")
    if filtros:
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(0, 5, filtros, align="C", new_x="LMARGIN", new_y="NEXT")
    if titulo == "Posição de Estoque":
        pdf.set_font("Helvetica", "", 8)
        resumo = (f"Itens: {len(produtos)}  |  Custo em estoque: R$ {valor_total:,.2f}  |  "
                  f"Venda potencial: R$ {valor_venda:,.2f}  |  Zerados: {qtd_zerados}  |  Abaixo mín.: {qtd_abaixo}")
        pdf.multi_cell(0, 5, resumo, align="C")
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 7)
    col_w = [16, 80, 28, 18, 22, 22]
    headers = ["Código", "Item", "Categoria", "Mín.", "Estoque", "Custo un."]
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 6, h, border=1, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 7)
    linha_alt = 5
    for p in produtos:
        cat = p.categoria.nome if getattr(p, "categoria", None) else "-"
        estoque = float(p.estoque or 0)
        minimo = float(p.estoque_minimo or 0)
        texto_cor = None
        if estoque <= 0:
            texto_cor = (200, 40, 40)
        elif estoque < minimo:
            texto_cor = (200, 140, 30)
        textos = [
            (p.codigo or "-")[:14],
            (p.nome or "-")[:52],
            cat[:18],
            f"{minimo:.2f}",
            f"{estoque:.2f}",
            f"R$ {float(p.preco_custo or 0):.2f}",
        ]
        if pdf.get_y() + linha_alt > pdf.h - pdf.b_margin:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 7)
            for i, h in enumerate(headers):
                pdf.cell(col_w[i], 6, h, border=1, align="C")
            pdf.ln()
            pdf.set_font("Helvetica", "", 7)
        x0 = pdf.get_x()
        y0 = pdf.get_y()
        for i, t in enumerate(textos):
            pdf.set_xy(x0 + sum(col_w[:i]), y0)
            if texto_cor and i in (3, 4):
                pdf.set_text_color(*texto_cor)
            else:
                pdf.set_text_color(0, 0, 0)
            pdf.cell(col_w[i], linha_alt, str(t), border=1, align="L" if i in (0, 1, 2) else "R")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(linha_alt)

    pdf.ln(4)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, f"Emitido em {datetime.now().strftime('%d/%m/%Y %H:%M')}", align="C", new_x="LMARGIN", new_y="NEXT")
    out = pdf.output()
    return out if isinstance(out, bytes) else bytes(out)


def gerar_pdf_movimentacoes(movs, empresa, filtros=None) -> bytes:
    """Gera PDF do relatorio consolidado de movimentacoes de estoque."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    empresa_nome = (empresa.razao_social or empresa.nome_fantasia or "Empresa") if empresa else "Empresa"
    pdf.cell(0, 8, empresa_nome, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "Movimentações de Estoque", align="C", new_x="LMARGIN", new_y="NEXT")
    if filtros:
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(0, 5, filtros, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 7)
    col_w = [26, 50, 26, 18, 24, 30]
    headers = ["Data", "Produto", "Tipo", "Qtd", "Saldo após", "Documento"]
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 6, h, border=1, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 7)
    linha_alt = 5
    for m in movs:
        nome_prod = m.produto.nome if getattr(m, "produto", None) else "-"
        doc = m.doc_tipo + (f" #{m.doc_id}" if m.doc_id else "")
        textos = [
            m.data.strftime('%d/%m/%Y %H:%M') if m.data else "-",
            nome_prod[:34],
            m.tipo,
            f"{float(m.quantidade):.3f}",
            f"{float(m.saldo_apos):.3f}" if m.saldo_apos is not None else "-",
            doc[:20],
        ]
        if pdf.get_y() + linha_alt > pdf.h - pdf.b_margin:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 7)
            for i, h in enumerate(headers):
                pdf.cell(col_w[i], 6, h, border=1, align="C")
            pdf.ln()
            pdf.set_font("Helvetica", "", 7)
        x0 = pdf.get_x()
        y0 = pdf.get_y()
        for i, t in enumerate(textos):
            pdf.set_xy(x0 + sum(col_w[:i]), y0)
            align = "L" if i in (0, 1, 2, 5) else "R"
            if i == 3 and m.quantidade < 0:
                pdf.set_text_color(200, 40, 40)
            elif i == 3:
                pdf.set_text_color(30, 150, 60)
            else:
                pdf.set_text_color(0, 0, 0)
            pdf.cell(col_w[i], linha_alt, str(t), border=1, align=align)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(linha_alt)

    pdf.ln(4)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, f"Emitido em {datetime.now().strftime('%d/%m/%Y %H:%M')}", align="C", new_x="LMARGIN", new_y="NEXT")
    out = pdf.output()
    return out if isinstance(out, bytes) else bytes(out)

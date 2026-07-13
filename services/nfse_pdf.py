import os
from datetime import datetime
from fpdf import FPDF

UPLOAD_DIR = "static/uploads/nfse"
os.makedirs(UPLOAD_DIR, exist_ok=True)


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
    ali_fed = getattr(nfse, 'aliquota_federal', None) or 0.0
    ali_est = getattr(nfse, 'aliquota_estadual', None) or 0.0
    ali_mun = getattr(nfse, 'aliquota_municipal', None) or 0.0
    if ali_fed > 0 or ali_est > 0 or ali_mun > 0:
        pdf.ln(3)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(100, 100, 100)
        v_tot = nfse.valor_total or 0
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
    pdf.cell(30, 8, f"R$ {nfse.valor_total or 0:.2f}", align="R")

    if getattr(nfse, 'iss_retido', False):
        ali_iss = getattr(nfse, 'aliquota_iss', 0) or 0
        vl_liquido = (nfse.valor_total or 0) - (nfse.valor_total or 0) * ali_iss / 100
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

    pdf_filename = f"nfse_{nfse.numero or nfse.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    pdf_path = os.path.join(UPLOAD_DIR, pdf_filename)
    pdf.output(pdf_path)
    return f"/static/uploads/nfse/{pdf_filename}"


def gerar_pdf_contas(contas, empresa, tipo="receber") -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    empresa_nome = (empresa.razao_social or empresa.nome_fantasia or "Empresa") if empresa else "Empresa"
    pdf.cell(0, 8, empresa_nome, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 12)
    titulo = "Contas a Receber" if tipo == "receber" else "Contas a Pagar"
    pdf.cell(0, 7, titulo, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 8)
    col_w = [62, 26, 22, 18, 62]
    parte_label = "Cliente" if tipo == "receber" else "Fornecedor"
    headers = ["Descricao", "Valor", "Vencimento", "Status", parte_label]
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 6, h, border=1, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 7)
    linha_alt = 5
    total = 0
    for c in contas:
        parte = (c.cliente.nome if tipo == "receber" and c.cliente else
                 c.fornecedor.nome if c.fornecedor else '-')
        textos = [c.descricao, f"R$ {c.valor:.2f}",
                  c.data_vencimento.strftime('%d/%m/%Y') if c.data_vencimento else '',
                  c.status.value if hasattr(c.status, 'value') else str(c.status), parte]
        total += c.valor
        if pdf.get_y() + linha_alt > pdf.h - pdf.b_margin:
            pdf.add_page()
        x0 = pdf.get_x()
        y0 = pdf.get_y()
        for i, t in enumerate(textos):
            pdf.set_xy(x0 + sum(col_w[:i]), y0)
            pdf.cell(col_w[i], linha_alt, t, border=1, align="L" if i in (0, 4) else "C")
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, f"Total: R$ {total:.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 4, f"Emitido em {datetime.now().strftime('%d/%m/%Y as %H:%M')}", align="C", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())

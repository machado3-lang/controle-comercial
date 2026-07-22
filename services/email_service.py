import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from typing import Optional
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from database import SessionLocal
from models import Empresa


def get_smtp_config(db) -> Optional[dict]:
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.smtp_host or not empresa.smtp_user or not empresa.smtp_password:
        return None
    return {
        "host": empresa.smtp_host,
        "port": empresa.smtp_port or 587,
        "user": empresa.smtp_user,
        "password": empresa.smtp_password,
        "from_email": empresa.smtp_from_email or empresa.email,
        "from_name": empresa.smtp_from_name or empresa.nome_fantasia or empresa.razao_social or "Sistema",
    }


def enviar_email(
    destinatario: str,
    assunto: str,
    corpo_html: str,
    anexos: Optional[list] = None,
    db=None,
) -> dict:
    config = get_smtp_config(db)
    if not config:
        return {"success": False, "error": "SMTP não configurado"}
    if not destinatario:
        return {"success": False, "error": "Destinatário não informado"}

    msg = MIMEMultipart('mixed')
    msg['From'] = f"{config['from_name']} <{config['from_email']}>"
    msg['To'] = destinatario
    msg['Subject'] = assunto

    msg_alternative = MIMEMultipart('alternative')
    msg_alternative.attach(MIMEText(corpo_html, 'html', 'utf-8'))
    msg.attach(msg_alternative)

    if anexos:
        for filename, content, mime_type in anexos:
            part = MIMEBase(*mime_type.split('/'))
            part.set_payload(content)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
            msg.attach(part)

    try:
        if config['port'] == 465:
            server = smtplib.SMTP_SSL(config['host'], config['port'], timeout=30)
        else:
            server = smtplib.SMTP(config['host'], config['port'], timeout=30)
            server.starttls()
        server.login(config['user'], config['password'])
        server.send_message(msg)
        server.quit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def render_email_template(template_name: str, context: dict) -> str:
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "emails")
    env = Environment(loader=FileSystemLoader(templates_dir))
    template = env.get_template(template_name)
    return template.render(**context)


def _fmt_valor(valor):
    try:
        return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "-"


def _whats_link_para(celular: str) -> str:
    if not celular:
        return ""
    digits = "".join(filter(str.isdigit, celular))
    if digits:
        return f"https://wa.me/55{digits}"
    return ""


def _xml_nfe_bytes(nfe) -> Optional[bytes]:
    if nfe is None:
        return None
    if getattr(nfe, "xml_text", None):
        return nfe.xml_text.encode("utf-8")
    if nfe.xml_path and os.path.exists(f".{nfe.xml_path}"):
        try:
            with open(f".{nfe.xml_path}", "r", encoding="utf-8") as f:
                return f.read().encode("utf-8")
        except Exception:
            return None
    return None


def _xml_nfse_bytes(nfse) -> Optional[bytes]:
    if nfse is None:
        return None
    if getattr(nfse, "xml_text", None):
        return nfse.xml_text.encode("utf-8")
    if nfse.xml_path and os.path.exists(f".{nfse.xml_path}"):
        try:
            with open(f".{nfse.xml_path}", "r", encoding="utf-8") as f:
                return f.read().encode("utf-8")
        except Exception:
            return None
    return None


def _pdf_nfse_bytes(nfse, db) -> Optional[bytes]:
    if nfse is None:
        return None
    # Tenta PDF ja existente em disco
    if nfse.pdf_path and os.path.exists(f".{nfse.pdf_path}"):
        try:
            with open(f".{nfse.pdf_path}", "rb") as f:
                return f.read()
        except Exception:
            pass
    # Gera sob demanda a partir do XML persistido no banco
    if getattr(nfse, "xml_text", None):
        try:
            from services.nfse_pdf import gerar_pdf_nfse
            from routers.nfse import STATUS_LABELS
            empresa = db.query(Empresa).first()
            cliente = nfse.cliente
            if empresa and cliente:
                pdf_url = gerar_pdf_nfse(nfse, empresa, cliente, nfse.itens, STATUS_LABELS)
                local = f".{pdf_url}"
                if os.path.exists(local):
                    with open(local, "rb") as f:
                        return f.read()
                        pass
        except Exception:
            pass
    return None


def _pdf_nfe_bytes(nfe, db) -> Optional[bytes]:
    if nfe is None:
        return None
    if nfe.pdf_path and os.path.exists(f".{nfe.pdf_path}"):
        try:
            with open(f".{nfe.pdf_path}", "rb") as f:
                return f.read()
        except Exception:
            pass
    xml = _xml_nfe_bytes(nfe)
    if xml:
        try:
            import tempfile, os as _os
            from services.nfe_danfe import gerar_danfe_pdf
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            gerar_danfe_pdf(xml.decode("utf-8"), tmp.name)
            with open(tmp.name, "rb") as f:
                data = f.read()
            _os.unlink(tmp.name)
            return data
        except Exception:
            return None
    return None


def _pdf_boleto_bytes(conta, db) -> Optional[bytes]:
    if conta is None or not conta.boleto_emitido or not conta.api_nosso_numero:
        return None
    try:
        from routers.sicoob import obter_pdf_boleto_bytes
        boleto_bytes, _ = obter_pdf_boleto_bytes(conta.api_nosso_numero, db)
        return boleto_bytes
    except Exception:
        return None


def enviar_documentos_cliente(
    db,
    cliente,
    nfses=None,
    nfes=None,
    contas=None,
    incluir_xml: bool = False,
) -> dict:
    """Envia um unico e-mail para o cliente com os documentos selecionados.
    Todos os anexos derivam dos objetos passados (nunca cruzados), garantindo
    que NF/boleto de um cliente nao sejam misturados.
    """
    nfses = nfses or []
    nfes = nfes or []
    contas = contas or []

    from models_nfe import NFSe

    if not cliente or not cliente.email:
        return {"success": False, "error": "Cliente sem e-mail"}

    empresa = db.query(Empresa).first()
    if not empresa:
        return {"success": False, "error": "Empresa nao configurada"}

    anexos = []
    itens_resumo = []

    for nfse in nfses:
        if nfse.cliente_id and cliente.id and nfse.cliente_id != cliente.id:
            continue
        pdf = _pdf_nfse_bytes(nfse, db)
        if pdf:
            anexos.append((f"NFSe_{nfse.numero or nfse.id}.pdf", pdf, "application/pdf"))
        if incluir_xml:
            xml = _xml_nfse_bytes(nfse)
            if xml:
                anexos.append((f"NFSe_{nfse.numero or nfse.id}.xml", xml, "application/xml"))
        itens_resumo.append(f"NFSe Nº {nfse.numero or nfse.id}")

    for nfe in nfes:
        if nfe.cliente_id and cliente.id and nfe.cliente_id != cliente.id:
            continue
        pdf = _pdf_nfe_bytes(nfe, db)
        if pdf:
            anexos.append((f"NFe_{nfe.numero}.pdf", pdf, "application/pdf"))
        if incluir_xml:
            xml = _xml_nfe_bytes(nfe)
            if xml:
                anexos.append((f"NFe_{nfe.numero}.xml", xml, "application/xml"))
        itens_resumo.append(f"NFe Nº {nfe.numero}")

    for conta in contas:
        if conta.cliente_id and cliente.id and conta.cliente_id != cliente.id:
            continue
        pdf = _pdf_boleto_bytes(conta, db)
        if pdf:
            anexos.append((f"Boleto_{conta.api_nosso_numero}.pdf", pdf, "application/pdf"))
        nfse = None
        if conta.nfse_id:
            nfse = db.query(NFSe).filter(NFSe.id == conta.nfse_id).first()
            if nfse:
                pdf = _pdf_nfse_bytes(nfse, db)
                if pdf:
                    anexos.append((f"NFSe_{nfse.numero or nfse.id}.pdf", pdf, "application/pdf"))
                if incluir_xml:
                    xml = _xml_nfse_bytes(nfse)
                    if xml:
                        anexos.append((f"NFSe_{nfse.numero or nfse.id}.xml", xml, "application/xml"))
                itens_resumo.append(f"NFSe Nº {nfse.numero or nfse.id} (conta)")
        itens_resumo.append(f"Boleto {conta.numero_documento or conta.nosso_numero or ''}".strip())

    if not anexos:
        return {"success": False, "error": "Nenhum documento disponível para envio"}

    whats_link = _whats_link_para(getattr(cliente, "celular", ""))
    context = {
        "empresa_nome": empresa.nome_fantasia or empresa.razao_social or "",
        "cliente_nome": getattr(cliente, "nome", "") or "",
        "itens_resumo": itens_resumo,
        "incluir_xml": incluir_xml,
        "whats_link": whats_link,
        "ano": datetime.now().year,
    }
    corpo = render_email_template("documentos.html", context)
    assunto = f"{empresa.nome_fantasia or empresa.razao_social or ''} - Documentos fiscais e boletos"

    result = enviar_email(cliente.email, assunto, corpo, anexos, db)
    return result


def enviar_notificacao_conta(conta_id: int):
    db = SessionLocal()
    try:
        from models import ContaReceber, Empresa, Cliente, NFSe

        conta = db.query(ContaReceber).options(
            ContaReceber.cliente,
        ).filter(ContaReceber.id == conta_id).first()
        if not conta:
            return
        if not conta.cliente or not conta.cliente.email:
            return
        if conta.email_enviado:
            return

        empresa = db.query(Empresa).first()
        if not empresa or not empresa.email_auto_enviar:
            return

        nfse = None
        if conta.nfse_id:
            nfse = db.query(NFSe).filter(NFSe.id == conta.nfse_id).first()

        # Só envia apos autorizacao: NFSe vinculada precisa estar autorizada e
        # com PDF disponivel (evita enviar documento incompleto em processamento).
        if nfse and nfse.status != "autorizada":
            return

        anexos = []

        if nfse:
            pdf = _pdf_nfse_bytes(nfse, db)
            if pdf:
                anexos.append((f"NFSe_{nfse.numero or nfse.id}.pdf", pdf, "application/pdf"))

        if conta.boleto_emitido and conta.api_nosso_numero:
            boleto_bytes = _pdf_boleto_bytes(conta, db)
            if boleto_bytes:
                anexos.append((f"Boleto_{conta.api_nosso_numero}.pdf", boleto_bytes, "application/pdf"))

        if not anexos:
            return

        valor_fmt = _fmt_valor(conta.valor)
        vencimento_fmt = conta.data_vencimento.strftime("%d/%m/%Y") if conta.data_vencimento else "-"

        whats_link = _whats_link_para(conta.cliente.celular or "")

        context = {
            "empresa_nome": empresa.nome_fantasia or empresa.razao_social or "",
            "cliente_nome": conta.cliente.nome or "",
            "descricao": conta.descricao or "",
            "valor": valor_fmt,
            "vencimento": vencimento_fmt,
            "nosso_numero": conta.nosso_numero or "",
            "numero_documento": conta.numero_documento or "",
            "nfse_numero": nfse.numero if nfse else "",
            "nfse_codigo_verificacao": nfse.codigo_verificacao if nfse else "",
            "whats_link": whats_link,
            "ano": datetime.now().year,
        }

        corpo = render_email_template("notificacao.html", context)
        assunto = f"{empresa.nome_fantasia or empresa.razao_social or ''} - Documento disponível"

        result = enviar_email(conta.cliente.email, assunto, corpo, anexos, db)
        if result["success"]:
            conta.email_enviado = True
            conta.data_envio_email = datetime.now()
            db.commit()
    except Exception as e:
        print(f"Erro ao enviar email: {e}")
    finally:
        db.close()

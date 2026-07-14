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

        anexos = []

        if nfse and nfse.pdf_path:
            pdf_path_local = f".{nfse.pdf_path}"
            if os.path.exists(pdf_path_local):
                with open(pdf_path_local, 'rb') as f:
                    content = f.read()
                anexos.append((f"NFSe_{nfse.numero or nfse.id}.pdf", content, 'application/pdf'))

        if conta.boleto_emitido and conta.api_nosso_numero:
            try:
                from routers.sicoob import obter_pdf_boleto_bytes
                boleto_bytes, boleto_err = obter_pdf_boleto_bytes(conta.api_nosso_numero, db)
                if boleto_bytes:
                    anexos.append((f"Boleto_{conta.api_nosso_numero}.pdf", boleto_bytes, 'application/pdf'))
            except Exception:
                pass

        valor_fmt = f"R$ {conta.valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        vencimento_fmt = conta.data_vencimento.strftime("%d/%m/%Y") if conta.data_vencimento else "-"

        celular = conta.cliente.celular or ""
        whats_link = ""
        if celular:
            digits = "".join(filter(str.isdigit, celular))
            if digits:
                whats_link = f"https://wa.me/55{digits}"

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

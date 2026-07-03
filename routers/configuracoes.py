import os
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models import Empresa, Cliente, Fornecedor, Assinatura, OrdemServico

router = APIRouter(prefix="/configuracoes", tags=["Configuracoes"])

CERT_DIR = "certs"
UPLOAD_DIR = "static/uploads"


@router.get("/")
def configuracoes(request: Request, db: Session = Depends(get_db), aba: str = "empresa"):
    from sqlalchemy import func
    empresa = db.query(Empresa).first()
    messages = []
    msg = request.session.pop("message", None)
    if msg:
        messages.append(msg)
    return request.app.state.templates.TemplateResponse(
        "configuracoes/form.html",
        {
            "request": request,
            "empresa": empresa,
            "messages": messages,
            "aba": aba,
        },
    )


@router.post("/")
async def salvar_configuracoes(
    request: Request,
    db: Session = Depends(get_db),
    aba: str = Form("empresa"),
    razao_social: str = Form(""),
    nome_fantasia: str = Form(""),
    cnpj: str = Form(""),
    inscricao_estadual: str = Form(""),
    inscricao_municipal: str = Form(""),
    endereco: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    estado: str = Form(""),
    cep: str = Form(""),
    codigo_ibge: str = Form(""),
    telefone: str = Form(""),
    celular: str = Form(""),
    email: str = Form(""),
    site: str = Form(""),
    senha_admin: str = Form(""),
    senha_admin_confirm: str = Form(""),
    senha_lembrete: str = Form(""),
    observacao: str = Form(""),
    bling_client_id: str = Form(""),
    bling_client_secret: str = Form(""),
    bling_api_key_v2: str = Form(""),
    bling_desabilitado: str = Form(""),
    sicoob_client_id: str = Form(""),
    sicoob_beneficiario: str = Form(""),
    sicoob_conta_corrente: str = Form(""),
    sicoob_cert_path: str = Form(""),
    sicoob_cert_password: str = Form(""),
    sicoob_cert_file: UploadFile = File(None),
    sicoob_key_file: UploadFile = File(None),
    logo: UploadFile = File(None),
    notaas_api_key: str = Form(""),
    notaas_ambiente: str = Form("2"),
    serie_nfe: int = Form(1),
    ultimo_numero_nfe: int = Form(0),
):
    empresa = db.query(Empresa).first()
    if empresa:
        empresa.razao_social = razao_social
        empresa.nome_fantasia = nome_fantasia
        empresa.cnpj = cnpj
        empresa.inscricao_estadual = inscricao_estadual
        empresa.inscricao_municipal = inscricao_municipal
        empresa.endereco = endereco
        empresa.bairro = bairro
        empresa.cidade = cidade
        empresa.estado = estado
        empresa.cep = cep
        empresa.codigo_ibge = codigo_ibge or None
        empresa.telefone = telefone
        empresa.celular = celular
        empresa.email = email
        empresa.site = site
        if senha_admin and senha_admin == senha_admin_confirm:
            empresa.senha_admin = senha_admin
            empresa.senha_lembrete = senha_lembrete
        empresa.observacao = observacao
        empresa.bling_client_id = bling_client_id
        empresa.bling_client_secret = bling_client_secret
        empresa.bling_api_key_v2 = bling_api_key_v2
        empresa.bling_desabilitado = (bling_desabilitado == "1")
        empresa.sicoob_client_id = sicoob_client_id
        empresa.sicoob_beneficiario = sicoob_beneficiario
        empresa.sicoob_conta_corrente = sicoob_conta_corrente
        if notaas_api_key:
            empresa.notaas_api_key = notaas_api_key
        empresa.notaas_ambiente = notaas_ambiente
        empresa.serie_nfe = serie_nfe
        empresa.ultimo_numero_nfe = ultimo_numero_nfe
    else:
        empresa = Empresa(
            razao_social=razao_social, nome_fantasia=nome_fantasia,
            cnpj=cnpj, inscricao_estadual=inscricao_estadual,
            inscricao_municipal=inscricao_municipal, endereco=endereco,
            bairro=bairro, cidade=cidade, estado=estado, cep=cep,
            codigo_ibge=codigo_ibge or None,
            telefone=telefone, celular=celular, email=email, site=site,
            senha_admin=senha_admin if senha_admin else None,
            senha_lembrete=senha_lembrete,
            observacao=observacao,
            bling_client_id=bling_client_id,
            bling_client_secret=bling_client_secret,
            bling_api_key_v2=bling_api_key_v2,
            bling_desabilitado=(bling_desabilitado == "1"),
            sicoob_client_id=sicoob_client_id,
            sicoob_beneficiario=sicoob_beneficiario,
            sicoob_conta_corrente=sicoob_conta_corrente,
            sicoob_cert_path=sicoob_cert_path,
            sicoob_cert_password=sicoob_cert_password,
            notaas_api_key=notaas_api_key or None,
            notaas_ambiente=notaas_ambiente,
            serie_nfe=serie_nfe,
            ultimo_numero_nfe=ultimo_numero_nfe,
        )
        db.add(empresa)

    if sicoob_cert_file and sicoob_cert_file.filename:
        os.makedirs(CERT_DIR, exist_ok=True)
        ext = os.path.splitext(sicoob_cert_file.filename)[1].lower()
        filename = f"sicoob_cert_{empresa.id or 'temp'}{ext}"
        filepath = os.path.join(CERT_DIR, filename)
        content = await sicoob_cert_file.read()
        with open(filepath, "wb") as f:
            f.write(content)
        empresa.sicoob_cert_path = filepath
        if sicoob_cert_password:
            empresa.sicoob_cert_password = sicoob_cert_password

    if sicoob_key_file and sicoob_key_file.filename:
        ext = os.path.splitext(sicoob_key_file.filename)[1].lower()
        filename = f"sicoob_key_{empresa.id or 'temp'}{ext}"
        filepath = os.path.join(CERT_DIR, filename)
        content = await sicoob_key_file.read()
        with open(filepath, "wb") as f:
            f.write(content)
        empresa.sicoob_cert_key_path = filepath

    if logo and logo.filename:
        ext = os.path.splitext(logo.filename)[1].lower()
        if ext in (".jpg", ".jpeg", ".png"):
            filename = f"logo{ext}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            content = await logo.read()
            with open(filepath, "wb") as f:
                f.write(content)
            empresa.logo = f"static/uploads/{filename}"

    empresa.sicoob_token = None
    empresa.updated_at = datetime.now()
    db.commit()
    request.session["message"] = {"tipo": "success", "texto": "Dados salvos com sucesso!"}
    return RedirectResponse(url=f"/configuracoes?aba={aba}", status_code=303)
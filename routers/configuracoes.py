import os
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models import Empresa

router = APIRouter(prefix="/configuracoes", tags=["Configuracoes"])

UPLOAD_DIR = "static/uploads"


@router.get("/")
def configuracoes(request: Request, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    messages = []
    msg = request.session.pop("message", None)
    if msg:
        messages.append(msg)
    return request.app.state.templates.TemplateResponse(
        "configuracoes/form.html",
        {"request": request, "empresa": empresa, "messages": messages}
    )


@router.post("/")
async def salvar_configuracoes(
    request: Request,
    db: Session = Depends(get_db),
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
    telefone: str = Form(""),
    celular: str = Form(""),
    email: str = Form(""),
    site: str = Form(""),
    senha_admin: str = Form(""),
    observacao: str = Form(""),
    logo: UploadFile = File(None),
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
        empresa.telefone = telefone
        empresa.celular = celular
        empresa.email = email
        empresa.site = site
        empresa.senha_admin = senha_admin if senha_admin else empresa.senha_admin
        empresa.observacao = observacao
        empresa.updated_at = datetime.now()
    else:
        empresa = Empresa(
            razao_social=razao_social, nome_fantasia=nome_fantasia,
            cnpj=cnpj, inscricao_estadual=inscricao_estadual,
            inscricao_municipal=inscricao_municipal, endereco=endereco,
            bairro=bairro, cidade=cidade, estado=estado, cep=cep,
            telefone=telefone, celular=celular, email=email, site=site,
            senha_admin=senha_admin if senha_admin else None,
            observacao=observacao
        )
        db.add(empresa)

    if logo and logo.filename:
        ext = os.path.splitext(logo.filename)[1].lower()
        if ext in (".jpg", ".jpeg", ".png"):
            filename = f"logo{ext}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            content = await logo.read()
            with open(filepath, "wb") as f:
                f.write(content)
            empresa.logo = f"static/uploads/{filename}"

    db.commit()
    request.session["message"] = {"tipo": "success", "texto": "Dados salvos com sucesso!"}
    return RedirectResponse(url="/configuracoes", status_code=303)

import logging
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Empresa, Transportadora
from app.core.security import verificar_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transportadoras", tags=["Transportadoras"])


@router.get("")
def listar_transportadoras(request: Request, db: Session = Depends(get_db)):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/dashboard", status_code=303)
    empresa = db.query(Empresa).first()
    transportadoras = db.query(Transportadora).order_by(Transportadora.nome).all()
    return request.app.state.templates.TemplateResponse(request,
        "transportadoras/listar.html",
        {"request": request, "empresa": empresa, "transportadoras": transportadoras}
    )


@router.post("/criar")
def criar_transportadora(
    request: Request, db: Session = Depends(get_db),
    nome: str = Form(...),
    cpf_cnpj: str = Form(""),
    inscricao_estadual: str = Form(""),
    endereco: str = Form(""),
    cidade: str = Form(""),
    estado: str = Form(""),
    cep: str = Form(""),
):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/dashboard", status_code=303)
    empresa = db.query(Empresa).first()
    t = Transportadora(
        empresa_id=empresa.id if empresa else None,
        nome=nome,
        cpf_cnpj=cpf_cnpj or None,
        inscricao_estadual=inscricao_estadual or None,
        endereco=endereco or None,
        cidade=cidade or None,
        estado=estado or None,
        cep=cep or None,
    )
    db.add(t)
    db.commit()
    request.session["message"] = f"Transportadora '{nome}' cadastrada!"
    return RedirectResponse(url="/transportadoras", status_code=303)


@router.post("/{transportadora_id}/editar")
def editar_transportadora(
    request: Request, transportadora_id: int, db: Session = Depends(get_db),
    nome: str = Form(...),
    cpf_cnpj: str = Form(""),
    inscricao_estadual: str = Form(""),
    endereco: str = Form(""),
    cidade: str = Form(""),
    estado: str = Form(""),
    cep: str = Form(""),
):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/dashboard", status_code=303)
    t = db.query(Transportadora).filter(Transportadora.id == transportadora_id).first()
    if t:
        t.nome = nome
        t.cpf_cnpj = cpf_cnpj or None
        t.inscricao_estadual = inscricao_estadual or None
        t.endereco = endereco or None
        t.cidade = cidade or None
        t.estado = estado or None
        t.cep = cep or None
        db.commit()
        request.session["message"] = "Transportadora atualizada!"
    return RedirectResponse(url="/transportadoras", status_code=303)


@router.post("/{transportadora_id}/excluir")
def excluir_transportadora(request: Request, transportadora_id: int, db: Session = Depends(get_db)):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/dashboard", status_code=303)
    t = db.query(Transportadora).filter(Transportadora.id == transportadora_id).first()
    if t:
        db.delete(t)
        db.commit()
        request.session["message"] = "Transportadora excluída!"
    return RedirectResponse(url="/transportadoras", status_code=303)

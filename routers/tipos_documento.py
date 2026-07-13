from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models import TipoDocumento

router = APIRouter(prefix="/tipos-documento", tags=["Tipos de Documento"])


@router.get("")
def listar_tipos(request: Request, db: Session = Depends(get_db)):
    tipos = db.query(TipoDocumento).order_by(TipoDocumento.nome).all()
    return request.app.state.templates.TemplateResponse(
        "contas/tipos_documento.html",
        {"request": request, "tipos": tipos}
    )


@router.post("/novo")
def criar_tipo(request: Request, db: Session = Depends(get_db), nome: str = Form(...)):
    if db.query(TipoDocumento).filter(TipoDocumento.nome == nome).first():
        request.session["error"] = "Tipo de documento já existe"
        return RedirectResponse(url="/tipos-documento", status_code=303)
    db.add(TipoDocumento(nome=nome))
    db.commit()
    request.session["message"] = "Tipo de documento criado com sucesso!"
    return RedirectResponse(url="/tipos-documento", status_code=303)


@router.post("/{tipo_id}/editar")
def editar_tipo(request: Request, tipo_id: int, db: Session = Depends(get_db), nome: str = Form(...)):
    tipo = db.query(TipoDocumento).filter(TipoDocumento.id == tipo_id).first()
    if not tipo:
        request.session["error"] = "Tipo de documento não encontrado"
        return RedirectResponse(url="/tipos-documento", status_code=303)
    if db.query(TipoDocumento).filter(TipoDocumento.nome == nome, TipoDocumento.id != tipo_id).first():
        request.session["error"] = "Nome já está em uso"
        return RedirectResponse(url="/tipos-documento", status_code=303)
    tipo.nome = nome
    db.commit()
    request.session["message"] = "Tipo de documento atualizado!"
    return RedirectResponse(url="/tipos-documento", status_code=303)


@router.post("/{tipo_id}/excluir")
def excluir_tipo(request: Request, tipo_id: int, db: Session = Depends(get_db)):
    tipo = db.query(TipoDocumento).filter(TipoDocumento.id == tipo_id).first()
    if not tipo:
        return JSONResponse({"erro": "Tipo de documento não encontrado"}, status_code=404)
    db.delete(tipo)
    db.commit()
    return JSONResponse({"ok": True, "redirect": "/tipos-documento"})


@router.get("/json")
def tipos_json(db: Session = Depends(get_db)):
    tipos = db.query(TipoDocumento).order_by(TipoDocumento.nome).all()
    return [{"id": t.id, "nome": t.nome} for t in tipos]

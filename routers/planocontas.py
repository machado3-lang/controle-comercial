from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models import PlanoDeContas

router = APIRouter(prefix="/plano-contas", tags=["Plano de Contas"])


def build_tree(contas, parent_id=None, nivel=0):
    result = []
    for c in contas:
        if c.parent_id == parent_id:
            c._nivel = nivel
            result.append(c)
            result.extend(build_tree(contas, c.id, nivel + 1))
    return result


@router.get("")
def listar_planos(request: Request, db: Session = Depends(get_db)):
    contas = db.query(PlanoDeContas).order_by(PlanoDeContas.codigo).all()
    tree = build_tree(contas)
    return request.app.state.templates.TemplateResponse(
        "contas/plano_contas.html",
        {"request": request, "tree": tree}
    )


@router.post("/novo")
def criar_conta(
    request: Request, db: Session = Depends(get_db),
    codigo: str = Form(...), nome: str = Form(...),
    tipo: str = Form(...), parent_id: int = Form(0)
):
    if db.query(PlanoDeContas).filter(PlanoDeContas.codigo == codigo).first():
        request.session["error"] = "Código já existe"
        return RedirectResponse(url="/plano-contas", status_code=303)
    parent = db.query(PlanoDeContas).filter(PlanoDeContas.id == parent_id).first() if parent_id else None
    nivel = (parent.nivel + 1) if parent else 1
    conta = PlanoDeContas(
        codigo=codigo, nome=nome, tipo=tipo,
        parent_id=parent_id if parent_id else None,
        nivel=nivel
    )
    db.add(conta)
    db.commit()
    request.session["message"] = "Conta criada com sucesso!"
    return RedirectResponse(url="/plano-contas", status_code=303)


@router.post("/{conta_id}/editar")
def editar_conta(
    request: Request, conta_id: int, db: Session = Depends(get_db),
    codigo: str = Form(...), nome: str = Form(...), tipo: str = Form(...)
):
    conta = db.query(PlanoDeContas).filter(PlanoDeContas.id == conta_id).first()
    if not conta:
        request.session["error"] = "Conta não encontrada"
        return RedirectResponse(url="/plano-contas", status_code=303)
    if db.query(PlanoDeContas).filter(PlanoDeContas.codigo == codigo, PlanoDeContas.id != conta_id).first():
        request.session["error"] = "Código já está em uso"
        return RedirectResponse(url="/plano-contas", status_code=303)
    conta.codigo = codigo
    conta.nome = nome
    conta.tipo = tipo
    db.commit()
    request.session["message"] = "Conta atualizada!"
    return RedirectResponse(url="/plano-contas", status_code=303)


@router.post("/{conta_id}/toggle")
def toggle_conta(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(PlanoDeContas).filter(PlanoDeContas.id == conta_id).first()
    if not conta:
        return JSONResponse({"erro": "Conta não encontrada"}, status_code=404)
    conta.ativo = not conta.ativo
    db.commit()
    return JSONResponse({"ok": True, "ativo": conta.ativo})


@router.post("/{conta_id}/excluir")
def excluir_conta(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(PlanoDeContas).filter(PlanoDeContas.id == conta_id).first()
    if not conta:
        return JSONResponse({"erro": "Conta não encontrada"}, status_code=404)
    if db.query(PlanoDeContas).filter(PlanoDeContas.parent_id == conta_id).first():
        return JSONResponse({"erro": "Exclua primeiro as subcontas"}, status_code=400)
    db.delete(conta)
    db.commit()
    return JSONResponse({"ok": True, "redirect": "/plano-contas"})


@router.get("/json")
def planos_json(tipo: str = "", db: Session = Depends(get_db)):
    query = db.query(PlanoDeContas).filter(PlanoDeContas.ativo == True)
    if tipo:
        query = query.filter(PlanoDeContas.tipo == tipo)
    contas = query.order_by(PlanoDeContas.codigo).all()
    return [{"id": c.id, "codigo": c.codigo, "nome": c.nome, "tipo": c.tipo} for c in contas]

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import CondicaoPagamento
from app.core.security import verificar_admin

router = APIRouter(prefix="/condicoes-pagamento", tags=["Condições de Pagamento"])


@router.get("")
def listar_condicoes(request: Request, db: Session = Depends(get_db)):
    condicoes = db.query(CondicaoPagamento).order_by(CondicaoPagamento.nome).all()
    return request.app.state.templates.TemplateResponse(request,
        "contas/condicoes_pagamento.html",
        {"request": request, "condicoes": condicoes}
    )


@router.post("/nova")
def criar_condicao(request: Request, db: Session = Depends(get_db),
                   nome: str = Form(...), num_parcelas: int = Form(...),
                   intervalo_dias: int = Form(30), primeiro_vencimento: int = Form(0)):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/", status_code=303)
    if db.query(CondicaoPagamento).filter(CondicaoPagamento.nome == nome).first():
        request.session["error"] = "Condição de pagamento já existe"
        return RedirectResponse(url="/condicoes-pagamento", status_code=303)
    db.add(CondicaoPagamento(
        nome=nome, num_parcelas=max(1, int(num_parcelas)),
        intervalo_dias=max(1, int(intervalo_dias)),
        primeiro_vencimento=max(0, int(primeiro_vencimento)),
    ))
    db.commit()
    request.session["message"] = "Condição de pagamento criada com sucesso!"
    return RedirectResponse(url="/condicoes-pagamento", status_code=303)


@router.post("/{condicao_id}/editar")
def editar_condicao(request: Request, condicao_id: int, db: Session = Depends(get_db),
                    nome: str = Form(...), num_parcelas: int = Form(...),
                    intervalo_dias: int = Form(30), primeiro_vencimento: int = Form(0)):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/", status_code=303)
    condicao = db.query(CondicaoPagamento).filter(CondicaoPagamento.id == condicao_id).first()
    if not condicao:
        request.session["error"] = "Condição de pagamento não encontrada"
        return RedirectResponse(url="/condicoes-pagamento", status_code=303)
    if db.query(CondicaoPagamento).filter(CondicaoPagamento.nome == nome, CondicaoPagamento.id != condicao_id).first():
        request.session["error"] = "Nome já está em uso"
        return RedirectResponse(url="/condicoes-pagamento", status_code=303)
    condicao.nome = nome
    condicao.num_parcelas = max(1, int(num_parcelas))
    condicao.intervalo_dias = max(1, int(intervalo_dias))
    condicao.primeiro_vencimento = max(0, int(primeiro_vencimento))
    db.commit()
    request.session["message"] = "Condição de pagamento atualizada!"
    return RedirectResponse(url="/condicoes-pagamento", status_code=303)


@router.post("/{condicao_id}/excluir")
def excluir_condicao(request: Request, condicao_id: int, db: Session = Depends(get_db)):
    if not verificar_admin(request, db):
        return JSONResponse({"erro": "Acesso negado"}, status_code=403)
    condicao = db.query(CondicaoPagamento).filter(CondicaoPagamento.id == condicao_id).first()
    if not condicao:
        return JSONResponse({"erro": "Condição de pagamento não encontrada"}, status_code=404)
    db.delete(condicao)
    db.commit()
    return JSONResponse({"ok": True, "redirect": "/condicoes-pagamento"})


@router.get("/ativas/json")
def condicoes_ativas_json(db: Session = Depends(get_db)):
    condicoes = db.query(CondicaoPagamento).filter(CondicaoPagamento.ativo == True).order_by(CondicaoPagamento.nome).all()
    return [{
        "id": c.id, "nome": c.nome, "num_parcelas": c.num_parcelas,
        "intervalo_dias": c.intervalo_dias, "primeiro_vencimento": c.primeiro_vencimento
    } for c in condicoes]

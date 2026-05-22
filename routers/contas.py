from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, date
from typing import Optional

from database import get_db
from models import ContaPagar, ContaReceber, Fornecedor, Cliente, StatusConta

router = APIRouter(prefix="/contas", tags=["Contas"])


# ─── CONTAS A PAGAR ───────────────────────────────────────────────

@router.get("/pagar")
def listar_contas_pagar(
    request: Request, db: Session = Depends(get_db),
    status_filtro: str = Query(""), busca: str = Query("")
):
    query = db.query(ContaPagar).join(Fornecedor, isouter=True)
    if status_filtro:
        query = query.filter(ContaPagar.status == status_filtro)
    if busca:
        query = query.filter(
            ContaPagar.descricao.ilike(f"%{busca}%") |
            Fornecedor.nome.ilike(f"%{busca}%")
        )
    contas = query.order_by(ContaPagar.data_vencimento.desc()).all()
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    return request.app.state.templates.TemplateResponse(
        "contas/pagar_listar.html",
        {"request": request, "contas": contas, "fornecedores": fornecedores,
         "status_filtro": status_filtro, "busca": busca, "StatusConta": StatusConta}
    )


@router.post("/pagar/novo")
def criar_conta_pagar(
    request: Request, db: Session = Depends(get_db),
    fornecedor_id: int = Form(0),
    descricao: str = Form(...),
    valor: float = Form(...),
    data_vencimento: str = Form(...),
    observacao: str = Form(""),
):
    venc = date.fromisoformat(data_vencimento)
    conta = ContaPagar(
        fornecedor_id=fornecedor_id if fornecedor_id else None,
        descricao=descricao, valor=valor,
        data_vencimento=venc, observacao=observacao
    )
    db.add(conta)
    db.commit()
    return RedirectResponse(url="/contas/pagar", status_code=303)


@router.get("/pagar/{conta_id}/pagar")
def pagar_conta(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if conta:
        conta.status = StatusConta.PAGO
        conta.data_pagamento = date.today()
        db.commit()
    return RedirectResponse(url="/contas/pagar", status_code=303)


@router.get("/pagar/{conta_id}/excluir")
def excluir_conta_pagar(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if conta:
        db.delete(conta)
        db.commit()
    return RedirectResponse(url="/contas/pagar", status_code=303)


# ─── CONTAS A RECEBER ─────────────────────────────────────────────

@router.get("/receber")
def listar_contas_receber(
    request: Request, db: Session = Depends(get_db),
    status_filtro: str = Query(""), busca: str = Query("")
):
    query = db.query(ContaReceber).join(Cliente, isouter=True)
    if status_filtro:
        query = query.filter(ContaReceber.status == status_filtro)
    if busca:
        query = query.filter(
            ContaReceber.descricao.ilike(f"%{busca}%") |
            Cliente.nome.ilike(f"%{busca}%")
        )
    contas = query.order_by(ContaReceber.data_vencimento.desc()).all()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    return request.app.state.templates.TemplateResponse(
        "contas/receber_listar.html",
        {"request": request, "contas": contas, "clientes": clientes,
         "status_filtro": status_filtro, "busca": busca, "StatusConta": StatusConta}
    )


@router.post("/receber/novo")
def criar_conta_receber(
    request: Request, db: Session = Depends(get_db),
    cliente_id: int = Form(0),
    descricao: str = Form(...),
    valor: float = Form(...),
    data_vencimento: str = Form(...),
    observacao: str = Form(""),
):
    venc = date.fromisoformat(data_vencimento)
    conta = ContaReceber(
        cliente_id=cliente_id if cliente_id else None,
        descricao=descricao, valor=valor,
        data_vencimento=venc, observacao=observacao
    )
    db.add(conta)
    db.commit()
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.post("/receber/{conta_id}/receber")
def receber_conta(
    request: Request, conta_id: int, db: Session = Depends(get_db),
    data_recebimento: str = Form(...)
):
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if conta:
        conta.status = StatusConta.PAGO
        conta.data_recebimento = date.fromisoformat(data_recebimento)
        db.commit()
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.get("/receber/{conta_id}/excluir")
def excluir_conta_receber(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if conta:
        db.delete(conta)
        db.commit()
    return RedirectResponse(url="/contas/receber", status_code=303)

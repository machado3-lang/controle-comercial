from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func as sql_func
from datetime import datetime, date, timedelta
from typing import Optional

from database import get_db
from models import ContaPagar, ContaReceber, Fornecedor, Cliente, StatusConta, Empresa

router = APIRouter(prefix="/contas", tags=["Contas"])


def get_messages(request: Request) -> list:
    messages = []
    if "message" in request.session:
        messages.append({"type": "success", "text": request.session.pop("message")})
    if "error" in request.session:
        messages.append({"type": "error", "text": request.session.pop("error")})
    return messages


@router.get("/pagar")
def contas_pagar(request: Request, db: Session = Depends(get_db)):
    from sqlalchemy import func
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    fornecedores_json = [{"id": f.id, "nome": f.nome} for f in fornecedores]
    contas = db.query(ContaPagar).options(joinedload(ContaPagar.fornecedor)).filter(
        ContaPagar.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    ).order_by(ContaPagar.data_vencimento).all()
    total_pendente = func.coalesce(func.sum(ContaPagar.valor), 0).label("total")
    total_pendente_valor = db.query(total_pendente).scalar()
    return request.app.state.templates.TemplateResponse(
        "contas/pagar.html",
        {"request": request, "contas": contas, "total_pendente": total_pendente_valor or 0, "fornecedores_json": fornecedores_json, "messages": get_messages(request)}
    )


@router.get("/pagar/nova")
def nova_conta_pagar(request: Request, db: Session = Depends(get_db)):
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    return request.app.state.templates.TemplateResponse(
        "contas/nova_pagar.html",
        {"request": request, "fornecedores_json": fornecedores_json}
    )


@router.post("/pagar/nova")
def criar_conta_pagar(
    request: Request,
    db: Session = Depends(get_db),
    descricao: str = Form(...),
    valor: float = Form(...),
    data_vencimento: date = Form(...),
    fornecedor_id: Optional[int] = Form(None),
    observacoes: Optional[str] = Form(None)
):
    from sqlalchemy import func
    conta = ContaPagar(
        descricao=descricao,
        valor=valor,
        data_vencimento=data_vencimento,
        fornecedor_id=fornecedor_id,
        observacoes=observacoes,
        status=StatusConta.PENDENTE
    )
    db.add(conta)
    db.commit()
    request.session["message"] = "Conta a pagar criada com sucesso!"
    return RedirectResponse(url="/contas/pagar", status_code=303)


@router.get("/pagar/{conta_id}/editar-form")
def editar_conta_pagar_form(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if not conta:
        return ""
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    return request.app.state.templates.TemplateResponse(
        "contas/editar_pagar_form.html",
        {"request": request, "conta": conta, "fornecedores": fornecedores, "fornecedores_json": fornecedores_json}
    )


@router.post("/pagar/{conta_id}/editar")
def atualizar_conta_pagar(
    request: Request,
    conta_id: int,
    db: Session = Depends(get_db),
    descricao: str = Form(...),
    valor: float = Form(...),
    data_vencimento: date = Form(...),
    fornecedor_id: Optional[int] = Form(None),
    observacoes: Optional[str] = Form(None),
    status: StatusConta = Form(...)
):
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if not conta:
        request.session["error"] = "Conta não encontrada"
        return RedirectResponse(url="/contas/pagar", status_code=303)
    conta.descricao = descricao
    conta.valor = valor
    conta.data_vencimento = data_vencimento
    conta.fornecedor_id = fornecedor_id
    conta.observacoes = observacoes
    conta.status = status
    db.commit()
    request.session["message"] = "Conta a pagar atualizada com sucesso!"
    return RedirectResponse(url="/contas/pagar", status_code=303)


@router.post("/pagar/{conta_id}/excluir")
def excluir_conta_pagar(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if conta:
        db.delete(conta)
        db.commit()
        request.session["message"] = "Conta a pagar excluída com sucesso!"
    else:
        request.session["error"] = "Conta não encontrada"
    return RedirectResponse(url="/contas/pagar", status_code=303)


@router.get("/receber")
def contas_receber(request: Request, db: Session = Depends(get_db)):
    from sqlalchemy import func
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome} for c in clientes]
    contas = db.query(ContaReceber).options(joinedload(ContaReceber.cliente)).filter(
        ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    ).order_by(ContaReceber.data_vencimento).all()
    total_pendente = func.coalesce(func.sum(ContaReceber.valor), 0).label("total")
    total_pendente_valor = db.query(total_pendente).scalar()
    return request.app.state.templates.TemplateResponse(
        "contas/receber.html",
        {"request": request, "contas": contas, "total_pendente": total_pendente_valor or 0, "clientes_json": clientes_json, "fornecedores_json": [], "messages": get_messages(request)}
    )


@router.get("/receber/nova")
def nova_conta_receber(request: Request, db: Session = Depends(get_db)):
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    return request.app.state.templates.TemplateResponse(
        "contas/nova_receber.html",
        {"request": request, "clientes_json": clientes_json}
    )


@router.post("/receber/nova")
def criar_conta_receber(
    request: Request,
    db: Session = Depends(get_db),
    descricao: str = Form(...),
    valor: float = Form(...),
    data_vencimento: date = Form(...),
    cliente_id: Optional[int] = Form(None),
    observacoes: Optional[str] = Form(None)
):
    from sqlalchemy import func
    conta = ContaReceber(
        descricao=descricao,
        valor=valor,
        data_vencimento=data_vencimento,
        cliente_id=cliente_id,
        observacoes=observacoes,
        status=StatusConta.PENDENTE
    )
    db.add(conta)
    db.commit()
    request.session["message"] = "Conta a receber criada com sucesso!"
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.get("/receber/{conta_id}/editar-form")
def editar_conta_receber_form(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if not conta:
        return ""
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    return request.app.state.templates.TemplateResponse(
        "contas/editar_receber_form.html",
        {"request": request, "conta": conta, "clientes": clientes, "clientes_json": clientes_json}
    )


@router.post("/receber/{conta_id}/editar")
def atualizar_conta_receber(
    request: Request,
    conta_id: int,
    db: Session = Depends(get_db),
    descricao: str = Form(...),
    valor: float = Form(...),
    data_vencimento: date = Form(...),
    cliente_id: Optional[int] = Form(None),
    observacoes: Optional[str] = Form(None),
    status: StatusConta = Form(...)
):
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if not conta:
        request.session["error"] = "Conta não encontrada"
        return RedirectResponse(url="/contas/receber", status_code=303)
    conta.descricao = descricao
    conta.valor = valor
    conta.data_vencimento = data_vencimento
    conta.cliente_id = cliente_id
    conta.observacoes = observacoes
    conta.status = status
    db.commit()
    request.session["message"] = "Conta a receber atualizada com sucesso!"
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.post("/receber/{conta_id}/excluir")
def excluir_conta_receber(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if conta:
        db.delete(conta)
        db.commit()
        request.session["message"] = "Conta a receber excluída com sucesso!"
    else:
        request.session["error"] = "Conta não encontrada"
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.post("/pagar/{conta_id}/baixar")
def baixar_conta_pagar(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if conta:
        conta.status = StatusConta.PAGO
        conta.data_pagamento = date.today()
        db.commit()
        request.session["message"] = "Conta paga com sucesso!"
    else:
        request.session["error"] = "Conta não encontrada"
    return RedirectResponse(url="/contas/pagar", status_code=303)


@router.post("/receber/{conta_id}/baixar")
def baixar_conta_receber(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if conta:
        conta.status = StatusConta.PAGO
        conta.data_recebimento = date.today()
        db.commit()
        request.session["message"] = "Conta recebida com sucesso!"
    else:
        request.session["error"] = "Conta não encontrada"
    return RedirectResponse(url="/contas/receber", status_code=303)


# Estorno de contas baixadas (reverte status para pendente)
@router.post("/pagar/{conta_id}/estornar")
def estornar_conta_pagar(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if conta and conta.status == StatusConta.PAGO:
        conta.status = StatusConta.PENDENTE
        conta.data_pagamento = None
        db.commit()
        request.session["message"] = "Baixa estornada - conta reaberta!"
    else:
        request.session["error"] = "Conta não encontrada ou não está paga"
    return RedirectResponse(url="/contas/pagar", status_code=303)


@router.post("/receber/{conta_id}/estornar")
def estornar_conta_receber(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if conta and conta.status == StatusConta.PAGO:
        conta.status = StatusConta.PENDENTE
        conta.data_recebimento = None
        db.commit()
        request.session["message"] = "Baixa estornada - conta reaberta!"
    else:
        request.session["error"] = "Conta não encontrada ou não está recebida"
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.get("/pagar/{conta_id}")
def ver_conta_pagar(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaPagar).options(joinedload(ContaPagar.fornecedor)).filter(ContaPagar.id == conta_id).first()
    if not conta:
        request.session["error"] = "Conta não encontrada"
        return RedirectResponse(url="/contas/pagar", status_code=303)
    empresa = db.query(Empresa).first()
    return request.app.state.templates.TemplateResponse(
        "contas/ver_pagar.html",
        {"request": request, "conta": conta, "empresa": empresa}
    )


@router.get("/receber/{conta_id}")
def ver_conta_receber(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaReceber).options(joinedload(ContaReceber.cliente)).filter(ContaReceber.id == conta_id).first()
    if not conta:
        request.session["error"] = "Conta não encontrada"
        return RedirectResponse(url="/contas/receber", status_code=303)
    empresa = db.query(Empresa).first()
    return request.app.state.templates.TemplateResponse(
        "contas/ver_receber.html",
        {"request": request, "conta": conta, "empresa": empresa}
    )


@router.get("/previsao-recebimentos")
def previsao_recebimentos(request: Request, db: Session = Depends(get_db), dias: int = 30):
    hoje = date.today()
    data_limite = hoje + timedelta(days=dias)
    contas = db.query(ContaReceber).options(joinedload(ContaReceber.cliente)).filter(
        ContaReceber.data_vencimento >= hoje,
        ContaReceber.data_vencimento <= data_limite,
        ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    ).order_by(ContaReceber.data_vencimento).all()
    total_previsto = sum(c.valor for c in contas)
    return request.app.state.templates.TemplateResponse(
        "contas/previsao.html",
        {"request": request, "contas": contas, "total_previsto": total_previsto, "hoje": hoje, "dias": dias}
    )


@router.get("/inadimplencia")
def inadimplencia(request: Request, db: Session = Depends(get_db), dias: int = 0):
    hoje = date.today()
    query = db.query(ContaReceber).options(joinedload(ContaReceber.cliente)).filter(
        ContaReceber.data_vencimento < hoje,
        ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    )
    if dias > 0:
        data_inicio = hoje - timedelta(days=dias)
        query = query.filter(ContaReceber.data_vencimento >= data_inicio)
    contas = query.order_by(ContaReceber.data_vencimento.desc()).all()
    total_inadimplente = sum(c.valor for c in contas)
    return request.app.state.templates.TemplateResponse(
        "contas/inadimplencia.html",
        {"request": request, "contas": contas, "total_inadimplente": total_inadimplente, "hoje": hoje, "dias": dias}
    )
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func as sql_func
from datetime import datetime, date
from typing import Optional

from database import get_db
from models import ContaPagar, ContaReceber, Fornecedor, Cliente, StatusConta, Empresa

router = APIRouter(prefix="/contas", tags=["Contas"])


def get_messages(request: Request) -> list:
    messages = []
    msg = request.session.pop("message", None)
    if msg:
        messages.append(msg)
    return messages


# ─── CONTAS A PAGAR ───────────────────────────────────────────────

@router.get("/pagar")
def listar_contas_pagar(
    request: Request, db: Session = Depends(get_db),
    status_filtro: str = Query(""), busca: str = Query(""),
    data_inicio: str = Query(""), data_fim: str = Query("")
):
    query = db.query(ContaPagar).join(Fornecedor, isouter=True)
    if status_filtro:
        query = query.filter(ContaPagar.status == status_filtro)
    if busca:
        query = query.filter(
            ContaPagar.descricao.ilike(f"%{busca}%") |
            Fornecedor.nome.ilike(f"%{busca}%")
        )
    if data_inicio:
        query = query.filter(ContaPagar.data_vencimento >= date.fromisoformat(data_inicio))
    if data_fim:
        query = query.filter(ContaPagar.data_vencimento <= date.fromisoformat(data_fim))
    contas = query.order_by(ContaPagar.data_vencimento.desc()).all()
    total_valor = sum(c.valor for c in contas)
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    messages = get_messages(request)
    return request.app.state.templates.TemplateResponse(
        "contas/pagar_listar.html",
        {"request": request, "contas": contas, "fornecedores": fornecedores,
         "status_filtro": status_filtro, "busca": busca, "StatusConta": StatusConta,
         "messages": messages, "data_inicio": data_inicio, "data_fim": data_fim,
         "total_valor": total_valor, "fornecedores_json": fornecedores_json}
    )


@router.get("/pagar/pdf")
def relatorio_pdf(
    request: Request, db: Session = Depends(get_db),
    status_filtro: str = Query(""), busca: str = Query(""),
    data_inicio: str = Query(""), data_fim: str = Query("")
):
    query = db.query(ContaPagar).join(Fornecedor, isouter=True)
    if status_filtro:
        query = query.filter(ContaPagar.status == status_filtro)
    if busca:
        query = query.filter(
            ContaPagar.descricao.ilike(f"%{busca}%") |
            Fornecedor.nome.ilike(f"%{busca}%")
        )
    if data_inicio:
        query = query.filter(ContaPagar.data_vencimento >= date.fromisoformat(data_inicio))
    if data_fim:
        query = query.filter(ContaPagar.data_vencimento <= date.fromisoformat(data_fim))
    contas = query.order_by(ContaPagar.data_vencimento.desc()).all()
    total_valor = sum(c.valor for c in contas)
    empresa = db.query(Empresa).first()
    return request.app.state.templates.TemplateResponse(
        "contas/pagar_pdf.html",
        {"request": request, "contas": contas, "empresa": empresa,
         "total_valor": total_valor, "data_geracao": date.today()}
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


@router.post("/pagar/{conta_id}/excluir")
def excluir_conta_pagar(request: Request, conta_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.senha_admin or senha != empresa.senha_admin:
        return JSONResponse({"erro": "Senha inválida"}, status_code=403)
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if conta:
        db.delete(conta)
        db.commit()
    return RedirectResponse(url="/contas/pagar", status_code=303)


@router.get("/pagar/{conta_id}/editar")
def editar_conta_pagar_page(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if not conta:
        return RedirectResponse(url="/contas/pagar", status_code=303)
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    return request.app.state.templates.TemplateResponse(
        "contas/_editar_conta_pagar.html",
        {"request": request, "conta": conta, "fornecedores": fornecedores, "fornecedores_json": fornecedores_json}
    )


@router.post("/pagar/{conta_id}/editar")
def editar_conta_pagar(request: Request, conta_id: int, db: Session = Depends(get_db), fornecedor_id: int = Form(0), descricao: str = Form(...), valor: float = Form(...), data_vencimento: str = Form(None), observacao: str = Form("")):
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if conta:
        conta.fornecedor_id = fornecedor_id if fornecedor_id else None
        conta.descricao = descricao
        conta.valor = valor
        if data_vencimento:
            conta.data_vencimento = date.fromisoformat(data_vencimento)
        conta.observacao = observacao
        db.commit()
    return RedirectResponse(url="/contas/pagar", status_code=303)


# ─── RELATÓRIO DE RECEBIMENTOS ───────────────────────────────────

@router.get("/recebimentos")
def listar_recebimentos(
    request: Request, db: Session = Depends(get_db),
    forma_pagamento: str = Query("", alias="forma_pagamento"),
    busca: str = Query("", alias="busca"),
    data_inicio: str = Query("", alias="data_inicio"),
    data_fim: str = Query("", alias="data_fim"),
    cliente_id: Optional[str] = Query("")
):
    query = db.query(ContaReceber).join(Cliente, isouter=True).filter(ContaReceber.status == StatusConta.PAGO)
    if forma_pagamento:
        query = query.filter(ContaReceber.forma_pagamento == forma_pagamento)
    if busca:
        query = query.filter(
            ContaReceber.descricao.ilike(f"%{busca}%") |
            Cliente.nome.ilike(f"%{busca}%")
        )
    if cliente_id and cliente_id.isdigit():
        query = query.filter(ContaReceber.cliente_id == int(cliente_id))
    if data_inicio:
        query = query.filter(ContaReceber.data_recebimento >= date.fromisoformat(data_inicio))
    if data_fim:
        query = query.filter(ContaReceber.data_recebimento <= date.fromisoformat(data_fim))
    recebimentos = query.order_by(ContaReceber.data_recebimento.desc()).all()
    total_valor = sum(r.valor for r in recebimentos)
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    return request.app.state.templates.TemplateResponse(
        "contas/recebimentos.html",
        {"request": request, "recebimentos": recebimentos, "clientes": clientes,
         "forma_pagamento": forma_pagamento, "busca": busca, "StatusConta": StatusConta,
         "data_inicio": data_inicio, "data_fim": data_fim, "total_valor": total_valor, "cliente_id": cliente_id}
    )


@router.get("/recebimentos/pdf")
def recebimentos_pdf(
    request: Request, db: Session = Depends(get_db),
    forma_pagamento: str = Query("", alias="forma_pagamento"),
    busca: str = Query("", alias="busca"),
    data_inicio: str = Query("", alias="data_inicio"),
    data_fim: str = Query("", alias="data_fim"),
    cliente_id: Optional[str] = Query("")
):
    query = db.query(ContaReceber).join(Cliente, isouter=True).filter(ContaReceber.status == StatusConta.PAGO)
    if forma_pagamento:
        query = query.filter(ContaReceber.forma_pagamento == forma_pagamento)
    if busca:
        query = query.filter(
            ContaReceber.descricao.ilike(f"%{busca}%") |
            Cliente.nome.ilike(f"%{busca}%")
        )
    if cliente_id and cliente_id.isdigit():
        query = query.filter(ContaReceber.cliente_id == int(cliente_id))
    if data_inicio:
        query = query.filter(ContaReceber.data_recebimento >= date.fromisoformat(data_inicio))
    if data_fim:
        query = query.filter(ContaReceber.data_recebimento <= date.fromisoformat(data_fim))
    recebimentos = query.order_by(ContaReceber.data_recebimento.desc()).all()
    total_valor = sum(r.valor for r in recebimentos)
    empresa = db.query(Empresa).first()
    return request.app.state.templates.TemplateResponse(
        "contas/recebimentos_imprimir.html",
        {"request": request, "recebimentos": recebimentos, "total_valor": total_valor, "empresa": empresa}
    )


# ─── CONTAS A RECEBER ─────────────────────────────────────────────

@router.get("/receber")
def listar_contas_receber(
    request: Request, db: Session = Depends(get_db),
    status_filtro: str = Query(""), busca: str = Query(""),
    data_inicio: str = Query(""), data_fim: str = Query("")
):
    query = db.query(ContaReceber).join(Cliente, isouter=True)
    if status_filtro:
        query = query.filter(ContaReceber.status == status_filtro)
    if busca:
        query = query.filter(
            ContaReceber.descricao.ilike(f"%{busca}%") |
            Cliente.nome.ilike(f"%{busca}%")
        )
    if data_inicio:
        query = query.filter(ContaReceber.data_vencimento >= date.fromisoformat(data_inicio))
    if data_fim:
        query = query.filter(ContaReceber.data_vencimento <= date.fromisoformat(data_fim))
    contas = query.order_by(ContaReceber.data_vencimento.desc()).all()
    total_valor = sum(c.valor for c in contas)
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    messages = get_messages(request)
    return request.app.state.templates.TemplateResponse(
        "contas/receber_listar.html",
        {"request": request, "contas": contas, "clientes": clientes,
         "status_filtro": status_filtro, "busca": busca, "StatusConta": StatusConta,
         "messages": messages, "data_inicio": data_inicio, "data_fim": data_fim,
         "total_valor": total_valor, "clientes_json": clientes_json}
    )


@router.get("/receber/pdf")
def relatorio_pdf_receber(
    request: Request, db: Session = Depends(get_db),
    status_filtro: str = Query(""), busca: str = Query(""),
    data_inicio: str = Query(""), data_fim: str = Query("")
):
    query = db.query(ContaReceber).join(Cliente, isouter=True)
    if status_filtro:
        query = query.filter(ContaReceber.status == status_filtro)
    if busca:
        query = query.filter(
            ContaReceber.descricao.ilike(f"%{busca}%") |
            Cliente.nome.ilike(f"%{busca}%")
        )
    if data_inicio:
        query = query.filter(ContaReceber.data_vencimento >= date.fromisoformat(data_inicio))
    if data_fim:
        query = query.filter(ContaReceber.data_vencimento <= date.fromisoformat(data_fim))
    contas = query.order_by(ContaReceber.data_vencimento.desc()).all()
    total_valor = sum(c.valor for c in contas)
    empresa = db.query(Empresa).first()
    return request.app.state.templates.TemplateResponse(
        "contas/receber_pdf.html",
        {"request": request, "contas": contas, "empresa": empresa,
         "total_valor": total_valor, "data_geracao": date.today()}
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


@router.get("/receber/{conta_id}/editar")
def editar_conta_receber_page(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if not conta:
        return RedirectResponse(url="/contas/receber", status_code=303)
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    return request.app.state.templates.TemplateResponse(
        "contas/_editar_conta_receber.html",
        {"request": request, "conta": conta, "clientes": clientes, "clientes_json": clientes_json}
    )


@router.post("/receber/{conta_id}/editar")
def editar_conta_receber(request: Request, conta_id: int, db: Session = Depends(get_db), cliente_id: int = Form(0), descricao: str = Form(...), valor: float = Form(...), data_vencimento: str = Form(None), observacao: str = Form("")):
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if conta:
        conta.cliente_id = cliente_id if cliente_id else None
        conta.descricao = descricao
        conta.valor = valor
        if data_vencimento:
            conta.data_vencimento = date.fromisoformat(data_vencimento)
        conta.observacao = observacao
        db.commit()
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.post("/receber/{conta_id}/excluir")
def excluir_conta_receber(request: Request, conta_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.senha_admin or senha != empresa.senha_admin:
        return JSONResponse({"erro": "Senha inválida"}, status_code=403)
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if conta:
        db.delete(conta)
        db.commit()
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.get("/receber/{conta_id}/imprimir-boleto")
def imprimir_boleto(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    empresa = db.query(Empresa).first()
    if not conta:
        return RedirectResponse(url="/contas/receber", status_code=303)
    return request.app.state.templates.TemplateResponse(
        "contas/imprimir_boleto.html",
        {"request": request, "conta": conta, "empresa": empresa}
    )
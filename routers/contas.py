from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse, Response
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func as sql_func, desc as sql_desc, asc as sql_asc, and_, or_
from datetime import datetime, date, timedelta
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import csv
import io
import logging

from database import get_db
from models import ContaPagar, ContaReceber, Fornecedor, Cliente, StatusConta, Empresa, TipoDocumento, PlanoDeContas
from app.core.security import verificar_admin, confirma_senha_usuario
from services.audit import registrar_auditoria

logger = logging.getLogger(__name__)


def to_decimal(v, default="0"):
    """Converte para Decimal com segurança; retorna default se vazio/inválido."""
    try:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return Decimal(str(default))
        return Decimal(str(v).replace(",", "."))
    except (ValueError, TypeError, InvalidOperation):
        return Decimal(str(default))


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contas", tags=["Contas"])


def get_messages(request: Request) -> list:
    messages = []
    if "message" in request.session:
        raw = request.session.pop("message")
        if isinstance(raw, dict):
            messages.append({"type": raw.get("tipo", "success"), "text": raw.get("texto", str(raw))})
        else:
            messages.append({"type": "success", "text": raw})
    if "error" in request.session:
        raw = request.session.pop("error")
        if isinstance(raw, dict):
            messages.append({"type": "error", "text": raw.get("texto", str(raw))})
        else:
            messages.append({"type": "error", "text": raw})
    return messages


def conta_vencida(conta, hoje: date = None) -> bool:
    if hoje is None:
        hoje = date.today()
    return (
        conta.data_vencimento is not None
        and conta.data_vencimento < hoje
        and conta.status in (StatusConta.PENDENTE, StatusConta.VENCIDO)
    )


@router.get("/pagar")
def contas_pagar(
    request: Request, db: Session = Depends(get_db),
    busca: str = Query(""), status_filtro: str = Query(""),
    data_inicio: str = Query(""), data_fim: str = Query(""),
    page: int = Query(1), per_page: int = Query(20),
    sort: str = Query("data_vencimento"), ordem: str = Query("asc"),
):
    hoje = date.today()
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    fornecedores_json = [{"id": f.id, "nome": f.nome} for f in fornecedores]
    query = db.query(ContaPagar).options(
        joinedload(ContaPagar.fornecedor),
        joinedload(ContaPagar.tipo_documento),
        joinedload(ContaPagar.plano_conta)
    )
    if status_filtro == "pendente":
        query = query.filter(ContaPagar.status == StatusConta.PENDENTE)
    elif status_filtro == "pago":
        query = query.filter(ContaPagar.status == StatusConta.PAGO)
    elif status_filtro == "vencido":
        query = query.filter(
            or_(
                ContaPagar.status == StatusConta.VENCIDO,
                and_(ContaPagar.status == StatusConta.PENDENTE, ContaPagar.data_vencimento < hoje)
            )
        )
    elif status_filtro == "cancelado":
        query = query.filter(ContaPagar.status == StatusConta.CANCELADO)
    else:
        query = query.filter(ContaPagar.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO]))
    if busca:
        query = query.filter(
            or_(
                ContaPagar.descricao.ilike(f"%{busca}%"),
                ContaPagar.fornecedor.has(Fornecedor.nome.ilike(f"%{busca}%"))
            )
        )
    if data_inicio:
        try:
            query = query.filter(ContaPagar.data_vencimento >= datetime.strptime(data_inicio, "%Y-%m-%d").date())
        except ValueError:
            request.session["message"] = {"tipo": "danger", "texto": "Data de início inválida. Use o formato AAAA-MM-DD."}
            return RedirectResponse(url="/contas/pagar", status_code=303)
    if data_fim:
        try:
            query = query.filter(ContaPagar.data_vencimento <= datetime.strptime(data_fim, "%Y-%m-%d").date())
        except ValueError:
            request.session["message"] = {"tipo": "danger", "texto": "Data de fim inválida. Use o formato AAAA-MM-DD."}
            return RedirectResponse(url="/contas/pagar", status_code=303)
    if sort == "fornecedor":
        query = query.outerjoin(Fornecedor, ContaPagar.fornecedor_id == Fornecedor.id)
    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    order_func = sql_desc if ordem == "desc" else sql_asc
    if sort == "fornecedor":
        contas = query.order_by(order_func(Fornecedor.nome), ContaPagar.id).offset(offset).limit(per_page).all()
    else:
        sort_col = getattr(ContaPagar, sort, ContaPagar.data_vencimento)
        contas = query.order_by(order_func(sort_col), ContaPagar.id).offset(offset).limit(per_page).all()
    total_pendente_valor = db.query(sql_func.coalesce(sql_func.sum(ContaPagar.valor), 0)).filter(
        ContaPagar.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    ).scalar() or 0
    tipos_documento = db.query(TipoDocumento).order_by(TipoDocumento.nome).all()
    planos_contas_receita = db.query(PlanoDeContas).filter(PlanoDeContas.tipo == "receita", PlanoDeContas.ativo == True).order_by(PlanoDeContas.codigo).all()
    planos_contas_despesa = db.query(PlanoDeContas).filter(PlanoDeContas.tipo == "despesa", PlanoDeContas.ativo == True).order_by(PlanoDeContas.codigo).all()
    return request.app.state.templates.TemplateResponse(request, 
        "contas/pagar.html",
        {"request": request, "contas": contas, "total_pendente": total_pendente_valor or 0,
         "fornecedores": fornecedores, "fornecedores_json": fornecedores_json, "messages": get_messages(request),
         "busca": busca, "status_filtro": status_filtro,
         "data_inicio": data_inicio, "data_fim": data_fim,
         "tipos_documento": tipos_documento, "planos_contas_receita": planos_contas_receita,
         "planos_contas_despesa": planos_contas_despesa,
         "page": page, "per_page": per_page, "total_pages": total_pages, "total_count": total_count,
         "sort": sort, "ordem": ordem}
    )


@router.get("/pagar/nova")
def nova_conta_pagar(request: Request, db: Session = Depends(get_db)):
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    tipos_documento = db.query(TipoDocumento).order_by(TipoDocumento.nome).all()
    planos_contas = db.query(PlanoDeContas).filter(PlanoDeContas.tipo == "despesa", PlanoDeContas.ativo == True).order_by(PlanoDeContas.codigo).all()
    return request.app.state.templates.TemplateResponse(request, 
        "contas/nova_pagar.html",
        {"request": request, "fornecedores": fornecedores, "fornecedores_json": fornecedores_json,
         "tipos_documento": tipos_documento, "planos_contas": planos_contas}
    )


@router.post("/pagar/nova")
def criar_conta_pagar(
    request: Request,
    db: Session = Depends(get_db),
    descricao: str = Form(...),
    valor: str = Form(...),
    data_vencimento: date = Form(...),
    fornecedor_id: Optional[int] = Form(None),
    observacao: Optional[str] = Form(None),
    numero_documento: Optional[str] = Form(None),
    tipo_documento_id: Optional[str] = Form(None),
    plano_conta_id: Optional[str] = Form(None),
    forma_pagamento: Optional[str] = Form(None)
):
    from sqlalchemy import func
    def to_int(v):
        try: return int(v) if v and v.strip() else None
        except (ValueError, TypeError, AttributeError):
            logger.debug(f"Falha ao converter para int: {v}")
            return None
    conta = ContaPagar(
        descricao=descricao,
        valor=to_decimal(valor),
        data_vencimento=data_vencimento,
        fornecedor_id=fornecedor_id,
        observacao=observacao,
        numero_documento=numero_documento,
        tipo_documento_id=to_int(tipo_documento_id),
        plano_conta_id=to_int(plano_conta_id),
        forma_pagamento=forma_pagamento,
        status=StatusConta.PENDENTE
    )
    db.add(conta)
    db.commit()
    request.session["message"] = "Conta a pagar criada com sucesso!"
    return RedirectResponse(url="/contas/pagar", status_code=303)


@router.get("/pagar/{conta_id}/editar-form")
def editar_conta_pagar_form(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaPagar).options(
        joinedload(ContaPagar.tipo_documento),
        joinedload(ContaPagar.plano_conta)
    ).filter(ContaPagar.id == conta_id).first()
    if not conta:
        return ""
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    tipos_documento = db.query(TipoDocumento).order_by(TipoDocumento.nome).all()
    planos_contas_despesa = db.query(PlanoDeContas).filter(PlanoDeContas.tipo == "despesa", PlanoDeContas.ativo == True).order_by(PlanoDeContas.codigo).all()
    return request.app.state.templates.TemplateResponse(request, 
        "contas/editar_pagar_form.html",
        {"request": request, "conta": conta, "fornecedores": fornecedores, "fornecedores_json": fornecedores_json,
         "tipos_documento": tipos_documento, "planos_contas": planos_contas_despesa}
    )


@router.post("/pagar/{conta_id}/editar")
def atualizar_conta_pagar(
    request: Request,
    conta_id: int,
    db: Session = Depends(get_db),
    descricao: str = Form(...),
    valor: str = Form(...),
    data_vencimento: date = Form(...),
    fornecedor_id: Optional[int] = Form(None),
    observacao: Optional[str] = Form(None),
    status: StatusConta = Form(...),
    numero_documento: Optional[str] = Form(None),
    tipo_documento_id: Optional[str] = Form(None),
    plano_conta_id: Optional[str] = Form(None),
    forma_pagamento: Optional[str] = Form(None)
):
    def to_int(v):
        try: return int(v) if v and v.strip() else None
        except (ValueError, TypeError, AttributeError):
            logger.debug(f"Falha ao converter para int: {v}")
            return None
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if not conta:
        request.session["error"] = "Conta não encontrada"
        return RedirectResponse(url="/contas/pagar", status_code=303)
    conta.descricao = descricao
    conta.valor = to_decimal(valor)
    conta.data_vencimento = data_vencimento
    conta.fornecedor_id = fornecedor_id
    conta.observacao = observacao
    conta.status = status
    conta.numero_documento = numero_documento
    conta.tipo_documento_id = to_int(tipo_documento_id)
    conta.plano_conta_id = to_int(plano_conta_id)
    conta.forma_pagamento = forma_pagamento
    db.commit()
    request.session["message"] = "Conta a pagar atualizada com sucesso!"
    return RedirectResponse(url="/contas/pagar", status_code=303)


@router.post("/pagar/{conta_id}/excluir")
def excluir_conta_pagar(request: Request, conta_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    if not confirma_senha_usuario(request, db, senha):
        return JSONResponse({"erro": "Senha inválida ou usuário não autorizado"}, status_code=403)
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if not conta:
        return JSONResponse({"erro": "Conta não encontrada"}, status_code=404)
    conta.status = StatusConta.EXCLUIDO
    db.commit()
    registrar_auditoria(
        db, request.session.get("user_id"), "excluir",
        "conta_pagar", conta_id, f"Conta: {conta.descricao}",
        request.client.host if request.client else None
    )
    return JSONResponse({"ok": True, "redirect": "/contas/pagar"})


@router.get("/receber")
def contas_receber(
    request: Request, db: Session = Depends(get_db),
    busca: str = Query(""), status_filtro: str = Query(""),
    data_inicio: str = Query(""), data_fim: str = Query(""),
    page: int = Query(1), per_page: int = Query(20),
    sort: str = Query("data_vencimento"), ordem: str = Query("asc"),
):
    from sqlalchemy import func
    hoje = date.today()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome} for c in clientes]
    query = db.query(ContaReceber).options(
        joinedload(ContaReceber.cliente),
        joinedload(ContaReceber.tipo_documento),
        joinedload(ContaReceber.plano_conta)
    )
    if status_filtro == "pendente":
        query = query.filter(ContaReceber.status == StatusConta.PENDENTE)
    elif status_filtro == "pago":
        query = query.filter(ContaReceber.status == StatusConta.PAGO)
    elif status_filtro == "vencido":
        query = query.filter(
            or_(
                ContaReceber.status == StatusConta.VENCIDO,
                and_(ContaReceber.status == StatusConta.PENDENTE, ContaReceber.data_vencimento < hoje)
            )
        )
    elif status_filtro == "cancelado":
        query = query.filter(ContaReceber.status == StatusConta.CANCELADO)
    elif status_filtro == "baixa_solicitada":
        query = query.filter(ContaReceber.status == StatusConta.BAIXA_SOLICITADA)
    else:
        query = query.filter(ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO, StatusConta.BAIXA_SOLICITADA]))
    if busca:
        query = query.filter(
            or_(
                ContaReceber.descricao.ilike(f"%{busca}%"),
                ContaReceber.cliente.has(Cliente.nome.ilike(f"%{busca}%"))
            )
        )
    if data_inicio:
        try:
            query = query.filter(ContaReceber.data_vencimento >= datetime.strptime(data_inicio, "%Y-%m-%d").date())
        except ValueError:
            request.session["message"] = {"tipo": "danger", "texto": "Data de início inválida. Use o formato AAAA-MM-DD."}
            return RedirectResponse(url="/contas/receber", status_code=303)
    if data_fim:
        try:
            query = query.filter(ContaReceber.data_vencimento <= datetime.strptime(data_fim, "%Y-%m-%d").date())
        except ValueError:
            request.session["message"] = {"tipo": "danger", "texto": "Data de fim inválida. Use o formato AAAA-MM-DD."}
            return RedirectResponse(url="/contas/receber", status_code=303)
    if sort == "cliente":
        query = query.outerjoin(Cliente, ContaReceber.cliente_id == Cliente.id)
    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    order_func = sql_desc if ordem == "desc" else sql_asc
    if sort == "cliente":
        contas = query.order_by(order_func(Cliente.nome), ContaReceber.id).offset(offset).limit(per_page).all()
    else:
        sort_col = getattr(ContaReceber, sort, ContaReceber.data_vencimento)
        contas = query.order_by(order_func(sort_col), ContaReceber.id).offset(offset).limit(per_page).all()
    total_pendente_valor = db.query(sql_func.coalesce(sql_func.sum(ContaReceber.valor), 0)).filter(
        ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    ).scalar() or 0
    tipos_documento = db.query(TipoDocumento).order_by(TipoDocumento.nome).all()
    planos_contas_receita = db.query(PlanoDeContas).filter(PlanoDeContas.tipo == "receita", PlanoDeContas.ativo == True).order_by(PlanoDeContas.codigo).all()
    planos_contas_despesa = db.query(PlanoDeContas).filter(PlanoDeContas.tipo == "despesa", PlanoDeContas.ativo == True).order_by(PlanoDeContas.codigo).all()
    return request.app.state.templates.TemplateResponse(request, 
        "contas/receber.html",
        {"request": request, "contas": contas, "total_pendente": total_pendente_valor or 0,
         "clientes": clientes, "clientes_json": clientes_json, "fornecedores_json": [], "messages": get_messages(request),
         "busca": busca, "status_filtro": status_filtro,
         "data_inicio": data_inicio, "data_fim": data_fim,
         "tipos_documento": tipos_documento, "planos_contas_receita": planos_contas_receita,
         "planos_contas_despesa": planos_contas_despesa,
         "page": page, "per_page": per_page, "total_pages": total_pages, "total_count": total_count,
         "sort": sort, "ordem": ordem}
    )


@router.get("/receber/nova")
def nova_conta_receber(request: Request, db: Session = Depends(get_db)):
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    tipos_documento = db.query(TipoDocumento).order_by(TipoDocumento.nome).all()
    planos_contas = db.query(PlanoDeContas).filter(PlanoDeContas.tipo == "receita", PlanoDeContas.ativo == True).order_by(PlanoDeContas.codigo).all()
    return request.app.state.templates.TemplateResponse(request, 
        "contas/nova_receber.html",
        {"request": request, "clientes": clientes, "clientes_json": clientes_json,
         "tipos_documento": tipos_documento, "planos_contas": planos_contas}
    )


@router.post("/receber/nova")
def criar_conta_receber(
    request: Request,
    db: Session = Depends(get_db),
    descricao: str = Form(...),
    valor: str = Form(...),
    data_vencimento: date = Form(...),
    cliente_id: Optional[int] = Form(None),
    observacao: Optional[str] = Form(None),
    numero_documento: Optional[str] = Form(None),
    tipo_documento_id: Optional[str] = Form(None),
    plano_conta_id: Optional[str] = Form(None),
    forma_pagamento: Optional[str] = Form(None)
):
    from sqlalchemy import func
    def to_int(v):
        try: return int(v) if v and v.strip() else None
        except (ValueError, TypeError, AttributeError):
            logger.debug(f"Falha ao converter para int: {v}")
            return None
    conta = ContaReceber(
        descricao=descricao,
        valor=to_decimal(valor),
        data_vencimento=data_vencimento,
        cliente_id=cliente_id,
        observacao=observacao,
        numero_documento=numero_documento,
        tipo_documento_id=to_int(tipo_documento_id),
        plano_conta_id=to_int(plano_conta_id),
        forma_pagamento=forma_pagamento,
        status=StatusConta.PENDENTE
    )
    db.add(conta)
    db.commit()
    request.session["message"] = "Conta a receber criada com sucesso!"
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.get("/receber/{conta_id}/editar-form")
def editar_conta_receber_form(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaReceber).options(
        joinedload(ContaReceber.tipo_documento),
        joinedload(ContaReceber.plano_conta)
    ).filter(ContaReceber.id == conta_id).first()
    if not conta:
        return ""
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    tipos_documento = db.query(TipoDocumento).order_by(TipoDocumento.nome).all()
    planos_contas_receita = db.query(PlanoDeContas).filter(PlanoDeContas.tipo == "receita", PlanoDeContas.ativo == True).order_by(PlanoDeContas.codigo).all()
    return request.app.state.templates.TemplateResponse(request, 
        "contas/editar_receber_form.html",
        {"request": request, "conta": conta, "clientes": clientes, "clientes_json": clientes_json,
         "tipos_documento": tipos_documento, "planos_contas": planos_contas_receita}
    )


@router.post("/receber/{conta_id}/editar")
def atualizar_conta_receber(
    request: Request,
    conta_id: int,
    db: Session = Depends(get_db),
    descricao: str = Form(...),
    valor: str = Form(...),
    data_vencimento: date = Form(...),
    cliente_id: Optional[int] = Form(None),
    observacao: Optional[str] = Form(None),
    status: StatusConta = Form(...),
    numero_documento: Optional[str] = Form(None),
    tipo_documento_id: Optional[str] = Form(None),
    plano_conta_id: Optional[str] = Form(None),
    forma_pagamento: Optional[str] = Form(None)
):
    def to_int(v):
        try: return int(v) if v and v.strip() else None
        except (ValueError, TypeError, AttributeError):
            logger.debug(f"Falha ao converter para int: {v}")
            return None
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if not conta:
        request.session["error"] = "Conta não encontrada"
        return RedirectResponse(url="/contas/receber", status_code=303)
    conta.descricao = descricao
    conta.valor = to_decimal(valor)
    conta.data_vencimento = data_vencimento
    conta.cliente_id = cliente_id
    conta.observacao = observacao
    conta.status = status
    conta.numero_documento = numero_documento
    conta.tipo_documento_id = to_int(tipo_documento_id)
    conta.plano_conta_id = to_int(plano_conta_id)
    conta.forma_pagamento = forma_pagamento
    db.commit()
    request.session["message"] = "Conta a receber atualizada com sucesso!"
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.post("/receber/{conta_id}/excluir")
def excluir_conta_receber(request: Request, conta_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    if not confirma_senha_usuario(request, db, senha):
        return JSONResponse({"erro": "Senha inválida ou usuário não autorizado"}, status_code=403)
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if not conta:
        return JSONResponse({"erro": "Conta não encontrada"}, status_code=404)
    conta.status = StatusConta.EXCLUIDO
    db.commit()
    registrar_auditoria(
        db, request.session.get("user_id"), "excluir",
        "conta_receber", conta_id, f"Conta: {conta.descricao}",
        request.client.host if request.client else None
    )
    return JSONResponse({"ok": True, "redirect": "/contas/receber"})


@router.post("/pagar/{conta_id}/baixar")
def baixar_conta_pagar(
    request: Request, conta_id: int, db: Session = Depends(get_db),
    valor_pago: str = Form(""), juros: str = Form(""), desconto: str = Form(""),
    data_pagamento: str = Form(""), senha: str = Form(""),
):
    if not confirma_senha_usuario(request, db, senha):
        request.session["error"] = "Senha inválida ou usuário não autorizado"
        return RedirectResponse(url="/contas/pagar", status_code=303)
    conta = db.query(ContaPagar).filter(ContaPagar.id == conta_id).first()
    if conta:
        if conta.status not in (StatusConta.PENDENTE, StatusConta.VENCIDO):
            request.session["error"] = "Apenas contas pendentes ou vencidas podem ser pagas"
            return RedirectResponse(url="/contas/pagar", status_code=303)
        def _num(v):
            try:
                return Decimal(str(v).replace(",", ".")) if v not in (None, "") else Decimal("0")
            except Exception:
                return Decimal("0")
        v_pago = _num(valor_pago) if valor_pago not in (None, "") else conta.valor
        v_juros = _num(juros)
        v_desc = _num(desconto)
        conta.valor_juros = v_juros
        conta.valor_desconto = v_desc
        conta.valor_total = v_pago + v_juros - v_desc
        conta.status = StatusConta.PAGO
        conta.data_pagamento = date.fromisoformat(data_pagamento) if data_pagamento else date.today()
        db.commit()
        request.session["message"] = "Conta paga com sucesso!"
    else:
        request.session["error"] = "Conta não encontrada"
    return RedirectResponse(url="/contas/pagar", status_code=303)


@router.post("/receber/{conta_id}/baixar")
def baixar_conta_receber(
    request: Request, conta_id: int, db: Session = Depends(get_db),
    valor_recebido: str = Form(""), juros: str = Form(""), desconto: str = Form(""),
    data_recebimento: str = Form(""), senha: str = Form(""),
):
    if not confirma_senha_usuario(request, db, senha):
        request.session["error"] = "Senha inválida ou usuário não autorizado"
        return RedirectResponse(url="/contas/receber", status_code=303)
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if conta:
        if conta.status not in (StatusConta.PENDENTE, StatusConta.VENCIDO):
            request.session["error"] = "Apenas contas pendentes ou vencidas podem ser recebidas"
            return RedirectResponse(url="/contas/receber", status_code=303)
        def _num(v):
            try:
                return Decimal(str(v).replace(",", ".")) if v not in (None, "") else Decimal("0")
            except Exception:
                return Decimal("0")
        v_rec = _num(valor_recebido) if valor_recebido not in (None, "") else conta.valor
        v_juros = _num(juros)
        v_desc = _num(desconto)
        conta.valor_juros = v_juros
        conta.valor_desconto = v_desc
        conta.valor_total = v_rec + v_juros - v_desc
        conta.status = StatusConta.PAGO
        conta.data_recebimento = date.fromisoformat(data_recebimento) if data_recebimento else date.today()
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
        conta.valor_juros = Decimal("0")
        conta.valor_desconto = Decimal("0")
        conta.valor_total = None
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
        conta.valor_juros = Decimal("0")
        conta.valor_desconto = Decimal("0")
        conta.valor_total = None
        db.commit()
        request.session["message"] = "Baixa estornada - conta reaberta!"
    else:
        request.session["error"] = "Conta não encontrada ou não está recebida"
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.get("/pagar/pdf")
def contas_pagar_pdf(request: Request, db: Session = Depends(get_db)):
    from services.nfse_pdf import gerar_pdf_contas
    empresa = db.query(Empresa).first()
    contas = db.query(ContaPagar).options(joinedload(ContaPagar.fornecedor)).filter(
        ContaPagar.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    ).order_by(ContaPagar.data_vencimento).all()
    pdf_bytes = gerar_pdf_contas(contas, empresa, tipo="pagar", filtros="Filtro: Em aberto (pendente/vencido)")
    from fastapi.responses import Response
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=contas_pagar.pdf"})


@router.get("/pagar/{conta_id}")
def ver_conta_pagar(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaPagar).options(
        joinedload(ContaPagar.fornecedor),
        joinedload(ContaPagar.tipo_documento),
        joinedload(ContaPagar.plano_conta)
    ).filter(ContaPagar.id == conta_id).first()
    if not conta:
        request.session["error"] = "Conta não encontrada"
        return RedirectResponse(url="/contas/pagar", status_code=303)
    empresa = db.query(Empresa).first()
    return request.app.state.templates.TemplateResponse(request, 
        "contas/ver_pagar.html",
        {"request": request, "conta": conta, "empresa": empresa}
    )


@router.get("/receber/pdf")
def contas_receber_pdf(request: Request, db: Session = Depends(get_db)):
    from services.nfse_pdf import gerar_pdf_contas
    empresa = db.query(Empresa).first()
    contas = db.query(ContaReceber).options(joinedload(ContaReceber.cliente)).filter(
        ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    ).order_by(ContaReceber.data_vencimento).all()
    pdf_bytes = gerar_pdf_contas(contas, empresa, tipo="receber", filtros="Filtro: Em aberto (pendente/vencido)")
    from fastapi.responses import Response
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=contas_receber.pdf"})


@router.get("/receber/{conta_id}")
def ver_conta_receber(request: Request, conta_id: int, db: Session = Depends(get_db)):
    conta = db.query(ContaReceber).options(
        joinedload(ContaReceber.cliente),
        joinedload(ContaReceber.tipo_documento),
        joinedload(ContaReceber.plano_conta)
    ).filter(ContaReceber.id == conta_id).first()
    if not conta:
        request.session["error"] = "Conta não encontrada"
        return RedirectResponse(url="/contas/receber", status_code=303)
    empresa = db.query(Empresa).first()
    return request.app.state.templates.TemplateResponse(request, 
        "contas/ver_receber.html",
        {"request": request, "conta": conta, "empresa": empresa}
    )


@router.get("/previsao-recebimentos")
def previsao_recebimentos(
    request: Request, db: Session = Depends(get_db), dias: int = 30,
    sort: str = Query("data_vencimento"), ordem: str = Query("asc"),
):
    hoje = date.today()
    data_limite = hoje + timedelta(days=dias)
    query = db.query(ContaReceber).options(joinedload(ContaReceber.cliente)).filter(
        ContaReceber.data_vencimento >= hoje,
        ContaReceber.data_vencimento <= data_limite,
        ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    )
    if sort == "cliente":
        query = query.outerjoin(Cliente, ContaReceber.cliente_id == Cliente.id)
    order_func = sql_desc if ordem == "desc" else sql_asc
    if sort == "cliente":
        contas = query.order_by(order_func(Cliente.nome), ContaReceber.id).all()
    else:
        sort_col = getattr(ContaReceber, sort, ContaReceber.data_vencimento)
        contas = query.order_by(order_func(sort_col), ContaReceber.id).all()
    total_previsto = sum(c.valor for c in contas)
    return request.app.state.templates.TemplateResponse(request, 
        "contas/previsao.html",
        {"request": request, "contas": contas, "total_previsto": total_previsto, "hoje": hoje, "dias": dias,
         "sort": sort, "ordem": ordem}
    )


@router.get("/inadimplencia")
def inadimplencia(
    request: Request, db: Session = Depends(get_db), dias: int = 0,
    sort: str = Query("data_vencimento"), ordem: str = Query("desc"),
):
    hoje = date.today()
    query = db.query(ContaReceber).options(joinedload(ContaReceber.cliente)).filter(
        ContaReceber.data_vencimento < hoje,
        ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    )
    if dias > 0:
        data_inicio = hoje - timedelta(days=dias)
        query = query.filter(ContaReceber.data_vencimento >= data_inicio)
    if sort == "cliente":
        query = query.outerjoin(Cliente, ContaReceber.cliente_id == Cliente.id)
    order_func = sql_desc if ordem == "desc" else sql_asc
    if sort == "cliente":
        contas = query.order_by(order_func(Cliente.nome), ContaReceber.id).all()
    else:
        sort_col = getattr(ContaReceber, sort, ContaReceber.data_vencimento)
        contas = query.order_by(order_func(sort_col), ContaReceber.id).all()
    total_inadimplente = sum(c.valor for c in contas)
    return request.app.state.templates.TemplateResponse(request, 
        "contas/inadimplencia.html",
        {"request": request, "contas": contas, "total_inadimplente": total_inadimplente, "hoje": hoje, "dias": dias,
         "sort": sort, "ordem": ordem}
    )


@router.get("/dre")
def dre(
    request: Request, db: Session = Depends(get_db),
    data_inicio: str = Query(""), data_fim: str = Query("")
):
    from collections import namedtuple
    from sqlalchemy import and_
    hoje = date.today()
    if not data_inicio:
        data_inicio = date(hoje.year, hoje.month, 1).isoformat()
    if not data_fim:
        data_fim = hoje.isoformat()
    try:
        di = datetime.strptime(data_inicio, "%Y-%m-%d").date()
        df = datetime.strptime(data_fim, "%Y-%m-%d").date()
    except ValueError:
        request.session["message"] = {"tipo": "danger", "texto": "Formato de data inválido. Use AAAA-MM-DD."}
        return RedirectResponse(url="/contas/dre", status_code=303)

    DRELine = namedtuple("DRELine", ["codigo", "nome", "nivel", "parent_id"])

    receitas = db.query(
        PlanoDeContas, sql_func.coalesce(sql_func.sum(ContaReceber.valor), 0)
    ).outerjoin(ContaReceber, and_(
        ContaReceber.plano_conta_id == PlanoDeContas.id,
        ContaReceber.status == StatusConta.PAGO,
        ContaReceber.data_recebimento >= di,
        ContaReceber.data_recebimento <= df
    )).filter(
        PlanoDeContas.tipo == "receita", PlanoDeContas.ativo == True
    ).group_by(PlanoDeContas.id, PlanoDeContas.codigo, PlanoDeContas.nome, PlanoDeContas.nivel, PlanoDeContas.parent_id).order_by(PlanoDeContas.codigo).all()

    despesas = db.query(
        PlanoDeContas, sql_func.coalesce(sql_func.sum(ContaPagar.valor), 0)
    ).outerjoin(ContaPagar, and_(
        ContaPagar.plano_conta_id == PlanoDeContas.id,
        ContaPagar.status == StatusConta.PAGO,
        ContaPagar.data_pagamento >= di,
        ContaPagar.data_pagamento <= df
    )).filter(
        PlanoDeContas.tipo == "despesa", PlanoDeContas.ativo == True
    ).group_by(PlanoDeContas.id, PlanoDeContas.codigo, PlanoDeContas.nome, PlanoDeContas.nivel, PlanoDeContas.parent_id).order_by(PlanoDeContas.codigo).all()

    # Include unclassified (plano_conta_id=NULL) accounts so DRE always reconciles
    receitas_sem_class = db.query(sql_func.coalesce(sql_func.sum(ContaReceber.valor), 0)).filter(
        ContaReceber.status == StatusConta.PAGO,
        ContaReceber.data_recebimento >= di,
        ContaReceber.data_recebimento <= df,
        ContaReceber.plano_conta_id.is_(None)
    ).scalar() or 0.0

    despesas_sem_class = db.query(sql_func.coalesce(sql_func.sum(ContaPagar.valor), 0)).filter(
        ContaPagar.status == StatusConta.PAGO,
        ContaPagar.data_pagamento >= di,
        ContaPagar.data_pagamento <= df,
        ContaPagar.plano_conta_id.is_(None)
    ).scalar() or 0.0

    if receitas_sem_class > 0:
        receitas.append((DRELine("--", "Sem Classificação", 0, None), receitas_sem_class))
    if despesas_sem_class > 0:
        despesas.append((DRELine("--", "Sem Classificação", 0, None), despesas_sem_class))

    total_receitas = sum(r[1] for r in receitas)
    total_despesas = sum(d[1] for d in despesas)
    saldo = total_receitas - total_despesas

    return request.app.state.templates.TemplateResponse(request, 
        "contas/dre.html",
        {"request": request, "receitas": receitas, "despesas": despesas,
         "total_receitas": total_receitas, "total_despesas": total_despesas,
         "saldo": saldo, "data_inicio": data_inicio, "data_fim": data_fim}
    )


@router.get("/pagar/exportar")
def exportar_contas_pagar(
    request: Request, db: Session = Depends(get_db),
    busca: str = Query(""), status_filtro: str = Query(""),
    data_inicio: str = Query(""), data_fim: str = Query(""),
    sort: str = Query("data_vencimento"), ordem: str = Query("asc"),
):
    query = db.query(ContaPagar).options(
        joinedload(ContaPagar.fornecedor),
    )
    if status_filtro == "pendente":
        query = query.filter(ContaPagar.status == StatusConta.PENDENTE)
    elif status_filtro == "pago":
        query = query.filter(ContaPagar.status == StatusConta.PAGO)
    elif status_filtro == "vencido":
        query = query.filter(ContaPagar.status == StatusConta.VENCIDO)
    elif status_filtro == "cancelado":
        query = query.filter(ContaPagar.status == StatusConta.CANCELADO)
    elif status_filtro == "baixa_solicitada":
        query = query.filter(ContaPagar.status == StatusConta.BAIXA_SOLICITADA)
    else:
        query = query.filter(ContaPagar.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO, StatusConta.BAIXA_SOLICITADA]))
    if busca:
        query = query.filter(
            or_(
                ContaPagar.descricao.ilike(f"%{busca}%"),
                ContaPagar.fornecedor.has(Fornecedor.nome.ilike(f"%{busca}%"))
            )
        )
    if data_inicio:
        try:
            query = query.filter(ContaPagar.data_vencimento >= datetime.strptime(data_inicio, "%Y-%m-%d").date())
        except ValueError:
            logger.warning(f"Data de início inválida no filtro de exportação: {data_inicio}")
    if data_fim:
        try:
            query = query.filter(ContaPagar.data_vencimento <= datetime.strptime(data_fim, "%Y-%m-%d").date())
        except ValueError:
            logger.warning(f"Data de fim inválida no filtro de exportação: {data_fim}")
    order_func = sql_desc if ordem == "desc" else sql_asc
    sort_col = getattr(ContaPagar, sort, ContaPagar.data_vencimento)
    contas = query.order_by(order_func(sort_col), ContaPagar.id).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Descricao", "Fornecedor", "Valor", "Vencimento", "Pagamento", "Status"])
    for c in contas:
        writer.writerow([
            c.descricao,
            c.fornecedor.nome if c.fornecedor else "-",
            f"R$ {c.valor:.2f}".replace(".", ","),
            str(c.data_vencimento),
            str(c.data_pagamento) if c.data_pagamento else "-",
            c.status.name.lower() if hasattr(c.status, 'name') else str(c.status),
        ])
    csv_content = output.getvalue()
    output.close()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=contas_pagar.csv"}
    )


@router.get("/receber/exportar")
def exportar_contas_receber(
    request: Request, db: Session = Depends(get_db),
    busca: str = Query(""), status_filtro: str = Query(""),
    data_inicio: str = Query(""), data_fim: str = Query(""),
    sort: str = Query("data_vencimento"), ordem: str = Query("asc"),
):
    query = db.query(ContaReceber).options(
        joinedload(ContaReceber.cliente),
    )
    if status_filtro == "pendente":
        query = query.filter(ContaReceber.status == StatusConta.PENDENTE)
    elif status_filtro == "pago":
        query = query.filter(ContaReceber.status == StatusConta.PAGO)
    elif status_filtro == "vencido":
        query = query.filter(ContaReceber.status == StatusConta.VENCIDO)
    elif status_filtro == "cancelado":
        query = query.filter(ContaReceber.status == StatusConta.CANCELADO)
    elif status_filtro == "baixa_solicitada":
        query = query.filter(ContaReceber.status == StatusConta.BAIXA_SOLICITADA)
    else:
        query = query.filter(ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO, StatusConta.BAIXA_SOLICITADA]))
    if busca:
        query = query.filter(
            or_(
                ContaReceber.descricao.ilike(f"%{busca}%"),
                ContaReceber.cliente.has(Cliente.nome.ilike(f"%{busca}%"))
            )
        )
    if data_inicio:
        try:
            query = query.filter(ContaReceber.data_vencimento >= datetime.strptime(data_inicio, "%Y-%m-%d").date())
        except ValueError:
            logger.warning(f"Data de início inválida no filtro de exportação: {data_inicio}")
    if data_fim:
        try:
            query = query.filter(ContaReceber.data_vencimento <= datetime.strptime(data_fim, "%Y-%m-%d").date())
        except ValueError:
            logger.warning(f"Data de fim inválida no filtro de exportação: {data_fim}")
    order_func = sql_desc if ordem == "desc" else sql_asc
    sort_col = getattr(ContaReceber, sort, ContaReceber.data_vencimento)
    contas = query.order_by(order_func(sort_col), ContaReceber.id).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Descricao", "Cliente", "Valor", "Vencimento", "Recebimento", "Status"])
    for c in contas:
        writer.writerow([
            c.descricao,
            c.cliente.nome if c.cliente else "-",
            f"R$ {c.valor:.2f}".replace(".", ","),
            str(c.data_vencimento),
            str(c.data_recebimento) if c.data_recebimento else "-",
            c.status.name.lower() if hasattr(c.status, 'name') else str(c.status),
        ])
    csv_content = output.getvalue()
    output.close()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=contas_receber.csv"}
    )
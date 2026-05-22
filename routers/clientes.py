from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date

from database import get_db
from models import Cliente, ContaReceber, Assinatura, OrdemServico


def _proximo_codigo_cliente(db: Session) -> str:
    ultimo = db.query(func.max(Cliente.codigo)).scalar()
    if ultimo:
        try:
            num = int(ultimo.split("-")[1]) + 1
        except (IndexError, ValueError):
            num = int(ultimo) + 1
    else:
        num = 1
    return f"CLI-{num:04d}"

router = APIRouter(prefix="/clientes", tags=["Clientes"])


@router.get("/")
def listar_clientes(request: Request, db: Session = Depends(get_db), busca: str = Query(""), situacao: str = Query("")):
    query = db.query(Cliente)
    if busca:
        query = query.filter(
            Cliente.nome.ilike(f"%{busca}%") | Cliente.cpf_cnpj.ilike(f"%{busca}%")
        )
    if situacao:
        query = query.filter(Cliente.situacao == situacao)
    clientes = query.order_by(Cliente.nome).all()
    return request.app.state.templates.TemplateResponse(
        "clientes/listar.html",
        {"request": request, "clientes": clientes, "busca": busca, "filtro_situacao": situacao}
    )


@router.get("/novo")
def novo_cliente(request: Request):
    return request.app.state.templates.TemplateResponse(
        "clientes/form.html",
        {"request": request, "cliente": None}
    )


@router.post("/novo")
def criar_cliente(
    request: Request,
    db: Session = Depends(get_db),
    nome: str = Form(...),
    cpf_cnpj: str = Form(""),
    tipo_pessoa: str = Form(""),
    email: str = Form(""),
    telefone: str = Form(""),
    celular: str = Form(""),
    endereco: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    estado: str = Form(""),
    cep: str = Form(""),
    contato: str = Form(""),
    fantasia: str = Form(""),
    inscricao_estadual: str = Form(""),
    inscricao_municipal: str = Form(""),
    situacao: str = Form("A"),
    data_cadastro: str = Form(""),
    observacao: str = Form(""),
):
    if data_cadastro:
        created_at = datetime.strptime(data_cadastro, "%Y-%m-%d")
    else:
        created_at = datetime.now()
    cliente = Cliente(
        codigo=_proximo_codigo_cliente(db),
        nome=nome, cpf_cnpj=cpf_cnpj, tipo_pessoa=tipo_pessoa,
        email=email, telefone=telefone,
        celular=celular, endereco=endereco, bairro=bairro, cidade=cidade,
        estado=estado, cep=cep, contato=contato, fantasia=fantasia,
        inscricao_estadual=inscricao_estadual, inscricao_municipal=inscricao_municipal,
        situacao=situacao, observacao=observacao, created_at=created_at,
        bling_pending_sync=True
    )
    db.add(cliente)
    db.commit()
    return RedirectResponse(url="/clientes", status_code=303)


@router.get("/{cliente_id}")
def detalhe_cliente(request: Request, cliente_id: int, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        return RedirectResponse(url="/clientes", status_code=303)
    contas = db.query(ContaReceber).filter(ContaReceber.cliente_id == cliente_id).order_by(ContaReceber.data_vencimento.desc()).all()
    assinaturas = db.query(Assinatura).filter(Assinatura.cliente_id == cliente_id).all()
    ordens = db.query(OrdemServico).filter(OrdemServico.cliente_id == cliente_id).order_by(OrdemServico.data_entrada.desc()).all()
    return request.app.state.templates.TemplateResponse(
        "clientes/detalhe.html",
        {"request": request, "cliente": cliente, "contas": contas, "assinaturas": assinaturas, "ordens": ordens}
    )


@router.get("/{cliente_id}/editar")
def editar_cliente(request: Request, cliente_id: int, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        return RedirectResponse(url="/clientes", status_code=303)
    return request.app.state.templates.TemplateResponse(
        "clientes/form.html",
        {"request": request, "cliente": cliente}
    )


@router.post("/{cliente_id}/editar")
def atualizar_cliente(
    request: Request,
    cliente_id: int,
    db: Session = Depends(get_db),
    nome: str = Form(...),
    cpf_cnpj: str = Form(""),
    email: str = Form(""),
    telefone: str = Form(""),
    celular: str = Form(""),
    endereco: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    estado: str = Form(""),
    cep: str = Form(""),
    contato: str = Form(""),
    fantasia: str = Form(""),
    inscricao_estadual: str = Form(""),
    inscricao_municipal: str = Form(""),
    situacao: str = Form("A"),
    tipo_pessoa: str = Form(""),
    data_cadastro: str = Form(""),
    observacao: str = Form(""),
):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        return RedirectResponse(url="/clientes", status_code=303)
    cliente.nome = nome
    cliente.cpf_cnpj = cpf_cnpj
    cliente.tipo_pessoa = tipo_pessoa
    cliente.email = email
    cliente.telefone = telefone
    cliente.celular = celular
    cliente.endereco = endereco
    cliente.bairro = bairro
    cliente.cidade = cidade
    cliente.estado = estado
    cliente.cep = cep
    cliente.contato = contato
    cliente.fantasia = fantasia
    cliente.inscricao_estadual = inscricao_estadual
    cliente.inscricao_municipal = inscricao_municipal
    cliente.situacao = situacao
    if data_cadastro:
        cliente.created_at = datetime.strptime(data_cadastro, "%Y-%m-%d")
    cliente.observacao = observacao
    cliente.updated_at = datetime.now()
    cliente.bling_pending_sync = True
    db.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}", status_code=303)


@router.get("/{cliente_id}/excluir")
def excluir_cliente(request: Request, cliente_id: int, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if cliente:
        db.delete(cliente)
        db.commit()
    return RedirectResponse(url="/clientes", status_code=303)

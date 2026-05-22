from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime

from database import get_db
from models import Fornecedor, ContaPagar


def _proximo_codigo_fornecedor(db: Session) -> str:
    ultimo = db.query(func.max(Fornecedor.codigo)).scalar()
    if ultimo:
        try:
            num = int(ultimo.split("-")[1]) + 1
        except (IndexError, ValueError):
            num = int(ultimo) + 1
    else:
        num = 1
    return f"FOR-{num:04d}"


router = APIRouter(prefix="/fornecedores", tags=["Fornecedores"])


@router.get("/")
def listar_fornecedores(request: Request, db: Session = Depends(get_db), busca: str = Query(""), situacao: str = Query("")):
    query = db.query(Fornecedor)
    if busca:
        query = query.filter(
            Fornecedor.nome.ilike(f"%{busca}%") | Fornecedor.cpf_cnpj.ilike(f"%{busca}%")
        )
    if situacao:
        query = query.filter(Fornecedor.situacao == situacao)
    fornecedores = query.order_by(Fornecedor.nome).all()
    return request.app.state.templates.TemplateResponse(
        "fornecedores/listar.html",
        {"request": request, "fornecedores": fornecedores, "busca": busca, "filtro_situacao": situacao}
    )


@router.get("/novo")
def novo_fornecedor(request: Request):
    return request.app.state.templates.TemplateResponse(
        "fornecedores/form.html",
        {"request": request, "fornecedor": None}
    )


@router.post("/novo")
def criar_fornecedor(
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
    fornecedor = Fornecedor(
        codigo=_proximo_codigo_fornecedor(db),
        nome=nome, cpf_cnpj=cpf_cnpj, tipo_pessoa=tipo_pessoa,
        email=email, telefone=telefone,
        celular=celular, endereco=endereco, bairro=bairro, cidade=cidade,
        estado=estado, cep=cep, contato=contato, fantasia=fantasia,
        inscricao_estadual=inscricao_estadual, inscricao_municipal=inscricao_municipal,
        situacao=situacao, observacao=observacao, created_at=created_at,
        bling_pending_sync=True
    )
    db.add(fornecedor)
    db.commit()
    return RedirectResponse(url="/fornecedores", status_code=303)


@router.get("/{fornecedor_id}")
def detalhe_fornecedor(request: Request, fornecedor_id: int, db: Session = Depends(get_db)):
    fornecedor = db.query(Fornecedor).filter(Fornecedor.id == fornecedor_id).first()
    if not fornecedor:
        return RedirectResponse(url="/fornecedores", status_code=303)
    contas = db.query(ContaPagar).filter(ContaPagar.fornecedor_id == fornecedor_id).order_by(ContaPagar.data_vencimento.desc()).all()
    return request.app.state.templates.TemplateResponse(
        "fornecedores/detalhe.html",
        {"request": request, "fornecedor": fornecedor, "contas": contas}
    )


@router.get("/{fornecedor_id}/editar")
def editar_fornecedor(request: Request, fornecedor_id: int, db: Session = Depends(get_db)):
    fornecedor = db.query(Fornecedor).filter(Fornecedor.id == fornecedor_id).first()
    if not fornecedor:
        return RedirectResponse(url="/fornecedores", status_code=303)
    return request.app.state.templates.TemplateResponse(
        "fornecedores/form.html",
        {"request": request, "fornecedor": fornecedor}
    )


@router.post("/{fornecedor_id}/editar")
def atualizar_fornecedor(
    request: Request,
    fornecedor_id: int,
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
    fornecedor = db.query(Fornecedor).filter(Fornecedor.id == fornecedor_id).first()
    if not fornecedor:
        return RedirectResponse(url="/fornecedores", status_code=303)
    fornecedor.nome = nome
    fornecedor.cpf_cnpj = cpf_cnpj
    fornecedor.tipo_pessoa = tipo_pessoa
    fornecedor.email = email
    fornecedor.telefone = telefone
    fornecedor.celular = celular
    fornecedor.endereco = endereco
    fornecedor.bairro = bairro
    fornecedor.cidade = cidade
    fornecedor.estado = estado
    fornecedor.cep = cep
    fornecedor.contato = contato
    fornecedor.fantasia = fantasia
    fornecedor.inscricao_estadual = inscricao_estadual
    fornecedor.inscricao_municipal = inscricao_municipal
    fornecedor.situacao = situacao
    if data_cadastro:
        fornecedor.created_at = datetime.strptime(data_cadastro, "%Y-%m-%d")
    fornecedor.observacao = observacao
    fornecedor.updated_at = datetime.now()
    fornecedor.bling_pending_sync = True
    db.commit()
    return RedirectResponse(url=f"/fornecedores/{fornecedor_id}", status_code=303)


@router.get("/{fornecedor_id}/excluir")
def excluir_fornecedor(request: Request, fornecedor_id: int, db: Session = Depends(get_db)):
    fornecedor = db.query(Fornecedor).filter(Fornecedor.id == fornecedor_id).first()
    if fornecedor:
        db.delete(fornecedor)
        db.commit()
    return RedirectResponse(url="/fornecedores", status_code=303)

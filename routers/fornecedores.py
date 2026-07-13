from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models import Fornecedor, ContaPagar, Empresa


def _proximo_codigo_fornecedor(db: Session) -> str:
    codigos = db.query(Fornecedor.codigo).filter(Fornecedor.codigo.isnot(None)).all()
    codigos = [c[0] for c in codigos if c[0]]
    
    usados = set()
    for c in codigos:
        try:
            num = int(c.split("-")[1])
            usados.add(num)
        except (IndexError, ValueError):
            continue
    
    max_num = max(usados) if usados else 0
    
    for i in range(1, max_num + 2):
        if i not in usados:
            return f"FOR-{i:04d}"
    
    return f"FOR-{max_num + 1:04d}"


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


@router.get("/buscar")
def buscar_fornecedores(request: Request, db: Session = Depends(get_db), q: str = Query("")):
    if not q or len(q) < 2:
        return {"fornecedores": []}
    query = db.query(Fornecedor).filter(Fornecedor.nome.ilike(f"%{q}%") | Fornecedor.cpf_cnpj.ilike(f"%{q}%") | Fornecedor.fantasia.ilike(f"%{q}%"))
    fornecedores = query.order_by(Fornecedor.nome).limit(20).all()
    return {"fornecedores": [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]}

@router.get("/novo")
def novo_fornecedor(request: Request, db: Session = Depends(get_db)):
    return request.app.state.templates.TemplateResponse(
        "fornecedores/form.html",
        {"request": request, "fornecedor": None, "proximo_codigo": _proximo_codigo_fornecedor(db)}
    )


@router.post("/novo")
def criar_fornecedor(
    request: Request,
    db: Session = Depends(get_db),
    codigo: str = Form(""),
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
    if not codigo:
        codigo = _proximo_codigo_fornecedor(db)
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


@router.post("/{fornecedor_id}/excluir")
def excluir_fornecedor(request: Request, fornecedor_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.senha_admin or senha != empresa.senha_admin:
        return JSONResponse({"success": False, "error": "Senha inválida"}, status_code=403)
    fornecedor = db.query(Fornecedor).filter(Fornecedor.id == fornecedor_id).first()
    if fornecedor:
        db.delete(fornecedor)
        db.commit()
        return {"success": True, "redirect": "/fornecedores"}
    return {"success": False, "error": "Fornecedor não encontrado"}

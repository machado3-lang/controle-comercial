from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date

from database import get_db
from models import Cliente, ContaReceber, Assinatura, OrdemServico, Empresa


def _proximo_codigo_cliente(db: Session) -> str:
    codigos = db.query(Cliente.codigo).filter(Cliente.codigo.isnot(None)).all()
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
            return f"CLI-{i:04d}"
    
    return f"CLI-{max_num + 1:04d}"

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


@router.get("/buscar")
def buscar_clientes(request: Request, db: Session = Depends(get_db), q: str = Query("")):
    if not q or len(q) < 2:
        return {"clientes": []}
    query = db.query(Cliente).filter(Cliente.nome.ilike(f"%{q}%") | Cliente.cpf_cnpj.ilike(f"%{q}%") | Cliente.fantasia.ilike(f"%{q}%"))
    clientes = query.order_by(Cliente.nome).limit(20).all()
    return {"clientes": [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj, "codigo": c.codigo} for c in clientes]}

@router.get("/novo")
def novo_cliente(request: Request, db: Session = Depends(get_db)):
    return request.app.state.templates.TemplateResponse(
        "clientes/form.html",
        {"request": request, "cliente": None, "proximo_codigo": _proximo_codigo_cliente(db)}
    )


@router.post("/novo")
def criar_cliente(
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
    codigo_ibge: str = Form(""),
    isento_ie: str = Form(""),
    indicador_ie: str = Form("contribuidor"),
    iss_retido: str = Form(""),
    situacao: str = Form("A"),
    data_cadastro: str = Form(""),
    observacao: str = Form(""),
):
    if not codigo:
        codigo = _proximo_codigo_cliente(db)
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
        codigo_ibge=codigo_ibge or None, isento_ie=(isento_ie == "1"),
        indicador_ie=indicador_ie, iss_retido=(iss_retido == "1"),
        situacao=situacao, observacao=observacao,
        created_at=created_at, bling_pending_sync=True
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
    codigo_ibge: str = Form(""),
    isento_ie: str = Form(""),
    indicador_ie: str = Form("contribuidor"),
    iss_retido: str = Form(""),
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
    cliente.codigo_ibge = codigo_ibge or None
    cliente.isento_ie = (isento_ie == "1")
    cliente.indicador_ie = indicador_ie
    cliente.iss_retido = (iss_retido == "1")
    cliente.situacao = situacao
    if data_cadastro:
        cliente.created_at = datetime.strptime(data_cadastro, "%Y-%m-%d")
    cliente.observacao = observacao
    cliente.updated_at = datetime.now()
    cliente.bling_pending_sync = True
    db.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}", status_code=303)


@router.post("/{cliente_id}/excluir")
def excluir_cliente(request: Request, cliente_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.senha_admin or senha != empresa.senha_admin:
        return JSONResponse({"success": False, "error": "Senha inválida"}, status_code=403)
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if cliente:
        db.delete(cliente)
        db.commit()
        return {"success": True, "redirect": "/clientes"}
    return {"success": False, "error": "Cliente não encontrado"}

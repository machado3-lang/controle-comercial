from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime
from database import get_db
from models import Fornecedor, ContaPagar, Empresa, HistoricoCadastro
from services.validators import validar_cliente_fornecedor
from services.sync_cliente_fornecedor import upsert_cliente_de_fornecedor
from app.core.security import confirma_senha_usuario
from services.audit import registrar_auditoria


def _proximo_codigo_fornecedor(db: Session) -> str:
    """Próximo código de fornecedor disponível.

    Varre os códigos já existentes e retorna o primeiro número livre
    (FOR-0001, FOR-0002, ...), pulando qualquer um já utilizado, evitando
    códigos duplicados.
    """
    codigos = db.query(Fornecedor.codigo).filter(Fornecedor.codigo.isnot(None)).all()
    usados = set()
    for c in codigos:
        try:
            usados.add(int(str(c[0]).split("-")[1]))
        except (IndexError, ValueError):
            continue
    for i in range(1, (max(usados) if usados else 0) + 2):
        if i not in usados:
            return f"FOR-{i:04d}"
    return "FOR-0001"


router = APIRouter(prefix="/fornecedores", tags=["Fornecedores"])


@router.get("/")
def listar_fornecedores(
    request: Request, db: Session = Depends(get_db),
    busca: str = Query(""), situacao: str = Query(""),
    page: int = Query(1), per_page: int = Query(20),
):
    query = db.query(Fornecedor)
    if busca:
        query = query.filter(
            Fornecedor.nome.ilike(f"%{busca}%") | Fornecedor.cpf_cnpj.ilike(f"%{busca}%")
        )
    if situacao:
        query = query.filter(Fornecedor.situacao == situacao)
    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    fornecedores = query.order_by(Fornecedor.nome).offset(offset).limit(per_page).all()
    return request.app.state.templates.TemplateResponse(request, 
        "fornecedores/listar.html",
        {"request": request, "fornecedores": fornecedores, "busca": busca,
         "filtro_situacao": situacao, "page": page, "per_page": per_page,
         "total_pages": total_pages, "total_count": total_count}
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
    return request.app.state.templates.TemplateResponse(request, 
        "fornecedores/form.html",
        {"request": request, "fornecedor": None, "proximo_codigo": _proximo_codigo_fornecedor(db)}
    )


@router.get("/checar-cpf-cnpj")
def checar_cpf_cnpj(q: str = Query(""), db: Session = Depends(get_db)):
    """Retorna se já existe um fornecedor com o CPF/CNPJ informado (alfanumerico)."""
    import re
    doc = re.sub(r"[^A-Za-z0-9]", "", q or "").upper()
    if not doc:
        return JSONResponse({"existe": False})
    for f in db.query(Fornecedor).filter(Fornecedor.cpf_cnpj.isnot(None), Fornecedor.cpf_cnpj != "").all():
        if re.sub(r"[^A-Za-z0-9]", "", f.cpf_cnpj or "").upper() == doc:
            return JSONResponse({"existe": True, "id": f.id, "nome": f.nome, "codigo": f.codigo})
    return JSONResponse({"existe": False})


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
    numero: str = Form(""),
    complemento: str = Form(""),
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
    data_sincronizacao: str = Form(""),
    confirmar_duplicado: str = Form(""),
    tambem_cliente: str = Form(""),
    next: str = Form(""),
):
    # Checagem de CPF/CNPJ duplicado (permite cadastro só após confirmação)
    import re
    doc = re.sub(r"[^A-Za-z0-9]", "", cpf_cnpj or "").upper()
    if doc:
        for f in db.query(Fornecedor).filter(Fornecedor.cpf_cnpj.isnot(None), Fornecedor.cpf_cnpj != "").all():
            if re.sub(r"[^A-Za-z0-9]", "", f.cpf_cnpj or "").upper() == doc:
                if not confirmar_duplicado:
                    request.session["message"] = {
                        "tipo": "warning",
                        "texto": f"Já existe um fornecedor com este CPF/CNPJ ({f.nome} - {f.codigo}). Cadastro bloqueado. Confirme no aviso do formulário para prosseguir."
                    }
                    return RedirectResponse(url="/fornecedores/novo", status_code=303)
                break

    erros = validar_cliente_fornecedor(
        nome=nome,
        tipo_pessoa=tipo_pessoa,
        cpf_cnpj=cpf_cnpj,
        ie=inscricao_estadual,
        uf=estado,
        cep=cep,
        telefone=telefone,
        celular=celular,
        email=email,
    )
    if erros:
        request.session["message"] = {"tipo": "danger", "texto": "; ".join(erros)}
        return RedirectResponse(url="/fornecedores/novo", status_code=303)

    if not codigo:
        codigo = _proximo_codigo_fornecedor(db)
    if data_cadastro:
        created_at = datetime.strptime(data_cadastro, "%Y-%m-%d")
    else:
        created_at = datetime.now()
    fornecedor = Fornecedor(
        codigo=codigo,
        nome=nome, cpf_cnpj=cpf_cnpj, tipo_pessoa=tipo_pessoa,
        email=email, telefone=telefone,
        celular=celular, endereco=endereco, numero=numero or None, complemento=complemento or None,
        bairro=bairro, cidade=cidade,
        estado=estado, cep=cep, contato=contato, fantasia=fantasia,
        inscricao_estadual=inscricao_estadual, inscricao_municipal=inscricao_municipal,
        tambem_cliente=(tambem_cliente == "1"),
        situacao=situacao, observacao=observacao, created_at=created_at,
        bling_pending_sync=True,
        data_sincronizacao=datetime.strptime(data_sincronizacao, "%Y-%m-%d %H:%M") if data_sincronizacao else None,
    )
    db.add(fornecedor)
    db.commit()
    if tambem_cliente == "1":
        upsert_cliente_de_fornecedor(db, fornecedor)
    # Redireciona para a origem (ex.: NF-e recebida) quando informado
    if next and next.startswith("/") and not next.startswith("//"):
        return RedirectResponse(url=next, status_code=303)
    return RedirectResponse(url="/fornecedores", status_code=303)


@router.get("/{fornecedor_id}")
def detalhe_fornecedor(request: Request, fornecedor_id: int, db: Session = Depends(get_db)):
    fornecedor = db.query(Fornecedor).filter(Fornecedor.id == fornecedor_id).first()
    if not fornecedor:
        return RedirectResponse(url="/fornecedores", status_code=303)
    contas = db.query(ContaPagar).filter(ContaPagar.fornecedor_id == fornecedor_id).order_by(ContaPagar.data_vencimento.desc()).all()
    historicos = db.query(HistoricoCadastro).filter(
        HistoricoCadastro.entidade_tipo == "fornecedor", HistoricoCadastro.entidade_id == fornecedor_id
    ).order_by(HistoricoCadastro.data.desc()).all()
    return request.app.state.templates.TemplateResponse(request, 
        "fornecedores/detalhe.html",
        {"request": request, "fornecedor": fornecedor, "contas": contas, "historicos": historicos}
    )


@router.get("/{fornecedor_id}/editar")
def editar_fornecedor(request: Request, fornecedor_id: int, db: Session = Depends(get_db)):
    fornecedor = db.query(Fornecedor).filter(Fornecedor.id == fornecedor_id).first()
    if not fornecedor:
        return RedirectResponse(url="/fornecedores", status_code=303)
    return request.app.state.templates.TemplateResponse(request, 
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
    numero: str = Form(""),
    complemento: str = Form(""),
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
    data_sincronizacao: str = Form(""),
    tambem_cliente: str = Form(""),
):
    fornecedor = db.query(Fornecedor).filter(Fornecedor.id == fornecedor_id).first()
    if not fornecedor:
        return RedirectResponse(url="/fornecedores", status_code=303)
    campos_antigos = {
        "nome": fornecedor.nome, "fantasia": fornecedor.fantasia, "cpf_cnpj": fornecedor.cpf_cnpj,
        "tipo_pessoa": fornecedor.tipo_pessoa, "email": fornecedor.email, "telefone": fornecedor.telefone,
        "celular": fornecedor.celular, "contato": fornecedor.contato, "endereco": fornecedor.endereco,
        "numero": fornecedor.numero, "complemento": fornecedor.complemento, "bairro": fornecedor.bairro,
        "cidade": fornecedor.cidade, "estado": fornecedor.estado, "cep": fornecedor.cep,
        "inscricao_estadual": fornecedor.inscricao_estadual, "inscricao_municipal": fornecedor.inscricao_municipal,
        "situacao": fornecedor.situacao, "observacao": fornecedor.observacao,
    }
    fornecedor.nome = nome
    fornecedor.cpf_cnpj = cpf_cnpj
    fornecedor.tipo_pessoa = tipo_pessoa
    fornecedor.email = email
    fornecedor.telefone = telefone
    fornecedor.celular = celular
    fornecedor.endereco = endereco
    fornecedor.numero = numero or None
    fornecedor.complemento = complemento or None
    fornecedor.bairro = bairro
    fornecedor.cidade = cidade
    fornecedor.estado = estado
    fornecedor.cep = cep
    fornecedor.contato = contato
    fornecedor.fantasia = fantasia
    fornecedor.inscricao_estadual = inscricao_estadual
    fornecedor.inscricao_municipal = inscricao_municipal
    fornecedor.tambem_cliente = (tambem_cliente == "1")
    fornecedor.situacao = situacao
    if data_cadastro:
        fornecedor.created_at = datetime.strptime(data_cadastro, "%Y-%m-%d")
    fornecedor.observacao = observacao
    if data_sincronizacao:
        fornecedor.data_sincronizacao = datetime.strptime(data_sincronizacao, "%Y-%m-%d %H:%M")
    fornecedor.updated_at = datetime.now()
    fornecedor.bling_pending_sync = True
    campos_novos = {
        "nome": nome, "fantasia": fantasia, "cpf_cnpj": cpf_cnpj,
        "tipo_pessoa": tipo_pessoa, "email": email, "telefone": telefone,
        "celular": celular, "contato": contato, "endereco": endereco,
        "numero": numero, "complemento": complemento, "bairro": bairro,
        "cidade": cidade, "estado": estado, "cep": cep,
        "inscricao_estadual": inscricao_estadual, "inscricao_municipal": inscricao_municipal,
        "situacao": situacao, "observacao": observacao,
    }
    from services.historico_cadastro import registrar_historico
    registrar_historico(db, "fornecedor", fornecedor.id,
                        {c: (campos_antigos[c], campos_novos[c]) for c in campos_antigos},
                        usuario_id=request.session.get("user_id"))
    db.commit()
    if tambem_cliente == "1":
        upsert_cliente_de_fornecedor(db, fornecedor)
    return RedirectResponse(url=f"/fornecedores/{fornecedor_id}", status_code=303)


@router.post("/{fornecedor_id}/excluir")
def excluir_fornecedor(request: Request, fornecedor_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    if not confirma_senha_usuario(request, db, senha):
        return JSONResponse({"success": False, "error": "Senha inválida ou usuário não autorizado"}, status_code=403)
    fornecedor = db.query(Fornecedor).filter(Fornecedor.id == fornecedor_id).first()
    if fornecedor:
        fornecedor_nome = fornecedor.nome
        fornecedor.situacao = "I"
        db.commit()
        registrar_auditoria(
            db, request.session.get("user_id"), "excluir",
            "fornecedor", fornecedor_id, f"Fornecedor: {fornecedor_nome}",
            request.client.host if request.client else None
        )
        return {"success": True, "redirect": "/fornecedores"}
    return {"success": False, "error": "Fornecedor não encontrado"}

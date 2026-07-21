from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime, date

from database import get_db
from models import Cliente, ContaReceber, Assinatura, OrdemServico, Empresa, HistoricoCadastro
from services.validators import validar_cliente_fornecedor
from app.core.security import confirma_senha_usuario
from services.audit import registrar_auditoria


def _proximo_codigo_cliente(db: Session) -> str:
    """Próximo código de cliente disponível.

    Varre os códigos já existentes e retorna o primeiro número livre
    (CLI-0001, CLI-0002, ...), pulando qualquer um já utilizado. Assim,
    se o CLI-0005 já existir, o gerador entrega CLI-0006 e segue, sem
    produzir códigos duplicados.
    """
    codigos = db.query(Cliente.codigo).filter(Cliente.codigo.isnot(None)).all()
    usados = set()
    for c in codigos:
        try:
            usados.add(int(str(c[0]).split("-")[1]))
        except (IndexError, ValueError):
            continue
    for i in range(1, (max(usados) if usados else 0) + 2):
        if i not in usados:
            return f"CLI-{i:04d}"
    return "CLI-0001"

router = APIRouter(prefix="/clientes", tags=["Clientes"])


@router.get("/")
def listar_clientes(
    request: Request, db: Session = Depends(get_db),
    busca: str = Query(""), situacao: str = Query(""),
    sort: str = Query("nome"), ordem: str = Query("asc"),
    page: int = Query(1), per_page: int = Query(20),
):
    query = db.query(Cliente)
    if busca:
        query = query.filter(
            Cliente.nome.ilike(f"%{busca}%") | Cliente.cpf_cnpj.ilike(f"%{busca}%")
        )
    if situacao:
        query = query.filter(Cliente.situacao == situacao)
    sort_col = {
        "codigo": Cliente.codigo,
        "nome": Cliente.nome,
        "fantasia": Cliente.fantasia,
    }.get(sort, Cliente.nome)
    if ordem == "desc":
        query = query.order_by(sort_col.desc())
    else:
        query = query.order_by(sort_col.asc())
    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    clientes = query.offset(offset).limit(per_page).all()
    return request.app.state.templates.TemplateResponse(request, 
        "clientes/listar.html",
        {"request": request, "clientes": clientes, "busca": busca,
         "filtro_situacao": situacao, "page": page, "per_page": per_page,
         "total_pages": total_pages, "total_count": total_count,
         "sort": sort, "ordem": ordem}
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
    return request.app.state.templates.TemplateResponse(request, 
        "clientes/form.html",
        {"request": request, "cliente": None, "proximo_codigo": _proximo_codigo_cliente(db)}
    )


@router.get("/checar-cpf-cnpj")
def checar_cpf_cnpj(q: str = Query(""), db: Session = Depends(get_db)):
    """Retorna se já existe um cliente com o CPF/CNPJ informado.

    Normaliza removendo mascara e mantendo letras maiusculas (CNPJ
    alfanumerico)."""
    import re
    doc = re.sub(r"[^A-Za-z0-9]", "", q or "").upper()
    if not doc:
        return JSONResponse({"existe": False})
    achou = None
    for c in db.query(Cliente).filter(Cliente.cpf_cnpj.isnot(None), Cliente.cpf_cnpj != "").all():
        if re.sub(r"[^A-Za-z0-9]", "", c.cpf_cnpj or "").upper() == doc:
            achou = c
            break
    if achou:
        return JSONResponse({
            "existe": True,
            "id": achou.id,
            "nome": achou.nome,
            "codigo": achou.codigo,
        })
    return JSONResponse({"existe": False})


@router.get("/diagnostico-codigos")
def diagnostico_codigos(request: Request, db: Session = Depends(get_db)):
    """Relatório read-only: cadastros sem código e códigos duplicados
    (clientes e fornecedores). Não altera nada."""
    from models import Fornecedor
    from collections import defaultdict

    def analisar(rows):
        sem_codigo = [r for r in rows if not (r.codigo and str(r.codigo).strip())]
        grupos = defaultdict(list)
        for r in rows:
            cod = (str(r.codigo).strip() if r.codigo else "")
            if cod:
                grupos[cod].append(r)
        duplicados = [(cod, regs) for cod, regs in grupos.items() if len(regs) > 1]
        duplicados.sort(key=lambda x: x[0])
        sem_codigo.sort(key=lambda r: (r.nome or "").lower())
        return sem_codigo, duplicados

    clientes = db.query(Cliente).all()
    fornecedores = db.query(Fornecedor).all()
    cli_sem, cli_dup = analisar(clientes)
    for_sem, for_dup = analisar(fornecedores)
    return request.app.state.templates.TemplateResponse(request,
        "clientes/diagnostico_codigos.html",
        {"request": request,
         "cli_sem": cli_sem, "cli_dup": cli_dup,
         "for_sem": for_sem, "for_dup": for_dup,
         "cli_dup_total": sum(len(r) for _, r in cli_dup),
         "for_dup_total": sum(len(r) for _, r in for_dup)}
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
    codigo_ibge: str = Form(""),
    isento_ie: str = Form(""),
    indicador_ie: str = Form("contribuidor"),
    iss_retido: str = Form(""),
    situacao: str = Form("A"),
    data_cadastro: str = Form(""),
    observacao: str = Form(""),
    data_sincronizacao: str = Form(""),
    confirmar_duplicado: str = Form(""),
):
    import re
    # Checagem de CPF/CNPJ duplicado (permite cadastro só após confirmação)
    doc = re.sub(r"[^A-Za-z0-9]", "", cpf_cnpj or "").upper()
    if doc:
        for c in db.query(Cliente).filter(Cliente.cpf_cnpj.isnot(None), Cliente.cpf_cnpj != "").all():
            if re.sub(r"[^A-Za-z0-9]", "", c.cpf_cnpj or "").upper() == doc:
                if not confirmar_duplicado:
                    request.session["message"] = {
                        "tipo": "warning",
                        "texto": f"Já existe um cliente com este CPF/CNPJ ({c.nome} - {c.codigo}). Cadastro bloqueado. Confirme no aviso do formulário para prosseguir."
                    }
                    return RedirectResponse(url="/clientes/novo", status_code=303)
                break

    # Validação centralizada
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
        return RedirectResponse(url="/clientes/novo", status_code=303)

    if not codigo:
        codigo = _proximo_codigo_cliente(db)
    if data_cadastro:
        created_at = datetime.strptime(data_cadastro, "%Y-%m-%d")
    else:
        created_at = datetime.now()
    cliente = Cliente(
        codigo=codigo,
        nome=nome, cpf_cnpj=cpf_cnpj, tipo_pessoa=tipo_pessoa,
        email=email, telefone=telefone,
        celular=celular, endereco=endereco, numero=numero or None, complemento=complemento or None,
        bairro=bairro, cidade=cidade,
        estado=estado, cep=cep, contato=contato, fantasia=fantasia,
        inscricao_estadual=inscricao_estadual, inscricao_municipal=inscricao_municipal,
        codigo_ibge=codigo_ibge or None, isento_ie=(isento_ie == "1"),
        indicador_ie=indicador_ie, iss_retido=(iss_retido == "1"),
        situacao=situacao, observacao=observacao,
        created_at=created_at, bling_pending_sync=True,
        data_sincronizacao=datetime.strptime(data_sincronizacao, "%Y-%m-%d %H:%M") if data_sincronizacao else None,
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
    historicos = db.query(HistoricoCadastro).filter(
        HistoricoCadastro.entidade_tipo == "cliente", HistoricoCadastro.entidade_id == cliente_id
    ).order_by(HistoricoCadastro.data.desc()).all()
    return request.app.state.templates.TemplateResponse(request, 
        "clientes/detalhe.html",
        {"request": request, "cliente": cliente, "contas": contas, "assinaturas": assinaturas, "ordens": ordens, "historicos": historicos}
    )


@router.get("/{cliente_id}/editar")
def editar_cliente(request: Request, cliente_id: int, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        return RedirectResponse(url="/clientes", status_code=303)
    return request.app.state.templates.TemplateResponse(request, 
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
    codigo_ibge: str = Form(""),
    isento_ie: str = Form(""),
    indicador_ie: str = Form("contribuidor"),
    iss_retido: str = Form(""),
    observacao: str = Form(""),
    data_sincronizacao: str = Form(""),
):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        return RedirectResponse(url="/clientes", status_code=303)
    cliente.nome = nome
    cliente.cpf_cnpj = cpf_cnpj
    cliente.tipo_pessoa = tipo_pessoa
    cliente.email = email
    cliente.telefone = telefone
    campos_antigos = {
        "nome": cliente.nome, "fantasia": cliente.fantasia, "cpf_cnpj": cliente.cpf_cnpj,
        "tipo_pessoa": cliente.tipo_pessoa, "email": cliente.email, "telefone": cliente.telefone,
        "celular": cliente.celular, "contato": cliente.contato, "endereco": cliente.endereco,
        "numero": cliente.numero, "complemento": cliente.complemento, "bairro": cliente.bairro,
        "cidade": cliente.cidade, "estado": cliente.estado, "cep": cliente.cep,
        "codigo_ibge": cliente.codigo_ibge, "inscricao_estadual": cliente.inscricao_estadual,
        "inscricao_municipal": cliente.inscricao_municipal, "isento_ie": cliente.isento_ie,
        "indicador_ie": cliente.indicador_ie, "iss_retido": cliente.iss_retido,
        "situacao": cliente.situacao, "observacao": cliente.observacao,
    }
    cliente.celular = celular
    cliente.endereco = endereco
    cliente.numero = numero or None
    cliente.complemento = complemento or None
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
    if data_sincronizacao:
        cliente.data_sincronizacao = datetime.strptime(data_sincronizacao, "%Y-%m-%d %H:%M")
    cliente.updated_at = datetime.now()
    cliente.bling_pending_sync = True
    campos_novos = {
        "nome": nome, "fantasia": fantasia, "cpf_cnpj": cpf_cnpj,
        "tipo_pessoa": tipo_pessoa, "email": email, "telefone": telefone,
        "celular": celular, "contato": contato, "endereco": endereco,
        "numero": numero, "complemento": complemento, "bairro": bairro,
        "cidade": cidade, "estado": estado, "cep": cep,
        "codigo_ibge": codigo_ibge, "inscricao_estadual": inscricao_estadual,
        "inscricao_municipal": inscricao_municipal, "isento_ie": (isento_ie == "1"),
        "indicador_ie": indicador_ie, "iss_retido": (iss_retido == "1"),
        "situacao": situacao, "observacao": observacao,
    }
    from services.historico_cadastro import registrar_historico
    registrar_historico(db, "cliente", cliente.id,
                        {c: (campos_antigos[c], campos_novos[c]) for c in campos_antigos},
                        usuario_id=request.session.get("user_id"))
    db.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}", status_code=303)


@router.post("/{cliente_id}/excluir")
def excluir_cliente(request: Request, cliente_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    if not confirma_senha_usuario(request, db, senha):
        return JSONResponse({"success": False, "error": "Senha inválida ou usuário não autorizado"}, status_code=403)
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if cliente:
        cliente_nome = cliente.nome
        cliente.situacao = "I"
        db.commit()
        registrar_auditoria(
            db, request.session.get("user_id"), "excluir",
            "cliente", cliente_id, f"Cliente: {cliente_nome}",
            request.client.host if request.client else None
        )
        return {"success": True, "redirect": "/clientes"}
    return {"success": False, "error": "Cliente não encontrado"}

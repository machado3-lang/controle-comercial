from fastapi import APIRouter, Depends, Request, Form, Query, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session, selectinload, joinedload
from sqlalchemy import func
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation


def to_decimal(v, default="0.00"):
    """Converte para Decimal com segurança; retorna default se vazio/inválido."""
    try:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return Decimal(str(default))
        return Decimal(str(v).replace(",", "."))
    except (ValueError, TypeError, InvalidOperation):
        return Decimal(str(default))
from typing import Optional
import os
import logging
from database import get_db
from models import Produto, Fornecedor, CategoriaProduto, PedidoVenda, MarcaProduto, ProdutoVariacao
from models import ProdutoComposicao, Empresa
from app.core.security import confirma_senha_usuario
from services.audit import registrar_auditoria

logger = logging.getLogger(__name__)

UNIDADES_MEDIDA = ["cm", "m", "mm", "UN", "KG", "L"]


def _proximo_sku_produto(db: Session) -> str:
    usados = set()
    vars = db.query(ProdutoVariacao.sku).filter(ProdutoVariacao.sku.isnot(None)).all()
    for v in vars:
        try:
            num = int(v[0].split("-")[-1])
            usados.add(num)
        except (ValueError, IndexError, AttributeError):
            continue
    
    max_num = max(usados) if usados else 0
    
    for i in range(1, max_num + 2):
        if i not in usados:
            return f"SKU-{i:05d}"
    
    return f"SKU-{max_num + 1:05d}"

def _proximo_codigo_produto(db: Session) -> str:
    """Próximo código de produto disponível.

    Varre os códigos já existentes e retorna o primeiro número livre
    (00001, 00002, ...), pulando qualquer um já utilizado. Assim, se o
    00005 já existir, o gerador entrega 00006 e segue, sem produzir códigos
    duplicados. Não depende de um contador persistido, evitando que o
    formulário de "novo" sempre abra com 00001.
    """
    codigos = db.query(Produto.codigo).filter(Produto.codigo.isnot(None)).all()
    usados = set()
    for c in codigos:
        val = c[0]
        if not val:
            continue
        try:
            usados.add(int(str(val).strip()))
        except ValueError:
            try:
                usados.add(int(str(val).split("-")[-1].strip()))
            except (ValueError, IndexError):
                continue

    inicio = max(usados) if usados else 0
    for i in range(1, inicio + 2):
        if i not in usados:
            return f"{i:05d}"

    return f"{inicio + 1:05d}"


router = APIRouter(prefix="/produtos", tags=["Produtos"])


@router.get("/buscar-insumos")
def buscar_insumos(request: Request, db: Session = Depends(get_db), q: str = Query("")):
    from sqlalchemy import or_
    if not q or len(q) < 2:
        return {"itens": []}
    query = db.query(Produto).filter(or_(Produto.nome.ilike(f"%{q}%"), Produto.codigo.ilike(f"%{q}%")))
    query = query.filter(Produto.situacao == "A", Produto.tipo == "produto")
    itens = query.order_by(Produto.nome).limit(20).all()
    return {"itens": [{"id": i.id, "nome": i.nome, "preco": float(i.preco or 0), "tipo": i.tipo, "descricao": i.descricao or i.nome, "variacoes": [{"id": v.id, "nome_variacao": v.nome_variacao, "preco_adicional": float(v.preco_adicional or 0)} for v in i.variacoes]} for i in itens]}

@router.get("/buscar")
def buscar_itens(request: Request, db: Session = Depends(get_db), q: str = Query("")):
    from sqlalchemy import or_
    if not q or len(q) < 2:
        return {"itens": []}
    query = db.query(Produto).filter(or_(Produto.nome.ilike(f"%{q}%"), Produto.codigo.ilike(f"%{q}%")))
    query = query.filter(Produto.situacao == "A", Produto.tipo.in_(["produto", "kit", "servico"]))
    itens = query.order_by(Produto.nome).limit(20).all()
    return {"itens": [{"id": i.id, "nome": i.nome, "preco": float(i.preco or 0), "tipo": i.tipo, "descricao": i.descricao or i.nome, "variacoes": [{"id": v.id, "nome_variacao": v.nome_variacao, "preco_adicional": float(v.preco_adicional or 0)} for v in i.variacoes], "composicoes": [{"insumo_id": c.insumo_id, "quantidade": c.quantidade_padrao} for c in i.composicoes]} for i in itens]}

@router.get("/proximo-sku")
def proximo_sku_endpoint(request: Request, db: Session = Depends(get_db)):
    return {"sku": _proximo_sku_produto(db)}

@router.get("/")
def listar_produtos(
    request: Request, db: Session = Depends(get_db),
    busca: str = Query(""), situacao: str = Query(""),
    fornecedor_id: Optional[str] = Query(""), categoria_id: Optional[str] = Query(""),
    marca_id: Optional[str] = Query(""), estoque_filtro: str = Query(""),
    tipo_filtro: str = Query(""),
    page: int = Query(1), per_page: int = Query(20),
):
    from sqlalchemy.orm import selectinload
    f_id = int(fornecedor_id) if fornecedor_id else None
    c_id = int(categoria_id) if categoria_id else None
    m_id = int(marca_id) if marca_id else None
    
    query = db.query(Produto).options(selectinload(Produto.variacoes))
    if busca:
        query = query.filter(Produto.nome.ilike(f"%{busca}%") | Produto.codigo.ilike(f"%{busca}%"))
    if estoque_filtro == "zerado":
        query = query.filter(Produto.estoque <= 0)
    elif estoque_filtro == "baixo":
        query = query.filter(Produto.estoque > 0, Produto.estoque_minimo > 0, Produto.estoque < Produto.estoque_minimo)
    if tipo_filtro:
        query = query.filter(Produto.tipo == tipo_filtro)
    if not situacao or situacao == "ativo":
        query = query.filter(Produto.situacao == "A")
    elif situacao == "inativo":
        query = query.filter(Produto.situacao != "A")
    if f_id:
        query = query.filter(Produto.fornecedor_id == f_id)
    if c_id:
        query = query.filter(Produto.categoria_id == c_id)
    if m_id:
        query = query.filter(Produto.marca_id == m_id)
    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    produtos = query.order_by(Produto.nome).offset(offset).limit(per_page).all()
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    proximo_pedido = db.query(func.max(PedidoVenda.numero)).scalar()
    try:
        proximo_pedido = str(int(proximo_pedido) + 1) if proximo_pedido else "1"
    except (ValueError, TypeError):
        logger.warning(f"Falha ao gerar próximo número de pedido a partir de: {proximo_pedido}")
        proximo_pedido = "1"
    fornecedor_nome = None
    if f_id:
        f_obj = db.query(Fornecedor).filter(Fornecedor.id == f_id).first()
        fornecedor_nome = f_obj.nome if f_obj else None
    return request.app.state.templates.TemplateResponse(request, 
        "produtos/listar.html",
        {"request": request, "produtos": produtos, "fornecedores": fornecedores, "categorias": categorias, "marcas": marcas, "busca": busca, "situacao": situacao, "fornecedor_id": f_id, "fornecedor_nome": fornecedor_nome, "categoria_id": c_id, "marca_id": m_id, "estoque_filtro": estoque_filtro, "tipo_filtro": tipo_filtro, "proximo_pedido": proximo_pedido, "page": page, "per_page": per_page, "total_pages": total_pages, "total_count": total_count}
    )


@router.get("/novo")
def novo_produto_form(request: Request, db: Session = Depends(get_db)):
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    variacoes = db.query(ProdutoVariacao).options(selectinload(ProdutoVariacao.produto)).join(Produto).filter(Produto.tipo == 'produto').all()
    variacoes_json = [{"id": v.id, "nome_variacao": v.nome_variacao, "produto_nome": v.produto.nome, "categoria_id": v.produto.categoria_id, "marca_id": v.produto.marca_id} for v in variacoes]
    itens_disponiveis = db.query(Produto).options(selectinload(Produto.variacoes)).order_by(Produto.nome).all()
    itens_json = [{"id": i.id, "nome": i.nome, "preco": float(i.preco or 0), "tipo": i.tipo, "descricao": i.descricao or i.nome} for i in itens_disponiveis if i.tipo in ('produto', 'servico')]
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    return request.app.state.templates.TemplateResponse(request, "produtos/form.html", {"request": request, "produto": None, "fornecedores": fornecedores, "categorias": categorias, "marcas": marcas, "UNIDADES_MEDIDA": UNIDADES_MEDIDA, "editar": False, "variacoes": variacoes, "variacoes_json": variacoes_json, "itens_json": itens_json, "itens_disponiveis": itens_disponiveis, "proximo_codigo": _proximo_codigo_produto(db), "fornecedores_json": fornecedores_json})


@router.post("/novo")
def criar_produto(
    request: Request, db: Session = Depends(get_db),
    codigo: str = Form(""),
    nome: str = Form(...),
    descricao: str = Form(""),
    preco: str = Form(""),
    preco_custo: str = Form(""),
    ncm: str = Form(""),
    unidade: str = Form("UN"),
    origem: int = Form(0),
    categoria_id: int = Form(0),
    fornecedor_id: int = Form(0),
    marca_id: int = Form(0),
    peso_liq: float = Form(0),
    peso_bruto: float = Form(0),
    altura: float = Form(0),
    largura: float = Form(0),
    profundidade: float = Form(0),
    unidade_medida: str = Form("cm"),
    estoque: float = Form(0),
    estoque_minimo: float = Form(0),
    tipo: str = Form("produto"),
    situacao: str = Form("A"),
    variacoes: str = Form(""),
    insumos: str = Form(""),
    codigo_lc116: str = Form(""),
    codigo_tributacao_municipal: str = Form(""),
    foto: UploadFile = File(None),
):
    if not codigo:
        codigo = _proximo_codigo_produto(db)
    preco_val = to_decimal(preco, "0.00")
    foto_path = None
    if foto and foto.filename:
        ext = foto.filename.split('.')[-1] if '.' in foto.filename else ''
        foto_path = f"/static/uploads/produtos/{int(datetime.now().timestamp())}.{ext}"
        os.makedirs("static/uploads/produtos", exist_ok=True)
        with open(f".{foto_path}", "wb") as f:
            f.write(foto.file.read())
    marca = None
    if marca_id:
        m = db.query(MarcaProduto).get(marca_id)
        marca = m.nome if m else None
    
    if tipo == 'servico' and not codigo_lc116:
        request.session['error'] = 'Código LC116 é obrigatório para serviços'
        return RedirectResponse(url='/produtos/novo', status_code=303)
    
    codigo_lc116_val = codigo_lc116 if tipo == 'servico' else None
    codigo_tributacao_val = codigo_tributacao_municipal if tipo == 'servico' else None
    
    produto = Produto(
        codigo=codigo if codigo else None,
        nome=nome,
        descricao=descricao,
        preco=preco_val,
        preco_custo=to_decimal(preco_custo) if preco_custo and preco_custo.strip() else None,
        ncm=ncm if ncm else None,
        unidade=unidade,
        origem=origem,
        categoria_id=categoria_id if categoria_id else None,
        fornecedor_id=fornecedor_id if fornecedor_id else None,
        marca_id=marca_id if marca_id else None,
        marca=marca,
        peso_liq=peso_liq if peso_liq else None,
        peso_bruto=peso_bruto if peso_bruto else None,
        altura=altura if altura else None,
        largura=largura if largura else None,
        profundidade=profundidade if profundidade else None,
        unidade_medida=unidade_medida,
        estoque=estoque if estoque else 0,
        estoque_minimo=estoque_minimo if estoque_minimo else 0,
        tipo=tipo,
        codigo_lc116=codigo_lc116_val,
        codigo_tributacao_municipal=codigo_tributacao_val,
        situacao=situacao,
        foto=foto_path,
        bling_pending_sync=True,
    )
    db.add(produto)
    db.commit()
    
    # Salvar variações se for produto
    if tipo == "produto":
        import json
        try:
            varList = json.loads(variacoes) if variacoes else []
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Falha ao decodificar JSON de variações na criação do produto: {variacoes}")
            varList = []
        if varList and len(varList) > 0:
            for item in varList:
                sku = item.get("sku") or _proximo_sku_produto(db)
                db.add(ProdutoVariacao(
                    produto_id=produto.id,
                    nome_variacao=item.get("nome_variacao", "Padrão"),
                    sku=sku,
                    preco_adicional=item.get("preco_adicional", 0),
                    estoque_atual=item.get("estoque_atual", 0),
                    estoque_minimo=item.get("estoque_minimo", 0)
                ))
        if not varList:
            db.add(ProdutoVariacao(
                produto_id=produto.id,
                nome_variacao="Padrão",
                sku=_proximo_sku_produto(db),
                preco_adicional=0,
                estoque_atual=0,
                estoque_minimo=0
            ))
        db.commit()
    # Salvar insumos (composição) se for kit ou serviço
    if tipo in ("kit", "servico") and insumos:
        import json
        try:
            insumos_list = json.loads(insumos)
            if insumos_list:
                for item in insumos_list:
                    db.add(ProdutoComposicao(
                        produto_pai_id=produto.id,
                        insumo_id=int(item["insumo_id"]),
                        quantidade_padrao=item["quantidade"]
                    ))
                db.commit()
        except Exception as e:
            logger.error(f"Erro ao salvar insumos do produto: {e}")
            request.session["error"] = "Erro ao salvar composição do produto"
    return RedirectResponse(url="/produtos", status_code=303)


@router.get("/{produto_id}/editar")
def editar_produto(request: Request, produto_id: int, db: Session = Depends(get_db)):
    from sqlalchemy.orm import joinedload
    produto = db.query(Produto).options(
        joinedload(Produto.variacoes), 
        joinedload(Produto.composicoes).joinedload(ProdutoComposicao.insumo)
    ).filter(Produto.id == produto_id).first()
    if not produto:
        return RedirectResponse(url="/produtos", status_code=303)
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    variacoes = db.query(ProdutoVariacao).options(selectinload(ProdutoVariacao.produto)).join(Produto).filter(Produto.tipo == 'produto').all()
    variacoes_json = [{"id": v.id, "nome_variacao": v.nome_variacao, "produto_nome": v.produto.nome, "categoria_id": v.produto.categoria_id, "marca_id": v.produto.marca_id} for v in variacoes]
    itens_disponiveis = db.query(Produto).options(selectinload(Produto.variacoes)).order_by(Produto.nome).all()
    itens_json = [{"id": i.id, "nome": i.nome, "preco": float(i.preco or 0), "tipo": i.tipo, "descricao": i.descricao or i.nome} for i in itens_disponiveis if i.tipo in ('produto', 'servico')]
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    return request.app.state.templates.TemplateResponse(request, 
        "produtos/form.html",
        {"request": request, "produto": produto, "fornecedores": fornecedores, "categorias": categorias, "marcas": marcas, "UNIDADES_MEDIDA": UNIDADES_MEDIDA, "editar": True, "variacoes": variacoes, "variacoes_json": variacoes_json, "itens_json": itens_json, "itens_disponiveis": itens_disponiveis, "fornecedores_json": fornecedores_json}
    )


@router.post("/{produto_id}/editar")
def atualizar_produto(
    request: Request, produto_id: int, db: Session = Depends(get_db),
    codigo: str = Form(""),
    nome: str = Form(...),
    descricao: str = Form(""),
    preco: str = Form(""),
    preco_custo: str = Form(""),
    ncm: str = Form(""),
    unidade: str = Form("UN"),
    origem: int = Form(0),
    categoria_id: int = Form(0),
    fornecedor_id: int = Form(0),
    marca_id: int = Form(0),
    peso_liq: float = Form(0),
    peso_bruto: float = Form(0),
    altura: float = Form(0),
    largura: float = Form(0),
    profundidade: float = Form(0),
    unidade_medida: str = Form("cm"),
    estoque: float = Form(0),
    estoque_minimo: float = Form(0),
    tipo: str = Form("produto"),
    situacao: str = Form("A"),
    variacoes: str = Form(""),
    insumos: str = Form(""),
    codigo_lc116: str = Form(""),
    codigo_tributacao_municipal: str = Form(""),
    foto: UploadFile = File(None),
):
    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if produto:
        preco_val = to_decimal(preco) if preco and preco.strip() else produto.preco
        produto.codigo = codigo if codigo else None
        produto.nome = nome
        produto.descricao = descricao
        produto.preco = preco_val
        # preco já foi tratado acima
        produto.preco_custo = to_decimal(preco_custo) if preco_custo and preco_custo.strip() else None
        produto.ncm = ncm if ncm else None
        produto.unidade = unidade
        produto.origem = origem
        produto.categoria_id = categoria_id if categoria_id else None
        produto.fornecedor_id = fornecedor_id if fornecedor_id else None
        if marca_id:
            m = db.query(MarcaProduto).get(marca_id)
            produto.marca = m.nome if m else None
            produto.marca_id = marca_id
        else:
            produto.marca = None
            produto.marca_id = None
        if foto and foto.filename:
            ext = foto.filename.split('.')[-1] if '.' in foto.filename else ''
            produto.foto = f"/static/uploads/produtos/{int(datetime.now().timestamp())}.{ext}"
            os.makedirs("static/uploads/produtos", exist_ok=True)
            with open(f".{produto.foto}", "wb") as f:
                f.write(foto.file.read())
        produto.peso_liq = peso_liq if peso_liq else None
        produto.peso_bruto = peso_bruto if peso_bruto else None
        produto.altura = altura if altura else None
        produto.largura = largura if largura else None
        produto.profundidade = profundidade if profundidade else None
        produto.unidade_medida = unidade_medida
        produto.estoque = estoque if estoque else 0
        produto.estoque_minimo = estoque_minimo if estoque_minimo else 0
        produto.tipo = tipo
        produto.codigo_lc116 = codigo_lc116 if tipo == 'servico' else None
        produto.codigo_tributacao_municipal = codigo_tributacao_municipal if tipo == 'servico' else None
        produto.situacao = situacao
        produto.bling_pending_sync = True
        db.commit()
        
        # Salvar variações se for produto
        if tipo == "produto":
            import json
            varList = json.loads(variacoes) if variacoes else []
            # Remove variações antigas e adiciona novas
            db.query(ProdutoVariacao).filter(ProdutoVariacao.produto_id == produto_id).delete()
            if len(varList) > 0:
                for item in varList:
                    db.add(ProdutoVariacao(
                        produto_id=produto_id,
                        nome_variacao=item.get("nome_variacao", "Padrão"),
                        sku=item.get("sku", ""),
                        preco_adicional=item.get("preco_adicional", 0),
                        estoque_atual=item.get("estoque_atual", 0),
                        estoque_minimo=item.get("estoque_minimo", 0)
                    ))
            # Garantir variação padrão
            else:
                db.add(ProdutoVariacao(
                    produto_id=produto_id,
                    nome_variacao="Padrão",
                    sku=_proximo_sku_produto(db),
                    preco_adicional=0,
                    estoque_atual=0,
                    estoque_minimo=0
                ))
            db.commit()
        
        # Salvar insumos (composição) se for kit ou serviço
        if tipo in ("kit", "servico") and insumos:
            db.query(ProdutoComposicao).filter(ProdutoComposicao.produto_pai_id == produto_id).delete()
            import json
            try:
                for item in json.loads(insumos):
                    db.add(ProdutoComposicao(
                        produto_pai_id=produto_id,
                        insumo_id=int(item["insumo_id"]),
                        quantidade_padrao=item["quantidade"]
                    ))
                db.commit()
            except Exception as e:
                logger.error(f"Erro ao salvar insumos na edição do produto {produto_id}: {e}")
                request.session["error"] = "Erro ao salvar composição do produto"
    return RedirectResponse(url="/produtos", status_code=303)


@router.post("/{produto_id}/excluir")
def excluir_produto(request: Request, produto_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    if not confirma_senha_usuario(request, db, senha):
        return JSONResponse({"erro": "Senha inválida ou usuário não autorizado"}, status_code=403)
    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if produto:
        produto_nome = produto.nome
        produto.situacao = "I"
        db.commit()
        registrar_auditoria(
            db, request.session.get("user_id"), "excluir",
            "produto", produto_id, f"Produto: {produto_nome}",
            request.client.host if request.client else None
        )
    return RedirectResponse(url="/produtos", status_code=303)


# Rotas de categoria
@router.get("/categorias")
def listar_categorias(request: Request, db: Session = Depends(get_db)):
    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    return request.app.state.templates.TemplateResponse(request, 
        "produtos/categorias.html",
        {"request": request, "categorias": categorias}
    )


@router.post("/categorias/nova")
def criar_categoria(request: Request, db: Session = Depends(get_db), nome: str = Form(...)):
    cat = CategoriaProduto(nome=nome)
    db.add(cat)
    db.commit()
    return RedirectResponse(url="/produtos/categorias", status_code=303)


@router.get("/categorias/editar/{categoria_id}")
def editar_categoria_form(request: Request, categoria_id: int, db: Session = Depends(get_db)):
    cat = db.query(CategoriaProduto).filter(CategoriaProduto.id == categoria_id).first()
    if not cat:
        return RedirectResponse(url="/produtos/categorias", status_code=303)
    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    return request.app.state.templates.TemplateResponse(request, "produtos/categorias.html", {"request": request, "categorias": categorias, "editar_categoria": cat})


@router.post("/categorias/editar/{categoria_id}")
def atualizar_categoria(request: Request, categoria_id: int, db: Session = Depends(get_db), nome: str = Form(...)):
    cat = db.query(CategoriaProduto).filter(CategoriaProduto.id == categoria_id).first()
    if cat:
        cat.nome = nome
        db.commit()
    return RedirectResponse(url="/produtos/categorias", status_code=303)


@router.post("/categorias/excluir/{categoria_id}")
def excluir_categoria(request: Request, categoria_id: int, db: Session = Depends(get_db)):
    produtos_vinculados = db.query(Produto).filter(Produto.categoria_id == categoria_id).count()
    if produtos_vinculados > 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Esta categoria possui {produtos_vinculados} produto(s) vinculado(s) e não pode ser excluída.")
    cat = db.query(CategoriaProduto).filter(CategoriaProduto.id == categoria_id).first()
    if cat:
        db.delete(cat)
        db.commit()
    return RedirectResponse(url="/produtos/categorias", status_code=303)


@router.get("/marcas")
def listar_marcas(request: Request, db: Session = Depends(get_db)):
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    return request.app.state.templates.TemplateResponse(request, 
        "produtos/marcas.html",
        {"request": request, "marcas": marcas}
    )


@router.post("/marcas/nova")
def criar_marca(request: Request, db: Session = Depends(get_db), nome: str = Form(...)):
    marca = MarcaProduto(nome=nome)
    db.add(marca)
    db.commit()
    return RedirectResponse(url="/produtos/marcas", status_code=303)


@router.get("/marcas/editar/{marca_id}")
def editar_marca_form(request: Request, marca_id: int, db: Session = Depends(get_db)):
    marca = db.query(MarcaProduto).filter(MarcaProduto.id == marca_id).first()
    if not marca:
        return RedirectResponse(url="/produtos/marcas", status_code=303)
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    return request.app.state.templates.TemplateResponse(request, "produtos/marcas.html", {"request": request, "marcas": marcas, "editar_marca": marca})


@router.post("/marcas/editar/{marca_id}")
def atualizar_marca(request: Request, marca_id: int, db: Session = Depends(get_db), nome: str = Form(...)):
    marca = db.query(MarcaProduto).filter(MarcaProduto.id == marca_id).first()
    if marca:
        marca.nome = nome
        db.commit()
    return RedirectResponse(url="/produtos/marcas", status_code=303)


@router.post("/marcas/excluir/{marca_id}")
def excluir_marca(request: Request, marca_id: int, db: Session = Depends(get_db)):
    produtos_vinculados = db.query(Produto).filter(Produto.marca_id == marca_id).count()
    if produtos_vinculados > 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Esta marca possui {produtos_vinculados} produto(s) vinculado(s) e não pode ser excluída.")
    marca = db.query(MarcaProduto).filter(MarcaProduto.id == marca_id).first()
    if marca:
        db.delete(marca)
        db.commit()
    return RedirectResponse(url="/produtos/marcas", status_code=303)


@router.get("/pdf-selecionados")
def pdf_selecionados(request: Request, db: Session = Depends(get_db), ids: str = Query("")):
    from sqlalchemy.orm import joinedload
    empresa = db.query(Empresa).first()
    id_list = [int(i) for i in ids.split(',') if i.isdigit()]
    produtos = db.query(Produto).options(joinedload(Produto.variacoes)).filter(Produto.id.in_(id_list)).all() if id_list else []
    return request.app.state.templates.TemplateResponse(request, 
        "produtos/pdf_selecionados.html",
        {"request": request, "produtos": produtos, "empresa": empresa}
    )

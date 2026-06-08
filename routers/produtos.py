from fastapi import APIRouter, Depends, Request, Form, Query, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime
from typing import Optional
import os
from database import get_db
from models import Produto, Fornecedor, CategoriaProduto, PedidoVenda, MarcaProduto

router = APIRouter(prefix="/produtos", tags=["Produtos"])


UNIDADES_MEDIDA = ["cm", "m", "mm", "UN", "KG", "L"]


@router.get("/")
def listar_produtos(request: Request, db: Session = Depends(get_db), busca: str = Query(""), situacao: str = Query(""), fornecedor_id: Optional[str] = Query(""), categoria_id: Optional[str] = Query(""), marca_id: Optional[str] = Query("")):
    f_id = int(fornecedor_id) if fornecedor_id else None
    c_id = int(categoria_id) if categoria_id else None
    m_id = int(marca_id) if marca_id else None
    
    query = db.query(Produto)
    if busca:
        query = query.filter(Produto.nome.ilike(f"%{busca}%") | Produto.codigo.ilike(f"%{busca}%"))
    if not situacao or situacao == "ativo":
        query = query.filter(Produto.situacao == "A")  # Padrão: ativos
    elif situacao == "inativo":
        query = query.filter(Produto.situacao != "A")
    # "todos" mostra todos (sem filtro)
    # "todos" mostra todos (nada filtrado)
    if f_id:
        query = query.filter(Produto.fornecedor_id == f_id)
    if c_id:
        query = query.filter(Produto.categoria_id == c_id)
    if m_id:
        query = query.filter(Produto.marca_id == m_id)
    produtos = query.order_by(Produto.nome).all()
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    proximo_pedido = db.query(func.max(PedidoVenda.numero)).scalar()
    try:
        proximo_pedido = str(int(proximo_pedido) + 1) if proximo_pedido else "1"
    except:
        proximo_pedido = "1"
    return request.app.state.templates.TemplateResponse(
        "produtos/listar.html",
        {"request": request, "produtos": produtos, "fornecedores": fornecedores, "categorias": categorias, "marcas": marcas, "busca": busca, "situacao": situacao, "fornecedor_id": f_id, "categoria_id": c_id, "marca_id": m_id, "proximo_pedido": proximo_pedido}
    )


@router.get("/novo")
def novo_produto_form(request: Request, db: Session = Depends(get_db)):
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    return request.app.state.templates.TemplateResponse("produtos/form.html", {"request": request, "produto": None, "fornecedores": fornecedores, "categorias": categorias, "marcas": marcas, "UNIDADES_MEDIDA": UNIDADES_MEDIDA, "editar": False})


@router.post("/novo")
def criar_produto(
    request: Request, db: Session = Depends(get_db),
    codigo: str = Form(""),
    nome: str = Form(...),
    descricao: str = Form(""),
    preco: float = Form(...),
    preco_custo: float = Form(0),
    ncm: str = Form(""),
    unidade: str = Form("UN"),
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
    foto: UploadFile = File(None),
):
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
    produto = Produto(
        codigo=codigo if codigo else None,
        nome=nome,
        descricao=descricao,
        preco=preco,
        preco_custo=preco_custo if preco_custo else None,
        ncm=ncm if ncm else None,
        unidade=unidade,
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
        situacao=situacao,
        foto=foto_path,
    )
    db.add(produto)
    db.commit()
    return RedirectResponse(url="/produtos", status_code=303)


@router.get("/{produto_id}/editar")
def editar_produto(request: Request, produto_id: int, db: Session = Depends(get_db)):
    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if not produto:
        return RedirectResponse(url="/produtos", status_code=303)
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    return request.app.state.templates.TemplateResponse(
        "produtos/form.html",
        {"request": request, "produto": produto, "fornecedores": fornecedores, "categorias": categorias, "marcas": marcas, "UNIDADES_MEDIDA": UNIDADES_MEDIDA, "editar": True}
    )


@router.post("/{produto_id}/editar")
def atualizar_produto(
    request: Request, produto_id: int, db: Session = Depends(get_db),
    codigo: str = Form(""),
    nome: str = Form(...),
    descricao: str = Form(""),
    preco: float = Form(...),
    preco_custo: float = Form(0),
    ncm: str = Form(""),
    unidade: str = Form("UN"),
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
    foto: UploadFile = File(None),
):
    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if produto:
        produto.codigo = codigo if codigo else None
        produto.nome = nome
        produto.descricao = descricao
        produto.preco = preco
        produto.preco_custo = preco_custo if preco_custo else None
        produto.ncm = ncm if ncm else None
        produto.unidade = unidade
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
        produto.situacao = situacao
        db.commit()
    return RedirectResponse(url="/produtos", status_code=303)


@router.get("/{produto_id}/excluir")
def excluir_produto(request: Request, produto_id: int, db: Session = Depends(get_db)):
    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if produto:
        db.delete(produto)
        db.commit()
    return RedirectResponse(url="/produtos", status_code=303)


# Rotas de categoria
@router.get("/categorias")
def listar_categorias(request: Request, db: Session = Depends(get_db)):
    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    return request.app.state.templates.TemplateResponse(
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
    return request.app.state.templates.TemplateResponse("produtos/categorias.html", {"request": request, "categorias": categorias, "editar_categoria": cat})


@router.post("/categorias/editar/{categoria_id}")
def atualizar_categoria(request: Request, categoria_id: int, db: Session = Depends(get_db), nome: str = Form(...)):
    cat = db.query(CategoriaProduto).filter(CategoriaProduto.id == categoria_id).first()
    if cat:
        cat.nome = nome
        db.commit()
    return RedirectResponse(url="/produtos/categorias", status_code=303)


@router.get("/categorias/excluir/{categoria_id}")
def excluir_categoria(request: Request, categoria_id: int, db: Session = Depends(get_db)):
    cat = db.query(CategoriaProduto).filter(CategoriaProduto.id == categoria_id).first()
    if cat:
        db.delete(cat)
        db.commit()
    return RedirectResponse(url="/produtos/categorias", status_code=303)


@router.get("/marcas")
def listar_marcas(request: Request, db: Session = Depends(get_db)):
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    return request.app.state.templates.TemplateResponse(
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
    return request.app.state.templates.TemplateResponse("produtos/marcas.html", {"request": request, "marcas": marcas, "editar_marca": marca})


@router.post("/marcas/editar/{marca_id}")
def atualizar_marca(request: Request, marca_id: int, db: Session = Depends(get_db), nome: str = Form(...)):
    marca = db.query(MarcaProduto).filter(MarcaProduto.id == marca_id).first()
    if marca:
        marca.nome = nome
        db.commit()
    return RedirectResponse(url="/produtos/marcas", status_code=303)


@router.get("/marcas/excluir/{marca_id}")
def excluir_marca(request: Request, marca_id: int, db: Session = Depends(get_db)):
    marca = db.query(MarcaProduto).filter(MarcaProduto.id == marca_id).first()
    if marca:
        db.delete(marca)
        db.commit()
    return RedirectResponse(url="/produtos/marcas", status_code=303)
from fastapi import APIRouter, Depends, Request, Form, Query, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime
import os
from database import get_db
from models import Produto, Fornecedor, CategoriaProduto, PedidoVenda

router = APIRouter(prefix="/produtos", tags=["Produtos"])


UNIDADES_MEDIDA = ["cm", "m", "mm", "UN", "KG", "L"]


@router.get("/")
def listar_produtos(request: Request, db: Session = Depends(get_db), busca: str = Query(""), situacao: str = Query(""), fornecedor_id: int = Query(0), categoria_id: int = Query(0), marca: str = Query("")):
    query = db.query(Produto)
    if busca:
        query = query.filter(Produto.nome.ilike(f"%{busca}%") | Produto.codigo.ilike(f"%{busca}%"))
    if situacao == "ativo":
        query = query.filter(Produto.situacao == "A")
    elif situacao == "inativo":
        query = query.filter(Produto.situacao != "A")
    if fornecedor_id:
        query = query.filter(Produto.fornecedor_id == fornecedor_id)
    if categoria_id:
        query = query.filter(Produto.categoria_id == categoria_id)
    if marca:
        query = query.filter(Produto.marca.ilike(f"%{marca}%"))
    produtos = query.order_by(Produto.nome).all()
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    proximo_pedido = db.query(func.max(PedidoVenda.numero)).scalar()
    try:
        proximo_pedido = str(int(proximo_pedido) + 1) if proximo_pedido else "1"
    except:
        proximo_pedido = "1"
    return request.app.state.templates.TemplateResponse(
        "produtos/listar.html",
        {"request": request, "produtos": produtos, "fornecedores": fornecedores, "categorias": categorias, "busca": busca, "situacao": situacao, "fornecedor_id": fornecedor_id, "categoria_id": categoria_id, "marca": marca, "proximo_pedido": proximo_pedido}
    )


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
    marca: str = Form(""),
    peso_liq: float = Form(0),
    peso_bruto: float = Form(0),
    altura: float = Form(0),
    largura: float = Form(0),
    profundidade: float = Form(0),
    unidade_medida: str = Form("cm"),
    estoque: float = Form(0),
    estoque_minimo: float = Form(0),
):
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
        marca=marca if marca else None,
        peso_liq=peso_liq if peso_liq else None,
        peso_bruto=peso_bruto if peso_bruto else None,
        altura=altura if altura else None,
        largura=largura if largura else None,
        profundidade=profundidade if profundidade else None,
        unidade_medida=unidade_medida,
        estoque=estoque if estoque else 0,
        estoque_minimo=estoque_minimo if estoque_minimo else 0,
        situacao="A",
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
    return request.app.state.templates.TemplateResponse(
        "produtos/form.html",
        {"request": request, "produto": produto, "fornecedores": fornecedores, "categorias": categorias, "UNIDADES_MEDIDA": UNIDADES_MEDIDA}
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
    marca: str = Form(""),
    peso_liq: float = Form(0),
    peso_bruto: float = Form(0),
    altura: float = Form(0),
    largura: float = Form(0),
    profundidade: float = Form(0),
    unidade_medida: str = Form("cm"),
    estoque: float = Form(0),
    estoque_minimo: float = Form(0),
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
        produto.marca = marca if marca else None
        produto.peso_liq = peso_liq if peso_liq else None
        produto.peso_bruto = peso_bruto if peso_bruto else None
        produto.altura = altura if altura else None
        produto.largura = largura if largura else None
        produto.profundidade = profundidade if profundidade else None
        produto.unidade_medida = unidade_medida
        produto.estoque = estoque if estoque else 0
        produto.estoque_minimo = estoque_minimo if estoque_minimo else 0
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


@router.get("/categorias/excluir/{categoria_id}")
def excluir_categoria(request: Request, categoria_id: int, db: Session = Depends(get_db)):
    cat = db.query(CategoriaProduto).filter(CategoriaProduto.id == categoria_id).first()
    if cat:
        db.delete(cat)
        db.commit()
    return RedirectResponse(url="/produtos/categorias", status_code=303)
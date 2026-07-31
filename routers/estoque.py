"""Rotas de controle de estoque: ajuste de inventario, historico e pecas da OS."""
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
from datetime import datetime, date
from database import get_db
from app.core.security import verificar_admin
from services.audit import registrar_auditoria
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/estoque", tags=["estoque"])


@router.get("/")
@router.get("/posicao")
def relatorio_posicao(
    request: Request, db: Session = Depends(get_db),
    categoria_id: int = Query(None), insumo: str = Query(None),
    abaixo_min: bool = Query(False), busca: str = Query(""),
    tipo: str = Query(""),
):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)
    from models import Produto, CategoriaProduto

    query = db.query(Produto)
    if tipo:
        query = query.filter(Produto.tipo == tipo)
    else:
        query = query.filter(Produto.tipo == "produto")
    if categoria_id:
        query = query.filter(Produto.categoria_id == categoria_id)
    if insumo == "sim":
        query = query.filter(Produto.eh_insumo == True)
    elif insumo == "nao":
        query = query.filter(or_(Produto.eh_insumo == False, Produto.eh_insumo.is_(None)))
    if abaixo_min:
        query = query.filter(Produto.estoque < Produto.estoque_minimo)
    if busca:
        query = query.filter(Produto.nome.ilike(f"%{busca}%"))
    produtos = query.order_by(Produto.nome).all()

    total_itens = len(produtos)
    valor_total = sum(float(p.estoque or 0) * float(p.preco_custo or 0) for p in produtos)
    valor_venda = sum(float(p.estoque or 0) * float(p.preco or 0) for p in produtos)
    qtd_zerados = sum(1 for p in produtos if (p.estoque or 0) <= 0)
    qtd_abaixo = sum(1 for p in produtos if (p.estoque or 0) < (p.estoque_minimo or 0))

    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    return request.app.state.templates.TemplateResponse(request,
        "estoque/posicao.html",
        {"request": request, "produtos": produtos, "categorias": categorias,
         "categoria_id": categoria_id, "insumo": insumo, "abaixo_min": abaixo_min,
         "busca": busca, "tipo": tipo, "total_itens": total_itens,
         "valor_total": valor_total, "valor_venda": valor_venda,
         "qtd_zerados": qtd_zerados, "qtd_abaixo": qtd_abaixo,
         "messages": _get_messages(request)}
    )


@router.get("/abaixo-minimo")
def relatorio_abaixo_minimo(request: Request, db: Session = Depends(get_db)):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)
    from models import Produto

    produtos = db.query(Produto).filter(
        Produto.tipo == "produto",
        Produto.estoque < Produto.estoque_minimo,
    ).order_by(Produto.estoque_minimo.desc()).all()
    return request.app.state.templates.TemplateResponse(request,
        "estoque/abaixo_minimo.html",
        {"request": request, "produtos": produtos, "messages": _get_messages(request)}
    )


@router.get("/movimentacoes")
def relatorio_movimentacoes(
    request: Request, db: Session = Depends(get_db),
    produto_id: int = Query(None), tipo: str = Query(""),
    data_ini: str = Query(""), data_fim: str = Query(""),
):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)
    from models_estoque import MovimentacaoEstoque
    from models import Produto

    query = db.query(MovimentacaoEstoque)
    if produto_id:
        query = query.filter(MovimentacaoEstoque.produto_id == produto_id)
    if tipo:
        query = query.filter(MovimentacaoEstoque.tipo == tipo)
    if data_ini:
        try:
            query = query.filter(MovimentacaoEstoque.data >= datetime.strptime(data_ini, "%Y-%m-%d"))
        except ValueError:
            pass
    if data_fim:
        try:
            fim = datetime.strptime(data_fim, "%Y-%m-%d")
            fim = fim.replace(hour=23, minute=59, second=59)
            query = query.filter(MovimentacaoEstoque.data <= fim)
        except ValueError:
            pass
    movs = query.order_by(MovimentacaoEstoque.data.desc()).limit(500).all()
    produtos = db.query(Produto).filter(Produto.tipo == "produto").order_by(Produto.nome).all()
    return request.app.state.templates.TemplateResponse(request,
        "estoque/movimentacoes.html",
        {"request": request, "movs": movs, "produtos": produtos,
         "produto_id": produto_id, "tipo": tipo, "data_ini": data_ini,
         "data_fim": data_fim, "messages": _get_messages(request)}
    )


@router.get("/posicao/pdf")
def relatorio_posicao_pdf(
    request: Request, db: Session = Depends(get_db),
    categoria_id: int = Query(None), insumo: str = Query(None),
    abaixo_min: bool = Query(False), busca: str = Query(""),
    tipo: str = Query(""),
):
    from models import Produto, CategoriaProduto, Empresa
    from services.nfse_pdf import gerar_pdf_estoque
    from fastapi.responses import Response

    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)

    query = db.query(Produto)
    if tipo:
        query = query.filter(Produto.tipo == tipo)
    else:
        query = query.filter(Produto.tipo == "produto")
    if categoria_id:
        query = query.filter(Produto.categoria_id == categoria_id)
    if insumo == "sim":
        query = query.filter(Produto.eh_insumo == True)
    elif insumo == "nao":
        query = query.filter(or_(Produto.eh_insumo == False, Produto.eh_insumo.is_(None)))
    if abaixo_min:
        query = query.filter(Produto.estoque < Produto.estoque_minimo)
    if busca:
        query = query.filter(Produto.nome.ilike(f"%{busca}%"))
    produtos = query.order_by(Produto.nome).all()

    valor_total = sum(float(p.estoque or 0) * float(p.preco_custo or 0) for p in produtos)
    valor_venda = sum(float(p.estoque or 0) * float(p.preco or 0) for p in produtos)
    qtd_zerados = sum(1 for p in produtos if (p.estoque or 0) <= 0)
    qtd_abaixo = sum(1 for p in produtos if (p.estoque or 0) < (p.estoque_minimo or 0))

    filtros = "Filtro: "
    partes = []
    if tipo: partes.append(f"tipo={tipo}")
    if categoria_id: partes.append("categoria")
    if insumo: partes.append(f"insumo={insumo}")
    if abaixo_min: partes.append("abaixo do mínimo")
    if busca: partes.append(f"busca='{busca}'")
    filtros += ", ".join(partes) if partes else "todos"

    empresa = db.query(Empresa).first()
    pdf = gerar_pdf_estoque(produtos, empresa, titulo="Posição de Estoque",
                            filtros=filtros, valor_total=valor_total,
                            valor_venda=valor_venda, qtd_zerados=qtd_zerados,
                            qtd_abaixo=qtd_abaixo)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=posicao_estoque.pdf"})


@router.get("/abaixo-minimo/pdf")
def relatorio_abaixo_minimo_pdf(request: Request, db: Session = Depends(get_db)):
    from models import Produto, Empresa
    from services.nfse_pdf import gerar_pdf_estoque
    from fastapi.responses import Response

    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)

    produtos = db.query(Produto).filter(
        Produto.tipo == "produto",
        Produto.estoque < Produto.estoque_minimo,
    ).order_by(Produto.estoque_minimo.desc()).all()
    empresa = db.query(Empresa).first()
    pdf = gerar_pdf_estoque(produtos, empresa, titulo="Itens Abaixo do Mínimo",
                            filtros="Filtro: abaixo do estoque mínimo")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=abaixo_minimo.pdf"})


@router.get("/movimentacoes/pdf")
def relatorio_movimentacoes_pdf(
    request: Request, db: Session = Depends(get_db),
    produto_id: int = Query(None), tipo: str = Query(""),
    data_ini: str = Query(""), data_fim: str = Query(""),
):
    from models_estoque import MovimentacaoEstoque
    from models import Empresa
    from services.nfse_pdf import gerar_pdf_movimentacoes
    from fastapi.responses import Response

    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)

    query = db.query(MovimentacaoEstoque)
    if produto_id:
        query = query.filter(MovimentacaoEstoque.produto_id == produto_id)
    if tipo:
        query = query.filter(MovimentacaoEstoque.tipo == tipo)
    if data_ini:
        try:
            query = query.filter(MovimentacaoEstoque.data >= datetime.strptime(data_ini, "%Y-%m-%d"))
        except ValueError:
            pass
    if data_fim:
        try:
            fim = datetime.strptime(data_fim, "%Y-%m-%d")
            fim = fim.replace(hour=23, minute=59, second=59)
            query = query.filter(MovimentacaoEstoque.data <= fim)
        except ValueError:
            pass
    movs = query.order_by(MovimentacaoEstoque.data.desc()).limit(500).all()

    filtros = "Filtro: "
    partes = []
    if produto_id: partes.append(f"produto #{produto_id}")
    if tipo: partes.append(f"tipo={tipo}")
    if data_ini: partes.append(f"de {data_ini}")
    if data_fim: partes.append(f"até {data_fim}")
    filtros += ", ".join(partes) if partes else "todos"
    empresa = db.query(Empresa).first()
    pdf = gerar_pdf_movimentacoes(movs, empresa, filtros=filtros)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=movimentacoes_estoque.pdf"})


@router.post("/produto/{produto_id}/ajustar")
def ajustar_estoque(
    request: Request, produto_id: int, db: Session = Depends(get_db),
    quantidade_fisica: float = Form(...), motivo: str = Form(""),
    variacoes: str = Form(""),
):
    from services.estoque_service import ajuste_inventario, ajustar_variacao
    from models import Produto

    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)

    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if not produto:
        request.session["error"] = "Produto nao encontrado"
        return RedirectResponse(url=request.headers.get("Referer", "/produtos"), status_code=303)

    usuario_id = request.session.get("usuario_id")

    # Produto com variacoes: ajuste por variacao
    if produto.variacoes:
        import json
        lista = []
        try:
            lista = json.loads(variacoes) if variacoes else []
        except (json.JSONDecodeError, TypeError):
            lista = []
        if not lista:
            request.session["error"] = "Informe a quantidade fisica de cada variacao"
            return RedirectResponse(url=request.headers.get("Referer", "/produtos"), status_code=303)
        ajustados = 0
        for v in lista:
            try:
                vid = int(v.get("variacao_id"))
                qtd = float(v.get("quantidade_fisica"))
            except (ValueError, TypeError, AttributeError):
                continue
            if ajustar_variacao(db, produto_id, vid, qtd, usuario_id=usuario_id, motivo=motivo or "Ajuste de inventario"):
                ajustados += 1
        if ajustados:
            request.session["message"] = f"Estoque de {produto.nome} ajustado por variacao ({ajustados} variacao(oes))"
            try:
                registrar_auditoria(db, usuario_id, "ajuste_estoque", "produto", produto_id,
                                    detalhes=f"por_variacao={ajustados}; motivo={motivo}")
            except Exception:
                pass
        else:
            request.session["message"] = "Nenhuma alteracao necessaria (saldo ja conferia)"
    else:
        ok = ajuste_inventario(db, produto_id, quantidade_fisica, usuario_id=usuario_id, motivo=motivo or "Ajuste de inventario")
        if ok:
            request.session["message"] = f"Estoque de {produto.nome} ajustado para {quantidade_fisica}"
            try:
                registrar_auditoria(db, usuario_id, "ajuste_estoque", "produto", produto_id,
                                    detalhes=f"qtd_fisica={quantidade_fisica}; motivo={motivo}")
            except Exception:
                pass
        else:
            request.session["message"] = "Nenhuma alteracao necessaria (saldo ja conferia) ou produto invalido"
    return RedirectResponse(url=request.headers.get("Referer", "/produtos"), status_code=303)


@router.get("/produto/{produto_id}/movimentacoes")
def historico_estoque(request: Request, produto_id: int, db: Session = Depends(get_db)):
    from models_estoque import MovimentacaoEstoque
    from models import Produto

    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)

    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    movs = db.query(MovimentacaoEstoque).filter(
        MovimentacaoEstoque.produto_id == produto_id
    ).order_by(MovimentacaoEstoque.data.desc()).all()
    return request.app.state.templates.TemplateResponse(request,
        "estoque/historico.html",
        {"request": request, "produto": produto, "movs": movs,
         "messages": _get_messages(request)}
    )


@router.post("/os/{os_id}/peca/adicionar")
def adicionar_peca_os(
    request: Request, os_id: int, db: Session = Depends(get_db),
    produto_id: int = Form(...), quantidade: float = Form(1),
):
    from models_estoque import OSPeca
    from models import OrdemServico, Produto

    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)

    os_obj = db.query(OrdemServico).filter(OrdemServico.id == os_id).first()
    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if not os_obj or not produto:
        request.session["error"] = "OS ou produto invalido"
        return RedirectResponse(url=request.headers.get("Referer", "/ordens-servico"), status_code=303)

    peca = OSPeca(os_id=os_id, produto_id=produto_id, quantidade=quantidade,
                   valor_unitario=produto.preco_custo or produto.preco)
    db.add(peca)
    db.commit()
    request.session["message"] = f"Peca {produto.nome} vinculada a OS #{os_id}"
    return RedirectResponse(url=request.headers.get("Referer", f"/ordens-servico/{os_id}"), status_code=303)


@router.post("/os/{os_id}/peca/{peca_id}/remover")
def remover_peca_os(request: Request, os_id: int, peca_id: int, db: Session = Depends(get_db)):
    from models_estoque import OSPeca
    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)
    peca = db.query(OSPeca).filter(OSPeca.id == peca_id, OSPeca.os_id == os_id).first()
    if peca:
        db.delete(peca)
        db.commit()
        request.session["message"] = "Peca removida da OS"
    return RedirectResponse(url=request.headers.get("Referer", f"/ordens-servico/{os_id}"), status_code=303)


def _get_messages(request):
    msgs = []
    for key in ("message", "error"):
        if request.session.get(key):
            msgs.append({"tipo": "success" if key == "message" else "danger", "texto": request.session.get(key)})
    return msgs

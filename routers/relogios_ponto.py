"""
Router de Relógios de Ponto (REPs) vendidos.

Cadastro standalone de relógios/ponto/catracas/controladores de acesso vendidos,
com vínculos opcionais a Cliente, Produto (modelo) e Fornecedor, campos de
cache para exibição rápida, e relatórios de quantidade/valor por modelo,
marca, fornecedor e cliente.
"""
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from datetime import datetime, date
from database import get_db
from models_relogios import RelogioPonto
from app.core.security import confirma_senha_usuario
from services.audit import registrar_auditoria


router = APIRouter(prefix="/relogios-ponto", tags=["Equipamentos Vendidos"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_valor_br(valor):
    """Converte texto de valor em float, aceitando formato brasileiro.

    Ex.: '1500', '1500,00', '1.500,00', 'R$ 1.500,00', '1,500.00'.
    Retorna None se não conseguir interpretar.
    """
    if not valor:
        return None
    s = valor.strip().upper().replace("R$", "").replace("$", "").strip()
    if not s:
        return None
    if "," in s and "." in s:
        # O separador que aparece por último é o decimal.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    s = "".join(c for c in s if c.isdigit() or c == ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _preencher_cache(db: Session, r: RelogioPonto, cliente_id, produto_id, fornecedor_id):
    """Preenche as colunas de cache a partir dos cadastros vinculados."""
    r.cliente_nome_cache = None
    r.cpf_cnpj_cache = None
    r.contato_cache = None
    r.modelo_cache = None
    r.marca_cache = None
    r.fornecedor_nome_cache = None

    if cliente_id:
        from models import Cliente
        c = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        if c:
            r.cliente_nome_cache = c.nome
            r.cpf_cnpj_cache = c.cpf_cnpj
            r.contato_cache = c.contato
    if produto_id:
        from models import Produto
        p = db.query(Produto).filter(Produto.id == produto_id).first()
        if p:
            r.modelo_cache = p.nome
            # Marca: prioriza a marca cadastrada (marca_id -> MarcaProduto.nome);
            # cai no texto livre (Produto.marca) quando não houver relação.
            marca = p.marca
            if (not marca) and p.marca_rel:
                marca = p.marca_rel.nome
            r.marca_cache = marca
    if fornecedor_id:
        from models import Fornecedor
        f = db.query(Fornecedor).filter(Fornecedor.id == fornecedor_id).first()
        if f:
            r.fornecedor_nome_cache = f.nome


def _aplicar_filtros(query, busca, marca, fornecedor_id, atestado, data_ini, data_fim):
    if busca:
        like = f"%{busca}%"
        query = query.filter(
            RelogioPonto.cliente_nome_cache.ilike(like)
            | RelogioPonto.modelo_cache.ilike(like)
            | RelogioPonto.marca_cache.ilike(like)
            | RelogioPonto.numero_serial.ilike(like)
            | RelogioPonto.cpf_cnpj_cache.ilike(like)
        )
    if marca:
        query = query.filter(RelogioPonto.marca_cache.ilike(f"%{marca}%"))
    if fornecedor_id:
        query = query.filter(RelogioPonto.fornecedor_id == fornecedor_id)
    if atestado in ("1", "0"):
        query = query.filter(RelogioPonto.atestado_tecnico == (atestado == "1"))
    if data_ini:
        try:
            query = query.filter(RelogioPonto.data_venda >= datetime.strptime(data_ini, "%Y-%m-%d").date())
        except ValueError:
            pass
    if data_fim:
        try:
            query = query.filter(RelogioPonto.data_venda <= datetime.strptime(data_fim, "%Y-%m-%d").date())
        except ValueError:
            pass
    return query


# ---------------------------------------------------------------------------
# Listagem
# ---------------------------------------------------------------------------
@router.get("/")
def listar_relogios(
    request: Request, db: Session = Depends(get_db),
    busca: str = Query(""), marca: str = Query(""),
    fornecedor_id: str = Query(""), atestado: str = Query(""),
    data_ini: str = Query(""), data_fim: str = Query(""),
    page: int = Query(1), per_page: int = Query(20),
):
    fid = None
    if fornecedor_id:
        try:
            fid = int(fornecedor_id)
        except ValueError:
            fid = None
    fornecedor_nome = ""
    if fid:
        from models import Fornecedor
        f = db.query(Fornecedor).filter(Fornecedor.id == fid).first()
        fornecedor_nome = f.nome if f else ""
    query = db.query(RelogioPonto)
    query = _aplicar_filtros(query, busca, marca, fid, atestado, data_ini, data_fim)
    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    relogios = (
        query.order_by(RelogioPonto.data_venda.desc(), RelogioPonto.id.desc())
        .offset(offset).limit(per_page).all()
    )
    return request.app.state.templates.TemplateResponse(request,
        "relogios_ponto/listar.html",
        {
            "request": request, "relogios": relogios,
            "busca": busca, "marca": marca, "fornecedor_id": fornecedor_id or "",
            "fornecedor_nome": fornecedor_nome,
            "atestado": atestado, "data_ini": data_ini, "data_fim": data_fim,
            "page": page, "per_page": per_page,
            "total_pages": total_pages, "total_count": total_count,
        }
    )


# ---------------------------------------------------------------------------
# Novo
# ---------------------------------------------------------------------------
@router.get("/novo")
def novo_relogio(request: Request, db: Session = Depends(get_db)):
    return request.app.state.templates.TemplateResponse(request,
        "relogios_ponto/form.html",
        {"request": request, "relogio": None,
         "hoje": date.today().isoformat()}
    )


@router.post("/novo")
def criar_relogio(
    request: Request, db: Session = Depends(get_db),
    data_venda: str = Form(""),
    cliente_id: int = Form(None),
    produto_id: int = Form(None),
    fornecedor_id: int = Form(None),
    numero_serial: str = Form(""),
    valor: str = Form(""),
    atestado_tecnico: str = Form(""),
    observacao: str = Form(""),
    observacao2: str = Form(""),
):
    r = RelogioPonto()
    if data_venda:
        try:
            r.data_venda = datetime.strptime(data_venda, "%Y-%m-%d").date()
        except ValueError:
            pass
    r.cliente_id = cliente_id or None
    r.produto_id = produto_id or None
    r.fornecedor_id = fornecedor_id or None
    r.numero_serial = numero_serial.strip() or None
    r.valor = _parse_valor_br(valor)
    r.atestado_tecnico = (atestado_tecnico == "1" or atestado_tecnico == "on")
    r.observacao = observacao or None
    r.observacao2 = observacao2 or None
    r.usuario_id = request.session.get("user_id")
    _preencher_cache(db, r, r.cliente_id, r.produto_id, r.fornecedor_id)
    db.add(r)
    db.commit()
    request.session["message"] = {"tipo": "success", "texto": "Equipamento vendido cadastrado com sucesso."}
    return RedirectResponse(url="/relogios-ponto", status_code=303)


# ---------------------------------------------------------------------------
# Relatório / Consultas agregadas  (deve vir ANTES de /{relogio_id})
# ---------------------------------------------------------------------------
@router.get("/relatorio")
def relatorio_relogios(
    request: Request, db: Session = Depends(get_db),
    busca: str = Query(""), marca: str = Query(""),
    fornecedor_id: str = Query(""), atestado: str = Query(""),
    data_ini: str = Query(""), data_fim: str = Query(""),
):
    fid = None
    if fornecedor_id:
        try:
            fid = int(fornecedor_id)
        except ValueError:
            fid = None
    query = db.query(RelogioPonto)
    query = _aplicar_filtros(query, busca, marca, fid, atestado, data_ini, data_fim)

    total_qtd = query.count()
    total_valor = query.with_entities(func.coalesce(func.sum(RelogioPonto.valor), 0)).scalar() or 0
    total_atestado = query.filter(RelogioPonto.atestado_tecnico == True).count()

    def agrupar(campo):
        linhas = (
            db.query(
                campo.label("chave"),
                func.count(RelogioPonto.id).label("qtd"),
                func.coalesce(func.sum(RelogioPonto.valor), 0).label("soma"),
            )
            .select_from(RelogioPonto)
        )
        linhas = _aplicar_filtros(linhas, busca, marca, fid, atestado, data_ini, data_fim)
        return linhas.group_by(campo).order_by(func.count(RelogioPonto.id).desc()).all()

    por_modelo = agrupar(RelogioPonto.modelo_cache)
    por_marca = agrupar(RelogioPonto.marca_cache)
    por_fornecedor = agrupar(RelogioPonto.fornecedor_nome_cache)
    por_cliente = agrupar(RelogioPonto.cliente_nome_cache)

    return request.app.state.templates.TemplateResponse(request,
        "relogios_ponto/relatorio.html",
        {
            "request": request,
            "busca": busca, "marca": marca, "fornecedor_id": fid or "",
            "atestado": atestado, "data_ini": data_ini, "data_fim": data_fim,
            "total_qtd": total_qtd, "total_valor": float(total_valor), "total_atestado": total_atestado,
            "por_modelo": por_modelo, "por_marca": por_marca,
            "por_fornecedor": por_fornecedor, "por_cliente": por_cliente,
        }
    )


# ---------------------------------------------------------------------------
# Detalhe
# ---------------------------------------------------------------------------
@router.get("/{relogio_id}")
def detalhe_relogio(request: Request, relogio_id: int, db: Session = Depends(get_db)):
    relogio = db.query(RelogioPonto).options(
        joinedload(RelogioPonto.cliente),
        joinedload(RelogioPonto.produto),
        joinedload(RelogioPonto.fornecedor),
    ).filter(RelogioPonto.id == relogio_id).first()
    if not relogio:
        return RedirectResponse(url="/relogios-ponto", status_code=303)
    return request.app.state.templates.TemplateResponse(request,
        "relogios_ponto/detalhe.html",
        {"request": request, "relogio": relogio}
    )


# ---------------------------------------------------------------------------
# Editar
# ---------------------------------------------------------------------------
@router.get("/{relogio_id}/editar")
def editar_relogio(request: Request, relogio_id: int, db: Session = Depends(get_db)):
    relogio = db.query(RelogioPonto).options(
        joinedload(RelogioPonto.cliente),
        joinedload(RelogioPonto.produto),
        joinedload(RelogioPonto.fornecedor),
    ).filter(RelogioPonto.id == relogio_id).first()
    if not relogio:
        return RedirectResponse(url="/relogios-ponto", status_code=303)
    return request.app.state.templates.TemplateResponse(request,
        "relogios_ponto/form.html",
        {"request": request, "relogio": relogio,
         "hoje": (relogio.data_venda or date.today()).isoformat()}
    )


@router.post("/{relogio_id}/editar")
def atualizar_relogio(
    request: Request, relogio_id: int, db: Session = Depends(get_db),
    data_venda: str = Form(""),
    cliente_id: int = Form(None),
    produto_id: int = Form(None),
    fornecedor_id: int = Form(None),
    numero_serial: str = Form(""),
    valor: str = Form(""),
    atestado_tecnico: str = Form(""),
    observacao: str = Form(""),
    observacao2: str = Form(""),
):
    relogio = db.query(RelogioPonto).filter(RelogioPonto.id == relogio_id).first()
    if not relogio:
        return RedirectResponse(url="/relogios-ponto", status_code=303)
    if data_venda:
        try:
            relogio.data_venda = datetime.strptime(data_venda, "%Y-%m-%d").date()
        except ValueError:
            relogio.data_venda = None
    relogio.cliente_id = cliente_id or None
    relogio.produto_id = produto_id or None
    relogio.fornecedor_id = fornecedor_id or None
    relogio.numero_serial = numero_serial.strip() or None
    relogio.valor = _parse_valor_br(valor)
    relogio.atestado_tecnico = (atestado_tecnico == "1" or atestado_tecnico == "on")
    relogio.observacao = observacao or None
    relogio.observacao2 = observacao2 or None
    _preencher_cache(db, relogio, relogio.cliente_id, relogio.produto_id, relogio.fornecedor_id)
    relogio.updated_at = datetime.now()
    db.commit()
    request.session["message"] = {"tipo": "success", "texto": "Equipamento vendido atualizado com sucesso."}
    return RedirectResponse(url=f"/relogios-ponto/{relogio.id}", status_code=303)


# ---------------------------------------------------------------------------
# Excluir
# ---------------------------------------------------------------------------
@router.post("/{relogio_id}/excluir")
def excluir_relogio(request: Request, relogio_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    if not confirma_senha_usuario(request, db, senha):
        return JSONResponse({"success": False, "error": "Senha inválida ou usuário não autorizado"}, status_code=403)
    relogio = db.query(RelogioPonto).filter(RelogioPonto.id == relogio_id).first()
    if not relogio:
        return JSONResponse({"success": False, "error": "Registro não encontrado"})
    ident = f"{relogio.modelo_cache or 'REP'} - {relogio.numero_serial or relogio.cliente_nome_cache or ''}"
    db.delete(relogio)
    db.commit()
    registrar_auditoria(
        db, request.session.get("user_id"), "excluir",
        "relogio_ponto", relogio_id, f"Relógio de ponto: {ident}",
        request.client.host if request.client else None
    )
    return {"success": True, "redirect": "/relogios-ponto", "message": "Registro excluído."}

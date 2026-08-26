import logging
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime
from database import get_db
from models import Produto, PedidoVenda, PedidoVendaItem, Cliente, StatusPedido, Fornecedor, FormaPagamento, ContaReceber, StatusConta, ProdutoVariacao, ProdutoComposicao, Empresa, PedidoConsolidadoItemOrigem
from models_nfe import NFSe, NFe
from app.core.security import confirma_senha_usuario
from services.audit import registrar_auditoria

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pedidos", tags=["Pedidos"])

STATUS_PEDIDO_LABELS = {
    StatusPedido.PENDENTE: "Pendente",
    StatusPedido.APROVADO: "Aprovado",
    StatusPedido.FATURADO: "Faturado",
    StatusPedido.PRE_VENDA: "Pré-venda",
    StatusPedido.CONSOLIDADO: "Consolidado",
    StatusPedido.AGRUPADO: "Agrupado",
    StatusPedido.CANCELADO: "Cancelado",
}

FORMAS_PAGAMENTO = {
    FormaPagamento.AVISTA: "À Vista",
    FormaPagamento.APRAZO: "À Prazo",
    FormaPagamento.CARTAO_CREDITO: "Cartão Crédito",
    FormaPagamento.CARTAO_DEBITO: "Cartão Débito",
    FormaPagamento.BOLETO: "Boleto",
}


def _proximo_numero_pedido(db) -> str:
    """Calcula o próximo número LIVRE de pedido.

    Ignora números não numéricos (ex.: 'PV-12'), que antes derrubavam a tela de
    novo pedido com ValueError, e garante que o número ainda não exista — a
    coluna é declarada unique no modelo.
    """
    usados = {
        str(row[0]).strip()
        for row in db.query(PedidoVenda.numero).filter(PedidoVenda.numero.isnot(None)).all()
        if row[0] is not None
    }
    maior = 0
    for valor in usados:
        try:
            maior = max(maior, int(valor))
        except (TypeError, ValueError):
            continue
    proximo = maior + 1
    while str(proximo) in usados:
        proximo += 1
    return str(proximo)


@router.get("/")
def listar_pedidos(
    request: Request, db: Session = Depends(get_db),
    busca: str = Query(""), status: str = Query(""), cliente_id: int = Query(0),
    page: int = Query(1), per_page: int = Query(20),
    sort: str = Query("data"), ordem: str = Query("desc"),
):
    query = db.query(PedidoVenda).join(Cliente)
    if busca:
        query = query.filter(Cliente.nome.ilike(f"%{busca}%") | PedidoVenda.numero.ilike(f"%{busca}%"))
    if status:
        query = query.filter(PedidoVenda.status == status)
    if cliente_id:
        query = query.filter(PedidoVenda.cliente_id == cliente_id)

    # Ordenação
    if sort == "numero":
        sort_attr = PedidoVenda.numero
    elif sort == "cliente":
        sort_attr = Cliente.nome
    elif sort == "total":
        sort_attr = PedidoVenda.total
    else:
        sort_attr = PedidoVenda.data

    if ordem == "asc":
        query = query.order_by(sort_attr.asc())
    else:
        query = query.order_by(sort_attr.desc())

    # Paginação
    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    pedidos = query.offset(offset).limit(per_page).all()

    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    return request.app.state.templates.TemplateResponse(request, 
        "pedidos/listar.html",
        {
            "request": request, "pedidos": pedidos, "busca": busca,
            "status": status,
            "STATUS_LABELS": STATUS_PEDIDO_LABELS, "clientes": clientes,
            "cliente_id": cliente_id, "page": page, "per_page": per_page,
            "total_pages": total_pages, "total_count": total_count,
            "sort": sort, "ordem": ordem
        }
    )


@router.get("/pre-venda/agrupar")
def agrupar_pre_venda(request: Request, db: Session = Depends(get_db), cliente_id: int = Query(0)):
    query = db.query(PedidoVenda).join(Cliente).filter(PedidoVenda.status == StatusPedido.PRE_VENDA)
    if cliente_id:
        query = query.filter(PedidoVenda.cliente_id == cliente_id)
    pedidos = query.order_by(Cliente.nome, PedidoVenda.data).all()
    clientes_agrupados = {}
    for p in pedidos:
        cid = p.cliente_id
        if cid not in clientes_agrupados:
            clientes_agrupados[cid] = {"cliente": p.cliente, "pedidos": [], "total": 0}
        clientes_agrupados[cid]["pedidos"].append(p)
        clientes_agrupados[cid]["total"] += p.total or 0
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    return request.app.state.templates.TemplateResponse(
        "pedidos/agrupar_pre_venda.html",
        {"request": request, "clientes_agrupados": clientes_agrupados, "clientes": clientes, "cliente_id": cliente_id}
    )


@router.post("/pre-venda/finalizar-grupo")
async def finalizar_grupo(
    request: Request, db: Session = Depends(get_db)
):
    import json
    form = await request.form()
    # Form data with multiple checkboxes named "pedido_ids"
    raw_ids = form.getlist("pedido_ids")
    ids = [int(pid) for pid in raw_ids if pid]
    pedidos = db.query(PedidoVenda).filter(PedidoVenda.id.in_(ids)).all()
    if not pedidos:
        return RedirectResponse(url="/pedidos/pre-venda/agrupar", status_code=303)
    cliente_id = pedidos[0].cliente_id
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        return RedirectResponse(url="/pedidos/pre-venda/agrupar", status_code=303)

    # Gerar número sequencial LIVRE para o novo pedido agrupado
    numero = _proximo_numero_pedido(db)
    empresa = db.query(Empresa).with_for_update().first()
    if empresa:
        try:
            empresa.ultimo_numero_pedido = max(int(numero), empresa.ultimo_numero_pedido or 0)
        except (TypeError, ValueError):
            pass

    novo_pedido = PedidoVenda(
        cliente_id=cliente_id,
        numero=numero,
        status=StatusPedido.FATURADO,
        tipo_pedido="venda",
        forma_pagamento=FormaPagamento.AVISTA
    )
    db.add(novo_pedido)
    db.flush()

    # Calcular descrição dos pedidos agrupados ANTES do loop
    pedidos_numeros = ", ".join([p.numero or f"#{p.id}" for p in pedidos])
    # Adicionar todos os itens
    for p in pedidos:
        for item in p.itens:
            # Pula itens-filho de kit: o total do pedido considera apenas o
            # kit-pai (a explosao e recriada na emissao da nota).
            if item.item_pai_id is not None:
                continue
            novo_item = PedidoVendaItem(
                pedido_id=novo_pedido.id,
                produto_id=item.produto_id,
                variacao_id=item.variacao_id if item.variacao_id else None,
                descricao=item.descricao,
                quantidade=item.quantidade,
                preco_unitario=item.preco_unitario,
                total=item.total,
                fornecedor_id=item.fornecedor_id
            )
            db.add(novo_item)
            novo_pedido.total = (novo_pedido.total or 0) + (item.total or 0)
        # Manter pedido antigo, apenas marcar referência.
        # Status AGRUPADO (e nao FATURADO) evita dupla contagem de receita:
        # o pedido agrupado (novo_pedido) e o unico considerado faturado.
        p.pedido_agrupado_id = novo_pedido.id
        p.status = StatusPedido.AGRUPADO
    novo_pedido.observacao = f"Pedidos agrupados: {pedidos_numeros}"
    # Gera cobrança para o pedido agrupado (evita pedido "solta" sem
    # conta a receber) — mesmo padrão de finalizar_pedido.
    try:
        from services.parcelamento import gerar_contas_receber, contas_receber_existentes_para, numero_documento_para_cobranca
        if not contas_receber_existentes_para(db, pedido=novo_pedido):
            venc = novo_pedido.data or date.today()
            gerar_contas_receber(
                db,
                cliente_id=cliente_id,
                descricao=f"Pedido agrupado {novo_pedido.numero or '#' + str(novo_pedido.id)}",
                valor_total=novo_pedido.total or 0,
                primeiro_vencimento=venc,
                num_parcelas=1,
                intervalo_dias=0,
                forma_pagamento="NFSe",
                numero_documento=numero_documento_para_cobranca(pedido=novo_pedido)
                or (str(novo_pedido.numero) if novo_pedido.numero else str(novo_pedido.id)),
                pedido_id=novo_pedido.id,
            )
    except Exception:
        logger.exception("Erro ao gerar cobrança do pedido agrupado %s", novo_pedido.id)
    db.commit()
    return RedirectResponse(url=f"/pedidos/{novo_pedido.id}", status_code=303)


@router.get("/novo")
def novo_pedido(request: Request, db: Session = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    itens_disponiveis = db.query(Produto).options(
        selectinload(Produto.variacoes),
        selectinload(Produto.composicoes)
    ).order_by(Produto.nome).all()
    itens_json = [{"id": i.id, "nome": i.nome, "preco": float(i.preco or 0), "tipo": i.tipo, "descricao": i.descricao or i.nome, "variacoes": [{"id": v.id, "nome_variacao": v.nome_variacao, "preco_adicional": float(v.preco_adicional or 0)} for v in i.variacoes], "composicoes": [{"insumo_id": c.insumo_id, "quantidade": c.quantidade_padrao} for c in i.composicoes]} for i in itens_disponiveis if i.tipo in ('produto', 'servico', 'kit')]
    clientes_json = [{"id": c.id, "nome": c.nome} for c in clientes]
    hoje = date.today().isoformat()
    proximo_numero = _proximo_numero_pedido(db)
    return request.app.state.templates.TemplateResponse(
        "pedidos/form.html",
        {"request": request, "clientes": clientes, "pedido": None, "date": date, "hoje": hoje, "proximo_numero": proximo_numero, "itens_json": itens_json, "itens_disponiveis": itens_disponiveis, "clientes_json": clientes_json}
    )


@router.post("/salvar")
def salvar_pedido(
    request: Request, db: Session = Depends(get_db),
    cliente_id: str = Form(""),
    numero: str = Form(""),
    data: str = Form(""),
    observacao: str = Form(""),
    forma_pagamento: str = Form("avista"),
    itens: str = Form("[]"),
    pedido_id: str = Form(""),
    acao: str = Form("emitir")
):
    import json
    from sqlalchemy import func
    cliente_id_int = int(cliente_id) if cliente_id else None
    if not cliente_id_int:
        return RedirectResponse(url="/pedidos/", status_code=303)
    pedido_id_int = int(pedido_id) if pedido_id else None
    if pedido_id:
        # EDIÇÃO: Atualizar pedido existente
        pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
        if not pedido:
            return RedirectResponse(url="/pedidos/", status_code=303)
        pedido.cliente_id = cliente_id_int
        if numero:
            pedido.numero = numero
        if data:
            pedido.data = date.fromisoformat(data)
        pedido.observacao = observacao
        if forma_pagamento == "prazo":
            pedido.forma_pagamento = FormaPagamento.APRAZO
        else:
            pedido.forma_pagamento = FormaPagamento.AVISTA
        # Limpar itens antigos
        db.query(PedidoVendaItem).filter(PedidoVendaItem.pedido_id == pedido_id).delete()
    else:
        # NOVO: Criar novo pedido
        if not numero:
            numero = _proximo_numero_pedido(db)
        pedido = PedidoVenda(
            cliente_id=cliente_id_int,
            numero=numero,
            data=date.fromisoformat(data) if data else date.today(),
            observacao=observacao,
            forma_pagamento=FormaPagamento.AVISTA if forma_pagamento != "prazo" else FormaPagamento.APRAZO,
        )
        db.add(pedido)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Erro ao salvar o pedido (numero=%s)", numero)
        request.session["error"] = (
            f"Não foi possível salvar o pedido. Verifique se o número '{numero}' já não está em uso."
        )
        return RedirectResponse(url="/pedidos/", status_code=303)
    
    try:
        itens_list = json.loads(itens)
        total = 0
        for item in itens_list:
            total += float(item.get("quantidade", 0)) * float(item.get("preco", 0))
            item_id = int(item.get("item_id")) if item.get("item_id") else None
            variacao_id = int(item.get("variacao_id")) if item.get("variacao_id") else None
            
            produto = db.query(Produto).filter(Produto.id == item_id).first() if item_id else None
            
            pai_id = None
            if produto and produto.tipo == "kit" and produto.composicoes:
                pi_pai = PedidoVendaItem(
                    pedido_id=pedido.id,
                    produto_id=produto.id,
                    descricao=produto.nome,
                    quantidade=float(item.get("quantidade", 1)),
                    preco_unitario=float(item.get("preco", 0)),
                    total=float(item.get("quantidade", 0)) * float(item.get("preco", 0)),
                    fornecedor_id=produto.fornecedor_id
                )
                db.add(pi_pai)
                db.flush()
                pai_id = pi_pai.id
                
                for comp in produto.composicoes:
                    comp_prod = db.query(Produto).filter(Produto.id == comp.insumo_id).first()
                    if comp_prod:
                        pi_filho = PedidoVendaItem(
                            pedido_id=pedido.id,
                            item_pai_id=pai_id,
                            produto_id=comp.insumo_id,
                            descricao=comp_prod.nome,
                            quantidade=comp.quantidade_padrao,
                            preco_unitario=float(comp_prod.preco or 0),
                            total=comp.quantidade_padrao * float(comp_prod.preco or 0),
                            fornecedor_id=comp_prod.fornecedor_id
                        )
                        db.add(pi_filho)
            else:
                pi = PedidoVendaItem(
                    pedido_id=pedido.id,
                    produto_id=item_id,
                    variacao_id=variacao_id,
                    descricao=item.get("descricao", ""),
                    quantidade=float(item.get("quantidade", 1)),
                    preco_unitario=float(item.get("preco", 0)),
                    total=float(item.get("quantidade", 0)) * float(item.get("preco", 0)),
                    fornecedor_id=produto.fornecedor_id if produto else None,
                )
                db.add(pi)
        pedido.total = total
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("Erro ao salvar itens do pedido %s", pedido.id if pedido else None)
        request.session["error"] = "Erro ao salvar os itens do pedido. Verifique os valores informados."
        return RedirectResponse(url="/pedidos/", status_code=303)

    if acao == "emitir":
        return RedirectResponse(url=f"/pedidos/{pedido.id}/imprimir", status_code=303)
    return RedirectResponse(url="/pedidos/", status_code=303)


@router.get("/{pedido_id}")
def detalhe_pedido(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    pedido = db.query(PedidoVenda).options(
        selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.produto),
        selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.variacao),
        selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.filhos).selectinload(PedidoVendaItem.fornecedor)
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        return RedirectResponse(url="/pedidos/", status_code=303)
    produtos = db.query(Produto).order_by(Produto.nome).all()
    return request.app.state.templates.TemplateResponse(
        "pedidos/detalhe.html",
        {"request": request, "pedido": pedido, "produtos": produtos, "STATUS_LABELS": STATUS_PEDIDO_LABELS, "FORMAS_PAGAMENTO": FORMAS_PAGAMENTO, "pedido_agrupado": pedido.pedido_agrupado}
    )


@router.get("/{pedido_id}/contas-vinculadas")
def pedido_contas_vinculadas(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return JSONResponse({"erro": "Não autorizado"}, status_code=403)
    n_itens = db.query(func.count(PedidoVendaItem.id)).filter(PedidoVendaItem.pedido_id == pedido_id).scalar() or 0
    n_nfse = db.query(func.count(NFSe.id)).filter(NFSe.pedido_id == pedido_id).scalar() or 0
    n_nfe = db.query(func.count(NFe.id)).filter(NFe.pedido_id == pedido_id).scalar() or 0
    n_origem = db.query(func.count(PedidoConsolidadoItemOrigem.id)).filter(PedidoConsolidadoItemOrigem.pedido_origem_id == pedido_id).scalar() or 0
    qtd = n_itens + n_nfse + n_nfe + n_origem
    return JSONResponse({"pedido_id": pedido_id, "tem_contas": qtd > 0, "qtd": qtd,
                          "detalhe": {"itens": n_itens, "nfse": n_nfse, "nfe": n_nfe, "consolidacoes": n_origem}})


@router.post("/{pedido_id}/excluir")
def excluir_pedido(
    request: Request, pedido_id: int, db: Session = Depends(get_db),
    senha: str = Form(""), excluir_contas: str = Form(""),
):
    if not confirma_senha_usuario(request, db, senha):
        return JSONResponse({"erro": "Senha inválida ou usuário não autorizado"}, status_code=403)
    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        return JSONResponse({"erro": "Pedido não encontrado"}, status_code=404)
    # Bloqueia exclusão se houver NF autorizada (exige cancelamento fiscal antes)
    nfse_aut = db.query(func.count(NFSe.id)).filter(
        NFSe.pedido_id == pedido_id, NFSe.status == "autorizada"
    ).scalar() or 0
    nfe_aut = db.query(func.count(NFe.id)).filter(
        NFe.pedido_id == pedido_id, NFe.status == "issued"
    ).scalar() or 0
    if nfse_aut or nfe_aut:
        return JSONResponse(
            {"erro": "Este pedido possui NFSe/NFe autorizada na Prefeitura/SEFAZ. Cancele a nota fiscal (pela tela de NF) antes de excluir o pedido."},
            status_code=400,
        )
    try:
        # Pedido dentro de uma consolidação: tratar para não deixar órfãos
        if pedido.consolidacao_id is not None:
            from routers.consolidacoes import _rebuild_itens_consolidacao
            from models import PedidoConsolidado, StatusConsolidacao
            cons = db.query(PedidoConsolidado).filter(
                PedidoConsolidado.id == pedido.consolidacao_id
            ).first()
            if cons and cons.status != StatusConsolidacao.ABERTO:
                return JSONResponse(
                    {"erro": "Não é possível excluir: este pedido pertence a uma consolidação já finalizada. Cancele a consolidação primeiro."},
                    status_code=400,
                )
            pedido.consolidacao_id = None
            pedido.status = StatusPedido.PRE_VENDA
            if cons:
                restantes = db.query(PedidoVenda).filter(
                    PedidoVenda.consolidacao_id == cons.id
                ).count()
                if restantes == 0:
                    for it in cons.itens:
                        db.query(PedidoConsolidadoItemOrigem).filter(
                            PedidoConsolidadoItemOrigem.item_consolidado_id == it.id
                        ).delete()
                    db.query(PedidoConsolidadoItem).filter(
                        PedidoConsolidadoItem.consolidacao_id == cons.id
                    ).delete()
                    db.delete(cons)
                else:
                    _rebuild_itens_consolidacao(db, cons)
        if excluir_contas == "1":
            for nfse in db.query(NFSe).filter(NFSe.pedido_id == pedido_id).all():
                db.delete(nfse)
            for nfe in db.query(NFe).filter(NFe.pedido_id == pedido_id).all():
                db.delete(nfe)
            for orig in db.query(PedidoConsolidadoItemOrigem).filter(PedidoConsolidadoItemOrigem.pedido_origem_id == pedido_id).all():
                db.delete(orig)
        db.delete(pedido)
        db.commit()
        registrar_auditoria(
            db, request.session.get("user_id"), "excluir",
            "pedido", pedido_id, f"Pedido #{pedido_id}",
            request.client.host if request.client else None
        )
        return JSONResponse({"ok": True, "redirect": "/pedidos/"})
    except Exception:
        db.rollback()
        logger.exception("Erro ao excluir pedido %s", pedido_id)
        n_nfse = db.query(func.count(NFSe.id)).filter(NFSe.pedido_id == pedido_id).scalar() or 0
        n_nfe = db.query(func.count(NFe.id)).filter(NFe.pedido_id == pedido_id).scalar() or 0
        n_itens = db.query(func.count(PedidoVendaItem.id)).filter(PedidoVendaItem.pedido_id == pedido_id).scalar() or 0
        if (n_nfse + n_nfe + n_itens) > 0:
            return JSONResponse(
                {"erro": "Não foi possível excluir: este pedido possui registros vinculados (NFSe, NFe ou itens). Marque a opção para excluí-los também."},
                status_code=400,
            )
        return JSONResponse({"erro": "Erro interno ao excluir o pedido"}, status_code=500)


@router.post("/{pedido_id}/status")
def atualizar_status(
    request: Request, pedido_id: int, db: Session = Depends(get_db),
    status: str = Form(...)
):
    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if pedido:
        novo = None
        try:
            novo = StatusPedido(status)
        except ValueError:
            request.session["error"] = "Status inválido"
            return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)
        # Regra de ouro: pedido já faturado não pode mudar de status.
        if pedido.status == StatusPedido.FATURADO:
            request.session["error"] = "Pedido já faturado não pode ter o status alterado"
            return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)
        # Não permitir faturar diretamente um pedido que já pertence a uma consolidação
        # (o faturamento deve ocorrer pela consolidação).
        if novo == StatusPedido.FATURADO and pedido.consolidacao_id is not None:
            request.session["error"] = "Este pedido pertence a uma consolidação; fature pela consolidação"
            return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)
        try:
            pedido.status = novo
            db.commit()
        except Exception:
            logger.exception("Erro ao atualizar status do pedido %s", pedido_id)
            request.session["error"] = "Erro ao atualizar status"
            return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)
        if novo == StatusPedido.FATURADO:
            # Apenas informa: mudar o status para Faturado NAO gera cobranca.
            # Para gerar contas a receber/boleto, deve-se usar "Finalizar Pedido".
            request.session["warning"] = (
                "Pedido marcado como Faturado. Atenção: esta ação NÃO gera a cobrança "
                "(contas a receber/boleto). Para gerar a cobrança, use o botão "
                "\"Finalizar Pedido\" antes de faturar."
            )
    return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)


@router.get("/{pedido_id}/editar")
def editar_pedido(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    from sqlalchemy.orm import selectinload, joinedload
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    itens_disponiveis = db.query(Produto).options(
        selectinload(Produto.variacoes),
        selectinload(Produto.composicoes)
    ).order_by(Produto.nome).all()
    itens_json = [{"id": i.id, "nome": i.nome, "preco": float(i.preco or 0), "tipo": i.tipo, "descricao": i.descricao or i.nome, "variacoes": [{"id": v.id, "nome_variacao": v.nome_variacao, "preco_adicional": float(v.preco_adicional or 0)} for v in i.variacoes], "composicoes": [{"insumo_id": c.insumo_id, "quantidade": c.quantidade_padrao} for c in i.composicoes]} for i in itens_disponiveis if i.tipo in ('produto', 'servico', 'kit')]
    clientes_json = [{"id": c.id, "nome": c.nome} for c in clientes]
    hoje = date.today().isoformat()
    # Carrega itens com produtos e variações para edição
    pedido = db.query(PedidoVenda).options(
        joinedload(PedidoVenda.itens).joinedload(PedidoVendaItem.produto),
        joinedload(PedidoVenda.itens).joinedload(PedidoVendaItem.variacao)
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        return RedirectResponse(url="/pedidos/", status_code=303)
    return request.app.state.templates.TemplateResponse(
        "pedidos/form.html",
        {"request": request, "pedido": pedido, "clientes": clientes, "itens_json": itens_json, "itens_disponiveis": itens_disponiveis, "date": date, "hoje": hoje, "clientes_json": clientes_json}
    )


@router.post("/{pedido_id}/finalizar")
def finalizar_pedido(
    request: Request, pedido_id: int, db: Session = Depends(get_db),
    tipo_pedido: str = Form(...),
    forma_pagamento: str = Form(None),
    gerar_boleto: bool = Form(False),
    terminos_boleto: str = Form(""),
    gerar_cobranca: bool = Form(False),
    num_parcelas: int = Form(1),
    primeiro_vencimento: str = Form(""),
    intervalo_dias: int = Form(30),
):
    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if pedido:
        if pedido.consolidacao_id is not None:
            request.session["error"] = "Este pedido pertence a uma consolidação; fature pela consolidação"
            return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)
        pedido.status = StatusPedido.FATURADO
        pedido.tipo_pedido = tipo_pedido
        if forma_pagamento:
            try:
                pedido.forma_pagamento = FormaPagamento(forma_pagamento)
            except:
                pass
        pedido.gerar_boleto = gerar_boleto
        pedido.terminos_boleto = terminos_boleto
        # Cria conta(s) a receber automática(s) — com suporte a parcelamento
        contas_geradas = []
        contas_existentes = []
        if gerar_cobranca or forma_pagamento == "boleto":
            from services.parcelamento import gerar_contas_receber, contas_receber_existentes_para, numero_documento_para_cobranca
            contas_existentes = contas_receber_existentes_para(db, pedido=pedido)
            if contas_existentes:
                # Evita cobranca em duplicidade (ex.: pedido finalizado 2x ou ja
                # faturado via NFSe/NFe). Reaproveita as contas existentes.
                logger.info(
                    "Pedido %s ja possui %s conta(s) a receber; nao serao geradas novas",
                    pedido.id, len(contas_existentes),
                )
            else:
                try:
                    venc = date.fromisoformat(primeiro_vencimento) if primeiro_vencimento else (pedido.data or date.today())
                except ValueError:
                    venc = pedido.data or date.today()
                contas_geradas = gerar_contas_receber(
                    db,
                    cliente_id=pedido.cliente_id,
                    descricao=f"Pedido {pedido.numero or '#' + str(pedido.id)}",
                    valor_total=pedido.total or 0,
                    primeiro_vencimento=venc,
                    num_parcelas=num_parcelas,
                    intervalo_dias=intervalo_dias,
                    forma_pagamento=forma_pagamento or "NFSe",
                    numero_documento=numero_documento_para_cobranca(pedido=pedido)
                    or (str(pedido.numero) if pedido.numero else str(pedido.id)),
                    pedido_id=pedido.id,
                )
        db.commit()
        # Emissão imediata de TODOS os boletos das parcelas (Sicoob).
        # Contas que ja possuem boleto sao ignoradas dentro do servico.
        contas_para_boleto = contas_geradas or contas_existentes
        if contas_para_boleto and gerar_boleto:
            from services.parcelamento import emitir_boletos_contas
            ok, erros = emitir_boletos_contas(db, contas_para_boleto)
            if erros:
                request.session["error"] = (
                    f"Pedido faturado; {ok} boleto(s) emitido(s), mas houve erro(s): " + "; ".join(erros)
                )
            else:
                request.session["message"] = f"Pedido faturado e {ok} boleto(s) emitido(s) com sucesso!"
        elif contas_geradas:
            request.session["message"] = f"Pedido faturado! {len(contas_geradas)} conta(s) a receber gerada(s)."
        elif contas_existentes:
            request.session["message"] = (
                f"Pedido faturado! Já existiam {len(contas_existentes)} conta(s) a receber vinculada(s); "
                "nenhuma cobrança duplicada foi gerada."
            )
        # Baixa de estoque na finalizacao (venda sem nota). Se o pedido ja
        # gerou NFSe/NFe, a baixa ocorre na nota (evita duplicar).
        try:
            if not pedido.nfse and not pedido.nfes:
                from services.estoque_service import baixar_pedido
                baixar_pedido(db, pedido)
        except Exception as e:
            logger.warning(f"Erro ao baixar estoque do pedido: {e}")
    return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)


@router.get("/{pedido_id}/imprimir")
def imprimir_pedido(request: Request, pedido_id: int, db: Session = Depends(get_db), tipo: str = Query("faturado"), termica: str = Query("")):
    from sqlalchemy.orm import selectinload
    pedido = db.query(PedidoVenda).options(
        selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.filhos),
        selectinload(PedidoVenda.cliente)
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        return RedirectResponse(url="/pedidos/", status_code=303)
    empresa = db.query(Empresa).first()
    template_name = "pedidos/imprimir_termica.html" if termica else "pedidos/imprimir.html"
    return request.app.state.templates.TemplateResponse(
        template_name,
        {"request": request, "pedido": pedido, "empresa": empresa, "tipo_impressao": tipo,
         "STATUS_LABELS": STATUS_PEDIDO_LABELS, "FORMAS_PAGAMENTO": FORMAS_PAGAMENTO,
         "now": datetime.now()}
    )


@router.get("/{pedido_id}/pdf")
def pdf_pedido(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse
    import os
    import tempfile
    pedido = db.query(PedidoVenda).options(
        selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.filhos),
        selectinload(PedidoVenda.cliente),
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        return RedirectResponse(url="/pedidos/", status_code=303)
    empresa = db.query(Empresa).first()
    context = {
        "request": request, "pedido": pedido, "empresa": empresa,
        "tipo_impressao": "faturado", "STATUS_LABELS": STATUS_PEDIDO_LABELS,
        "FORMAS_PAGAMENTO": FORMAS_PAGAMENTO, "now": datetime.now(),
    }
    try:
        from weasyprint import HTML
        template = request.app.state.templates.env.get_template("pedidos/imprimir.html")
        html_str = template.render(**context)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=tempfile.gettempdir())
        tmp.close()
        HTML(string=html_str).write_pdf(target=tmp.name)
        return FileResponse(
            tmp.name,
            media_type="application/pdf",
            filename=f"pedido_{pedido.numero or pedido.id}.pdf",
        )
    except Exception as e:
        logger.warning("Falha ao gerar PDF do pedido %s: %s", pedido_id, e)
        # Fallback: mantém o comportamento anterior (impressão via navegador)
        return RedirectResponse(url=f"/pedidos/{pedido_id}/imprimir", status_code=303)

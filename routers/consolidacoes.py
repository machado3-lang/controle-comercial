from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from sqlalchemy.orm import Session, selectinload, joinedload
from sqlalchemy import func, desc, asc, and_, or_
from datetime import date, datetime
from decimal import Decimal
import json
import logging

from database import get_db
from models import (
    PedidoVenda, PedidoVendaItem, PedidoConsolidado, PedidoConsolidadoItem,
    PedidoConsolidadoItemOrigem, Cliente, Produto, ProdutoVariacao, StatusPedido,
    FormaPagamento, StatusConsolidacao, ContaReceber, Empresa, Usuario
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/consolidacoes", tags=["Consolidações"])

STATUS_CONSOLIDACAO_LABELS = {
    StatusConsolidacao.ABERTO: "Aberto",
    StatusConsolidacao.PROCESSANDO: "Processando",
    StatusConsolidacao.CONCLUIDO: "Concluído",
    StatusConsolidacao.CANCELADO: "Cancelado",
}


@router.get("/")
def listar_consolidacoes(
    request: Request,
    db: Session = Depends(get_db),
    busca: str = Query(""),
    status: str = Query(""),
    cliente_id: int = Query(0),
    data_inicio: str = Query(""),
    data_fim: str = Query(""),
    sort: str = Query("data"),
    ordem: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=10, le=100),
):
    query = db.query(PedidoConsolidado).join(Cliente)
    if busca:
        query = query.filter(
            Cliente.nome.ilike(f"%{busca}%") | PedidoConsolidado.numero.ilike(f"%{busca}%")
        )
    if status:
        query = query.filter(PedidoConsolidado.status == status)
    if cliente_id:
        query = query.filter(PedidoConsolidado.cliente_id == cliente_id)
    if data_inicio:
        try:
            dt = datetime.strptime(data_inicio, "%Y-%m-%d").date()
            query = query.filter(PedidoConsolidado.data >= dt)
        except ValueError:
            pass
    if data_fim:
        try:
            dt = datetime.strptime(data_fim, "%Y-%m-%d").date()
            query = query.filter(PedidoConsolidado.data <= dt)
        except ValueError:
            pass

    order_func = desc if ordem == "desc" else asc
    sort_map = {
        "numero": PedidoConsolidado.numero,
        "cliente": Cliente.nome,
        "data": PedidoConsolidado.data,
        "total": PedidoConsolidado.total,
        "status": PedidoConsolidado.status,
    }
    sort_col = sort_map.get(sort, PedidoConsolidado.data)
    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)
    consolidacoes = query.order_by(order_func(sort_col), PedidoConsolidado.id).offset(
        (page - 1) * per_page
    ).limit(per_page).all()

    return request.app.state.templates.TemplateResponse(
        request,
        "consolidacoes/listar.html",
        {
            "request": request,
            "consolidacoes": consolidacoes,
            "busca": busca,
            "status": status,
            "cliente_id": cliente_id,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "STATUS_LABELS": STATUS_CONSOLIDACAO_LABELS,
            "sort": sort,
            "ordem": ordem,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_count": total_count,
        },
    )


@router.get("/nova")
def nova_consolidacao(
    request: Request,
    db: Session = Depends(get_db),
    cliente_id: int = Query(0),
    periodo_inicio: str = Query(""),
    periodo_fim: str = Query(""),
):
    """Tela para criar nova consolidação - seleciona pré-pedidos por cliente/período"""
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    hoje = date.today().isoformat()
    primeiro_dia_mes = date.today().replace(day=1).isoformat()

    # Busca pré-pedidos disponíveis (apenas PRE_VENDA, não consolidados)
    query = db.query(PedidoVenda).join(Cliente).filter(
        PedidoVenda.status == StatusPedido.PRE_VENDA,
        PedidoVenda.consolidacao_id.is_(None)
    )
    if cliente_id:
        query = query.filter(PedidoVenda.cliente_id == cliente_id)
    if periodo_inicio:
        query = query.filter(PedidoVenda.data >= periodo_inicio)
    if periodo_fim:
        query = query.filter(PedidoVenda.data <= periodo_fim)

    pedidos = query.order_by(Cliente.nome, PedidoVenda.data).all()

    # Agrupa por cliente
    clientes_agrupados = {}
    for p in pedidos:
        cid = p.cliente_id
        if cid not in clientes_agrupados:
            clientes_agrupados[cid] = {
                "cliente": p.cliente,
                "pedidos": [],
                "total": Decimal("0"),
                "qtd_itens": 0,
            }
        clientes_agrupados[cid]["pedidos"].append(p)
        clientes_agrupados[cid]["total"] += p.total or Decimal("0")
        clientes_agrupados[cid]["qtd_itens"] += len(p.itens) if p.itens else 0

    return request.app.state.templates.TemplateResponse(
        request,
        "consolidacoes/nova.html",
        {
            "request": request,
            "clientes": clientes,
            "clientes_agrupados": clientes_agrupados,
            "cliente_id": cliente_id,
            "periodo_inicio": periodo_inicio or primeiro_dia_mes,
            "periodo_fim": periodo_fim or hoje,
            "hoje": hoje,
        },
    )


@router.post("/criar")
async def criar_consolidacao(
    request: Request,
    db: Session = Depends(get_db),
    cliente_id: int = Form(0),
    periodo_inicio: str = Form(""),
    periodo_fim: str = Form(""),
    forma_pagamento: str = Form(""),
    gerar_boleto: bool = Form(False),
    terminos_boleto: str = Form(""),
    pedido_ids: str = Form("[]"),
    observacao: str = Form(""),
):
    """Cria uma nova consolidação a partir dos pré-pedidos selecionados"""
    import json
    from sqlalchemy import func

    try:
        ids_selecionados = json.loads(pedido_ids)
    except json.JSONDecodeError:
        ids_selecionados = []

    if not ids_selecionados:
        request.session["error"] = "Nenhum pedido selecionado para consolidação"
        return RedirectResponse(url="/consolidacoes/nova", status_code=303)

    pedidos = db.query(PedidoVenda).filter(
        PedidoVenda.id.in_(ids_selecionados),
        PedidoVenda.consolidacao_id.is_(None)
    ).all()

    if not pedidos:
        request.session["error"] = "Nenhum pedido válido encontrado"
        return RedirectResponse(url="/consolidacoes/nova", status_code=303)

    # Permite pedidos de clientes diferentes: o cliente TITULAR é o do PRIMEIRO
    # pedido selecionado (matriz). Pedidos de outros clientes entram na consolidação
    # mesmo assim (ex.: filiais consolidadas no CNPJ da matriz).
    cliente_ids = list(dict.fromkeys(p.cliente_id for p in pedidos))
    cliente_id = cliente_ids[0]
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if len(cliente_ids) > 1:
        request.session["success"] = (
            f"Consolidação criada com {len(cliente_ids)} clientes diferentes. "
            f"Faturamento será em nome de {cliente.nome if cliente else 'cliente titular'} (primeiro pedido)."
        )

    # Gera número da consolidação
    empresa = db.query(Empresa).with_for_update().first()
    if empresa:
        empresa.ultimo_numero_pedido = (empresa.ultimo_numero_pedido or 0) + 1
        numero = f"CONS-{empresa.ultimo_numero_pedido:06d}"
    else:
        ultimo = db.query(func.max(PedidoConsolidado.numero)).scalar()
        num = int(ultimo.split("-")[-1]) + 1 if ultimo else 1
        numero = f"CONS-{num:06d}"

    # Cria a consolidação
    consolidacao = PedidoConsolidado(
        numero=numero,
        data=date.today(),
        data_fechamento=date.today(),
        cliente_id=cliente_id,
        status=StatusConsolidacao.ABERTO,
        forma_pagamento=forma_pagamento if forma_pagamento else None,
        gerar_boleto=gerar_boleto,
        terminos_boleto=terminos_boleto,
        observacao=observacao,
        periodo_inicio=date.fromisoformat(periodo_inicio) if periodo_inicio else None,
        periodo_fim=date.fromisoformat(periodo_fim) if periodo_fim else None,
    )
    db.add(consolidacao)
    db.flush()

    # Agrega itens dos pedidos selecionados
    itens_agregados = {}  # key: (produto_id, variacao_id, descricao, preco_unitario)
    total_consolidado = Decimal("0")

    for pedido in pedidos:
        for item in pedido.itens:
            key = (item.produto_id, item.variacao_id, item.descricao, item.preco_unitario)
            if key not in itens_agregados:
                itens_agregados[key] = {
                    "produto_id": item.produto_id,
                    "variacao_id": item.variacao_id,
                    "descricao": item.descricao,
                    "quantidade": Decimal("0"),
                    "preco_unitario": item.preco_unitario,
                    "total": Decimal("0"),
                    "unidade": "UN",
                    "ncm": None,
                    "cfop": None,
                    "origens": [],
                }
            agg = itens_agregados[key]
            agg["quantidade"] += Decimal(str(item.quantidade))
            agg["total"] += item.total or Decimal("0")
            agg["origens"].append({
                "pedido_id": pedido.id,
                "item_id": item.id,
                "quantidade": Decimal(str(item.quantidade)),
                "preco_unitario": item.preco_unitario,
                "total": item.total or Decimal("0"),
            })
        # Marca pedido como consolidado
        pedido.consolidacao_id = consolidacao.id
        pedido.status = StatusPedido.CONSOLIDADO

    # Cria itens consolidados
    for agg in itens_agregados.values():
        item_cons = PedidoConsolidadoItem(
            consolidacao_id=consolidacao.id,
            produto_id=agg["produto_id"],
            variacao_id=agg["variacao_id"],
            descricao=agg["descricao"],
            quantidade=agg["quantidade"],
            preco_unitario=agg["preco_unitario"],
            total=agg["total"],
            unidade=agg["unidade"],
            ncm=agg["ncm"],
            cfop=agg["cfop"],
        )
        db.add(item_cons)
        db.flush()

        # Cria rastreabilidade das origens
        for orig in agg["origens"]:
            item_origem = PedidoConsolidadoItemOrigem(
                item_consolidado_id=item_cons.id,
                pedido_origem_id=orig["pedido_id"],
                item_origem_id=orig["item_id"],
                quantidade=orig["quantidade"],
                preco_unitario=orig["preco_unitario"],
                total=orig["total"],
            )
            db.add(item_origem)

        total_consolidado += agg["total"]

    consolidacao.total = total_consolidado
    db.commit()
    request.session["success"] = f"Consolidação {numero} criada com sucesso!"
    return RedirectResponse(url=f"/consolidacoes/{consolidacao.id}", status_code=303)


def _rebuild_itens_consolidacao(db, consolidacao):
    """Reagrega os itens de todos os pedidos vinculados à consolidação."""
    # Limpa itens e origens atuais
    for item in consolidacao.itens:
        db.query(PedidoConsolidadoItemOrigem).filter(
            PedidoConsolidadoItemOrigem.item_consolidado_id == item.id
        ).delete()
    db.query(PedidoConsolidadoItem).filter(
        PedidoConsolidadoItem.consolidacao_id == consolidacao.id
    ).delete()
    db.flush()

    itens_agregados = {}
    total_consolidado = Decimal("0")
    for pedido in consolidacao.pedidos_origem:
        for item in pedido.itens:
            key = (item.produto_id, item.variacao_id, item.descricao, item.preco_unitario)
            if key not in itens_agregados:
                itens_agregados[key] = {
                    "produto_id": item.produto_id, "variacao_id": item.variacao_id,
                    "descricao": item.descricao, "quantidade": Decimal("0"),
                    "preco_unitario": item.preco_unitario, "total": Decimal("0"),
                    "unidade": "UN", "ncm": None, "cfop": None, "origens": [],
                }
            agg = itens_agregados[key]
            agg["quantidade"] += Decimal(str(item.quantidade))
            agg["total"] += item.total or Decimal("0")
            agg["origens"].append({
                "pedido_id": pedido.id, "item_id": item.id,
                "quantidade": Decimal(str(item.quantidade)),
                "preco_unitario": item.preco_unitario, "total": item.total or Decimal("0"),
            })
    for agg in itens_agregados.values():
        item_cons = PedidoConsolidadoItem(
            consolidacao_id=consolidacao.id, produto_id=agg["produto_id"],
            variacao_id=agg["variacao_id"], descricao=agg["descricao"],
            quantidade=agg["quantidade"], preco_unitario=agg["preco_unitario"],
            total=agg["total"], unidade=agg["unidade"], ncm=agg["ncm"], cfop=agg["cfop"],
        )
        db.add(item_cons)
        db.flush()
        for orig in agg["origens"]:
            db.add(PedidoConsolidadoItemOrigem(
                item_consolidado_id=item_cons.id, pedido_origem_id=orig["pedido_id"],
                item_origem_id=orig["item_id"], quantidade=orig["quantidade"],
                preco_unitario=orig["preco_unitario"], total=orig["total"],
            ))
        total_consolidado += agg["total"]
    consolidacao.total = total_consolidado


@router.post("/{consolidacao_id}/adicionar")
def adicionar_pedido_consolidacao(
    request: Request, consolidacao_id: int, db: Session = Depends(get_db),
    pedido_id: int = Form(...),
):
    """Adiciona um pedido PRE_VENDA a uma consolidação ABERTA (antes do faturamento)."""
    consolidacao = db.query(PedidoConsolidado).filter(PedidoConsolidado.id == consolidacao_id).first()
    if not consolidacao:
        return RedirectResponse(url="/consolidacoes/", status_code=303)
    if consolidacao.status != StatusConsolidacao.ABERTO:
        request.session["error"] = "Apenas consolidações em aberto podem receber pedidos"
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        request.session["error"] = "Pedido não encontrado"
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)
    if pedido.status != StatusPedido.PRE_VENDA:
        request.session["error"] = "Apenas pedidos em pré-venda podem ser consolidados"
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)
    if pedido.consolidacao_id is not None:
        request.session["error"] = "Este pedido já pertence a outra consolidação"
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    pedido.consolidacao_id = consolidacao.id
    pedido.status = StatusPedido.CONSOLIDADO
    _rebuild_itens_consolidacao(db, consolidacao)
    db.commit()
    request.session["success"] = "Pedido adicionado à consolidação"
    return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)


@router.post("/{consolidacao_id}/remover/{pedido_id}")
def remover_pedido_consolidacao(
    request: Request, consolidacao_id: int, pedido_id: int, db: Session = Depends(get_db),
):
    """Remove um pedido de uma consolidação ABERTA. Se ficar vazia, a consolidação é excluída."""
    consolidacao = db.query(PedidoConsolidado).filter(PedidoConsolidado.id == consolidacao_id).first()
    if not consolidacao:
        return RedirectResponse(url="/consolidacoes/", status_code=303)
    if consolidacao.status != StatusConsolidacao.ABERTO:
        request.session["error"] = "Apenas consolidações em aberto permitem remoção de pedidos"
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if pedido and pedido.consolidacao_id == consolidacao_id:
        pedido.consolidacao_id = None
        pedido.status = StatusPedido.PRE_VENDA
        db.flush()

    # Se não restarem pedidos, exclui a consolidação (não é salva vazia).
    restantes = db.query(PedidoVenda).filter(PedidoVenda.consolidacao_id == consolidacao_id).count()
    if restantes == 0:
        for item in consolidacao.itens:
            db.query(PedidoConsolidadoItemOrigem).filter(
                PedidoConsolidadoItemOrigem.item_consolidado_id == item.id
            ).delete()
        db.query(PedidoConsolidadoItem).filter(
            PedidoConsolidadoItem.consolidacao_id == consolidacao_id
        ).delete()
        db.delete(consolidacao)
        db.commit()
        request.session["info"] = "Consolidação vazia foi excluída"
        return RedirectResponse(url="/consolidacoes/", status_code=303)

    _rebuild_itens_consolidacao(db, consolidacao)
    db.commit()
    request.session["success"] = "Pedido removido da consolidação"
    return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)



@router.get("/{consolidacao_id}")
def detalhe_consolidacao(
    request: Request,
    consolidacao_id: int,
    db: Session = Depends(get_db),
):
    consolidacao = db.query(PedidoConsolidado).options(
        selectinload(PedidoConsolidado.cliente),
        selectinload(PedidoConsolidado.itens).selectinload(PedidoConsolidadoItem.produto),
        selectinload(PedidoConsolidado.itens).selectinload(PedidoConsolidadoItem.variacao),
        selectinload(PedidoConsolidado.itens).selectinload(PedidoConsolidadoItem.itens_origem).selectinload(PedidoConsolidadoItemOrigem.pedido_origem),
        selectinload(PedidoConsolidado.itens).selectinload(PedidoConsolidadoItem.itens_origem).selectinload(PedidoConsolidadoItemOrigem.item_origem),
        selectinload(PedidoConsolidado.pedidos_origem),
        selectinload(PedidoConsolidado.contas_receber),
        selectinload(PedidoConsolidado.nfse),
    ).filter(PedidoConsolidado.id == consolidacao_id).first()

    if not consolidacao:
        return RedirectResponse(url="/consolidacoes/", status_code=303)

    itens_json = []
    for item in consolidacao.itens:
        origens = []
        for o in item.itens_origem:
            origens.append({
                "pedido_origem_id": o.pedido_origem_id,
                "pedido_origem_numero": o.pedido_origem.numero if o.pedido_origem else None,
                "quantidade": float(o.quantidade or 0),
                "preco_unitario": float(o.preco_unitario or 0),
                "total": float(o.total or 0),
            })
        itens_json.append({
            "id": item.id,
            "descricao": item.descricao,
            "quantidade": float(item.quantidade or 0),
            "preco_unitario": float(item.preco_unitario or 0),
            "total": float(item.total or 0),
            "unidade": item.unidade or "UN",
            "itens_origem": origens,
        })

    pedidos_disponiveis = []
    if consolidacao.status == StatusConsolidacao.ABERTO:
        pedidos_disponiveis = (
            db.query(PedidoVenda)
            .join(Cliente)
            .filter(
                PedidoVenda.status == StatusPedido.PRE_VENDA,
                PedidoVenda.consolidacao_id.is_(None),
            )
            .order_by(Cliente.nome, PedidoVenda.numero)
            .all()
        )

    return request.app.state.templates.TemplateResponse(
        request,
        "consolidacoes/detalhe.html",
        {
            "request": request,
            "consolidacao": consolidacao,
            "itens_json": itens_json,
            "pedidos_disponiveis": pedidos_disponiveis,
            "STATUS_LABELS": STATUS_CONSOLIDACAO_LABELS,
            "FORMAS_PAGAMENTO": {
                FormaPagamento.AVISTA: "À Vista",
                FormaPagamento.APRAZO: "À Prazo",
                FormaPagamento.CARTAO_CREDITO: "Cartão Crédito",
                FormaPagamento.CARTAO_DEBITO: "Cartão Débito",
                FormaPagamento.BOLETO: "Boleto",
            },
        },
    )


@router.post("/{consolidacao_id}/finalizar")
def finalizar_consolidacao(
    request: Request,
    consolidacao_id: int,
    db: Session = Depends(get_db),
    forma_pagamento: str = Form(...),
    gerar_boleto: bool = Form(False),
    terminos_boleto: str = Form(""),
    observacao: str = Form(""),
    num_parcelas: int = Form(1),
    primeiro_vencimento: str = Form(""),
    intervalo_dias: int = Form(30),
):
    """Finaliza a consolidação - gera contas a receber e prepara para faturamento"""
    consolidacao = db.query(PedidoConsolidado).filter(
        PedidoConsolidado.id == consolidacao_id
    ).first()

    if not consolidacao:
        return RedirectResponse(url="/consolidacoes/", status_code=303)

    if consolidacao.status != StatusConsolidacao.ABERTO:
        request.session["error"] = "Apenas consolidações em aberto podem ser finalizadas"
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    consolidacao.status = StatusConsolidacao.CONCLUIDO
    consolidacao.forma_pagamento = forma_pagamento if forma_pagamento else None
    consolidacao.gerar_boleto = gerar_boleto
    consolidacao.terminos_boleto = terminos_boleto
    if observacao:
        consolidacao.observacao = (consolidacao.observacao or "") + "\n" + observacao
    consolidacao.finalizado_at = datetime.now()
    # TODO: get current user id from session
    # consolidacao.finalizado_por = current_user_id

    # Cria conta(s) a receber se necessário — com suporte a parcelamento
    contas_geradas = []
    if forma_pagamento in ["aprazo", "boleto"] or gerar_boleto:
        from services.parcelamento import gerar_contas_receber
        try:
            venc = date.fromisoformat(primeiro_vencimento) if primeiro_vencimento else (consolidacao.data_fechamento or date.today())
        except ValueError:
            venc = consolidacao.data_fechamento or date.today()
        contas_geradas = gerar_contas_receber(
            db,
            cliente_id=consolidacao.cliente_id,
            descricao=f"Consolidação {consolidacao.numero}",
            valor_total=consolidacao.total or 0,
            primeiro_vencimento=venc,
            num_parcelas=num_parcelas,
            intervalo_dias=intervalo_dias,
            forma_pagamento=forma_pagamento or "NFSe",
            consolidacao_id=consolidacao.id,
        )

    db.commit()

    # Emissão imediata de TODOS os boletos das parcelas (Sicoob)
    if contas_geradas and (gerar_boleto or forma_pagamento == "boleto"):
        from services.parcelamento import emitir_boletos_contas
        ok, erros = emitir_boletos_contas(db, contas_geradas)
        if erros:
            request.session["error"] = (
                f"Consolidação finalizada; {ok} boleto(s) emitido(s), mas houve erro(s): " + "; ".join(erros)
            )
            return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)
        request.session["success"] = f"Consolidação finalizada e {ok} boleto(s) emitido(s) com sucesso!"
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    request.session["success"] = "Consolidação finalizada com sucesso!"
    return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)


@router.post("/{consolidacao_id}/cancelar")
def cancelar_consolidacao(
    request: Request,
    consolidacao_id: int,
    db: Session = Depends(get_db),
    motivo: str = Form(""),
):
    """Cancela a consolidação e libera os pedidos originais"""
    consolidacao = db.query(PedidoConsolidado).filter(
        PedidoConsolidado.id == consolidacao_id
    ).first()

    if not consolidacao:
        return RedirectResponse(url="/consolidacoes/", status_code=303)

    if consolidacao.status == StatusConsolidacao.CANCELADO:
        request.session["error"] = "Consolidação já está cancelada"
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    # Libera os pedidos originais
    for pedido in consolidacao.pedidos_origem:
        pedido.consolidacao_id = None
        pedido.status = StatusPedido.PRE_VENDA  # Volta para pré-venda

    consolidacao.status = StatusConsolidacao.CANCELADO
    if motivo:
        consolidacao.observacao = (consolidacao.observacao or "") + f"\nCancelado: {motivo}"

    db.commit()
    request.session["success"] = "Consolidação cancelada e pedidos liberados"
    return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)


@router.get("/{consolidacao_id}/imprimir")
def imprimir_consolidacao(
    request: Request,
    consolidacao_id: int,
    db: Session = Depends(get_db),
    tipo: str = Query("consolidado"),
    termica: str = Query(""),
):
    consolidacao = db.query(PedidoConsolidado).options(
        selectinload(PedidoConsolidado.cliente),
        selectinload(PedidoConsolidado.itens).selectinload(PedidoConsolidadoItem.produto),
        selectinload(PedidoConsolidado.itens).selectinload(PedidoConsolidadoItem.variacao),
        selectinload(PedidoConsolidado.pedidos_origem),
    ).filter(PedidoConsolidado.id == consolidacao_id).first()

    if not consolidacao:
        return RedirectResponse(url="/consolidacoes/", status_code=303)

    empresa = db.query(Empresa).first()
    template_name = "consolidacoes/imprimir_termica.html" if termica else "consolidacoes/imprimir.html"

    return request.app.state.templates.TemplateResponse(
        request,
        template_name,
        {
            "request": request,
            "consolidacao": consolidacao,
            "empresa": empresa,
            "tipo_impressao": tipo,
            "STATUS_LABELS": STATUS_CONSOLIDACAO_LABELS,
            "FORMAS_PAGAMENTO": {
                FormaPagamento.AVISTA: "À Vista",
                FormaPagamento.APRAZO: "À Prazo",
                FormaPagamento.CARTAO_CREDITO: "Cartão Crédito",
                FormaPagamento.CARTAO_DEBITO: "Cartão Débito",
                FormaPagamento.BOLETO: "Boleto",
            },
        },
    )


@router.get("/pedidos-disponiveis")
def pedidos_disponiveis_api(
    request: Request,
    db: Session = Depends(get_db),
    cliente_id: int = Query(0),
    periodo_inicio: str = Query(""),
    periodo_fim: str = Query(""),
):
    """API para buscar pré-pedidos disponíveis para consolidação (AJAX)"""
    query = db.query(PedidoVenda).join(Cliente).filter(
        PedidoVenda.status == StatusPedido.PRE_VENDA,
        PedidoVenda.consolidacao_id.is_(None)
    )
    if cliente_id:
        query = query.filter(PedidoVenda.cliente_id == cliente_id)
    if periodo_inicio:
        query = query.filter(PedidoVenda.data >= periodo_inicio)
    if periodo_fim:
        query = query.filter(PedidoVenda.data <= periodo_fim)

    pedidos = query.order_by(Cliente.nome, PedidoVenda.data).all()

    clientes_agrupados = {}
    for p in pedidos:
        cid = p.cliente_id
        if cid not in clientes_agrupados:
            clientes_agrupados[cid] = {
                "cliente_id": p.cliente.id,
                "cliente_nome": p.cliente.nome,
                "pedidos": [],
                "total": 0,
                "qtd_itens": 0,
            }
        clientes_agrupados[cid]["pedidos"].append({
            "id": p.id,
            "numero": p.numero or f"#{p.id}",
            "data": p.data.isoformat() if p.data else None,
            "total": float(p.total or 0),
            "qtd_itens": len(p.itens) if p.itens else 0,
        })
        clientes_agrupados[cid]["total"] += float(p.total or 0)
        clientes_agrupados[cid]["qtd_itens"] += len(p.itens) if p.itens else 0

    return JSONResponse({
        "clientes_agrupados": list(clientes_agrupados.values()),
        "total_pedidos": len(pedidos),
        "total_geral": sum(float(p.total or 0) for p in pedidos),
    })
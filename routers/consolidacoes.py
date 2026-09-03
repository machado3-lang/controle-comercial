from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from sqlalchemy.orm import Session, selectinload, joinedload
from sqlalchemy import func, desc, asc, and_, or_
from datetime import date, datetime
from decimal import Decimal
import json
import logging

from database import get_db
from services.nfse_service import formatar_aviso_nfse
from models import (
    PedidoVenda, PedidoVendaItem, PedidoConsolidado, PedidoConsolidadoItem,
    PedidoConsolidadoItemOrigem, Cliente, Produto, ProdutoVariacao, StatusPedido,
    FormaPagamento, StatusConsolidacao, ContaReceber, StatusConta, Empresa, Usuario
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/consolidacoes", tags=["Consolidações"])

STATUS_CONSOLIDACAO_LABELS = {
    StatusConsolidacao.ABERTO: "Aberto",
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


def _parse_pedido_ids(form) -> list:
    """Extrai os IDs de pedidos enviados pelo formulário de nova consolidação.

    O formulário envia o campo `pedido_ids` REPETIDO: um checkbox por pedido
    marcado e (historicamente) um hidden com a lista em JSON. Um parâmetro
    `Form(str)` simples receberia apenas o ÚLTIMO valor (um id solto), o que
    quebrava o `in_()` com ArgumentError (erro 500). Aqui lemos todos os
    valores e aceitamos tanto ids soltos quanto JSON.
    """
    valores = []
    for campo in ("pedido_ids", "pedido_ids_json"):
        try:
            valores.extend(form.getlist(campo))
        except AttributeError:
            valor = form.get(campo)
            if valor is not None:
                valores.append(valor)

    ids, vistos = [], set()
    for bruto in valores:
        if not isinstance(bruto, str):
            continue
        bruto = bruto.strip()
        if not bruto:
            continue
        if bruto.startswith("["):
            try:
                candidatos = json.loads(bruto)
            except json.JSONDecodeError:
                candidatos = []
            if not isinstance(candidatos, list):
                candidatos = []
        else:
            candidatos = bruto.split(",")
        for candidato in candidatos:
            try:
                pedido_id = int(str(candidato).strip())
            except (TypeError, ValueError):
                continue
            if pedido_id > 0 and pedido_id not in vistos:
                vistos.add(pedido_id)
                ids.append(pedido_id)
    return ids


def _parse_data(valor):
    """Converte 'YYYY-MM-DD' em date, devolvendo None se vazio/inválido."""
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _gerar_numero_consolidacao(db) -> str:
    """Gera o próximo número CONS-XXXXXX garantindo que não exista no banco.

    `pedidos_consolidados.numero` é UNIQUE: sem esta verificação uma colisão
    (contador reiniciado, importação manual) derrubaria o commit com 500.
    """
    empresa = db.query(Empresa).with_for_update().first()
    if empresa:
        sequencia = empresa.ultimo_numero_pedido or 0
    else:
        ultimo = db.query(func.max(PedidoConsolidado.numero)).scalar()
        try:
            sequencia = int(str(ultimo).rsplit("-", 1)[-1])
        except (TypeError, ValueError):
            sequencia = 0

    for _ in range(100):
        sequencia += 1
        numero = f"CONS-{sequencia:06d}"
        existe = db.query(PedidoConsolidado.id).filter(
            PedidoConsolidado.numero == numero
        ).first()
        if not existe:
            if empresa:
                empresa.ultimo_numero_pedido = sequencia
            return numero
    raise RuntimeError("Não foi possível gerar um número livre para a consolidação")


@router.post("/criar")
async def criar_consolidacao(request: Request, db: Session = Depends(get_db)):
    """Cria uma nova consolidação a partir dos pré-pedidos selecionados"""
    form = await request.form()
    ids_selecionados = _parse_pedido_ids(form)
    forma_pagamento = (form.get("forma_pagamento") or "").strip()
    gerar_boleto = str(form.get("gerar_boleto") or "").strip().lower() in (
        "1", "true", "on", "yes", "sim"
    )
    terminos_boleto = form.get("terminos_boleto") or ""
    observacao = form.get("observacao") or ""
    periodo_inicio = _parse_data(form.get("periodo_inicio"))
    periodo_fim = _parse_data(form.get("periodo_fim"))

    if not ids_selecionados:
        request.session["error"] = "Nenhum pedido selecionado para consolidação"
        return RedirectResponse(url="/consolidacoes/nova", status_code=303)

    pedidos = db.query(PedidoVenda).filter(
        PedidoVenda.id.in_(ids_selecionados),
        PedidoVenda.consolidacao_id.is_(None)
    ).all()

    if not pedidos:
        request.session["error"] = (
            "Nenhum pedido válido encontrado (os pedidos podem já pertencer a outra consolidação)"
        )
        return RedirectResponse(url="/consolidacoes/nova", status_code=303)

    # Só pré-vendas podem ser consolidadas (mesma regra de /adicionar)
    invalidos = [p for p in pedidos if p.status != StatusPedido.PRE_VENDA]
    if invalidos:
        numeros = ", ".join(p.numero or f"#{p.id}" for p in invalidos)
        request.session["error"] = (
            f"Apenas pedidos em pré-venda podem ser consolidados. Verifique: {numeros}"
        )
        return RedirectResponse(url="/consolidacoes/nova", status_code=303)

    # Mantém a ordem de seleção: o PRIMEIRO pedido define o cliente titular.
    posicao = {pedido_id: i for i, pedido_id in enumerate(ids_selecionados)}
    pedidos.sort(key=lambda p: posicao.get(p.id, len(posicao)))

    try:
        # Permite pedidos de clientes diferentes: o cliente TITULAR é o do PRIMEIRO
        # pedido selecionado (matriz). Pedidos de outros clientes entram na consolidação
        # mesmo assim (ex.: filiais consolidadas no CNPJ da matriz).
        cliente_ids = list(dict.fromkeys(p.cliente_id for p in pedidos))
        cliente_id = cliente_ids[0]
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        aviso_multi_cliente = ""
        if len(cliente_ids) > 1:
            aviso_multi_cliente = (
                f" Atenção: {len(cliente_ids)} clientes diferentes; o faturamento será em nome de "
                f"{cliente.nome if cliente else 'cliente titular'} (primeiro pedido)."
            )

        numero = _gerar_numero_consolidacao(db)

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
            periodo_inicio=periodo_inicio,
            periodo_fim=periodo_fim,
        )
        db.add(consolidacao)
        db.flush()

        # Agrega itens dos pedidos selecionados
        itens_agregados = {}  # key: (produto_id, variacao_id, descricao, preco_unitario)
        total_consolidado = Decimal("0")

        for pedido in pedidos:
            for item in pedido.itens:
                # Pula itens-filho de kit (item_pai_id): sao representados pela
                # explosao do kit-pai na emissao, evitando dupla contagem.
                if item.item_pai_id is not None:
                    continue
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
                        "fornecedor_id": item.fornecedor_id,
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
                fornecedor_id=agg["fornecedor_id"],
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
    except Exception:
        db.rollback()
        logger.exception("Erro ao criar consolidação (pedidos=%s)", ids_selecionados)
        request.session["error"] = (
            "Erro ao criar a consolidação. Nenhuma alteração foi salva; "
            "confira os pedidos selecionados e tente novamente."
        )
        return RedirectResponse(url="/consolidacoes/nova", status_code=303)

    request.session["message"] = (
        f"Consolidação {numero} criada com {len(pedidos)} pedido(s)!{aviso_multi_cliente}"
    )
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
            if item.item_pai_id is not None:
                continue
            key = (item.produto_id, item.variacao_id, item.descricao, item.preco_unitario)
            if key not in itens_agregados:
                itens_agregados[key] = {
                    "produto_id": item.produto_id, "variacao_id": item.variacao_id,
                    "descricao": item.descricao, "quantidade": Decimal("0"),
                    "preco_unitario": item.preco_unitario, "total": Decimal("0"),
                    "unidade": "UN", "ncm": None, "cfop": None, "fornecedor_id": item.fornecedor_id, "origens": [],
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
            fornecedor_id=agg["fornecedor_id"],
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
    request.session["message"] = "Pedido adicionado à consolidação"
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
        request.session["message"] = "Consolidação vazia foi excluída"
        return RedirectResponse(url="/consolidacoes/", status_code=303)

    _rebuild_itens_consolidacao(db, consolidacao)
    db.commit()
    request.session["message"] = "Pedido removido da consolidação"
    return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)



@router.get("/pedidos-disponiveis")
def pedidos_disponiveis_api(
    request: Request,
    db: Session = Depends(get_db),
    cliente_id: int = Query(0),
    periodo_inicio: str = Query(""),
    periodo_fim: str = Query(""),
):
    """API para buscar pré-pedidos disponíveis para consolidação (AJAX).

    IMPORTANTE: precisa ser declarada ANTES de `GET /{consolidacao_id}`,
    senão a rota dinâmica captura o caminho e devolve 422.
    """
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
        selectinload(PedidoConsolidado.nfes),
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

    # Cria conta(s) a receber com suporte a parcelamento, independente da
    # forma de pagamento (à vista/cartão também geram o registro financeiro).
    from services.parcelamento import gerar_contas_receber, contas_receber_existentes_para, numero_documento_para_cobranca
    contas_geradas = contas_receber_existentes_para(db, consolidacao=consolidacao)
    if contas_geradas:
        logger.info(
            "Consolidação %s já possui %s conta(s) a receber; nenhuma nova será gerada",
            consolidacao.id, len(contas_geradas),
        )
    else:
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
            numero_documento=numero_documento_para_cobranca(consolidacao=consolidacao)
            or (str(consolidacao.numero) if consolidacao.numero else None),
            consolidacao_id=consolidacao.id,
        )

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Erro ao finalizar consolidação %s", consolidacao_id)
        request.session["error"] = "Erro ao finalizar a consolidação. Nenhuma alteração foi salva."
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    # Emissão imediata de TODOS os boletos das parcelas (Sicoob)
    if contas_geradas and (gerar_boleto or forma_pagamento == "boleto"):
        from services.parcelamento import emitir_boletos_contas
        ok, erros = emitir_boletos_contas(db, contas_geradas)
        if erros:
            request.session["error"] = (
                f"Consolidação finalizada; {ok} boleto(s) emitido(s), mas houve erro(s): " + "; ".join(erros)
            )
            return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)
        request.session["message"] = f"Consolidação finalizada e {ok} boleto(s) emitido(s) com sucesso!"
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    request.session["message"] = "Consolidação finalizada com sucesso!"
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

    # Cancelamento fiscal real: NFSe e NFe vinculadas devem ser canceladas
    # na Prefeitura/SEFAZ antes de liberar os pedidos. Falha no cancelamento
    # fiscal aborta o cancelamento da consolidação (sem efeito colateral).
    empresa = db.query(Empresa).first()

    if consolidacao.nfse and (consolidacao.nfse.status or '').lower() in ("autorizada", "pendente"):
        if not empresa:
            request.session["error"] = "Empresa não configurada; não é possível cancelar a NFSe."
            return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)
        try:
            from services.nfse_betha import BethaNfseService
            service = BethaNfseService(empresa=empresa)
            resultado = service.cancelar_nfse_assinado(
                consolidacao.nfse.numero, tpAmb=1,
                motivo=motivo or "Cancelamento da consolidação",
                chave_acesso=consolidacao.nfse.codigo_verificacao,
                protocolo_dps=consolidacao.nfse.protocolo,
            )
            if not resultado.get('sucesso'):
                erros = resultado.get('erros', [])
                msg = '; '.join(e.get('mensagem', '') for e in erros)
                aviso = formatar_aviso_nfse(msg, acao="cancelar a NFSe")
                request.session["message" if aviso["tipo"] == "warning" else "error"] = (
                    f"{aviso['texto']}. A consolidação NÃO foi cancelada."
                    if aviso["tipo"] == "warning" else
                    f"Falha ao cancelar NFSe na Prefeitura: {msg}. "
                    "A consolidação NÃO foi cancelada."
                )
                return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)
            consolidacao.nfse.status = "cancelada"
            consolidacao.nfse.mensagem_retorno = f"Cancelada via consolidação: {motivo}"
        except Exception as e:
            aviso = formatar_aviso_nfse(str(e), acao="cancelar a NFSe")
            request.session["message" if aviso["tipo"] == "warning" else "error"] = (
                f"{aviso['texto']}. A consolidação NÃO foi cancelada."
                if aviso["tipo"] == "warning" else
                f"Erro ao cancelar NFSe na Prefeitura: {str(e)}. "
                "A consolidação NÃO foi cancelada."
            )
            return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    for nfe in consolidacao.nfes:
        st = (nfe.status or '').lower()
        if st == "rascunho":
            nfe.status = "cancelled"
            continue
        if st not in ("issued", "pendente"):
            continue
        if not nfe.invoice_id:
            request.session["error"] = (
                f"NFe #{nfe.numero} sem invoiceId; não é possível cancelá-la na SEFAZ. "
                "A consolidação NÃO foi cancelada."
            )
            return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)
        try:
            from services.nfe_notaas import cancelar_nfe
            resultado = cancelar_nfe(empresa, nfe.invoice_id, motivo or "Cancelamento da consolidação")
            nfe.status = "cancelled"
            nfe.mensagem_retorno = json.dumps(resultado)
        except Exception as e:
            request.session["error"] = (
                f"Erro ao cancelar NFe #{nfe.numero} na SEFAZ: {str(e)}. "
                "A consolidação NÃO foi cancelada."
            )
            return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    # Libera os pedidos originais
    for pedido in consolidacao.pedidos_origem:
        pedido.consolidacao_id = None
        pedido.status = StatusPedido.PRE_VENDA  # Volta para pré-venda

    consolidacao.status = StatusConsolidacao.CANCELADO
    if motivo:
        consolidacao.observacao = (consolidacao.observacao or "") + f"\nCancelado: {motivo}"

    # Estorna os registros financeiros já gerados pela consolidação
    for conta in consolidacao.contas_receber:
        conta.status = StatusConta.CANCELADO
    if consolidacao.nfse and (consolidacao.nfse.status or '').lower() != "cancelada":
        consolidacao.nfse.status = "cancelada"

    db.commit()
    request.session["message"] = "Consolidação cancelada e pedidos liberados"
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
            "now": datetime.now(),
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

from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime
from database import get_db
from models import Produto, PedidoVenda, PedidoVendaItem, Cliente, StatusPedido, Fornecedor, FormaPagamento, ContaReceber, StatusConta

router = APIRouter(prefix="/pedidos", tags=["Pedidos"])

STATUS_PEDIDO_LABELS = {
    StatusPedido.PENDENTE: "Pendente",
    StatusPedido.APROVADO: "Aprovado",
    StatusPedido.FATURADO: "Faturado",
    StatusPedido.PRE_VENDA: "Pré-venda",
    StatusPedido.CANCELADO: "Cancelado",
}

FORMAS_PAGAMENTO = {
    FormaPagamento.AVISTA: "À Vista",
    FormaPagamento.APRAZO: "À Prazo",
    FormaPagamento.CARTAO_CREDITO: "Cartão Crédito",
    FormaPagamento.CARTAO_DEBITO: "Cartão Débito",
    FormaPagamento.BOLETO: "Boleto",
}


@router.get("/")
def listar_pedidos(request: Request, db: Session = Depends(get_db), busca: str = Query(""), status: str = Query(""), cliente_id: int = Query(0)):
    query = db.query(PedidoVenda).join(Cliente)
    if busca:
        query = query.filter(Cliente.nome.ilike(f"%{busca}%") | PedidoVenda.numero.ilike(f"%{busca}%"))
    if status:
        query = query.filter(PedidoVenda.status == status)
    if cliente_id:
        query = query.filter(PedidoVenda.cliente_id == cliente_id)
    pedidos = query.order_by(PedidoVenda.data.desc()).all()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    return request.app.state.templates.TemplateResponse(
        "pedidos/listar.html",
        {"request": request, "pedidos": pedidos, "busca": busca, "STATUS_LABELS": STATUS_PEDIDO_LABELS, "clientes": clientes, "cliente_id": cliente_id}
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
    novo_pedido = PedidoVenda(
        cliente_id=cliente_id,
        status=StatusPedido.FATURADO,
        tipo_pedido="venda",
        forma_pagamento=FormaPagamento.AVISTA
    )
    db.add(novo_pedido)
    db.commit()
    # Calcular descrição dos pedidos agrupados ANTES do loop
    pedidos_numeros = ", ".join([p.numero or f"#{p.id}" for p in pedidos])
    # Adicionar todos os itens
    for p in pedidos:
        for item in p.itens:
            novo_item = PedidoVendaItem(
                pedido_id=novo_pedido.id,
                produto_id=item.produto_id,
                descricao=item.descricao,
                quantidade=item.quantidade,
                preco_unitario=item.preco_unitario,
                total=item.total,
                fornecedor_id=item.fornecedor_id
            )
            db.add(novo_item)
            novo_pedido.total = (novo_pedido.total or 0) + item.total
        # Manter pedido antigo, apenas marcar referência
        p.pedido_agrupado_id = novo_pedido.id
        p.status = StatusPedido.FATURADO  # Atualiza status para indicar agrupado
    novo_pedido.observacao = f"Pedidos agrupados: {pedidos_numeros}"
    db.commit()
    return RedirectResponse(url=f"/pedidos/{novo_pedido.id}", status_code=303)


@router.get("/novo")
def novo_pedido(request: Request, db: Session = Depends(get_db)):
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    produtos = db.query(Produto).order_by(Produto.nome).all()
    produtos_json = [{"id": p.id, "nome": p.nome, "preco": p.preco, "descricao": p.descricao or p.nome} for p in produtos]
    ultimo_numero = db.query(PedidoVenda.numero).order_by(PedidoVenda.numero.desc()).first()
    proximo_numero = str(int(ultimo_numero[0]) + 1) if ultimo_numero and ultimo_numero[0] else "1"
    return request.app.state.templates.TemplateResponse(
        "pedidos/form.html",
        {"request": request, "clientes": clientes, "produtos": produtos, "pedido": None, "date": date, "proximo_numero": proximo_numero, "produtos_json": produtos_json}
    )


@router.post("/salvar")
def salvar_pedido(
    request: Request, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    numero: str = Form(""),
    data: str = Form(""),
    observacao: str = Form(""),
    itens: str = Form("[]"),
    fornecedores_itens: str = Form("[]"),
    pedido_id: int = Form(None)
):
    import json
    from sqlalchemy import func
    if pedido_id:
        # EDIÇÃO: Atualizar pedido existente
        pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
        if not pedido:
            return RedirectResponse(url="/pedidos", status_code=303)
        pedido.cliente_id = cliente_id
        if numero:
            pedido.numero = numero
        if data:
            pedido.data = date.fromisoformat(data)
        pedido.observacao = observacao
        # Limpar itens antigos
        db.query(PedidoVendaItem).filter(PedidoVendaItem.pedido_id == pedido_id).delete()
    else:
        # NOVO: Criar novo pedido
        if not numero:
            ultimo_numero = db.query(func.max(PedidoVenda.numero)).scalar()
            ultimo_val = 0
            if ultimo_numero:
                try:
                    ultimo_val = int(ultimo_numero)
                except:
                    pass
            numero = str(ultimo_val + 1)
        pedido = PedidoVenda(
            cliente_id=cliente_id,
            numero=numero,
            data=date.fromisoformat(data) if data else date.today(),
            observacao=observacao,
        )
        db.add(pedido)
    db.commit()
    
    try:
        itens_list = json.loads(itens)
        total = 0
        for item in itens_list:
            total += float(item.get("quantidade", 0)) * float(item.get("preco", 0))
            produto_id = int(item.get("produto_id")) if item.get("produto_id") else None
            produto = db.query(Produto).filter(Produto.id == produto_id).first() if produto_id else None
            pi = PedidoVendaItem(
                pedido_id=pedido.id,
                produto_id=produto_id,
                descricao=item.get("descricao", ""),
                quantidade=float(item.get("quantidade", 1)),
                preco_unitario=float(item.get("preco", 0)),
                total=float(item.get("quantidade", 0)) * float(item.get("preco", 0)),
                fornecedor_id=produto.fornecedor_id if produto else None,
            )
            db.add(pi)
        pedido.total = total
        db.commit()
    except:
        pass
    
    return RedirectResponse(url="/pedidos", status_code=303)


@router.get("/{pedido_id}")
def detalhe_pedido(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        return RedirectResponse(url="/pedidos", status_code=303)
    produtos = db.query(Produto).order_by(Produto.nome).all()
    return request.app.state.templates.TemplateResponse(
        "pedidos/detalhe.html",
        {"request": request, "pedido": pedido, "produtos": produtos, "STATUS_LABELS": STATUS_PEDIDO_LABELS, "FORMAS_PAGAMENTO": FORMAS_PAGAMENTO, "pedido_agrupado": pedido.pedido_agrupado}
    )


@router.get("/{pedido_id}/editar")
def editar_pedido(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        return RedirectResponse(url="/pedidos", status_code=303)
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    produtos = db.query(Produto).order_by(Produto.nome).all()
    produtos_json = [{"id": p.id, "nome": p.nome, "preco": p.preco, "descricao": p.descricao or p.nome} for p in produtos]
    return request.app.state.templates.TemplateResponse(
        "pedidos/form.html",
        {"request": request, "pedido": pedido, "clientes": clientes, "produtos": produtos, "produtos_json": produtos_json, "date": date}
    )


@router.post("/{pedido_id}/status")
def atualizar_status(
    request: Request, pedido_id: int, db: Session = Depends(get_db),
    status: str = Form(...)
):
    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if pedido:
        try:
            pedido.status = StatusPedido(status)
            db.commit()
        except:
            pass
    return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)


@router.get("/{pedido_id}/excluir")
def excluir_pedido(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if pedido:
        db.delete(pedido)
        db.commit()
    return RedirectResponse(url="/pedidos", status_code=303)


@router.post("/{pedido_id}/finalizar")
def finalizar_pedido(
    request: Request, pedido_id: int, db: Session = Depends(get_db),
    tipo_pedido: str = Form(...),
    forma_pagamento: str = Form(None),
    gerar_boleto: bool = Form(False),
    terminos_boleto: str = Form("")
):
    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if pedido:
        pedido.status = StatusPedido.FATURADO
        pedido.tipo_pedido = tipo_pedido
        if forma_pagamento:
            try:
                pedido.forma_pagamento = FormaPagamento(forma_pagamento)
            except:
                pass
        pedido.gerar_boleto = gerar_boleto
        pedido.terminos_boleto = terminos_boleto
        # Cria conta a receber automática
        if forma_pagamento == "boleto":
            cr = ContaReceber(
                cliente_id=pedido.cliente_id,
                descricao=f"Pedido {pedido.numero or '#' + str(pedido.id)}",
                valor=pedido.total or 0,
                data_vencimento=pedido.data,
                forma_pagamento="Boleto"
            )
            db.add(cr)
        db.commit()
    return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)


@router.get("/{pedido_id}/imprimir")
def imprimir_pedido(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        return RedirectResponse(url="/pedidos", status_code=303)
    produtos = db.query(Produto).order_by(Produto.nome).all()
    return request.app.state.templates.TemplateResponse(
        "pedidos/imprimir.html",
        {"request": request, "pedido": pedido, "STATUS_LABELS": STATUS_PEDIDO_LABELS, "FORMAS_PAGAMENTO": FORMAS_PAGAMENTO}
    )


@router.get("/{pedido_id}/pdf")
def pdf_pedido(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse
    import os
    # PDF generation would go here
    pedido = db.query(PedidoVenda).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        return RedirectResponse(url="/pedidos", status_code=303)
    # For now redirect to imprimir
    return RedirectResponse(url=f"/pedidos/{pedido_id}/imprimir", status_code=303)
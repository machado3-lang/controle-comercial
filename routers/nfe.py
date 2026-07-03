import os
import json
from datetime import datetime, date
from fastapi import APIRouter, Depends, Request, Form, Query, HTTPException
from fastapi.responses import RedirectResponse, Response, JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc

from database import get_db
from models import (Cliente, Empresa, PedidoVenda, PedidoVendaItem,
                    StatusPedido, Produto, OrdemServico, CfopNatureza)
from models_nfe import NFe, NFeItem
from services.nfe_notaas import (
    emitir_nfe, consultar_status, baixar_pdf, baixar_xml,
    cancelar_nfe, montar_payload_nfe, explodir_itens, consultar_municipios,
    _limpar_doc
)

router = APIRouter(prefix="/nfe", tags=["NFe"])

UPLOAD_DIR = "static/uploads/nfe"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _proximo_numero(empresa: Empresa, db: Session) -> int:
    numero = (empresa.ultimo_numero_nfe or 0) + 1
    empresa.ultimo_numero_nfe = numero
    db.commit()
    return numero


def _get_messages(request):
    messages = []
    msg = request.session.pop("message", None)
    if msg:
        if isinstance(msg, dict):
            messages.append(msg)
        else:
            messages.append({"tipo": "success", "texto": msg})
    err = request.session.pop("error", None)
    if err:
        messages.append({"tipo": "danger", "texto": err})
    return messages


STATUS_LABELS = {
    "rascunho": "Rascunho",
    "pendente": "Pendente",
    "queued": "Na fila",
    "processing": "Processando",
    "issued": "Autorizada",
    "error": "Erro",
    "cancelled": "Cancelada",
}


@router.get("/cfop")
def listar_cfop(request: Request, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    cfop_list = db.query(CfopNatureza).order_by(CfopNatureza.cfop).all()
    messages = _get_messages(request)
    return request.app.state.templates.TemplateResponse(
        "nfe/cfop.html",
        {"request": request, "empresa": empresa,
         "cfop_list": cfop_list, "messages": messages}
    )


@router.get("/cfop/buscar")
def buscar_cfop(
    request: Request, db: Session = Depends(get_db),
    q: str = Query(""),
):
    if not q:
        return JSONResponse({"results": []})
    results = db.query(CfopNatureza).filter(
        CfopNatureza.cfop.ilike(f"%{q}%")
    ).order_by(CfopNatureza.cfop).limit(20).all()
    return JSONResponse({"results": [{"id": r.id, "cfop": r.cfop, "natureza": r.natureza} for r in results]})


@router.post("/cfop/salvar")
def salvar_cfop(
    request: Request, db: Session = Depends(get_db),
    cfop: str = Form(...),
    natureza: str = Form(...),
    id: int = Form(0),
):
    if id:
        reg = db.query(CfopNatureza).filter(CfopNatureza.id == id).first()
        if reg:
            reg.cfop = cfop
            reg.natureza = natureza
    else:
        existing = db.query(CfopNatureza).filter(CfopNatureza.cfop == cfop).first()
        if existing:
            existing.natureza = natureza
        else:
            db.add(CfopNatureza(cfop=cfop, natureza=natureza))
    db.commit()
    return JSONResponse({"success": True})


@router.post("/cfop/excluir")
def excluir_cfop(
    request: Request, db: Session = Depends(get_db),
    id: int = Form(...),
):
    cfop = db.query(CfopNatureza).filter(CfopNatureza.id == id).first()
    if cfop:
        db.delete(cfop)
        db.commit()
    return JSONResponse({"success": True})


@router.get("/")
def listar_nfe(
    request: Request, db: Session = Depends(get_db),
    status: str = Query(""), busca: str = Query(""),
):
    empresa = db.query(Empresa).first()
    query = db.query(NFe).options(
        joinedload(NFe.pedido), joinedload(NFe.os), joinedload(NFe.itens)
    )
    if status:
        query = query.filter(NFe.status == status)
    if busca:
        query = query.filter(
            NFe.numero.cast(str).ilike(f"%{busca}%")
        )
    notas = query.order_by(desc(NFe.id)).all()
    return request.app.state.templates.TemplateResponse(
        "nfe/lista.html",
        {"request": request, "notas": notas, "status": status, "busca": busca,
         "messages": _get_messages(request), "empresa": empresa,
         "STATUS_LABELS": STATUS_LABELS}
    )


@router.get("/config")
def config_nfe(request: Request, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    cfop_list = db.query(CfopNatureza).order_by(CfopNatureza.cfop).all()
    return request.app.state.templates.TemplateResponse(
        "nfe/config.html",
        {"request": request, "empresa": empresa,
         "cfop_list": cfop_list,
         "messages": _get_messages(request)}
    )


@router.post("/config")
def salvar_config(
    request: Request, db: Session = Depends(get_db),
    notaas_api_key: str = Form(None),
    notaas_ambiente: str = Form("2"),
    serie_nfe: int = Form(1),
    ultimo_numero_nfe: int = Form(0),
    cfop_padrao: str = Form("5102"),
):
    empresa = db.query(Empresa).first()
    if not empresa:
        request.session["error"] = "Empresa não configurada"
        return RedirectResponse(url="/nfe/config", status_code=303)
    if notaas_api_key:
        empresa.notaas_api_key = notaas_api_key
    empresa.notaas_ambiente = notaas_ambiente
    empresa.serie_nfe = serie_nfe
    empresa.ultimo_numero_nfe = ultimo_numero_nfe
    empresa.cfop_padrao = cfop_padrao
    db.commit()
    request.session["message"] = "Configurações NFe salvas!"
    return RedirectResponse(url="/nfe/config", status_code=303)


@router.get("/emitir/pedido/{pedido_id}")
def emitir_pedido_form(
    request: Request, pedido_id: int, db: Session = Depends(get_db),
):
    empresa = db.query(Empresa).first()
    pedido = db.query(PedidoVenda).options(
        joinedload(PedidoVenda.cliente),
        joinedload(PedidoVenda.itens).joinedload(PedidoVendaItem.produto)
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        request.session["error"] = "Pedido não encontrado"
        return RedirectResponse(url="/nfe", status_code=303)

    itens_nfe, itens_nfse = explodir_itens(pedido=pedido, db=db)
    return request.app.state.templates.TemplateResponse(
        "nfe/emissao.html",
        {"request": request, "pedido": pedido, "itens_nfe": itens_nfe,
         "itens_nfse": itens_nfse, "empresa": empresa,
         "messages": _get_messages(request)}
    )


@router.post("/emitir/pedido/{pedido_id}")
def emitir_pedido_submit(
    request: Request, pedido_id: int, db: Session = Depends(get_db),
    natureza_operacao: str = Form("Venda de mercadoria"),
    cfop: str = Form(None),
):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.notaas_api_key:
        request.session["error"] = "API Key NotaAs não configurada"
        return RedirectResponse(url="/nfe/config", status_code=303)

    pedido = db.query(PedidoVenda).options(
        joinedload(PedidoVenda.cliente),
        joinedload(PedidoVenda.itens).joinedload(PedidoVendaItem.produto)
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        request.session["error"] = "Pedido não encontrado"
        return RedirectResponse(url="/nfe", status_code=303)

    itens_nfe, _ = explodir_itens(pedido=pedido, db=db)
    if not itens_nfe:
        request.session["error"] = "Nenhum item do tipo produto para emitir NFe"
        return RedirectResponse(url=f"/nfe/emitir/pedido/{pedido_id}", status_code=303)

    cliente = pedido.cliente
    ie = _limpar_doc(cliente.inscricao_estadual) if hasattr(cliente, 'inscricao_estadual') else None
    if not ie and not cliente.isento_ie:
        request.session["error"] = f"Cliente '{cliente.nome}' não possui Inscrição Estadual e não está marcado como Isento IE. Cadastre a IE ou marque como isento."
        return RedirectResponse(url=f"/nfe/emitir/pedido/{pedido_id}", status_code=303)

    try:
        numero_nfe = _proximo_numero(empresa, db)
        total = sum(i.get("preco_unitario", 0) * i.get("quantidade", 0) for i in itens_nfe)
        nfe = NFe(
            pedido_id=pedido_id,
            cliente_id=cliente.id,
            numero=numero_nfe,
            serie=empresa.serie_nfe or 1,
            status="rascunho",
            natureza_operacao=natureza_operacao,
            cfop=cfop or empresa.cfop_padrao,
            valor_total=total,
        )
        db.add(nfe)
        db.flush()

        for item in itens_nfe:
            nfe_item = NFeItem(
                nfe_id=nfe.id,
                produto_id=item.get("produto_id"),
                descricao=item.get("descricao", ""),
                ncm=item.get("ncm"),
                cfop=cfop or empresa.cfop_padrao,
                unidade=item.get("unidade", "UN"),
                quantidade=item.get("quantidade", 1),
                preco_unitario=item.get("preco_unitario", 0),
                total=item.get("quantidade", 1) * item.get("preco_unitario", 0),
            )
            db.add(nfe_item)

        if pedido.status == StatusPedido.PENDENTE:
            pedido.status = StatusPedido.FATURADO

        db.commit()
        request.session["message"] = f"Rascunho NFe #{numero_nfe} salvo! Revise antes de transmitir."
        return RedirectResponse(url=f"/nfe/{nfe.id}/previa", status_code=303)
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro ao salvar rascunho NFe: {str(e)}"
        return RedirectResponse(url=f"/nfe/emitir/pedido/{pedido_id}", status_code=303)


@router.get("/emitir/avulsa")
def emitir_avulsa_form(
    request: Request, db: Session = Depends(get_db),
):
    empresa = db.query(Empresa).first()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    produtos = db.query(Produto).filter(
        Produto.tipo == "produto", Produto.situacao == "A"
    ).order_by(Produto.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "cpf_cnpj": c.cpf_cnpj,
                       "cidade": c.cidade, "estado": c.estado} for c in clientes]
    produtos_json = [{"id": p.id, "nome": p.nome, "preco": p.preco,
                       "ncm": p.ncm, "unidade": p.unidade or "UN",
                       "estoque": p.estoque or 0, "codigo": p.codigo or ""} for p in produtos]
    return request.app.state.templates.TemplateResponse(
        "nfe/emissao_avulsa.html",
        {"request": request, "empresa": empresa, "clientes": clientes,
         "produtos": produtos, "clientes_json": clientes_json,
         "produtos_json": produtos_json,
         "messages": _get_messages(request)}
    )


@router.post("/emitir/avulsa")
def emitir_avulsa_submit(
    request: Request, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    natureza_operacao: str = Form("Venda de mercadoria"),
    cfop: str = Form(None),
    itens_json: str = Form(...),
    desconto: float = Form(0.0),
):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.notaas_api_key:
        request.session["error"] = "API Key NotaAs não configurada"
        return RedirectResponse(url="/nfe/config", status_code=303)

    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        request.session["error"] = "Cliente não encontrado"
        return RedirectResponse(url="/nfe/emitir/avulsa", status_code=303)

    try:
        itens_data = json.loads(itens_json)
    except json.JSONDecodeError:
        request.session["error"] = "JSON de itens inválido"
        return RedirectResponse(url="/nfe/emitir/avulsa", status_code=303)

    if not itens_data:
        request.session["error"] = "Adicione pelo menos um item"
        return RedirectResponse(url="/nfe/emitir/avulsa", status_code=303)

    ie = _limpar_doc(cliente.inscricao_estadual) if hasattr(cliente, 'inscricao_estadual') else None
    if not ie and not cliente.isento_ie:
        request.session["error"] = f"Cliente '{cliente.nome}' não possui Inscrição Estadual e não está marcado como Isento IE. Cadastre a IE ou marque como isento."
        return RedirectResponse(url="/nfe/emitir/avulsa", status_code=303)

    itens_nfe = []
    for item in itens_data:
        itens_nfe.append({
            "produto_id": item.get("produto_id"),
            "descricao": item.get("descricao", ""),
            "ncm": item.get("ncm") or "99999999",
            "unidade": item.get("unidade", "UN"),
            "quantidade": float(item.get("quantidade", 1)),
            "preco_unitario": float(item.get("preco_unitario", 0)),
        })

    if desconto > 0:
        total_bruto = sum(i["preco_unitario"] * i["quantidade"] for i in itens_nfe)
        if total_bruto > 0:
            fator = 1 - (desconto / total_bruto)
            for item in itens_nfe:
                item["preco_unitario"] = round(item["preco_unitario"] * fator, 2)

    try:
        numero_nfe = _proximo_numero(empresa, db)
        total = sum(i["preco_unitario"] * i["quantidade"] for i in itens_nfe)
        nfe = NFe(
            cliente_id=cliente.id,
            numero=numero_nfe,
            serie=empresa.serie_nfe or 1,
            status="rascunho",
            natureza_operacao=natureza_operacao,
            cfop=cfop or empresa.cfop_padrao,
            valor_total=total,
        )
        db.add(nfe)
        db.flush()

        for item in itens_nfe:
            nfe_item = NFeItem(
                nfe_id=nfe.id,
                produto_id=item.get("produto_id"),
                descricao=item["descricao"],
                ncm=item["ncm"],
                cfop=cfop or empresa.cfop_padrao,
                unidade=item["unidade"],
                quantidade=item["quantidade"],
                preco_unitario=item["preco_unitario"],
                total=item["preco_unitario"] * item["quantidade"],
            )
            db.add(nfe_item)

        db.commit()
        request.session["message"] = f"Rascunho NFe #{numero_nfe} salvo! Revise antes de transmitir."
        return RedirectResponse(url=f"/nfe/{nfe.id}/previa", status_code=303)
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro ao salvar rascunho NFe: {str(e)}"
        return RedirectResponse(url="/nfe/emitir/avulsa", status_code=303)


def _validar_rascunho(nfe, cliente, empresa) -> list:
    erros = []
    if not cliente:
        erros.append("Cliente não informado")
        return erros
    if not cliente.nome:
        erros.append("Nome do cliente é obrigatório")
    if not cliente.cpf_cnpj:
        erros.append("CPF/CNPJ do cliente é obrigatório")
    if not cliente.endereco or not cliente.bairro or not cliente.cidade or not cliente.estado:
        erros.append("Endereço completo do cliente é obrigatório (logradouro, bairro, cidade, UF)")
    if not nfe.itens or len(nfe.itens) == 0:
        erros.append("NFe deve ter pelo menos 1 item")
    for i, item in enumerate(nfe.itens):
        if not item.ncm or item.ncm == "99999999":
            erros.append(f"Item #{i+1} ({item.descricao}): NCM é obrigatório")
        if not item.cfop:
            erros.append(f"Item #{i+1} ({item.descricao}): CFOP é obrigatório")
    return erros


@router.get("/{nfe_id}/previa")
def ver_previa(request: Request, nfe_id: int, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    nfe = db.query(NFe).options(
        joinedload(NFe.itens), joinedload(NFe.pedido), joinedload(NFe.cliente),
        joinedload(NFe.os)
    ).filter(NFe.id == nfe_id).first()
    if not nfe:
        request.session["error"] = "NFe não encontrada"
        return RedirectResponse(url="/nfe", status_code=303)
    if nfe.status != "rascunho":
        return RedirectResponse(url=f"/nfe/{nfe_id}", status_code=303)

    erros = _validar_rascunho(nfe, nfe.cliente or (nfe.pedido.cliente if nfe.pedido else None), empresa)
    return request.app.state.templates.TemplateResponse(
        "nfe/previa.html",
        {"request": request, "nfe": nfe, "empresa": empresa,
         "erros": erros, "STATUS_LABELS": STATUS_LABELS,
         "messages": _get_messages(request)}
    )


@router.post("/{nfe_id}/transmitir")
def transmitir_nfe(request: Request, nfe_id: int, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.notaas_api_key:
        request.session["error"] = "API Key NotaAs não configurada"
        return RedirectResponse(url="/nfe/config", status_code=303)

    nfe = db.query(NFe).options(
        joinedload(NFe.itens), joinedload(NFe.pedido), joinedload(NFe.cliente)
    ).filter(NFe.id == nfe_id).first()
    if not nfe:
        request.session["error"] = "NFe não encontrada"
        return RedirectResponse(url="/nfe", status_code=303)
    if nfe.status != "rascunho":
        request.session["error"] = "NFe já foi transmitida"
        return RedirectResponse(url=f"/nfe/{nfe_id}", status_code=303)

    cliente = nfe.cliente or (nfe.pedido.cliente if nfe.pedido else None)
    erros = _validar_rascunho(nfe, cliente, empresa)
    if erros:
        request.session["error"] = "Corrija os erros antes de transmitir: " + "; ".join(erros)
        return RedirectResponse(url=f"/nfe/{nfe_id}/previa", status_code=303)

    itens_nfe = [{
        "produto_id": i.produto_id,
        "descricao": i.descricao,
        "ncm": i.ncm,
        "cfop": i.cfop,
        "unidade": i.unidade,
        "quantidade": i.quantidade,
        "preco_unitario": i.preco_unitario,
    } for i in nfe.itens]

    try:
        payload = montar_payload_nfe(
            empresa, cliente, itens_nfe,
            numero_nfe=nfe.numero,
            serie=nfe.serie,
            natureza_operacao=nfe.natureza_operacao,
            cfop=nfe.cfop,
        )

        result = emitir_nfe(empresa, payload)
        invoice_id = result.get("invoiceId")

        nfe.invoice_id = invoice_id
        nfe.status = "queued"
        db.commit()

        request.session["message"] = f"NFe #{nfe.numero} transmitida com sucesso!"
        return RedirectResponse(url=f"/nfe/{nfe.id}", status_code=303)
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro ao transmitir NFe: {str(e)}"
        return RedirectResponse(url=f"/nfe/{nfe_id}/previa", status_code=303)


@router.get("/{nfe_id}")
def ver_nfe(request: Request, nfe_id: int, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    nfe = db.query(NFe).options(
        joinedload(NFe.itens), joinedload(NFe.pedido), joinedload(NFe.os), joinedload(NFe.cliente)
    ).filter(NFe.id == nfe_id).first()
    if not nfe:
        request.session["error"] = "NFe não encontrada"
        return RedirectResponse(url="/nfe", status_code=303)

    if nfe.invoice_id and nfe.status in ("queued", "processing"):
        try:
            status_data = consultar_status(empresa, nfe.invoice_id)
            novo_status = status_data.get("status")
            if novo_status and novo_status != nfe.status:
                nfe.status = novo_status
                nfe.chave_acesso = status_data.get("chaveAcesso") or nfe.chave_acesso
                nfe.protocolo = status_data.get("nProt") or nfe.protocolo
                if novo_status == "issued":
                    nfe.data_emissao = datetime.now()
                db.commit()
        except Exception:
            pass

    return request.app.state.templates.TemplateResponse(
        "nfe/detalhe.html",
        {"request": request, "nfe": nfe, "empresa": empresa,
         "STATUS_LABELS": STATUS_LABELS,
         "messages": _get_messages(request)}
    )


@router.get("/{nfe_id}/pdf")
def baixar_pdf_nfe(request: Request, nfe_id: int, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    nfe = db.query(NFe).filter(NFe.id == nfe_id).first()
    if not nfe or not nfe.invoice_id:
        raise HTTPException(status_code=404, detail="NFe não encontrada")
    try:
        pdf_bytes = baixar_pdf(empresa, nfe.invoice_id)
        return Response(content=pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": f"inline; filename=nfe_{nfe.numero}.pdf"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{nfe_id}/xml")
def baixar_xml_nfe(request: Request, nfe_id: int, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    nfe = db.query(NFe).filter(NFe.id == nfe_id).first()
    if not nfe or not nfe.invoice_id:
        raise HTTPException(status_code=404, detail="NFe não encontrada")
    try:
        xml_text = baixar_xml(empresa, nfe.invoice_id)
        return Response(content=xml_text, media_type="application/xml",
                        headers={"Content-Disposition": f"attachment; filename=nfe_{nfe.numero}.xml"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{nfe_id}/cancelar")
def cancelar_nfe_view(
    request: Request, nfe_id: int, db: Session = Depends(get_db),
    motivo: str = Form(...),
):
    empresa = db.query(Empresa).first()
    nfe = db.query(NFe).filter(NFe.id == nfe_id).first()
    if not nfe:
        request.session["error"] = "NFe não encontrada"
        return RedirectResponse(url="/nfe", status_code=303)
    if not nfe.invoice_id:
        request.session["error"] = "NFe sem invoiceId - não pode ser cancelada pela API"
        return RedirectResponse(url=f"/nfe/{nfe_id}", status_code=303)
    try:
        result = cancelar_nfe(empresa, nfe.invoice_id, motivo)
        nfe.status = "cancelled"
        nfe.mensagem_retorno = json.dumps(result)
        db.commit()
        request.session["message"] = "NFe cancelada com sucesso!"
    except Exception as e:
        request.session["error"] = f"Erro ao cancelar NFe: {str(e)}"
    return RedirectResponse(url=f"/nfe/{nfe_id}", status_code=303)


@router.get("/invoices/{invoice_id}/status")
def poll_status(
    request: Request, invoice_id: str, db: Session = Depends(get_db),
):
    empresa = db.query(Empresa).first()
    if not empresa:
        return JSONResponse({"error": "Empresa não configurada"}, status_code=400)
    try:
        data = consultar_status(empresa, invoice_id)
        nfe = db.query(NFe).filter(NFe.invoice_id == invoice_id).first()
        if nfe:
            novo_status = data.get("status")
            if novo_status and novo_status != nfe.status:
                nfe.status = novo_status
                nfe.chave_acesso = data.get("chaveAcesso") or nfe.chave_acesso
                nfe.protocolo = data.get("nProt") or nfe.protocolo
                if novo_status == "issued":
                    nfe.data_emissao = datetime.now()
                db.commit()
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/municipios")
def buscar_municipios(
    request: Request, db: Session = Depends(get_db),
    uf: str = Query(""),
):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.notaas_api_key:
        return JSONResponse({"error": "API Key NotaAs não configurada"}, status_code=400)
    try:
        data = consultar_municipios(empresa, uf or None)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/produtos/buscar")
def buscar_produtos_nfe(
    request: Request, db: Session = Depends(get_db),
    q: str = Query(""),
):
    if not q or len(q) < 2:
        return JSONResponse({"results": []})
    from sqlalchemy import or_
    results = db.query(Produto).filter(
        Produto.tipo == "produto",
        Produto.situacao == "A",
        or_(
            Produto.nome.ilike(f"%{q}%"),
            Produto.codigo.ilike(f"%{q}%"),
        )
    ).order_by(Produto.nome).limit(20).all()
    return JSONResponse({"results": [{
        "id": p.id, "nome": p.nome, "ncm": p.ncm or "99999999",
        "unidade": p.unidade or "UN", "preco": p.preco or 0,
        "estoque": p.estoque_atual or 0, "codigo": p.codigo or "",
    } for p in results]})


@router.post("/webhook")
async def webhook_nfe(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
        event = body.get("event", "")
        data = body.get("data", {})
        invoice_id = data.get("invoiceId") or body.get("invoiceId")
        
        if not invoice_id:
            return JSONResponse({"status": "ignored"})

        nfe = db.query(NFe).filter(NFe.invoice_id == invoice_id).first()
        if not nfe:
            return JSONResponse({"status": "not_found"})

        if event in ("nfe.issued", "nfce.issued"):
            nfe.status = "issued"
            nfe.chave_acesso = data.get("chaveAcesso") or nfe.chave_acesso
            nfe.protocolo = data.get("nProt") or nfe.protocolo
            nfe.data_emissao = datetime.now()
        elif event in ("nfe.error", "nfce.error"):
            nfe.status = "error"
            nfe.mensagem_retorno = json.dumps(data)
        elif event in ("nfe.cancelled", "nfce.cancelled"):
            nfe.status = "cancelled"
            nfe.mensagem_retorno = json.dumps(data)
        
        db.commit()
        return JSONResponse({"status": "updated", "invoice_id": invoice_id})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

import os
import json
import re
import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date
from fastapi import APIRouter, Depends, Request, Form, Query, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse, JSONResponse, Response, FileResponse
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import desc, asc
from database import get_db
from models import Cliente, Empresa, PedidoVenda, PedidoVendaItem, PedidoConsolidado, PedidoConsolidadoItem, Produto, ContaReceber, StatusConta, OrdemServico, Assinatura, Fornecedor
from models_nfe import NFSe, NFSeItem, NFSeRecebida
from services.nfse_betha import emitir_completa, emitir_rascunho, NFSeBethaError, BethaNfseService
from services.nfse_pdf import gerar_pdf_nfse, gerar_danfse_pdf, is_xml_nfse_nacional
from services.nfe_notaas import explodir_itens_consolidacao

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nfse", tags=["NFSe"])

STATUS_LABELS = {
    "rascunho": "Rascunho",
    "pendente": "Pendente",
    "em_processamento": "Em Processamento",
    "autorizada": "Autorizada",
    "erro": "Erro",
    "cancelada": "Cancelada",
}

UPLOAD_DIR = "static/uploads/nfse"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _proximo_numero(empresa: Empresa, db: Session) -> int:
    empresa_locked = db.query(Empresa).filter(Empresa.id == empresa.id).with_for_update().first()
    numero = (empresa_locked.ultimo_numero_nfse or 0) + 1
    empresa_locked.ultimo_numero_nfse = numero
    db.commit()
    return numero


def _get_messages(request):
    messages = []
    msg = request.session.get("message", None)
    if msg:
        if isinstance(msg, dict):
            messages.append(msg)
        else:
            messages.append({"tipo": "success", "texto": msg})
    err = request.session.get("error", None)
    if err:
        messages.append({"tipo": "danger", "texto": err})
    return messages


@router.get("/")
def listar_nfse(
    request: Request, db: Session = Depends(get_db),
    status: str = Query(""), busca: str = Query(""),
    data_inicio: str = Query(""), data_fim: str = Query(""),
    page: int = Query(1), per_page: int = Query(20),
    sort: str = Query("id"), ordem: str = Query("desc"),
):
    empresa = db.query(Empresa).first()
    from models import Cliente
    query = db.query(NFSe).options(
        joinedload(NFSe.pedido), joinedload(NFSe.cliente),
    )
    if sort == "cliente":
        query = query.outerjoin(Cliente, NFSe.cliente_id == Cliente.id)
    if status:
        query = query.filter(NFSe.status == status)
    if busca:
        query = query.filter(
            NFSe.numero.ilike(f"%{busca}%")
        )
    if data_inicio:
        query = query.filter(NFSe.data_emissao >= datetime.strptime(data_inicio, "%Y-%m-%d"))
    if data_fim:
        query = query.filter(NFSe.data_emissao <= datetime.strptime(data_fim + " 23:59:59", "%Y-%m-%d %H:%M:%S"))
    
    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    order_func = desc if ordem == "desc" else asc
    if sort == "cliente":
        nfse_list = query.order_by(order_func(Cliente.nome), NFSe.id).offset(offset).limit(per_page).all()
    else:
        sort_col = getattr(NFSe, sort, NFSe.id)
        nfse_list = query.order_by(order_func(sort_col), NFSe.id).offset(offset).limit(per_page).all()
    
    nfse_ids_sem_cobranca = set()
    for n in nfse_list:
        cob = db.query(ContaReceber).filter(
            ContaReceber.observacao.like(f"%NFSe #{n.id}%")
        ).first()
        if not cob:
            nfse_ids_sem_cobranca.add(n.id)

    # NFSe recebidas (somos o tomador) no perÃ­odo selecionado, p/ exibiÃ§Ã£o no rodapÃ©
    q_rec = db.query(NFSeRecebida)
    if data_inicio:
        q_rec = q_rec.filter(NFSeRecebida.data_emissao >= datetime.strptime(data_inicio, "%Y-%m-%d"))
    if data_fim:
        q_rec = q_rec.filter(NFSeRecebida.data_emissao <= datetime.strptime(data_fim + " 23:59:59", "%Y-%m-%d %H:%M:%S"))
    nfse_recebidas = q_rec.order_by(desc(NFSeRecebida.data_emissao)).all()

    return request.app.state.templates.TemplateResponse(request, 
        "nfse/lista.html",
        {"request": request, "nfse": nfse_list, "status": status, "busca": busca,
         "data_inicio": data_inicio, "data_fim": data_fim,
         "page": page, "per_page": per_page, "total_pages": total_pages, "total_count": total_count,
         "sort": sort, "ordem": ordem,
         "messages": _get_messages(request), "empresa": empresa,
         "STATUS_LABELS": STATUS_LABELS,
         "nfse_ids_sem_cobranca": nfse_ids_sem_cobranca,
         "nfse_recebidas": nfse_recebidas}
    )


@router.get("/recebidas")
def listar_nfse_recebidas(
    request: Request, db: Session = Depends(get_db),
    data_inicio: str = Query(""), data_fim: str = Query(""),
    busca: str = Query(""), page: int = Query(1),
    ordenar: str = Query("data_emissao"), direcao: str = Query("desc"),
):
    """Lista as NFSe recebidas (somos o tomador), filtrÃ¡veis por perÃ­odo e nÂº, com ordenaÃ§Ã£o e paginaÃ§Ã£o."""
    sort_map = {
        "emitente_nome": NFSeRecebida.emitente_nome,
        "numero": NFSeRecebida.numero,
        "valor_total": NFSeRecebida.valor_total,
        "data_emissao": NFSeRecebida.data_emissao,
        "status": NFSeRecebida.status,
    }
    sort_col = sort_map.get(ordenar, NFSeRecebida.data_emissao)
    if direcao == "asc":
        order_expr = asc(sort_col)
    else:
        order_expr = desc(sort_col)

    q = db.query(NFSeRecebida)
    if busca:
        q = q.filter(NFSeRecebida.numero.ilike(f"%{busca}%"))
    if data_inicio:
        q = q.filter(NFSeRecebida.data_emissao >= datetime.strptime(data_inicio, "%Y-%m-%d"))
    if data_fim:
        q = q.filter(NFSeRecebida.data_emissao <= datetime.strptime(data_fim + " 23:59:59", "%Y-%m-%d %H:%M:%S"))

    total = q.count()
    per_page = 25
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    recebidas = q.order_by(order_expr).offset((page - 1) * per_page).limit(per_page).all()

    return request.app.state.templates.TemplateResponse(request,
        "nfse/recebidas.html",
        {"request": request, "recebidas": recebidas, "status": "", "busca": busca,
         "data_inicio": data_inicio, "data_fim": data_fim, "page": page,
         "total_pages": total_pages, "total": total, "per_page": per_page,
         "ordenar": ordenar, "direcao": direcao,
         "messages": _get_messages(request), "empresa": db.query(Empresa).first(),
         "STATUS_LABELS": STATUS_LABELS}
    )


@router.get("/emitir/avulsa")
def emitir_avulsa_form(request: Request, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    servicos = db.query(Produto).filter(
        Produto.tipo == "servico", Produto.situacao == "A"
    ).order_by(Produto.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "cpf_cnpj": c.cpf_cnpj,
                       "cidade": c.cidade, "estado": c.estado} for c in clientes]
    servicos_json = [{"id": p.id, "nome": p.nome, "preco": p.preco,
                       "codigo_lc116": p.codigo_lc116 or "",
                       "codigo_tributacao_municipal": p.codigo_tributacao_municipal or "",
                       "unidade": p.unidade or "UN"} for p in servicos]
    return request.app.state.templates.TemplateResponse(request, 
        "nfse/emissao_avulsa.html",
        {"request": request, "empresa": empresa, "clientes": clientes,
         "servicos": servicos, "clientes_json": clientes_json,
         "servicos_json": servicos_json, "messages": _get_messages(request)}
    )


@router.post("/emitir/avulsa")
def emitir_avulsa_salvar(
    request: Request, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    data_competencia: str = Form(""),
    natureza_operacao: str = Form(""),
    regime_especial: str = Form(""),
    municipio_codigo: str = Form(""),
    municipio_nome: str = Form(""),
    itens_json: str = Form(...),
    gerar_cobranca: bool = Form(True),
    desconto: float = Form(0.0),
    observacoes: str = Form(""),
):
    empresa = db.query(Empresa).first()
    if not empresa:
        return JSONResponse({"error": "Empresa nÃ£o cadastrada"}, status_code=400)

    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        return JSONResponse({"error": "Cliente nÃ£o encontrado"}, status_code=404)

    try:
        itens_data = json.loads(itens_json)
    except json.JSONDecodeError:
        return JSONResponse({"error": "JSON de itens invÃ¡lido"}, status_code=400)

    if not itens_data:
        return JSONResponse({"error": "Adicione pelo menos um serviÃ§o"}, status_code=400)

    codigos_lc116 = set(i.get("codigo_lc116", "") for i in itens_data if i.get("codigo_lc116"))
    if len(codigos_lc116) > 1:
        return JSONResponse({
            "error": f"Itens com cÃ³digos LC116 diferentes: {', '.join(sorted(codigos_lc116))}. A prefeitura de Dourados-MS nÃ£o aceita mÃºltiplos cÃ³digos na mesma NFS-e."
        }, status_code=400)

    # Aplica desconto proporcional aos itens
    if desconto > 0:
        total_bruto = sum(Decimal(str(i.get("valor_total", 0))) for i in itens_data)
        if total_bruto > 0:
            fator = Decimal("1") - (Decimal(str(desconto)) / total_bruto)
            for item in itens_data:
                item["valor_unitario"] = round(Decimal(str(item.get("valor_unitario", 0))) * fator, 2)
                item["valor_total"] = round(item["valor_unitario"] * Decimal(str(item.get("quantidade", 1))), 2)

    valor_total = sum(Decimal(str(i.get("valor_total", 0))) for i in itens_data)
    numero = str(_proximo_numero(empresa, db))

    nfse = NFSe(
        numero=numero,
        cliente_id=cliente.id,
        data_emissao=datetime.now(),
        status="rascunho",
        origem="avulsa",
        valor_total=valor_total,
        natureza_operacao=natureza_operacao or None,
        regime_especial=regime_especial or None,
        municipio_codigo=municipio_codigo or None,
        municipio_nome=municipio_nome or None,
        iss_retido=getattr(cliente, 'iss_retido', False) or False,
        aliquota_iss=empresa.aliquota_iss or 2.0,
        aliquota_federal=empresa.aliquota_federal or 0.0,
        aliquota_estadual=empresa.aliquota_estadual or 0.0,
        aliquota_municipal=empresa.aliquota_municipal or 0.0,
        observacoes=observacoes or "",
    )
    db.add(nfse)
    db.flush()

    for item in itens_data:
        nfse_item = NFSeItem(
            nfse_id=nfse.id,
            produto_id=item.get("produto_id") or None,
            descricao=item.get("descricao", ""),
            quantidade=Decimal(str(item.get("quantidade", 1))),
            valor_unitario=Decimal(str(item.get("valor_unitario", 0))),
            valor_total=Decimal(str(item.get("valor_total", 0))),
            codigo_servico=item.get("codigo_lc116", ""),
            tributacao_municipal=item.get("codigo_tributacao_municipal", ""),
        )
        db.add(nfse_item)

    db.commit()

    # Gera cobranÃ§a automaticamente (se solicitado)
    if gerar_cobranca:
        try:
            cobranca = ContaReceber(
                cliente_id=cliente_id,
                descricao=f"NFSe Avulsa #{nfse.numero}",
                valor=valor_total,
                data_vencimento=datetime.strptime(data_competencia, '%Y-%m-%d').date() if data_competencia else date.today(),
                forma_pagamento="NFSe",
                observacao=f"Gerado automaticamente da NFSe #{nfse.id}",
                nfse_id=nfse.id,
            )
            db.add(cobranca)
            db.commit()
        except Exception:
            pass

    request.session["message"] = f"Rascunho NFSe #{nfse.numero} salvo com sucesso!"
    return RedirectResponse(url="/nfse", status_code=303)


@router.get("/emitir/{pedido_id}")
def pagina_emitir(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoVenda).options(
        selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.produto)
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido nÃ£o encontrado")
    return request.app.state.templates.TemplateResponse(request, 
        "nfse/emissao.html",
        {"request": request, "pedido": pedido}
    )


@router.post("/emitir/{pedido_id}")
def emitir_nfse(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoVenda).options(
        selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.produto)
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido nÃ£o encontrado")

    itens_servico = [i for i in pedido.itens if i.produto and i.produto.tipo == 'servico']
    if not itens_servico:
        return JSONResponse({"error": "Nenhum item de serviÃ§o no pedido"}, status_code=400)

    codigos_lc116 = set(i.produto.codigo_lc116 for i in itens_servico if i.produto.codigo_lc116)
    if len(codigos_lc116) > 1:
        return JSONResponse({
            "error": f"Pedido possui itens de serviÃ§o com cÃ³digos LC116 diferentes: {', '.join(sorted(codigos_lc116))}. A prefeitura de Dourados-MS nÃ£o aceita mÃºltiplos cÃ³digos na mesma NFS-e. Remova ou separe os itens em pedidos diferentes."
        }, status_code=400)

    empresa = db.query(Empresa).first()
    if not empresa:
        return JSONResponse({"error": "Empresa nÃ£o cadastrada"}, status_code=400)
    numero_nfse = _proximo_numero(empresa, db)

    try:
        resultado = emitir_completa(pedido, db, tpAmb=1, numero_nfse=numero_nfse)

        valor_total = sum(Decimal(str(i.total or 0)) for i in itens_servico)
        if valor_total == 0:
            valor_total = Decimal(str(pedido.total or 0))

        iss_retido = getattr(pedido.cliente, 'iss_retido', False) or False
        empresa = db.query(Empresa).first()
        nfse = NFSe(
            pedido_id=pedido_id,
            cliente_id=pedido.cliente_id,
            numero=resultado.get('numero') or numero_nfse,
            codigo_verificacao=resultado.get('codigo_verificacao'),
            status="autorizada" if resultado.get('protocolo') else "pendente",
            valor_total=valor_total,
            origem="assinatura" if pedido.assinatura_id else "pedido",
            data_emissao=resultado.get('data_emissao'),
            iss_retido=iss_retido,
            aliquota_iss=empresa.aliquota_iss or 2.0,
            aliquota_federal=empresa.aliquota_federal or 0.0,
            aliquota_estadual=empresa.aliquota_estadual or 0.0,
            aliquota_municipal=empresa.aliquota_municipal or 0.0,
        )
        db.add(nfse)
        db.flush()

        for item in itens_servico:
            nfse_item = NFSeItem(
                nfse_id=nfse.id,
                produto_id=item.produto_id,
                descricao=item.descricao or item.produto.nome,
                quantidade=Decimal(str(item.quantidade or 1)),
                valor_unitario=Decimal(str(item.preco_unitario or 0)),
                valor_total=Decimal(str(item.total or 0)),
                codigo_servico=item.produto.codigo_lc116 or "",
                tributacao_municipal=item.produto.codigo_tributacao_municipal or "",
            )
            db.add(nfse_item)

        # Salva XML da NFSe
        try:
            dps_xml = resultado.get('xml')
            if dps_xml:
                xml_filename = f"nfse_{nfse.id}.xml"
                xml_path = f"static/uploads/nfs/{xml_filename}"
                os.makedirs(os.path.dirname(xml_path), exist_ok=True)
                with open(xml_path, 'w', encoding='utf-8') as f:
                    f.write(dps_xml)
                nfse.xml_path = f"/{xml_path.replace(os.sep, '/')}"
                nfse.xml_text = dps_xml
        except Exception:
            pass

        db.commit()

        # Gera PDF automaticamente
        try:
            empresa = db.query(Empresa).first()
            cliente = pedido.cliente
            itens = db.query(NFSeItem).filter(NFSeItem.nfse_id == nfse.id).all()
            pdf_url = gerar_pdf_nfse(nfse, empresa, cliente, itens, STATUS_LABELS)
            nfse.pdf_path = pdf_url
            db.commit()
        except Exception:
            pass

        # Gera cobranÃ§a automaticamente
        try:
            cobranca = ContaReceber(
                cliente_id=pedido.cliente_id,
                descricao=f"NFSe Pedido #{pedido.numero or pedido.id}",
                valor=valor_total,
                data_vencimento=pedido.data,
                forma_pagamento="NFSe",
                observacao=f"Gerado automaticamente da NFSe #{nfse.id} (Pedido #{pedido.id})",
                nfse_id=nfse.id,
            )
            db.add(cobranca)
            db.commit()
        except Exception:
            pass

        resp = {
            "success": True,
            "protocolo": resultado.get('protocolo'),
            "erros": resultado.get('erros', [])
        }
        if resultado.get('retry_iss_retido'):
            if not resultado.get('protocolo') or resultado.get('erros'):
                resp['aviso'] = "ISS retido foi marcado automaticamente, mas a NFSe ainda foi rejeitada. Verifique o cadastro do cliente."
            else:
                resp['aviso'] = "ISS retido foi marcado automaticamente pelo tomador e a NFSe foi emitida com sucesso."
        return JSONResponse(resp)
    except NFSeBethaError as e:
        db.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)
    except Exception as e:
        db.rollback()
        return JSONResponse({"error": f"Erro inesperado: {str(e)}"}, status_code=500)


@router.get("/emitir/os/{os_id}")
def emitir_os_nfse_form(
    request: Request, os_id: int, db: Session = Depends(get_db),
):
    empresa = db.query(Empresa).first()
    os = db.query(OrdemServico).options(
        joinedload(OrdemServico.cliente)
    ).filter(OrdemServico.id == os_id).first()
    if not os:
        request.session["error"] = "Ordem de ServiÃ§o nÃ£o encontrada"
        return RedirectResponse(url="/ordens-servico", status_code=303)

    if not os.valor_servico or os.valor_servico <= 0:
        request.session["error"] = "OS sem valor de serviÃ§o para emitir NFSe"
        return RedirectResponse(url=f"/ordens-servico/{os_id}", status_code=303)

    return request.app.state.templates.TemplateResponse(request, 
        "nfse/emissao_os.html",
        {"request": request, "os": os, "empresa": empresa,
         "messages": _get_messages(request)}
    )


@router.post("/emitir/os/{os_id}")
def emitir_os_nfse_submit(
    request: Request, os_id: int, db: Session = Depends(get_db),
):
    os = db.query(OrdemServico).options(
        joinedload(OrdemServico.cliente)
    ).filter(OrdemServico.id == os_id).first()
    if not os:
        request.session["error"] = "Ordem de ServiÃ§o nÃ£o encontrada"
        return RedirectResponse(url="/ordens-servico", status_code=303)

    if not os.valor_servico or os.valor_servico <= 0:
        request.session["error"] = "OS sem valor de serviÃ§o para emitir NFSe"
        return RedirectResponse(url=f"/ordens-servico/{os_id}", status_code=303)

    empresa = db.query(Empresa).first()
    if not empresa:
        request.session["error"] = "Empresa nÃ£o configurada"
        return RedirectResponse(url="/ordens-servico", status_code=303)

    try:
        numero_nfse = str(_proximo_numero(empresa, db))
        now = datetime.now()

        iss_retido = getattr(os.cliente, 'iss_retido', False) or False
        nfse = NFSe(
            numero=numero_nfse,
            cliente_id=os.cliente_id,
            status="rascunho",
            valor_total=os.valor_servico,
            data_emissao=now,
            origem="os",
            iss_retido=iss_retido,
            aliquota_iss=empresa.aliquota_iss or 2.0,
            aliquota_federal=empresa.aliquota_federal or 0.0,
            aliquota_estadual=empresa.aliquota_estadual or 0.0,
            aliquota_municipal=empresa.aliquota_municipal or 0.0,
        )
        db.add(nfse)
        db.flush()

        servico_produto = db.query(Produto).filter(
            Produto.tipo == "servico", Produto.situacao == "A"
        ).first()

        nfse_item = NFSeItem(
            nfse_id=nfse.id,
            produto_id=servico_produto.id if servico_produto else None,
            descricao=os.servicos_executados or "ServiÃ§o prestado",
            quantidade=1,
            valor_unitario=os.valor_servico,
            valor_total=os.valor_servico,
            codigo_servico=servico_produto.codigo_lc116 if servico_produto else "",
            tributacao_municipal=servico_produto.codigo_tributacao_municipal if servico_produto else "",
        )
        db.add(nfse_item)

        db.commit()
        request.session["message"] = f"Rascunho NFSe #{numero_nfse} gerado para OS #{os_id}! Revise antes de emitir."
        return RedirectResponse(url=f"/nfse/detalhe/{nfse.id}", status_code=303)
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro ao gerar NFSe: {str(e)}"
        return RedirectResponse(url=f"/ordens-servico/{os_id}", status_code=303)


@router.get("/emitir/consolidacao/{consolidacao_id}")
def pagina_emitir_consolidacao(request: Request, consolidacao_id: int, db: Session = Depends(get_db)):
    consolidacao = db.query(PedidoConsolidado).options(
        selectinload(PedidoConsolidado.itens).selectinload(PedidoConsolidadoItem.produto),
        selectinload(PedidoConsolidado.cliente)
    ).filter(PedidoConsolidado.id == consolidacao_id).first()
    if not consolidacao:
        raise HTTPException(status_code=404, detail="ConsolidaÃ§Ã£o nÃ£o encontrada")
    if consolidacao.status != "concluido":
        raise HTTPException(status_code=400, detail="Apenas consolidaÃ§Ãµes finalizadas podem emitir NFSe")
    if consolidacao.nfse:
        raise HTTPException(status_code=400, detail="Esta consolidaÃ§Ã£o jÃ¡ possui NFSe emitida")
    
    # Explode itens
    itens_nfe, itens_nfse = explodir_itens_consolidacao(consolidacao=consolidacao, db=db)
    
    empresa = db.query(Empresa).first()
    return request.app.state.templates.TemplateResponse(request,
        "nfe/emissao_consolidacao.html",
        {"request": request, "consolidacao": consolidacao,
         "itens_nfe": itens_nfe, "itens_nfse": itens_nfse,
         "empresa": empresa, "messages": _get_messages(request)}
    )


@router.post("/emitir/consolidacao/{consolidacao_id}")
def emitir_consolidacao_nfse(request: Request, consolidacao_id: int, db: Session = Depends(get_db)):
    """Salva rascunho NFe + NFSe da consolidaÃ§Ã£o"""
    consolidacao = db.query(PedidoConsolidado).options(
        selectinload(PedidoConsolidado.itens).selectinload(PedidoConsolidadoItem.produto),
        selectinload(PedidoConsolidado.cliente)
    ).filter(PedidoConsolidado.id == consolidacao_id).first()
    if not consolidacao:
        raise HTTPException(status_code=404, detail="ConsolidaÃ§Ã£o nÃ£o encontrada")
    if consolidacao.status != "concluido":
        request.session["error"] = "Apenas consolidaÃ§Ãµes finalizadas podem emitir NFe/NFSe"
        return RedirectResponse(url=f"/nfe/emitir/consolidacao/{consolidacao_id}", status_code=303)
    if consolidacao.nfse:
        request.session["error"] = "Esta consolidaÃ§Ã£o jÃ¡ possui NFSe"
        return RedirectResponse(url=f"/nfe/emitir/consolidacao/{consolidacao_id}", status_code=303)

    itens_nfe, itens_nfse = explodir_itens_consolidacao(consolidacao=consolidacao, db=db)
    empresa = db.query(Empresa).first()
    if not empresa:
        request.session["error"] = "Empresa nÃ£o cadastrada"
        return RedirectResponse(url=f"/nfe/emitir/consolidacao/{consolidacao_id}", status_code=303)

    if not itens_nfe and not itens_nfse:
        request.session["error"] = "Nenhum item para emitir"
        return RedirectResponse(url=f"/nfe/emitir/consolidacao/{consolidacao_id}", status_code=303)

    # Validar cliente para NFe
    cliente = consolidacao.cliente
    if itens_nfe:
        ie = _limpar_doc(cliente.inscricao_estadual) if hasattr(cliente, 'inscricao_estadual') else None
        if not ie and not cliente.isento_ie and cliente.indicador_ie != "nao_contribuinte":
            request.session["error"] = f"Cliente '{cliente.nome}' nÃ£o possui InscriÃ§Ã£o Estadual e nÃ£o estÃ¡ marcado como Isento IE ou NÃ£o contribuinte."
            return RedirectResponse(url=f"/nfe/emitir/consolidacao/{consolidacao_id}", status_code=303)

    # Validar LC116 Ãºnico para NFSe
    codigos_lc116 = set()
    for item in itens_nfse:
        if item.produto and item.produto.codigo_lc116:
            codigos_lc116.add(item.produto.codigo_lc116)
    if len(codigos_lc116) > 1:
        request.session["error"] = f"ConsolidaÃ§Ã£o possui itens de serviÃ§o com cÃ³digos LC116 diferentes: {', '.join(sorted(codigos_lc116))}. A prefeitura nÃ£o aceita mÃºltiplos cÃ³digos na mesma NFS-e."
        return RedirectResponse(url=f"/nfe/emitir/consolidacao/{consolidacao_id}", status_code=303)

    try:
        # NFe
        nfe = None
        if itens_nfe:
            from sqlalchemy import func
            empresa_locked = db.query(Empresa).filter(Empresa.id == empresa.id).with_for_update().first()
            numero_nfe = (empresa_locked.ultimo_numero_nfe or 0) + 1
            empresa_locked.ultimo_numero_nfe = numero_nfe
            total_nfe = sum(i.get("preco_unitario", 0) * i.get("quantidade", 0) for i in itens_nfe)
            now = datetime.now()
            
            nfe = NFe(
                consolidacao_id=consolidacao_id,
                origem="consolidacao",
                cliente_id=cliente.id,
                numero=numero_nfe,
                serie=empresa.serie_nfe or 1,
                status="rascunho",
                natureza_operacao="Venda de mercadoria",
                cfop=empresa.cfop_padrao or "5102",
                valor_total=total_nfe,
                data_emissao=now,
                data_saida=now,
                aliquota_federal=empresa.nfe_aliquota_federal or 0.0,
                aliquota_estadual=empresa.nfe_aliquota_estadual or 0.0,
            )
            db.add(nfe)
            db.flush()

            for item in itens_nfe:
                nfe_item = NFeItem(
                    nfe_id=nfe.id,
                    produto_id=item.get("produto_id"),
                    descricao=item.get("descricao", ""),
                    ncm=item.get("ncm"),
                    cfop=empresa.cfop_padrao or "5102",
                    unidade=item.get("unidade", "UN"),
                    quantidade=item.get("quantidade", 1),
                    preco_unitario=item.get("preco_unitario", 0),
                    total=item.get("quantidade", 1) * item.get("preco_unitario", 0),
                )
                db.add(nfe_item)

        # NFSe
        nfse = None
        if itens_nfse:
            numero_nfse = str((empresa.ultimo_numero_nfse or 0) + 1)
            empresa.ultimo_numero_nfse = int(numero_nfse)
            valor_servicos = sum(
                Decimal(str(item.total or item.preco_unitario or 0)) * Decimal(str(item.quantidade or 1))
                for item in itens_nfse
            )
            
            iss_retido = getattr(cliente, 'iss_retido', False) or False
            nfse = NFSe(
                consolidacao_id=consolidacao_id,
                cliente_id=cliente.id,
                numero=numero_nfse,
                status="rascunho",
                valor_total=valor_servicos,
                origem="consolidacao",
                data_emissao=datetime.now(),
                iss_retido=iss_retido,
                aliquota_iss=empresa.aliquota_iss or 2.0,
                aliquota_federal=empresa.aliquota_federal or 0.0,
                aliquota_estadual=empresa.aliquota_estadual or 0.0,
                aliquota_municipal=empresa.aliquota_municipal or 0.0,
            )
            db.add(nfse)
            db.flush()

            for item in itens_nfse:
                nfse_item = NFSeItem(
                    nfse_id=nfse.id,
                    produto_id=item.produto_id,
                    descricao=item.descricao or item.produto.nome,
                    quantidade=Decimal(str(item.quantidade or 1)),
                    valor_unitario=Decimal(str(item.preco_unitario or 0)),
                    valor_total=Decimal(str(item.total or 0)),
                    codigo_servico=item.produto.codigo_lc116 or "",
                    tributacao_municipal=item.produto.codigo_tributacao_municipal or "",
                )
                db.add(nfse_item)

        db.commit()
        
        msg = f"Rascunhos salvos para ConsolidaÃ§Ã£o #{consolidacao.numero or consolidacao_id}!"
        if nfe:
            msg += f" NFe #{nfe.numero}"
        if nfse:
            msg += f" NFSe #{nfse.numero}"
        request.session["message"] = msg
        
        # Redirect to preview
        if nfe:
            return RedirectResponse(url=f"/nfe/{nfe.id}/previa", status_code=303)
        else:
            return RedirectResponse(url=f"/nfse/detalhe/{nfse.id}", status_code=303)
            
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro ao salvar rascunhos: {str(e)}"
        return RedirectResponse(url=f"/nfe/emitir/consolidacao/{consolidacao_id}", status_code=303)


@router.get("/{nfse_id}/editar")
def editar_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.itens).selectinload(NFSeItem.produto),
        selectinload(NFSe.pedido),
        selectinload(NFSe.cliente),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")
    st = (nfse.status or '').lower()
    if st not in ("rascunho", "erro"):
        request.session["error"] = "SÃ³ Ã© possÃ­vel editar NFSe em rascunho ou erro"
        return RedirectResponse(url="/nfse", status_code=303)

    empresa = db.query(Empresa).first()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    servicos = db.query(Produto).filter(
        Produto.tipo == "servico", Produto.situacao == "A"
    ).order_by(Produto.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "cpf_cnpj": c.cpf_cnpj,
                       "cidade": c.cidade, "estado": c.estado} for c in clientes]
    servicos_json = [{"id": p.id, "nome": p.nome, "preco": p.preco,
                       "codigo_lc116": p.codigo_lc116 or "",
                       "codigo_tributacao_municipal": p.codigo_tributacao_municipal or "",
                       "unidade": p.unidade or "UN"} for p in servicos]
    return request.app.state.templates.TemplateResponse(request, 
        "nfse/editar.html",
        {"request": request, "nfse": nfse, "empresa": empresa,
         "clientes": clientes, "servicos": servicos,
         "clientes_json": clientes_json, "servicos_json": servicos_json}
    )


@router.post("/{nfse_id}/editar")
def editar_nfse_salvar(
    request: Request, nfse_id: int, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    data_competencia: str = Form(""),
    natureza_operacao: str = Form(""),
    regime_especial: str = Form(""),
    municipio_codigo: str = Form(""),
    municipio_nome: str = Form(""),
    itens_json: str = Form(...),
    desconto: float = Form(0.0),
    observacoes: str = Form(""),
):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.itens),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")
    st = (nfse.status or '').lower()
    if st not in ("rascunho", "erro"):
        request.session["error"] = "SÃ³ Ã© possÃ­vel editar NFSe em rascunho ou erro"
        return RedirectResponse(url="/nfse", status_code=303)

    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        return JSONResponse({"error": "Cliente nÃ£o encontrado"}, status_code=404)

    try:
        itens_data = json.loads(itens_json)
    except json.JSONDecodeError:
        request.session["error"] = "JSON de itens invÃ¡lido"
        return RedirectResponse(url=f"/nfse/{nfse_id}/editar", status_code=303)

    if not itens_data:
        request.session["error"] = "Adicione pelo menos um serviÃ§o"
        return RedirectResponse(url=f"/nfse/{nfse_id}/editar", status_code=303)

    codigos_lc116 = set(i.get("codigo_lc116", "") for i in itens_data if i.get("codigo_lc116"))
    if len(codigos_lc116) > 1:
        request.session["error"] = f"Itens com cÃ³digos LC116 diferentes: {', '.join(sorted(codigos_lc116))}"
        return RedirectResponse(url=f"/nfse/{nfse_id}/editar", status_code=303)

    # Aplica desconto proporcional aos itens
    if desconto > 0:
        total_bruto = sum(Decimal(str(i.get("valor_total", 0))) for i in itens_data)
        if total_bruto > 0:
            fator = Decimal("1") - (Decimal(str(desconto)) / total_bruto)
            for item in itens_data:
                item["valor_unitario"] = round(Decimal(str(item.get("valor_unitario", 0))) * fator, 2)
                item["valor_total"] = round(item["valor_unitario"] * Decimal(str(item.get("quantidade", 1))), 2)

    valor_total = sum(Decimal(str(i.get("valor_total", 0))) for i in itens_data)

    nfse.cliente_id = cliente.id
    nfse.valor_total = valor_total
    nfse.natureza_operacao = natureza_operacao or None
    nfse.regime_especial = regime_especial or None
    nfse.municipio_codigo = municipio_codigo or None
    nfse.municipio_nome = municipio_nome or None
    nfse.iss_retido = getattr(cliente, 'iss_retido', False) or False
    nfse.observacoes = observacoes or ""
    if data_competencia:
        nfse.data_emissao = datetime.strptime(data_competencia, '%Y-%m-%d')

    for old_item in nfse.itens:
        db.delete(old_item)
    db.flush()

    for item in itens_data:
        nfse_item = NFSeItem(
            nfse_id=nfse.id,
            produto_id=item.get("produto_id") or None,
            descricao=item.get("descricao", ""),
            quantidade=Decimal(str(item.get("quantidade", 1))),
            valor_unitario=Decimal(str(item.get("valor_unitario", 0))),
            valor_total=Decimal(str(item.get("valor_total", 0))),
            codigo_servico=item.get("codigo_lc116", ""),
            tributacao_municipal=item.get("codigo_tributacao_municipal", ""),
        )
        db.add(nfse_item)

    db.commit()
    request.session["message"] = f"Rascunho NFSe #{nfse.numero} atualizado!"
    return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)


@router.get("/detalhe/{nfse_id}")
def detalhe_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.itens).selectinload(NFSeItem.produto),
        selectinload(NFSe.pedido),
        selectinload(NFSe.cliente),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")

    cobranca = db.query(ContaReceber).filter(
        ContaReceber.observacao.like(f"%NFSe #{nfse.id}%")
    ).first()

    return request.app.state.templates.TemplateResponse(request, 
        "nfse/detalhe.html",
        {"request": request, "nfse": nfse, "STATUS_LABELS": STATUS_LABELS, "cobranca": cobranca}
    )


def _cliente_para_nfse(nfse: NFSe):
    """Resolve o cliente da NFSe, com fallback para os dados do tomador vindos do ADN.

    Quando a NFSe foi importada do ADN e nenhum Cliente foi vinculado (CNPJ/CPF
    nÃ£o cadastrado), usamos o nome/CNPJ do tomador extraÃ­do do XML para nÃ£o
    bloquear a geraÃ§Ã£o do PDF.
    """
    from types import SimpleNamespace
    if nfse.cliente:
        return nfse.cliente
    if nfse.pedido and nfse.pedido.cliente:
        return nfse.pedido.cliente
    if getattr(nfse, 'tomador_nome', None):
        return SimpleNamespace(
            nome=nfse.tomador_nome,
            cpf_cnpj=getattr(nfse, 'tomador_cpf_cnpj', None) or '-',
            endereco='', bairro='', cidade='', estado='', cep='', email='',
        )
    return None


def _buscar_xml_nacional_adn(nfse: NFSe):
    """Tenta obter o XML completo da NFS-e Nacional (infNFSe+DPS) no Portal ADN.

    Notas emitidas por prefeituras proprietarias (ex.: Betha) costumam ter
    apenas a DPS persistida; o XML nacional autorizado vive no Portal Nacional
    e e a fonte correta para gerar o DANFSe padronizado.
    """
    if not nfse.codigo_verificacao:
        return None
    try:
        from services.nfse_betha import BethaNfseService
        service = BethaNfseService()
        sit = service.consultar_situacao_nfse(str(nfse.numero), nfse.codigo_verificacao)
        xml = sit.get('xml') if isinstance(sit, dict) else None
        if xml and is_xml_nfse_nacional(xml):
            return xml
    except Exception as e:
        logger.warning(f"Erro ao buscar XML nacional no ADN: {e}")
    return None


@router.get("/{nfse_id}/pdf")
def baixar_pdf_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.itens),
        selectinload(NFSe.pedido),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")

    # 0. DANFSe padronizado (NFS-e Nacional 2.0) a partir do XML completo.
    #    Prioriza o xml_text se ja estiver no formato nacional (infNFSe+DPS);
    #    caso contrario (nota emitida por prefeitura proprietaria, ex. Betha,
    #    que persiste apenas a DPS), busca o XML nacional no Portal ADN e o
    #    persiste para as proximas geracoes.
    xml_nacional = None
    if nfse.xml_text and is_xml_nfse_nacional(nfse.xml_text):
        xml_nacional = nfse.xml_text
    elif (nfse.status or '').lower() in ('autorizada', 'cancelada') and nfse.codigo_verificacao:
        xml_nacional = _buscar_xml_nacional_adn(nfse)
        if xml_nacional:
            nfse.xml_text = xml_nacional

    if xml_nacional:
        try:
            pdf_filename = f"danfse_{nfse.numero or nfse.id}.pdf"
            pdf_path = f"static/uploads/nfse/{pdf_filename}"
            os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
            gerar_danfse_pdf(
                xml_nacional, pdf_path,
                cancelada=((nfse.status or '').lower() == 'cancelada'),
            )
            nfse.pdf_path = f"/{pdf_path.replace(os.sep, '/')}"
            db.commit()
            return FileResponse(pdf_path, media_type="application/pdf",
                                filename=pdf_filename)
        except Exception as e:
            logger.warning(f"Erro ao gerar DANFSe padronizado: {e}")

    # 1. Fallback: PDF local ja existente (gerado anteriormente)
    if nfse.pdf_path and os.path.exists(f".{nfse.pdf_path}"):
        from fastapi.responses import FileResponse
        return FileResponse(f".{nfse.pdf_path}", media_type="application/pdf",
                            filename=f"nfse_{nfse.numero or nfse.id}.pdf")

    # 2. Fallback: gera PDF local (leiaute proprietario) a partir dos dados do banco
    empresa = db.query(Empresa).first()
    cliente = _cliente_para_nfse(nfse)
    if not cliente:
        raise HTTPException(status_code=400, detail="Cliente/Tomador nÃ£o encontrado para gerar PDF")
    itens = nfse.itens
    try:
        pdf_url = gerar_pdf_nfse(nfse, empresa, cliente, itens, STATUS_LABELS)
        nfse.pdf_path = pdf_url
        db.commit()
        from fastapi.responses import FileResponse
        return FileResponse(f".{pdf_url}", media_type="application/pdf",
                            filename=f"nfse_{nfse.numero or nfse.id}.pdf")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar PDF: {str(e)}")


@router.get("/{nfse_id}/xml")
def baixar_xml_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.itens),
        selectinload(NFSe.pedido),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")

    if nfse.xml_text:
        return Response(content=nfse.xml_text, media_type="application/xml",
                        headers={"Content-Disposition": f"attachment; filename=\"nfse_{nfse.numero or nfse.id}.xml\""})

    if nfse.xml_path and os.path.exists(f".{nfse.xml_path}"):
        from fastapi.responses import FileResponse
        return FileResponse(f".{nfse.xml_path}", media_type="application/xml",
                           filename=f"nfse_{nfse.numero or nfse.id}.xml",
                           headers={"Content-Disposition": f"attachment; filename=\"nfse_{nfse.numero or nfse.id}.xml\""})

    raise HTTPException(status_code=404, detail="XML nÃ£o disponÃ­vel para esta NFSe")


@router.post("/{nfse_id}/gerar-cobranca")
def gerar_cobranca_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.pedido),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")

    cobranca_existente = db.query(ContaReceber).filter(
        ContaReceber.observacao.like(f"%NFSe #{nfse.id}%")
    ).first()
    if cobranca_existente:
        request.session["error"] = "CobranÃ§a jÃ¡ existe para esta NFSe"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)

    cliente_id = None
    if nfse.pedido:
        cliente_id = nfse.pedido.cliente_id

    if not cliente_id:
        request.session["error"] = "NÃ£o foi possÃ­vel identificar o cliente para gerar cobranÃ§a"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)

    cobranca = ContaReceber(
        cliente_id=cliente_id,
        descricao=f"NFSe #{nfse.numero or nfse.id}",
        valor=nfse.valor_total or 0,
        data_vencimento=date.today(),
        forma_pagamento="NFSe",
        observacao=f"Gerado manualmente da NFSe #{nfse.id}",
        nfse_id=nfse.id,
    )
    db.add(cobranca)
    db.commit()
    request.session["message"] = f"CobranÃ§a gerada com sucesso para NFSe #{nfse.numero}!"
    return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)


@router.post("/{nfse_id}/cancelar")
def cancelar_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db),
                  motivo: str = Form("Cancelamento solicitado")):
    nfse = db.query(NFSe).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")
    if (nfse.status or '').lower() not in ("autorizada", "pendente"):
        request.session["error"] = f"NÃ£o Ã© possÃ­vel cancelar NFSe com status {nfse.status}"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)

    try:
        service = BethaNfseService()
        # Tenta cancelamento com assinatura XML (ABRASF)
        resultado = service.cancelar_nfse_assinado(nfse.numero, tpAmb=1, motivo=motivo, chave_acesso=nfse.codigo_verificacao, protocolo_dps=nfse.protocolo)

        if resultado.get('sucesso'):
            nfse.status = "cancelada"
            nfse.mensagem_retorno = f"Cancelada: {motivo}"
            db.commit()
            request.session["message"] = f"NFSe #{nfse.numero} cancelada com sucesso!"
        else:
            erros = resultado.get('erros', [])
            msg = '; '.join(e.get('mensagem', '') for e in erros)
            request.session["error"] = f"Erro ao cancelar NFSe: {msg}"
    except NFSeBethaError as e:
        request.session["error"] = f"Erro ao cancelar NFSe: {str(e)}"
    except Exception as e:
        request.session["error"] = f"Erro ao cancelar NFSe: {str(e)}"

    return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)


@router.post("/{nfse_id}/excluir")
def excluir_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")
    if (nfse.status or '').lower() != "rascunho":
        request.session["error"] = "SÃ³ Ã© possÃ­vel excluir NFSe em rascunho"
        return RedirectResponse(url="/nfse", status_code=303)
    try:
        db.query(ContaReceber).filter(ContaReceber.nfse_id == nfse.id).delete(
            synchronize_session=False
        )
        db.query(Assinatura).filter(Assinatura.nfse_id == nfse.id).delete(
            synchronize_session=False
        )
        db.delete(nfse)
        db.commit()
        request.session["message"] = f"NFSe #{nfse.numero} excluÃ­da"
    except Exception:
        db.rollback()
        logger.exception("Erro ao excluir NFSe %s", nfse_id)
        request.session["error"] = "Erro ao excluir a NFSe. Verifique se hÃ¡ contas ou assinaturas vinculadas."
    return RedirectResponse(url="/nfse", status_code=303)


@router.post("/{nfse_id}/transmitir")
def transmitir_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db), background_tasks: BackgroundTasks = None):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.itens),
        selectinload(NFSe.cliente),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")
    if (nfse.status or '').lower() not in ("rascunho", "pendente", "erro"):
        request.session["error"] = f"NÃ£o Ã© possÃ­vel transmitir NFSe com status {nfse.status}"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)
    if not nfse.itens:
        request.session["error"] = "NFSe sem itens para transmitir"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)
    if not nfse.cliente:
        request.session["error"] = "NFSe sem cliente vinculado"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)

    from services.nfse_betha import emitir_rascunho, sincronizar_nfse
    try:
        resultado = emitir_rascunho(nfse, db, tpAmb=1)
        sp = resultado.get('status_processamento', 'erro')
        novo_protocolo = resultado.get('protocolo')

        # NÃ£o sobrescreve protocolo se nova tentativa nÃ£o retornou um
        if novo_protocolo:
            nfse.protocolo = novo_protocolo
        nfse.data_emissao = resultado.get('data_emissao')

        erros = resultado.get('erros', [])
        if erros:
            msg_erro = "; ".join(f"[{e.get('codigo','')}] {e.get('mensagem','')}" for e in erros)
            nfse.mensagem_retorno = msg_erro

        # Se DPS jÃ¡ foi recepcionada e temos protocolo, tenta sync em vez de erro
        tem_protocolo = bool(nfse.protocolo)
        dps_duplicada = any('jÃ¡ recepcionada' in (e.get('mensagem','') or '').lower() for e in erros)

        if sp == 'sucesso':
            nfse.codigo_verificacao = resultado.get('codigo_verificacao')
            nfse.numero = resultado.get('numero') or nfse.numero
            nfse.status = "autorizada"
            nfse.mensagem_retorno = None
            dps_xml = resultado.get('xml')
            if dps_xml:
                xml_filename = f"nfse_{nfse.id}.xml"
                xml_path = f"static/uploads/nfs/{xml_filename}"
                os.makedirs(os.path.dirname(xml_path), exist_ok=True)
                with open(xml_path, 'w', encoding='utf-8') as f:
                    f.write(dps_xml)
                nfse.xml_path = f"/{xml_path.replace(os.sep, '/')}"
                nfse.xml_text = dps_xml
            # Regenera PDF com dados oficiais
            from services.nfse_pdf import gerar_pdf_nfse, gerar_danfse_pdf, is_xml_nfse_nacional
            empresa = db.query(Empresa).first()
            cliente = nfse.cliente or (nfse.pedido.cliente if nfse.pedido else None)
            if empresa and cliente:
                pdf_url = gerar_pdf_nfse(nfse, empresa, cliente, nfse.itens, STATUS_LABELS)
                nfse.pdf_path = pdf_url
            db.commit()
            msg = f"NFSe #{nfse.numero} emitida com sucesso!"
            if resultado.get('retry_iss_retido'):
                msg += " ISS retido foi marcado automaticamente."
            request.session["message"] = msg
            if background_tasks:
                # Envio pos-autorizacao feito na sincronizacao (status autorizada),
                # nao aqui, para nao enviar documento ainda em processamento.
                pass
        elif sp == 'processando':
            nfse.status = "em_processamento"
            db.commit()
            request.session["message"] = (f"NFSe #{nfse.numero} enviada! "
                "Aguardando processamento na prefeitura. Use o botÃ£o Sincronizar para verificar o status.")
        elif dps_duplicada and tem_protocolo:
            # DPS jÃ¡ recebida â€” tenta sincronizar com protocolo existente
            try:
                sync_result = sincronizar_nfse(nfse.protocolo, tpAmb=1)
                sp_sync = sync_result.get('status_processamento')
                if sp_sync == 'sucesso':
                    nfse.codigo_verificacao = sync_result.get('codigo_verificacao')
                    nfse.numero = sync_result.get('numero') or nfse.numero
                    nfse.status = "autorizada"
                    nfse.mensagem_retorno = None
                    from services.nfse_betha import gerar_dps_xml_nfse
                    num = int(nfse.numero) if nfse.numero and nfse.numero.isdigit() else None
                    dps_xml = gerar_dps_xml_nfse(nfse, db, 1, num)
                    if dps_xml:
                        xml_filename = f"nfse_{nfse.id}.xml"
                        xml_path = f"static/uploads/nfs/{xml_filename}"
                        os.makedirs(os.path.dirname(xml_path), exist_ok=True)
                        with open(xml_path, 'w', encoding='utf-8') as f:
                            f.write(dps_xml)
                        nfse.xml_path = f"/{xml_path.replace(os.sep, '/')}"
                        nfse.xml_text = dps_xml
                    empresa = db.query(Empresa).first()
                    cliente = nfse.cliente or (nfse.pedido.cliente if nfse.pedido else None)
                    if empresa and cliente:
                        pdf_url = gerar_pdf_nfse(nfse, empresa, cliente, nfse.itens, STATUS_LABELS)
                        nfse.pdf_path = pdf_url
                    db.commit()
                    request.session["message"] = f"NFSe #{nfse.numero} jÃ¡ estava processada! Autorizada com sucesso."
                    if background_tasks:
                        # Envio pos-autorizacao feito na sincronizacao.
                        pass
                elif sp_sync == 'processando':
                    nfse.status = "em_processamento"
                    nfse.mensagem_retorno = "DPS jÃ¡ recebida, aguardando processamento."
                    db.commit()
                    request.session["message"] = "DPS jÃ¡ recepcionada anteriormente. NFSe ainda em processamento."
                else:
                    nfse.status = "erro"
                    erros_sync = sync_result.get('erros', [])
                    msg_erro_sync = "; ".join(f"[{e.get('codigo','')}] {e.get('mensagem','')}" for e in erros_sync)
                    nfse.mensagem_retorno = msg_erro_sync
                    db.commit()
                    request.session["error"] = msg_erro_sync
            except Exception as e:
                nfse.status = "erro"
                nfse.mensagem_retorno = str(e)
                db.commit()
                request.session["error"] = str(e)
        elif dps_duplicada and not tem_protocolo:
            # DPS jÃ¡ recebida sem protocolo â€” gera novo nÃºmero e reenvia
            empresa = db.query(Empresa).first()
            if empresa:
                novo_numero = _proximo_numero(empresa, db)
                nfse.numero = str(novo_numero)
                # Recria resultado2 com novo nÃºmero
                try:
                    resultado2 = emitir_rascunho(nfse, db, tpAmb=1)
                    sp2 = resultado2.get('status_processamento', 'erro')
                    novo_protocolo2 = resultado2.get('protocolo')
                    if novo_protocolo2:
                        nfse.protocolo = novo_protocolo2
                    erros2 = resultado2.get('erros', [])
                    if sp2 == 'sucesso':
                        nfse.codigo_verificacao = resultado2.get('codigo_verificacao')
                        nfse.numero = resultado2.get('numero') or nfse.numero
                        nfse.status = "autorizada"
                        nfse.mensagem_retorno = None
                        dps_xml = resultado2.get('xml')
                        if dps_xml:
                            xml_filename = f"nfse_{nfse.id}.xml"
                            xml_path = f"static/uploads/nfs/{xml_filename}"
                            os.makedirs(os.path.dirname(xml_path), exist_ok=True)
                            with open(xml_path, 'w', encoding='utf-8') as f:
                                f.write(dps_xml)
                            nfse.xml_path = f"/{xml_path.replace(os.sep, '/')}"
                            nfse.xml_text = dps_xml
                        cliente = nfse.cliente or (nfse.pedido.cliente if nfse.pedido else None)
                        if empresa and cliente:
                            pdf_url = gerar_pdf_nfse(nfse, empresa, cliente, nfse.itens, STATUS_LABELS)
                            nfse.pdf_path = pdf_url
                        db.commit()
                        request.session["message"] = f"NFSe #{nfse.numero} reemitida com novo nÃºmero!"
                        if background_tasks:
                            # Envio pos-autorizacao feito na sincronizacao.
                            pass
                    elif sp2 == 'processando':
                        nfse.status = "em_processamento"
                        nfse.mensagem_retorno = "Reenviado com novo nÃºmero, aguardando processamento."
                        db.commit()
                        request.session["message"] = f"DPS jÃ¡ recepcionada. NFSe reenviada com novo nÃºmero #{novo_numero}."
                    else:
                        nfse.status = "erro"
                        msg_erro = "; ".join(f"[{e.get('codigo','')}] {e.get('mensagem','')}" for e in erros2)
                        nfse.mensagem_retorno = msg_erro
                        db.commit()
                        request.session["error"] = msg_erro
                except Exception as e2:
                    nfse.status = "erro"
                    nfse.mensagem_retorno = str(e2)
                    db.commit()
                    request.session["error"] = f"Erro ao reenviar com novo nÃºmero: {e2}"
            else:
                nfse.status = "erro"
                db.commit()
                request.session["error"] = "DPS jÃ¡ recepcionada e sem protocolo para consulta."
        else:
            nfse.status = "erro"
            db.commit()
            request.session["error"] = "; ".join(f"[{e.get('codigo','')}] {e.get('mensagem','')}" for e in erros)

        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)
    except NFSeBethaError as e:
        db.rollback()
        request.session["error"] = f"Erro ao transmitir NFSe: {str(e)}"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro inesperado: {str(e)}"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)


@router.post("/{nfse_id}/sincronizar")
def sincronizar_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).options(selectinload(NFSe.itens)).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")
    if (nfse.status or '').lower() not in ("em_processamento", "pendente", "erro", "autorizada", "cancelada"):
        request.session["error"] = "SÃ³ Ã© possÃ­vel sincronizar NFSe em processamento, pendente, erro, autorizada ou cancelada"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)
    if not nfse.protocolo:
        request.session["error"] = "NFSe sem protocolo de transmissÃ£o"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)

    from services.nfse_betha import sincronizar_nfse as sync_func
    try:
        resultado = sync_func(nfse.protocolo, tpAmb=1, numero_nfse=nfse.numero)
        sp = resultado.get('status_processamento', 'erro')

        if sp == 'cancelada':
            nfse.status = "cancelada"
            nfse.mensagem_retorno = "Cancelado via portal Betha"
            db.commit()
            request.session["message"] = f"NFSe #{nfse.numero} cancelada (sincronizado do portal)"
            return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)
        elif sp == 'sucesso':
            cv = resultado.get('codigo_verificacao')
            nfse.codigo_verificacao = cv
            nfse.chave_acesso = cv
            nfse.numero = resultado.get('numero') or nfse.numero
            nfse.status = "autorizada"
            nfse.mensagem_retorno = None

            from services.nfse_betha import gerar_dps_xml_nfse
            # Tenta usar XML oficial da Betha se retornado
            xml_oficial = resultado.get('xml_documento')
            if xml_oficial:
                xml_filename = f"nfse_{nfse.id}.xml"
                xml_path = f"static/uploads/nfs/{xml_filename}"
                os.makedirs(os.path.dirname(xml_path), exist_ok=True)
                with open(xml_path, 'w', encoding='utf-8') as f:
                    f.write(xml_oficial)
                nfse.xml_path = f"/{xml_path.replace(os.sep, '/')}"
                nfse.xml_text = xml_oficial
            else:
                numero = int(nfse.numero) if nfse.numero and nfse.numero.isdigit() else None
                dps_xml = gerar_dps_xml_nfse(nfse, db, 1, numero)
                if dps_xml:
                    xml_filename = f"nfse_{nfse.id}.xml"
                    xml_path = f"static/uploads/nfs/{xml_filename}"
                    os.makedirs(os.path.dirname(xml_path), exist_ok=True)
                    with open(xml_path, 'w', encoding='utf-8') as f:
                        f.write(dps_xml)
                    nfse.xml_path = f"/{xml_path.replace(os.sep, '/')}"
                    nfse.xml_text = dps_xml

            # Gera DANFSe local via brazilfiscalreport (ADN descontinuado)
            try:
                from services.nfse_pdf import gerar_danfse_pdf, is_xml_nfse_nacional
                xml_nacional = nfse.xml_text
                if xml_nacional and is_xml_nfse_nacional(xml_nacional):
                    pdf_filename = f"danfse_{nfse.numero or nfse.id}.pdf"
                    pdf_path = f"static/uploads/nfse/{pdf_filename}"
                    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
                    gerar_danfse_pdf(
                        xml_nacional, pdf_path,
                        cancelada=((nfse.status or '').lower() == 'cancelada'),
                    )
                    nfse.pdf_path = f"/{pdf_path.replace(os.sep, '/')}"
            except Exception as e:
                logger.warning(f"Erro ao gerar DANFSe local: {e}")

            # Fallback: gera PDF local (leiaute proprietÃ¡rio FPDF)
            if not nfse.pdf_path:
                from services.nfse_pdf import gerar_pdf_nfse, gerar_danfse_pdf, is_xml_nfse_nacional
                empresa = db.query(Empresa).first()
                cliente = nfse.cliente or (nfse.pedido.cliente if nfse.pedido else None)
                if empresa and cliente:
                    pdf_url = gerar_pdf_nfse(nfse, empresa, cliente, nfse.itens, STATUS_LABELS)
                    nfse.pdf_path = pdf_url

            db.commit()
            if nfse.pdf_path:
                request.session["message"] = f"NFSe #{nfse.numero} autorizada com DANFSe"
            else:
                request.session["message"] = f"NFSe #{nfse.numero} autorizada"

            # Dispara e-mail pos-autorizacao (evita enviar documento em processamento)
            try:
                from services.email_service import enviar_notificacao_conta
                from models import ContaReceber
                contas_vinculadas = db.query(ContaReceber).filter(
                    ContaReceber.nfse_id == nfse.id
                ).all()
                for c in contas_vinculadas:
                    enviar_notificacao_conta(c.id)
            except Exception as e:
                logger.warning(f"Erro ao disparar e-mail pos-autorizacao NFSe: {e}")

            # Baixa de estoque: insumos consumidos na NFSe (SAIDA_INSUMO)
            try:
                from services.estoque_service import baixar_nfse
                baixar_nfse(db, nfse)
            except Exception as e:
                logger.warning(f"Erro ao baixar estoque NFSe: {e}")
        elif sp == 'processando':
            db.commit()
            request.session["message"] = "NFSe ainda em processamento na prefeitura. Tente novamente em alguns segundos."
        else:
            nfse.status = "erro"
            erros = resultado.get('erros', [])
            nfse.mensagem_retorno = "; ".join(f"[{e.get('codigo','')}] {e.get('mensagem','')}" for e in erros)
            db.commit()
            request.session["error"] = nfse.mensagem_retorno

        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)
    except NFSeBethaError as e:
        request.session["error"] = f"Erro ao sincronizar: {str(e)}"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)
    except Exception as e:
        request.session["error"] = f"Erro inesperado: {str(e)}"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)


def _confirmar_cancelamentos_adn(emitidas):
    """Tarefa em background: confirma cancelamento via SEFIN (tipoEvento 101101)
    para cada NFSe emitida, atualizando o banco. Evita estourar o timeout do
    proxy durante a listagem do ADN."""
    from database import SessionLocal as _SL
    db_bg = _SL()
    try:
        empresa = db_bg.query(Empresa).first()
        svc = BethaNfseService(empresa=empresa)
        for n in emitidas:
            chave = n.get('chaveAcesso')
            if not chave:
                continue
            try:
                sit = svc.consultar_situacao_nfse(n.get('numero') or '', chave)
                if sit.get('situacao') == 'cancelada':
                    nf = db_bg.query(NFSe).filter(NFSe.chave_acesso == chave).first()
                    if nf and nf.status != 'cancelada':
                        nf.status = 'cancelada'
                        db_bg.commit()
            except Exception as e:
                logger.warning(f"ADN bg: falha ao confirmar cancelamento da NFSe {n.get('numero')}: {e}")
    finally:
        db_bg.close()


@router.get("/adn-listar")
def listar_nfse_adn(
    request: Request, db: Session = Depends(get_db),
    data_inicio: str = Query(""), data_fim: str = Query(""),
    tipo: str = Query(""), retorno: str = Query(""),
    background_tasks: BackgroundTasks = None,
):
    """Lista NFS-e do ADN (Ambiente de Dados Nacional) por perÃ­odo"""
    empresa = db.query(Empresa).first()
    if not data_inicio or not data_fim:
        request.session["error"] = "Informe data inÃ­cio e data fim"
        return RedirectResponse(url="/nfse", status_code=303)

    try:
        service = BethaNfseService(empresa=empresa)
        notas = service.listar_nfse_adn(data_inicio, data_fim, empresa_cnpj=empresa.cnpj)
        if tipo:
            notas = [n for n in notas if n.get('tipo') == tipo]
        emitidas = [n for n in notas if n.get('tipo') == 'emitida']
        recebidas = [n for n in notas if n.get('tipo') == 'recebida']

        # Salva/atualiza as NFSe emitidas por nÃ³s no banco (upsert por chaveAcesso)
        salvos = 0
        atualizados = 0
        for n in emitidas:
            chave = n.get('chaveAcesso')
            if not chave:
                continue
            existente = db.query(NFSe).filter(NFSe.chave_acesso == chave).first()
            if not existente:
                existente = db.query(NFSe).filter(NFSe.codigo_verificacao == chave).first()
                if existente and not existente.chave_acesso:
                    existente.chave_acesso = chave
            if existente:
                if n.get('cancelada') and existente.status != 'cancelada':
                    existente.status = 'cancelada'
                    atualizados += 1
                # Backfill: NFSe importada antes de termos o tomador_nome/cliente
                if not existente.cliente_id and not getattr(existente, 'tomador_nome', None):
                    existente.tomador_nome = n.get('tomador_nome')
                    existente.tomador_cpf_cnpj = n.get('tomador_cnpj')
                    doc = n.get('tomador_cnpj') or ''
                    doc_clean = re.sub(r'\D', '', doc)
                    cli = None
                    if doc_clean:
                        cli = db.query(Cliente).filter(
                            Cliente.cpf_cnpj != None, Cliente.cpf_cnpj.like(f"%{doc_clean}%")
                        ).first()
                    if not cli and n.get('tomador_nome'):
                        nome_l = re.sub(r'\s+', ' ', n['tomador_nome'].strip().lower())
                        for c in db.query(Cliente).filter(Cliente.nome != None).all():
                            if re.sub(r'\s+', ' ', (c.nome or '').strip().lower()) == nome_l:
                                cli = c
                                break
                    if cli:
                        existente.cliente_id = cli.id
                    if cli or existente.tomador_nome:
                        atualizados += 1
                continue
            nfse = NFSe()
            nfse.chave_acesso = chave
            nfse.numero = str(n.get('numero')) if n.get('numero') else None
            nfse.codigo_verificacao = chave
            dh = n.get('dhEmi')
            if dh:
                for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                    try:
                        nfse.data_emissao = datetime.strptime(dh[:19], fmt)
                        break
                    except Exception:
                        continue
            nfse.valor_total = float(n.get('valor') or 0)
            nfse.status = 'cancelada' if n.get('cancelada') else 'autorizada'
            nfse.origem = 'adn'
            nfse.xml_text = n.get('xml')
            nfse.tomador_nome = n.get('tomador_nome')
            nfse.tomador_cpf_cnpj = n.get('tomador_cnpj')
            doc = n.get('tomador_cnpj') or ''
            doc_clean = re.sub(r'\D', '', doc)
            cliente = None
            if doc_clean:
                cliente = db.query(Cliente).filter(
                    Cliente.cpf_cnpj != None, Cliente.cpf_cnpj.like(f"%{doc_clean}%")
                ).first()
            # Fallback: vincula pelo nome do tomador quando o CNPJ nÃ£o bate
            if not cliente and n.get('tomador_nome'):
                nome_l = re.sub(r'\s+', ' ', n['tomador_nome'].strip().lower())
                for c in db.query(Cliente).filter(Cliente.nome != None).all():
                    if re.sub(r'\s+', ' ', (c.nome or '').strip().lower()) == nome_l:
                        cliente = c
                        break
            if cliente:
                nfse.cliente_id = cliente.id
            db.add(nfse)
            salvos += 1
        if salvos or atualizados:
            db.commit()

        # Salva as NFSe recebidas (somos o tomador) em tabela prÃ³pria
        salvos_rec = 0
        atualizados_rec = 0
        for n in recebidas:
            chave = n.get('chaveAcesso')
            if not chave:
                continue
            existente = db.query(NFSeRecebida).filter(NFSeRecebida.chave_acesso == chave).first()
            if existente:
                if n.get('cancelada') and existente.status != 'cancelada':
                    existente.status = 'cancelada'
                    atualizados_rec += 1
                continue
            rec = NFSeRecebida()
            rec.chave_acesso = chave
            rec.numero = str(n.get('numero')) if n.get('numero') else None
            rec.codigo_verificacao = chave
            dh = n.get('dhEmi')
            if dh:
                for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                    try:
                        rec.data_emissao = datetime.strptime(dh[:19], fmt)
                        break
                    except Exception:
                        continue
            rec.valor_total = float(n.get('valor') or 0)
            rec.status = 'cancelada' if n.get('cancelada') else 'autorizada'
            rec.xml_text = n.get('xml')
            rec.emitente_nome = n.get('emitente_nome')
            rec.emitente_cnpj = n.get('emitente_cnpj')
            rec.origem = 'adn'
            doc = n.get('emitente_cnpj') or ''
            doc_clean = re.sub(r'\D', '', doc)
            if doc_clean:
                forn = db.query(Fornecedor).filter(
                    Fornecedor.cpf_cnpj != None, Fornecedor.cpf_cnpj.like(f"%{doc_clean}%")
                ).first()
                if forn:
                    rec.fornecedor_id = forn.id
            db.add(rec)
            salvos_rec += 1
        if salvos_rec or atualizados_rec:
            db.commit()

        # Confirma cancelamentos via SEFIN em background (nÃ£o bloqueia a resposta)
        if background_tasks is not None and emitidas:
            background_tasks.add_task(_confirmar_cancelamentos_adn, emitidas)

        msg = f"ADN: {len(notas)} NFS-e encontradas"
        if salvos or atualizados:
            msg += f" ({salvos} emitidas salvas, {atualizados} atualizadas)"
        if salvos_rec or atualizados_rec:
            msg += f"; {salvos_rec} recebidas salvas, {atualizados_rec} atualizadas"
        if not tipo:
            msg += f" ({len(emitidas)} emitidas, {len(recebidas)} recebidas)"
        elif tipo == 'emitida':
            msg = f"ADN: {len(notas)} NFS-e emitidas encontradas"
        elif tipo == 'recebida':
            msg = f"ADN: {len(notas)} NFS-e recebidas encontradas"

        # Busca de recebidas: salva no banco e redireciona para a pÃ¡gina dedicada
        if tipo == 'recebida' or retorno == 'recebidas':
            request.session["message"] = msg
            return RedirectResponse(
                url=f"/nfse/recebidas?data_inicio={data_inicio}&data_fim={data_fim}",
                status_code=303)

        return request.app.state.templates.TemplateResponse(request,
            "nfse/lista.html",
            {"request": request, "nfse": [], "status": "", "busca": "",
             "data_inicio": data_inicio, "data_fim": data_fim,
             "messages": [{"tipo": "success", "texto": msg}],
             "empresa": empresa, "STATUS_LABELS": STATUS_LABELS,
             "nfse_ids_sem_cobranca": set(),
             "adn_notas": notas, "adn_emitidas": emitidas, "adn_recebidas": []},
            background=background_tasks)
    except NFSeBethaError as e:
        request.session["error"] = f"Erro ADN: {str(e)}"
        return RedirectResponse(url="/nfse", status_code=303)
    except Exception as e:
        request.session["error"] = f"Erro inesperado ADN: {str(e)}"
        return RedirectResponse(url="/nfse", status_code=303)


@router.get("/adn-danfse/{chave_acesso}")
def adn_danfse(request: Request, chave_acesso: str, db: Session = Depends(get_db)):
    """Baixa DANFSe (gerado localmente) a partir da chave de acesso"""
    from fastapi.responses import Response, FileResponse
    from services.nfse_pdf import gerar_danfse_pdf, is_xml_nfse_nacional
    from services.nfse_betha import BethaNfseService
    from models_nfe import NFSeRecebida
    import os

    nfse = db.query(NFSe).options(
        selectinload(NFSe.itens),
        selectinload(NFSe.pedido),
    ).filter(NFSe.codigo_verificacao == chave_acesso).first()

    cancelada = False
    if nfse:
        xml_nacional = nfse.xml_text
        if not xml_nacional:
            try:
                service = BethaNfseService(empresa=None)
                sit = service.consultar_situacao_nfse(str(nfse.numero), chave_acesso)
                xml_nacional = sit.get('xml') if isinstance(sit, dict) else None
                if xml_nacional:
                    nfse.xml_text = xml_nacional
                    db.commit()
            except Exception:
                pass
        cancelada = ((nfse.status or '').lower() == 'cancelada')
    else:
        nfse_rec = db.query(NFSeRecebida).filter(
            NFSeRecebida.chave_acesso == chave_acesso
        ).first()
        if not nfse_rec:
            return Response(status_code=404, content="NFSe nÃ£o encontrada")
        xml_nacional = nfse_rec.xml_text
        cancelada = nfse_rec.cancelada or ((nfse_rec.status or '').lower() == 'cancelada')

    if not xml_nacional or not is_xml_nfse_nacional(xml_nacional):
        return Response(status_code=400, content="XML nacional nÃ£o disponÃ­vel para geraÃ§Ã£o do DANFSe")

    try:
        pdf_dir = "static/uploads/nfse"
        os.makedirs(pdf_dir, exist_ok=True)

        pdf_filename = f"danfse_{chave_acesso}.pdf"
        pdf_path = os.path.join(pdf_dir, pdf_filename)

        gerar_danfse_pdf(xml_nacional, pdf_path, cancelada=cancelada)

        if os.path.exists(pdf_path):
            return FileResponse(pdf_path, media_type="application/pdf",
                              filename=pdf_filename)
        else:
            return Response(status_code=500, content="Erro ao gerar DANFSe")
    except Exception as e:
        return Response(status_code=500, content=f"Erro ao gerar DANFSe: {str(e)}")


@router.get("/adn-xml/{chave_acesso}")
def adn_xml(request: Request, chave_acesso: str, db: Session = Depends(get_db)):
    """Proxy para baixar XML do ADN pelo servidor (com certificado)"""
    from fastapi.responses import Response
    from services.nfse_betha import ADN_NFSE_URL
    import httpx
    try:
        empresa = db.query(Empresa).first()
        service = BethaNfseService(empresa=empresa)
        session = service._get_adn_session()
        r = session.get(f"{ADN_NFSE_URL}/nfse/{chave_acesso}", timeout=30)
        if r.status_code == 200:
            data = r.json()
            xml_b64 = data.get('nfseXmlGZipB64')
            if xml_b64:
                import base64, gzip
                xml = gzip.decompress(base64.b64decode(xml_b64)).decode('utf-8')
                return Response(content=xml, media_type="application/xml",
                                headers={"Content-Disposition": f"attachment; filename=nfse_{chave_acesso}.xml"})
        return Response(status_code=404, content="XML nÃ£o encontrado no ADN")
    except Exception as e:
        return Response(status_code=500, content=str(e))

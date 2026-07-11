import os
import json
import logging
from datetime import datetime, date
from fastapi import APIRouter, Depends, Request, Form, Query, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import desc
from database import get_db
from models import Cliente, Empresa, PedidoVenda, PedidoVendaItem, Produto, ContaReceber, StatusConta, OrdemServico
from models_nfe import NFSe, NFSeItem
from services.nfse_betha import emitir_completa, emitir_rascunho, NFSeBethaError, BethaNfseService
from services.nfse_pdf import gerar_pdf_nfse

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
    numero = (empresa.ultimo_numero_nfse or 0) + 1
    empresa.ultimo_numero_nfse = numero
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


@router.get("/")
def listar_nfse(
    request: Request, db: Session = Depends(get_db),
    status: str = Query(""), busca: str = Query(""),
):
    empresa = db.query(Empresa).first()
    query = db.query(NFSe).options(
        joinedload(NFSe.pedido),
    )
    if status:
        query = query.filter(NFSe.status == status)
    if busca:
        query = query.filter(
            NFSe.numero.cast(str).ilike(f"%{busca}%")
        )
    nfse_list = query.order_by(desc(NFSe.id)).all()

    nfse_ids_sem_cobranca = set()
    for n in nfse_list:
        cob = db.query(ContaReceber).filter(
            ContaReceber.observacao.like(f"%NFSe #{n.id}%")
        ).first()
        if not cob:
            nfse_ids_sem_cobranca.add(n.id)

    return request.app.state.templates.TemplateResponse(
        "nfse/lista.html",
        {"request": request, "nfse": nfse_list, "status": status, "busca": busca,
         "messages": _get_messages(request), "empresa": empresa,
         "STATUS_LABELS": STATUS_LABELS,
         "nfse_ids_sem_cobranca": nfse_ids_sem_cobranca}
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
    return request.app.state.templates.TemplateResponse(
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
        return JSONResponse({"error": "Empresa não cadastrada"}, status_code=400)

    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        return JSONResponse({"error": "Cliente não encontrado"}, status_code=404)

    try:
        itens_data = json.loads(itens_json)
    except json.JSONDecodeError:
        return JSONResponse({"error": "JSON de itens inválido"}, status_code=400)

    if not itens_data:
        return JSONResponse({"error": "Adicione pelo menos um serviço"}, status_code=400)

    codigos_lc116 = set(i.get("codigo_lc116", "") for i in itens_data if i.get("codigo_lc116"))
    if len(codigos_lc116) > 1:
        return JSONResponse({
            "error": f"Itens com códigos LC116 diferentes: {', '.join(sorted(codigos_lc116))}. A prefeitura de Dourados-MS não aceita múltiplos códigos na mesma NFS-e."
        }, status_code=400)

    # Aplica desconto proporcional aos itens
    if desconto > 0:
        total_bruto = sum(float(i.get("valor_total", 0)) for i in itens_data)
        if total_bruto > 0:
            fator = 1 - (desconto / total_bruto)
            for item in itens_data:
                item["valor_unitario"] = round(float(item.get("valor_unitario", 0)) * fator, 2)
                item["valor_total"] = round(item["valor_unitario"] * float(item.get("quantidade", 1)), 2)

    valor_total = sum(float(i.get("valor_total", 0)) for i in itens_data)
    numero = str(_proximo_numero(empresa, db))

    nfse = NFSe(
        numero=numero,
        cliente_id=cliente.id,
        data_emissao=datetime.now(),
        status="rascunho",
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
            quantidade=float(item.get("quantidade", 1)),
            valor_unitario=float(item.get("valor_unitario", 0)),
            valor_total=float(item.get("valor_total", 0)),
            codigo_servico=item.get("codigo_lc116", ""),
            tributacao_municipal=item.get("codigo_tributacao_municipal", ""),
        )
        db.add(nfse_item)

    db.commit()

    # Gera cobrança automaticamente (se solicitado)
    if gerar_cobranca:
        try:
            cobranca = ContaReceber(
                cliente_id=cliente_id,
                descricao=f"NFSe Avulsa #{nfse.numero}",
                valor=valor_total,
                data_vencimento=datetime.strptime(data_competencia, '%Y-%m-%d').date() if data_competencia else date.today(),
                forma_pagamento="NFSe",
                observacao=f"Gerado automaticamente da NFSe #{nfse.id}",
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
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return request.app.state.templates.TemplateResponse(
        "nfse/emissao.html",
        {"request": request, "pedido": pedido}
    )


@router.post("/emitir/{pedido_id}")
def emitir_nfse(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoVenda).options(
        selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.produto)
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    itens_servico = [i for i in pedido.itens if i.produto and i.produto.tipo == 'servico']
    if not itens_servico:
        return JSONResponse({"error": "Nenhum item de serviço no pedido"}, status_code=400)

    codigos_lc116 = set(i.produto.codigo_lc116 for i in itens_servico if i.produto.codigo_lc116)
    if len(codigos_lc116) > 1:
        return JSONResponse({
            "error": f"Pedido possui itens de serviço com códigos LC116 diferentes: {', '.join(sorted(codigos_lc116))}. A prefeitura de Dourados-MS não aceita múltiplos códigos na mesma NFS-e. Remova ou separe os itens em pedidos diferentes."
        }, status_code=400)

    empresa = db.query(Empresa).first()
    if not empresa:
        return JSONResponse({"error": "Empresa não cadastrada"}, status_code=400)
    numero_nfse = _proximo_numero(empresa, db)

    try:
        resultado = emitir_completa(pedido, db, tpAmb=1, numero_nfse=numero_nfse)

        valor_total = sum(float(i.total or 0) for i in itens_servico)
        if valor_total == 0:
            valor_total = float(pedido.total or 0)

        iss_retido = getattr(pedido.cliente, 'iss_retido', False) or False
        empresa = db.query(Empresa).first()
        nfse = NFSe(
            pedido_id=pedido_id,
            cliente_id=pedido.cliente_id,
            numero=resultado.get('numero') or numero_nfse,
            codigo_verificacao=resultado.get('codigo_verificacao'),
            status="autorizada" if resultado.get('protocolo') else "pendente",
            valor_total=valor_total,
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
                quantidade=float(item.quantidade or 1),
                valor_unitario=float(item.preco_unitario or 0),
                valor_total=float(item.total or 0),
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

        # Gera cobrança automaticamente
        try:
            cobranca = ContaReceber(
                cliente_id=pedido.cliente_id,
                descricao=f"NFSe Pedido #{pedido.numero or pedido.id}",
                valor=valor_total,
                data_vencimento=pedido.data,
                forma_pagamento="NFSe",
                observacao=f"Gerado automaticamente da NFSe #{nfse.id} (Pedido #{pedido.id})",
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
        request.session["error"] = "Ordem de Serviço não encontrada"
        return RedirectResponse(url="/ordens-servico", status_code=303)

    if not os.valor_servico or os.valor_servico <= 0:
        request.session["error"] = "OS sem valor de serviço para emitir NFSe"
        return RedirectResponse(url=f"/ordens-servico/{os_id}", status_code=303)

    return request.app.state.templates.TemplateResponse(
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
        request.session["error"] = "Ordem de Serviço não encontrada"
        return RedirectResponse(url="/ordens-servico", status_code=303)

    if not os.valor_servico or os.valor_servico <= 0:
        request.session["error"] = "OS sem valor de serviço para emitir NFSe"
        return RedirectResponse(url=f"/ordens-servico/{os_id}", status_code=303)

    empresa = db.query(Empresa).first()
    if not empresa:
        request.session["error"] = "Empresa não configurada"
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
            descricao=os.servicos_executados or "Serviço prestado",
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


@router.get("/{nfse_id}/editar")
def editar_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.itens).selectinload(NFSeItem.produto),
        selectinload(NFSe.pedido),
        selectinload(NFSe.cliente),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe não encontrada")
    if nfse.status not in ("rascunho", "erro"):
        request.session["error"] = "Só é possível editar NFSe em rascunho ou erro"
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
    return request.app.state.templates.TemplateResponse(
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
        raise HTTPException(status_code=404, detail="NFSe não encontrada")
    if nfse.status not in ("rascunho", "erro"):
        request.session["error"] = "Só é possível editar NFSe em rascunho ou erro"
        return RedirectResponse(url="/nfse", status_code=303)

    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        return JSONResponse({"error": "Cliente não encontrado"}, status_code=404)

    try:
        itens_data = json.loads(itens_json)
    except json.JSONDecodeError:
        request.session["error"] = "JSON de itens inválido"
        return RedirectResponse(url=f"/nfse/{nfse_id}/editar", status_code=303)

    if not itens_data:
        request.session["error"] = "Adicione pelo menos um serviço"
        return RedirectResponse(url=f"/nfse/{nfse_id}/editar", status_code=303)

    codigos_lc116 = set(i.get("codigo_lc116", "") for i in itens_data if i.get("codigo_lc116"))
    if len(codigos_lc116) > 1:
        request.session["error"] = f"Itens com códigos LC116 diferentes: {', '.join(sorted(codigos_lc116))}"
        return RedirectResponse(url=f"/nfse/{nfse_id}/editar", status_code=303)

    # Aplica desconto proporcional aos itens
    if desconto > 0:
        total_bruto = sum(float(i.get("valor_total", 0)) for i in itens_data)
        if total_bruto > 0:
            fator = 1 - (desconto / total_bruto)
            for item in itens_data:
                item["valor_unitario"] = round(float(item.get("valor_unitario", 0)) * fator, 2)
                item["valor_total"] = round(item["valor_unitario"] * float(item.get("quantidade", 1)), 2)

    valor_total = sum(float(i.get("valor_total", 0)) for i in itens_data)

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
            quantidade=float(item.get("quantidade", 1)),
            valor_unitario=float(item.get("valor_unitario", 0)),
            valor_total=float(item.get("valor_total", 0)),
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
        raise HTTPException(status_code=404, detail="NFSe não encontrada")

    cobranca = db.query(ContaReceber).filter(
        ContaReceber.observacao.like(f"%NFSe #{nfse.id}%")
    ).first()

    return request.app.state.templates.TemplateResponse(
        "nfse/detalhe.html",
        {"request": request, "nfse": nfse, "STATUS_LABELS": STATUS_LABELS, "cobranca": cobranca}
    )


@router.get("/{nfse_id}/pdf")
def baixar_pdf_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.itens),
        selectinload(NFSe.pedido),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe não encontrada")

    if nfse.pdf_path and os.path.exists(f".{nfse.pdf_path}"):
        from fastapi.responses import FileResponse
        return FileResponse(f".{nfse.pdf_path}", media_type="application/pdf",
                           filename=f"nfse_{nfse.numero or nfse.id}.pdf")

    # Tenta baixar DANFSe da Betha
    if nfse.status == 'autorizada' and nfse.numero:
        try:
            from services.nfse_betha import BethaNfseService
            service = BethaNfseService()
            danfse_url = service.obter_danfse_url(str(nfse.numero), nfse.codigo_verificacao)
            if danfse_url:
                import requests
                r = requests.get(danfse_url, timeout=30, verify=False)
                if r.status_code == 200 and 'application/pdf' in r.headers.get('content-type', ''):
                    pdf_filename = f"nfse_{nfse.numero or nfse.id}.pdf"
                    pdf_path = f"static/uploads/nfse/{pdf_filename}"
                    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
                    with open(pdf_path, 'wb') as f:
                        f.write(r.content)
                    nfse.pdf_path = f"/{pdf_path.replace(os.sep, '/')}"
                    db.commit()
                    from fastapi.responses import FileResponse
                    return FileResponse(pdf_path, media_type="application/pdf",
                                       filename=pdf_filename)
        except Exception as e:
            logger.warning(f"Erro ao baixar DANFSe da Betha: {e}")

    empresa = db.query(Empresa).first()
    cliente = nfse.cliente or (nfse.pedido.cliente if nfse.pedido else None)
    if not cliente:
        raise HTTPException(status_code=400, detail="Cliente não encontrado para gerar PDF")
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
        raise HTTPException(status_code=404, detail="NFSe não encontrada")

    if nfse.xml_path and os.path.exists(f".{nfse.xml_path}"):
        from fastapi.responses import FileResponse
        return FileResponse(f".{nfse.xml_path}", media_type="application/xml",
                           filename=f"nfse_{nfse.numero or nfse.id}.xml",
                           headers={"Content-Disposition": f"attachment; filename=\"nfse_{nfse.numero or nfse.id}.xml\""})

    raise HTTPException(status_code=404, detail="XML não disponível para esta NFSe")


@router.post("/{nfse_id}/gerar-cobranca")
def gerar_cobranca_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.pedido),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe não encontrada")

    cobranca_existente = db.query(ContaReceber).filter(
        ContaReceber.observacao.like(f"%NFSe #{nfse.id}%")
    ).first()
    if cobranca_existente:
        request.session["error"] = "Cobrança já existe para esta NFSe"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)

    cliente_id = None
    if nfse.pedido:
        cliente_id = nfse.pedido.cliente_id

    if not cliente_id:
        request.session["error"] = "Não foi possível identificar o cliente para gerar cobrança"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)

    cobranca = ContaReceber(
        cliente_id=cliente_id,
        descricao=f"NFSe #{nfse.numero or nfse.id}",
        valor=nfse.valor_total or 0,
        data_vencimento=date.today(),
        forma_pagamento="NFSe",
        observacao=f"Gerado manualmente da NFSe #{nfse.id}",
    )
    db.add(cobranca)
    db.commit()
    request.session["message"] = f"Cobrança gerada com sucesso para NFSe #{nfse.numero}!"
    return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)


@router.post("/{nfse_id}/cancelar")
def cancelar_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db),
                  motivo: str = Form("Cancelamento solicitado")):
    nfse = db.query(NFSe).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe não encontrada")
    if nfse.status not in ("autorizada", "pendente"):
        request.session["error"] = f"Não é possível cancelar NFSe com status {nfse.status}"
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
        raise HTTPException(status_code=404, detail="NFSe não encontrada")
    if nfse.status != "rascunho":
        request.session["error"] = "Só é possível excluir NFSe em rascunho"
        return RedirectResponse(url="/nfse", status_code=303)
    db.delete(nfse)
    db.commit()
    request.session["message"] = f"NFSe #{nfse.numero} excluída"
    return RedirectResponse(url="/nfse", status_code=303)


@router.post("/{nfse_id}/transmitir")
def transmitir_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.itens),
        selectinload(NFSe.cliente),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe não encontrada")
    if nfse.status not in ("rascunho", "pendente", "erro"):
        request.session["error"] = f"Não é possível transmitir NFSe com status {nfse.status}"
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

        # Não sobrescreve protocolo se nova tentativa não retornou um
        if novo_protocolo:
            nfse.protocolo = novo_protocolo
        nfse.data_emissao = resultado.get('data_emissao')

        erros = resultado.get('erros', [])
        if erros:
            msg_erro = "; ".join(f"[{e.get('codigo','')}] {e.get('mensagem','')}" for e in erros)
            nfse.mensagem_retorno = msg_erro

        # Se DPS já foi recepcionada e temos protocolo, tenta sync em vez de erro
        tem_protocolo = bool(nfse.protocolo)
        dps_duplicada = any('já recepcionada' in (e.get('mensagem','') or '').lower() for e in erros)

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
            # Regenera PDF com dados oficiais
            from services.nfse_pdf import gerar_pdf_nfse
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
        elif sp == 'processando':
            nfse.status = "em_processamento"
            db.commit()
            request.session["message"] = (f"NFSe #{nfse.numero} enviada! "
                "Aguardando processamento na prefeitura. Use o botão Sincronizar para verificar o status.")
        elif dps_duplicada and tem_protocolo:
            # DPS já recebida — tenta sincronizar com protocolo existente
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
                    from services.nfse_pdf import gerar_pdf_nfse
                    empresa = db.query(Empresa).first()
                    cliente = nfse.cliente or (nfse.pedido.cliente if nfse.pedido else None)
                    if empresa and cliente:
                        pdf_url = gerar_pdf_nfse(nfse, empresa, cliente, nfse.itens, STATUS_LABELS)
                        nfse.pdf_path = pdf_url
                    db.commit()
                    request.session["message"] = f"NFSe #{nfse.numero} já estava processada! Autorizada com sucesso."
                elif sp_sync == 'processando':
                    nfse.status = "em_processamento"
                    nfse.mensagem_retorno = "DPS já recebida, aguardando processamento."
                    db.commit()
                    request.session["message"] = "DPS já recepcionada anteriormente. NFSe ainda em processamento."
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
            # DPS já recebida sem protocolo — gera novo número e reenvia
            empresa = db.query(Empresa).first()
            if empresa:
                novo_numero = _proximo_numero(empresa, db)
                nfse.numero = str(novo_numero)
                # Recria resultado2 com novo número
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
                        from services.nfse_pdf import gerar_pdf_nfse
                        cliente = nfse.cliente or (nfse.pedido.cliente if nfse.pedido else None)
                        if empresa and cliente:
                            pdf_url = gerar_pdf_nfse(nfse, empresa, cliente, nfse.itens, STATUS_LABELS)
                            nfse.pdf_path = pdf_url
                        db.commit()
                        request.session["message"] = f"NFSe #{nfse.numero} reemitida com novo número!"
                    elif sp2 == 'processando':
                        nfse.status = "em_processamento"
                        nfse.mensagem_retorno = "Reenviado com novo número, aguardando processamento."
                        db.commit()
                        request.session["message"] = f"DPS já recepcionada. NFSe reenviada com novo número #{novo_numero}."
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
                    request.session["error"] = f"Erro ao reenviar com novo número: {e2}"
            else:
                nfse.status = "erro"
                db.commit()
                request.session["error"] = "DPS já recepcionada e sem protocolo para consulta."
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
        raise HTTPException(status_code=404, detail="NFSe não encontrada")
    if nfse.status not in ("em_processamento", "pendente", "erro", "autorizada", "cancelada"):
        request.session["error"] = "Só é possível sincronizar NFSe em processamento, pendente, erro, autorizada ou cancelada"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)
    if not nfse.protocolo:
        request.session["error"] = "NFSe sem protocolo de transmissão"
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
            nfse.codigo_verificacao = resultado.get('codigo_verificacao')
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

            # Tenta baixar PDF — 1º ADN, 2º Betha, 3º fallback local
            try:
                from services.nfse_betha import BethaNfseService
                service = BethaNfseService()
                chave = nfse.codigo_verificacao
                pdf_bytes = None
                # 1ª tentativa: ADN (DANFSe oficial)
                if chave:
                    pdf_bytes = service.baixar_danfse_adn(chave)
                # 2ª tentativa: Betha (REST / recoverpdfservlet)
                if not pdf_bytes:
                    pdf_params = {k.replace('pdf_', ''): resultado[k] for k in resultado if k.startswith('pdf_')}
                    danfse_url = resultado.get('url_danfse')
                    if not danfse_url and pdf_params:
                        danfse_url = service.obter_danfse_url(str(nfse.numero), chave, pdf_params)
                    if danfse_url:
                        import requests
                        r = requests.get(danfse_url, timeout=30, verify=False)
                        if r.status_code == 200 and 'application/pdf' in r.headers.get('content-type', ''):
                            pdf_bytes = r.content
                # Salva PDF se conseguiu
                if pdf_bytes:
                    pdf_filename = f"nfse_{nfse.numero or nfse.id}.pdf"
                    pdf_path = f"static/uploads/nfse/{pdf_filename}"
                    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
                    with open(pdf_path, 'wb') as f:
                        f.write(pdf_bytes)
                    nfse.pdf_path = f"/{pdf_path.replace(os.sep, '/')}"
            except Exception as e:
                logger.warning(f"Erro ao baixar PDF: {e}")

            # Fallback: gera PDF local
            if not nfse.pdf_path:
                from services.nfse_pdf import gerar_pdf_nfse
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

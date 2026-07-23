import logging
import os
import json
import re
import secrets
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date
from fastapi import APIRouter, Depends, Request, Form, Query, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, Response, JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, String

from database import get_db
from models import (Cliente, Empresa, PedidoVenda, PedidoVendaItem,
                    StatusPedido, Produto, OrdemServico, CfopNatureza,
                    ContaReceber, PedidoConsolidado, PedidoConsolidadoItem)
from models_nfe import NFe, NFeItem, NFSe, NFSeItem
from services.nfe_notaas import (
    emitir_nfe, consultar_status, baixar_pdf, baixar_xml,
    cancelar_nfe, montar_payload_nfe, explodir_itens, consultar_municipios,
    _limpar_doc
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nfe", tags=["NFe"])

from app.core.config import settings
UPLOAD_DIR = os.path.join(settings.UPLOAD_DIR, "nfe")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _proximo_numero(empresa: Empresa, db: Session) -> int:
    empresa_locked = db.query(Empresa).filter(Empresa.id == empresa.id).with_for_update().first()
    numero = (empresa_locked.ultimo_numero_nfe or 0) + 1
    empresa_locked.ultimo_numero_nfe = numero
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


def _extrair_erro_nfe(data):
    """Extrai um motivo legível da SEFAZ/NotaAS a partir do dict retornado
    (cStat, xMotivo, protocolo, mensagem). Retorna string ou None."""
    if not isinstance(data, dict):
        return None
    cstat = data.get("cStat") or data.get("cstat")
    status = data.get("status")
    msg = (data.get("message") or data.get("motivo") or data.get("xMotivo")
           or data.get("mensagem") or data.get("xMotivoAutorizacao"))
    protocolo = (data.get("protocolo") or data.get("nProt")
                or data.get("nProtAutorizacao") or data.get("chaveAcesso"))
    partes = []
    if cstat:
        partes.append(f"cStat: {cstat}")
    if msg:
        partes.append(str(msg))
    if status and status not in ("issued", "queued", "processing"):
        partes.append(f"status: {status}")
    if protocolo and protocolo not in partes:
        partes.append(f"protocolo: {protocolo}")
    if not partes:
        for chave in ("erros", "erro", "errors", "errosList"):
            lista = data.get(chave)
            if isinstance(lista, list) and lista:
                sub = "; ".join(
                    str(e.get("mensagem") or e.get("message") or e)
                    for e in lista if isinstance(e, dict)
                )
                if sub:
                    partes.append(sub)
                break
    return " | ".join(partes) if partes else None


def _salvar_xml_nfe(empresa, nfe, db):
    if not nfe.invoice_id or nfe.xml_path:
        return
    try:
        xml_text = baixar_xml(empresa, nfe.invoice_id)
        xml_filename = f"nfe_{nfe.numero}.xml"
        xml_path = f"static/uploads/nfe/{xml_filename}"
        os.makedirs(os.path.dirname(xml_path), exist_ok=True)
        with open(xml_path, 'w', encoding='utf-8') as f:
            f.write(xml_text)
        nfe.xml_path = f"/{xml_path.replace(os.sep, '/')}"
        nfe.xml_text = xml_text
        db.commit()
    except Exception as e:
        logger.warning(f"Erro ao salvar XML NFe #{nfe.id}: {e}")


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
    return request.app.state.templates.TemplateResponse(request, 
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
    sort: str = Query("id"), ordem: str = Query("desc"),
    page: int = Query(1, ge=1), per_page: int = Query(20, ge=10, le=100),
    page_sefaz: int = Query(1, ge=1), per_page_sefaz: int = Query(20, ge=5, le=100),
):
    empresa = db.query(Empresa).first()
    query = db.query(NFe).options(
        joinedload(NFe.pedido), joinedload(NFe.os), joinedload(NFe.itens),
        joinedload(NFe.cliente),
    )
    if status:
        query = query.filter(NFe.status == status)
    if busca:
        query = query.filter(
            NFe.numero.cast(String).ilike(f"%{busca}%")
        )
    from sqlalchemy import asc as sql_asc
    order_func = desc if ordem == "desc" else sql_asc
    total_count = query.count()
    if sort == "cliente":
        query = query.outerjoin(Cliente, NFe.cliente_id == Cliente.id)
        notas = query.order_by(order_func(Cliente.nome), NFe.id).all()
    else:
        sort_col = getattr(NFe, sort, NFe.id)
        notas = query.order_by(order_func(sort_col), NFe.id).all()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    notas = notas[start:start + per_page]
    # Carrega NFe distribuídas do banco local
    from models import NFeDistribuida
    cnpj_clean = re.sub(r'\D', '', empresa.cnpj) if empresa and empresa.cnpj else ''
    todas_dist = db.query(NFeDistribuida).order_by(NFeDistribuida.nsu.desc()).all()
    dist_emitidas = []
    dist_recebidas = []
    vistos = set()
    for n in todas_dist:
        if n.chave_acesso in vistos: continue
        vistos.add(n.chave_acesso)
        emit_clean = re.sub(r'\D', '', n.emitente_cnpj or '')
        dest_clean = re.sub(r'\D', '', n.destinatario_cnpj or '')
        info = {
            'chaveAcesso': n.chave_acesso, 'numero': n.numero,
            'dhEmi': n.dh_emi, 'valor': n.valor,
            'emitente_nome': n.emitente_nome, 'emitente_cnpj': n.emitente_cnpj,
            'destinatario_nome': n.destinatario_nome, 'destinatario_cnpj': n.destinatario_cnpj,
        }
        if emit_clean == cnpj_clean:
            info['tipo'] = 'emitida'
            dist_emitidas.append(info)
        elif dest_clean == cnpj_clean:
            info['tipo'] = 'recebida'
            dist_recebidas.append(info)

    # Inclui NFes emitidas localmente como 'emitidas'
    for n in db.query(NFe).options(joinedload(NFe.cliente)).filter(
        NFe.status.in_(['issued', 'cancelled']),
        NFe.chave_acesso.isnot(None),
    ).all():
        if n.chave_acesso in vistos:
            continue
        vistos.add(n.chave_acesso)
        dh_emi = n.data_emissao.strftime('%Y-%m-%dT%H:%M:%S') if n.data_emissao else ''
        dist_emitidas.append({
            'chaveAcesso': n.chave_acesso, 'numero': str(n.numero),
            'dhEmi': dh_emi, 'valor': n.valor_total,
            'emitente_nome': empresa.razao_social, 'emitente_cnpj': empresa.cnpj,
            'destinatario_nome': n.cliente.nome if n.cliente else '',
            'destinatario_cnpj': n.cliente.cpf_cnpj if n.cliente else '',
        })
    # Aplica ordenação nas listas da SEFAZ conforme params sort/ordem
    def _sort_key(item):
        if sort == "contraparte":
            return (item.get('emitente_nome') or item.get('destinatario_nome') or '').lower()
        elif sort == "numero":
            v = item.get('numero') or ''
            return int(v) if v.isdigit() else v
        elif sort == "valor":
            return item.get('valor') or 0
        else:
            return item.get('dhEmi') or ''
    rev = ordem == "desc"
    dist_emitidas.sort(key=_sort_key, reverse=rev)
    dist_recebidas.sort(key=_sort_key, reverse=rev)
    return request.app.state.templates.TemplateResponse(request, 
        "nfe/lista.html",
        {"request": request, "notas": notas, "status": status, "busca": busca,
         "messages": _get_messages(request), "empresa": empresa,
         "STATUS_LABELS": STATUS_LABELS,
         "sort": sort, "ordem": ordem,
         "page": page, "per_page": per_page, "total_pages": total_pages, "total_count": total_count,
         "page_sefaz": page_sefaz, "per_page_sefaz": per_page_sefaz,
         "dist_notas": dist_emitidas + dist_recebidas,
         "dist_emitidas": dist_emitidas, "dist_recebidas": dist_recebidas}
    )


@router.get("/recebidas")
def listar_nfe_recebidas(
    request: Request, db: Session = Depends(get_db),
    data_inicio: str = Query(""), data_fim: str = Query(""),
    busca: str = Query(""), page: int = Query(1),
    ordenar: str = Query("dhEmi"), direcao: str = Query("desc"),
):
    """Lista as NFe recebidas (somos o destinatário) via SEFAZ, com filtros, ordenação e paginação."""
    from models import NFeDistribuida
    empresa = db.query(Empresa).first()
    cnpj_clean = re.sub(r'\D', '', empresa.cnpj) if empresa and empresa.cnpj else ''
    todas = db.query(NFeDistribuida).all()
    recebidas = []
    for n in todas:
        dest_clean = re.sub(r'\D', '', n.destinatario_cnpj or '')
        emit_clean = re.sub(r'\D', '', n.emitente_cnpj or '')
        if dest_clean == cnpj_clean and emit_clean != cnpj_clean:
            recebidas.append(n)
    if busca:
        busca_l = busca.lower()
        recebidas = [n for n in recebidas if (n.numero or '').lower().find(busca_l) >= 0]
    if data_inicio:
        recebidas = [n for n in recebidas if (n.dh_emi or '')[:10] >= data_inicio]
    if data_fim:
        recebidas = [n for n in recebidas if (n.dh_emi or '')[:10] <= data_fim]

    def _sort_key(item):
        if ordenar == "emitente_nome":
            return (item.emitente_nome or '').lower()
        elif ordenar == "numero":
            v = item.numero or ''
            return int(v) if v.isdigit() else 0
        elif ordenar == "valor":
            return float(item.valor or 0)
        return item.dh_emi or ''
    recebidas.sort(key=_sort_key, reverse=(direcao == "desc"))

    per_page = 25
    total = len(recebidas)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    pagina = recebidas[(page - 1) * per_page: (page - 1) * per_page + per_page]

    return request.app.state.templates.TemplateResponse(request,
        "nfe/recebidas.html",
        {"request": request, "recebidas": pagina, "busca": busca,
         "data_inicio": data_inicio, "data_fim": data_fim, "page": page,
         "total_pages": total_pages, "total": total, "per_page": per_page,
         "ordenar": ordenar, "direcao": direcao,
         "messages": _get_messages(request), "empresa": empresa,
         "STATUS_LABELS": STATUS_LABELS}
    )


@router.get("/config")
def config_nfe(request: Request, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    cfop_list = db.query(CfopNatureza).order_by(CfopNatureza.cfop).all()
    return request.app.state.templates.TemplateResponse(request, 
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
    return request.app.state.templates.TemplateResponse(request, 
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

    itens_nfe, itens_nfse = explodir_itens(pedido=pedido, db=db)
    if not itens_nfe:
        if itens_nfse:
            request.session["message"] = "Pedido contém apenas serviços. Redirecionando para emissão de NFSe."
            return RedirectResponse(url=f"/nfse/emitir/{pedido_id}", status_code=303)
        else:
            request.session["error"] = "Nenhum item do tipo produto para emitir NFe"
        return RedirectResponse(url=f"/nfe/emitir/pedido/{pedido_id}", status_code=303)

    cliente = pedido.cliente
    ie = _limpar_doc(cliente.inscricao_estadual) if hasattr(cliente, 'inscricao_estadual') else None
    if not ie and not cliente.isento_ie and cliente.indicador_ie != "nao_contribuinte":
        request.session["error"] = f"Cliente '{cliente.nome}' não possui Inscrição Estadual e não está marcado como Isento IE ou Não contribuinte. Cadastre a IE, marque como isento, ou altere o Tipo Contribuinte para Não contribuinte."
        return RedirectResponse(url=f"/nfe/emitir/pedido/{pedido_id}", status_code=303)

    try:
        numero_nfe = _proximo_numero(empresa, db)
        total = sum(i.get("preco_unitario", 0) * i.get("quantidade", 0) for i in itens_nfe)
        now = datetime.now()
        nfe = NFe(
            pedido_id=pedido_id,
            origem="assinatura" if pedido.assinatura_id else "pedido",
            cliente_id=cliente.id,
            numero=numero_nfe,
            serie=empresa.serie_nfe or 1,
            status="rascunho",
            natureza_operacao=natureza_operacao,
            cfop=cfop or empresa.cfop_padrao,
            valor_total=total,
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
                cfop=cfop or empresa.cfop_padrao,
                unidade=item.get("unidade", "UN"),
                quantidade=item.get("quantidade", 1),
                preco_unitario=item.get("preco_unitario", 0),
                total=item.get("quantidade", 1) * item.get("preco_unitario", 0),
            )
            db.add(nfe_item)

        # Criar NFSe se houver itens de serviço
        codigos_lc116 = set()
        for item in itens_nfse:
            if item.produto and item.produto.codigo_lc116:
                codigos_lc116.add(item.produto.codigo_lc116)
        if len(codigos_lc116) > 1:
            db.rollback()
            request.session["error"] = f"Pedido possui itens de serviço com códigos LC116 diferentes: {', '.join(sorted(codigos_lc116))}. A prefeitura de Dourados-MS não aceita múltiplos códigos na mesma NFS-e. Remova ou separe os itens em pedidos diferentes."
            return RedirectResponse(url=f"/nfe/emitir/pedido/{pedido_id}", status_code=303)

        numero_nfse = str((empresa.ultimo_numero_nfse or 0) + 1)
        empresa.ultimo_numero_nfse = int(numero_nfse)
        valor_servicos = sum(
            Decimal(str(item.total or item.preco_unitario or 0)) * Decimal(str(item.quantidade or 1))
            for item in itens_nfse
        )

        iss_retido = getattr(cliente, 'iss_retido', False) or False
        nfse = NFSe(
            pedido_id=pedido_id,
            numero=numero_nfse,
            status="rascunho",
            valor_total=valor_servicos,
            data_emissao=now,
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
                valor_total=Decimal(str(item.total or (item.preco_unitario or 0) * (item.quantidade or 1))),
                codigo_servico=item.produto.codigo_lc116 or "",
                tributacao_municipal=item.produto.codigo_tributacao_municipal or "",
            )
            db.add(nfse_item)

        db.commit()
        msg = f"Rascunho NFe #{numero_nfe} salvo! Revise antes de transmitir."
        if numero_nfse:
            msg += f" Rascunho NFSe #{numero_nfse} criado para os serviços."
        request.session["message"] = msg
        return RedirectResponse(url=f"/nfe/{nfe.id}/previa", status_code=303)
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro ao salvar rascunho NFe: {str(e)}"
        return RedirectResponse(url=f"/nfe/emitir/pedido/{pedido_id}", status_code=303)


@router.get("/emitir/os/{os_id}")
def emitir_os_form(
    request: Request, os_id: int, db: Session = Depends(get_db),
):
    empresa = db.query(Empresa).first()
    os = db.query(OrdemServico).options(
        joinedload(OrdemServico.cliente)
    ).filter(OrdemServico.id == os_id).first()
    if not os:
        request.session["error"] = "Ordem de Serviço não encontrada"
        return RedirectResponse(url="/ordens-servico", status_code=303)

    if not os.valor_pecas or os.valor_pecas <= 0:
        request.session["message"] = "OS sem valor de peças. Redirecionando para emissão de NFSe."
        return RedirectResponse(url=f"/nfse/emitir/os/{os_id}", status_code=303)

    itens_nfe, _ = explodir_itens(os=os, db=db)
    return request.app.state.templates.TemplateResponse(request, 
        "nfe/emissao.html",
        {"request": request, "pedido": os, "itens_nfe": itens_nfe,
         "itens_nfse": [], "empresa": empresa,
         "total_os": os.valor_total,
         "messages": _get_messages(request)}
    )


@router.post("/emitir/os/{os_id}")
def emitir_os_submit(
    request: Request, os_id: int, db: Session = Depends(get_db),
    natureza_operacao: str = Form("Venda de mercadoria"),
    cfop: str = Form(None),
):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.notaas_api_key:
        request.session["error"] = "API Key NotaAs não configurada"
        return RedirectResponse(url="/nfe/config", status_code=303)

    os = db.query(OrdemServico).options(
        joinedload(OrdemServico.cliente)
    ).filter(OrdemServico.id == os_id).first()
    if not os:
        request.session["error"] = "Ordem de Serviço não encontrada"
        return RedirectResponse(url="/ordens-servico", status_code=303)

    if not os.valor_pecas or os.valor_pecas <= 0:
        request.session["error"] = "OS sem valor de peças para emitir NFe"
        return RedirectResponse(url=f"/nfe/emitir/os/{os_id}", status_code=303)

    itens_nfe, _ = explodir_itens(os=os, db=db)
    if not itens_nfe:
        request.session["error"] = "Nenhum item de produto para emitir NFe"
        return RedirectResponse(url=f"/nfe/emitir/os/{os_id}", status_code=303)

    cliente = os.cliente
    ie = _limpar_doc(cliente.inscricao_estadual) if hasattr(cliente, 'inscricao_estadual') else None
    if not ie and not cliente.isento_ie and cliente.indicador_ie != "nao_contribuinte":
        request.session["error"] = f"Cliente '{cliente.nome}' não possui Inscrição Estadual e não está marcado como Isento IE ou Não contribuinte."
        return RedirectResponse(url=f"/nfe/emitir/os/{os_id}", status_code=303)

    try:
        numero_nfe = _proximo_numero(empresa, db)
        total = sum(i.get("preco_unitario", 0) * i.get("quantidade", 0) for i in itens_nfe)
        now = datetime.now()
        nfe = NFe(
            os_id=os_id,
            origem="os",
            cliente_id=cliente.id,
            numero=numero_nfe,
            serie=empresa.serie_nfe or 1,
            status="rascunho",
            natureza_operacao=natureza_operacao,
            cfop=cfop or empresa.cfop_padrao,
            valor_total=total,
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
                cfop=cfop or empresa.cfop_padrao,
                unidade=item.get("unidade", "UN"),
                quantidade=item.get("quantidade", 1),
                preco_unitario=item.get("preco_unitario", 0),
                total=item.get("quantidade", 1) * item.get("preco_unitario", 0),
            )
            db.add(nfe_item)

        db.commit()
        request.session["message"] = f"Rascunho NFe #{numero_nfe} salvo para OS #{os_id}! Revise antes de transmitir."
        return RedirectResponse(url=f"/nfe/{nfe.id}/previa", status_code=303)
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro ao salvar rascunho NFe: {str(e)}"
        return RedirectResponse(url=f"/nfe/emitir/os/{os_id}", status_code=303)


@router.get("/emitir/consolidacao/{consolidacao_id}")
def emitir_consolidacao_form(
    request: Request, consolidacao_id: int, db: Session = Depends(get_db),
):
    empresa = db.query(Empresa).first()
    consolidacao = db.query(PedidoConsolidado).options(
        joinedload(PedidoConsolidado.cliente),
        joinedload(PedidoConsolidado.itens).joinedload(PedidoConsolidadoItem.produto)
    ).filter(PedidoConsolidado.id == consolidacao_id).first()
    if not consolidacao:
        request.session["error"] = "Consolidação não encontrada"
        return RedirectResponse(url="/consolidacoes", status_code=303)

    if consolidacao.status != "concluido":
        request.session["error"] = "Apenas consolidações finalizadas podem emitir NFe"
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    itens_nfe, itens_nfse = explodir_itens_consolidacao(consolidacao=consolidacao, db=db)
    return request.app.state.templates.TemplateResponse(request, 
        "nfe/emissao.html",
        {"request": request, "pedido": consolidacao, "itens_nfe": itens_nfe,
         "itens_nfse": itens_nfse, "empresa": empresa,
         "messages": _get_messages(request)}
    )


@router.post("/emitir/consolidacao/{consolidacao_id}")
def emitir_consolidacao_submit(
    request: Request, consolidacao_id: int, db: Session = Depends(get_db),
    natureza_operacao: str = Form("Venda de mercadoria"),
    cfop: str = Form(None),
):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.notaas_api_key:
        request.session["error"] = "API Key NotaAs não configurada"
        return RedirectResponse(url="/nfe/config", status_code=303)

    consolidacao = db.query(PedidoConsolidado).options(
        joinedload(PedidoConsolidado.cliente),
        joinedload(PedidoConsolidado.itens).joinedload(PedidoConsolidadoItem.produto)
    ).filter(PedidoConsolidado.id == consolidacao_id).first()
    if not consolidacao:
        request.session["error"] = "Consolidação não encontrada"
        return RedirectResponse(url="/consolidacoes", status_code=303)

    if consolidacao.status != "concluido":
        request.session["error"] = "Apenas consolidações finalizadas podem emitir NFe"
        return RedirectResponse(url=f"/consolidacoes/{consolidacao_id}", status_code=303)

    itens_nfe, itens_nfse = explodir_itens_consolidacao(consolidacao=consolidacao, db=db)
    if not itens_nfe:
        if itens_nfse:
            request.session["message"] = "Consolidação contém apenas serviços. Redirecionando para emissão de NFSe."
            return RedirectResponse(url=f"/nfse/emitir/consolidacao/{consolidacao_id}", status_code=303)
        else:
            request.session["error"] = "Nenhum item do tipo produto para emitir NFe"
        return RedirectResponse(url=f"/nfe/emitir/consolidacao/{consolidacao_id}", status_code=303)

    cliente = consolidacao.cliente
    ie = _limpar_doc(cliente.inscricao_estadual) if hasattr(cliente, 'inscricao_estadual') else None
    if not ie and not cliente.isento_ie and cliente.indicador_ie != "nao_contribuinte":
        request.session["error"] = f"Cliente '{cliente.nome}' não possui Inscrição Estadual e não está marcado como Isento IE ou Não contribuinte. Cadastre a IE, marque como isento, ou altere o Tipo Contribuinte para Não contribuinte."
        return RedirectResponse(url=f"/nfe/emitir/consolidacao/{consolidacao_id}", status_code=303)

    try:
        numero_nfe = _proximo_numero(empresa, db)
        total = sum(i.get("preco_unitario", 0) * i.get("quantidade", 0) for i in itens_nfe)
        now = datetime.now()
        nfe = NFe(
            consolidacao_id=consolidacao_id,
            origem="consolidacao",
            cliente_id=cliente.id,
            numero=numero_nfe,
            serie=empresa.serie_nfe or 1,
            status="rascunho",
            natureza_operacao=natureza_operacao,
            cfop=cfop or empresa.cfop_padrao,
            valor_total=total,
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
                cfop=cfop or empresa.cfop_padrao,
                unidade=item.get("unidade", "UN"),
                quantidade=item.get("quantidade", 1),
                preco_unitario=item.get("preco_unitario", 0),
                total=item.get("quantidade", 1) * item.get("preco_unitario", 0),
            )
            db.add(nfe_item)

        # Criar NFSe se houver itens de serviço
        codigos_lc116 = set()
        for item in itens_nfse:
            if item.produto and item.produto.codigo_lc116:
                codigos_lc116.add(item.produto.codigo_lc116)
        if len(codigos_lc116) > 1:
            db.rollback()
            request.session["error"] = f"Consolidação possui itens de serviço com códigos LC116 diferentes: {', '.join(sorted(codigos_lc116))}. A prefeitura de Dourados-MS não aceita múltiplos códigos na mesma NFS-e. Remova ou separe os itens em consolidações diferentes."
            return RedirectResponse(url=f"/nfe/emitir/consolidacao/{consolidacao_id}", status_code=303)

        numero_nfse = str((empresa.ultimo_numero_nfse or 0) + 1)
        empresa.ultimo_numero_nfse = int(numero_nfse)
        valor_servicos = sum(
            Decimal(str(item.total or item.preco_unitario or 0)) * Decimal(str(item.quantidade or 1))
            for item in itens_nfse
        )

        iss_retido = getattr(cliente, 'iss_retido', False) or False
        nfse = NFSe(
            consolidacao_id=consolidacao_id,
            numero=numero_nfse,
            status="rascunho",
            valor_total=valor_servicos,
            data_emissao=now,
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
                valor_total=Decimal(str(item.total or (item.preco_unitario or 0) * (item.quantidade or 1))),
                codigo_servico=item.produto.codigo_lc116 or "",
                tributacao_municipal=item.produto.codigo_tributacao_municipal or "",
            )
            db.add(nfse_item)

        db.commit()
        msg = f"Rascunho NFe #{numero_nfe} salvo! Revise antes de transmitir."
        if numero_nfse:
            msg += f" Rascunho NFSe #{numero_nfse} criado para os serviços."
        request.session["message"] = msg
        return RedirectResponse(url=f"/nfe/{nfe.id}/previa", status_code=303)
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro ao salvar rascunho NFe: {str(e)}"
        return RedirectResponse(url=f"/nfe/emitir/consolidacao/{consolidacao_id}", status_code=303)


@router.get("/emitir/avulsa")
def emitir_avulsa_form(
    request: Request, db: Session = Depends(get_db),
    editar: int = Query(None),
):
    empresa = db.query(Empresa).first()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    produtos = db.query(Produto).filter(
        Produto.tipo == "produto", Produto.situacao == "A"
    ).order_by(Produto.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "cpf_cnpj": c.cpf_cnpj,
                       "cidade": c.cidade, "estado": c.estado} for c in clientes]
    produtos_json = [{"id": p.id, "nome": p.nome, "preco": float(p.preco or 0),
                       "ncm": p.ncm, "unidade": p.unidade or "UN",
                       "estoque": p.estoque or 0, "codigo": p.codigo or ""} for p in produtos]

    saved_editar_id = request.session.pop("nfe_avulsa_editar_id", None)
    editar = saved_editar_id or editar
    saved_cliente_id = request.session.pop("nfe_avulsa_cliente_id", None)
    saved_cfop = request.session.pop("nfe_avulsa_cfop", None)
    saved_natureza = request.session.pop("nfe_avulsa_natureza", None)
    saved_itens = request.session.pop("nfe_avulsa_itens", None)
    saved_desconto = request.session.pop("nfe_avulsa_desconto", None)
    saved_data_emissao = request.session.pop("nfe_avulsa_data_emissao", None)
    saved_hora_emissao = request.session.pop("nfe_avulsa_hora_emissao", None)
    saved_data_saida = request.session.pop("nfe_avulsa_data_saida", None)
    saved_hora_saida = request.session.pop("nfe_avulsa_hora_saida", None)
    saved_finalidade = request.session.pop("nfe_avulsa_finalidade", None)
    saved_indicador_presenca = request.session.pop("nfe_avulsa_indicador_presenca", None)
    erro_ie_cliente_id = request.session.pop("nfe_avulsa_erro_ie", None)

    return request.app.state.templates.TemplateResponse(request, 
        "nfe/emissao_avulsa.html",
        {"request": request, "empresa": empresa, "clientes": clientes,
         "produtos": produtos, "clientes_json": clientes_json,
         "produtos_json": produtos_json,
         "messages": _get_messages(request),
         "editar": editar,
         "saved_cliente_id": saved_cliente_id,
         "saved_cfop": saved_cfop,
         "saved_natureza": saved_natureza,
         "saved_itens": saved_itens,
         "saved_desconto": saved_desconto,
         "saved_data_emissao": saved_data_emissao,
         "saved_hora_emissao": saved_hora_emissao,
         "saved_data_saida": saved_data_saida,
         "saved_hora_saida": saved_hora_saida,
         "saved_finalidade": saved_finalidade,
         "saved_indicador_presenca": saved_indicador_presenca,
         "erro_ie_cliente_id": erro_ie_cliente_id}
    )


def _parse_nfe_datetime(data_str: str, hora_str: str) -> datetime:
    if data_str:
        try:
            h, m = hora_str.split(":") if hora_str else ("00", "00")
            return datetime.strptime(f"{data_str} {h}:{m}", "%Y-%m-%d %H:%M")
        except (ValueError, AttributeError):
            return datetime.now()
    return None


@router.post("/emitir/avulsa")
def emitir_avulsa_submit(
    request: Request, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    natureza_operacao: str = Form("Venda de mercadoria"),
    cfop: str = Form(None),
    itens_json: str = Form(...),
    desconto: float = Form(0.0),
    data_emissao: str = Form(""),
    hora_emissao: str = Form(""),
    data_saida: str = Form(""),
    hora_saida: str = Form(""),
    finalidade: str = Form("normal"),
    indicador_presenca: int = Form(1),
    gerar_cobranca: bool = Form(False),
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
    if not ie and not cliente.isento_ie and cliente.indicador_ie != "nao_contribuinte":
        request.session["nfe_avulsa_cliente_id"] = cliente_id
        request.session["nfe_avulsa_cfop"] = cfop
        request.session["nfe_avulsa_natureza"] = natureza_operacao
        request.session["nfe_avulsa_itens"] = itens_json
        request.session["nfe_avulsa_desconto"] = desconto
        request.session["nfe_avulsa_erro_ie"] = str(cliente.id)
        request.session["nfe_avulsa_data_emissao"] = data_emissao
        request.session["nfe_avulsa_hora_emissao"] = hora_emissao
        request.session["nfe_avulsa_data_saida"] = data_saida
        request.session["nfe_avulsa_hora_saida"] = hora_saida
        request.session["nfe_avulsa_finalidade"] = finalidade
        request.session["nfe_avulsa_indicador_presenca"] = indicador_presenca
        return RedirectResponse(url="/nfe/emitir/avulsa", status_code=303)

    itens_nfe = []
    for item in itens_data:
        prod_id = item.get("produto_id")
        origem = 0
        if prod_id:
            prod = db.query(Produto).filter(Produto.id == prod_id).first()
            if prod:
                origem = prod.origem or 0
        itens_nfe.append({
            "produto_id": prod_id,
            "descricao": item.get("descricao", ""),
            "ncm": item.get("ncm") or "99999999",
            "unidade": item.get("unidade", "UN"),
            "quantidade": Decimal(str(item.get("quantidade", 1))),
            "preco_unitario": Decimal(str(item.get("preco_unitario", 0))),
            "origem": origem,
        })

    if desconto > 0:
        total_bruto = sum(i["preco_unitario"] * i["quantidade"] for i in itens_nfe)
        if total_bruto > 0:
            fator = Decimal("1") - (Decimal(str(desconto)) / total_bruto)
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
            data_emissao=_parse_nfe_datetime(data_emissao, hora_emissao),
            data_saida=_parse_nfe_datetime(data_saida, hora_saida),
            finalidade=finalidade,
            indicador_presenca=indicador_presenca,
            aliquota_federal=empresa.nfe_aliquota_federal or 0.0,
            aliquota_estadual=empresa.nfe_aliquota_estadual or 0.0,
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
            if item.get("produto_id"):
                prod = db.query(Produto).filter(Produto.id == item["produto_id"]).first()
                if prod:
                    prod.estoque = (prod.estoque or 0) - float(item["quantidade"])

        if gerar_cobranca:
            cobranca = ContaReceber(
                cliente_id=cliente.id,
                descricao=f"NFe Avulsa #{numero_nfe}",
                valor=total,
                data_vencimento=datetime.strptime(data_emissao, '%Y-%m-%d').date() if data_emissao else date.today(),
                forma_pagamento="NFe",
                observacao=f"Gerado automaticamente da NFe #{nfe.id}",
            )
            db.add(cobranca)

        db.commit()
        msg = f"Rascunho NFe #{numero_nfe} salvo! Revise antes de transmitir."
        if gerar_cobranca:
            msg += " Cobrança gerada."
        request.session["message"] = msg
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
    if not cliente.codigo_ibge:
        erros.append("Código IBGE do município do cliente é obrigatório")
    if not empresa.codigo_ibge:
        erros.append("Código IBGE da empresa é obrigatório (configure em Configurações)")
    if not nfe.itens or len(nfe.itens) == 0:
        erros.append("NFe deve ter pelo menos 1 item")
    for i, item in enumerate(nfe.itens):
        ncm = (item.ncm or "").replace(".", "")
        if not ncm or ncm == "99999999":
            erros.append(f"Item #{i+1} ({item.descricao}): NCM é obrigatório")
        elif len(ncm) != 8:
            erros.append(f"Item #{i+1} ({item.descricao}): NCM deve ter 8 dígitos (atual: {ncm})")
        if not item.cfop:
            erros.append(f"Item #{i+1} ({item.descricao}): CFOP é obrigatório")
    return erros


@router.get("/distribuicao")
def nfe_distribuicao(
    request: Request, db: Session = Depends(get_db),
    data_inicio: str = Query(""), data_fim: str = Query(""),
    tipo: str = Query(""), reiniciar: bool = Query(False), retorno: str = Query(""),
    sort: str = Query("dhEmi"), ordem: str = Query("desc"),
    page_sefaz: int = Query(1, ge=1), per_page_sefaz: int = Query(20, ge=5, le=100),
):
    """Lista NFe (emitidas e recebidas) via SEFAZ (Distribuição DF-e)"""
    from services.nfe_distribuicao import NFeDistribuicaoService
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.cnpj:
        request.session["error"] = "Empresa sem CNPJ configurado"
        return RedirectResponse(url="/nfe", status_code=303)

    from models import NFeDistribuida
    try:
        if reiniciar:
            # Reinicia o cursor para baixar todo o histórico da SEFAZ.
            # Atenção: a SEFAZ bloqueia (cStat 656) re-downloads completos
            # feitos em sequência; use apenas quando necessário (após ~1h).
            empresa.nfe_ultnsu = None
            db.commit()

        service = NFeDistribuicaoService(empresa, db=db)
        notas = service.listar_nfe(empresa.cnpj)
        cnpj_clean = re.sub(r'\D', '', empresa.cnpj)

        # Backfill XML para registros antigos que estão sem
        from models import NFeDistribuida as NFeDist
        sem_xml = db.query(NFeDist).filter(NFeDist.xml.is_(None), NFeDist.chave_acesso.isnot(None)).all()
        for reg in sem_xml:
            try:
                service.consultar_por_chave(empresa.cnpj, reg.chave_acesso)
            except Exception as e:
                logger.warning(f"Backfill XML falhou {reg.chave_acesso}: {e}")

        # Recarrega dados após backfill
        todas = db.query(NFeDistribuida).order_by(NFeDistribuida.nsu.desc()).all()
        previstos = set()
        notas_local = []
        for n in todas:
            chave = n.chave_acesso
            if chave in previstos:
                continue
            previstos.add(chave)
            emit_clean = re.sub(r'\D', '', n.emitente_cnpj or '')
            dest_clean = re.sub(r'\D', '', n.destinatario_cnpj or '')
            if emit_clean == cnpj_clean:
                tipo_n = 'emitida'
            elif dest_clean == cnpj_clean:
                tipo_n = 'recebida'
            else:
                tipo_n = 'outra'
            notas_local.append({
                'chaveAcesso': chave,
                'numero': n.numero,
                'dhEmi': n.dh_emi,
                'valor': n.valor,
                'emitente_nome': n.emitente_nome,
                'emitente_cnpj': n.emitente_cnpj,
                'destinatario_nome': n.destinatario_nome,
                'destinatario_cnpj': n.destinatario_cnpj,
                'NSU': n.nsu,
                'schema': n.schema_nfe,
                'tipo': tipo_n,
                'xml': n.xml,
            })

        # Inclui NFes emitidas localmente (tabela nfe) como 'emitidas'
        from models import NFe as NFeLocal
        for n in db.query(NFeLocal).options(joinedload(NFeLocal.cliente)).filter(
            NFeLocal.status.in_(['issued', 'cancelled']),
            NFeLocal.chave_acesso.isnot(None),
        ).all():
            if n.chave_acesso in previstos:
                continue
            previstos.add(n.chave_acesso)
            dh_emi = n.data_emissao.strftime('%Y-%m-%dT%H:%M:%S') if n.data_emissao else ''
            notas_local.append({
                'chaveAcesso': n.chave_acesso,
                'id': n.id,
                'numero': str(n.numero),
                'dhEmi': dh_emi,
                'valor': n.valor_total,
                'emitente_nome': empresa.razao_social,
                'emitente_cnpj': empresa.cnpj,
                'destinatario_nome': n.cliente.nome if n.cliente else '',
                'destinatario_cnpj': n.cliente.cpf_cnpj if n.cliente else '',
                'NSU': '',
                'schema': '',
                'tipo': 'emitida',
                'xml': '',
            })

        if data_inicio:
            notas_local = [n for n in notas_local if (n.get('dhEmi') or '')[:10] >= data_inicio]
        if data_fim:
            notas_local = [n for n in notas_local if (n.get('dhEmi') or '')[:10] <= data_fim]
        if tipo:
            notas_local = [n for n in notas_local if n.get('tipo') == tipo]
        rev = ordem == "desc"
        if sort == "contraparte":
            notas_local.sort(key=lambda x: (x.get('emitente_nome') or x.get('destinatario_nome') or '').lower(), reverse=rev)
        elif sort == "numero":
            notas_local.sort(key=lambda x: int(x.get('numero') or 0) if (x.get('numero') or '').isdigit() else x.get('numero') or '', reverse=rev)
        elif sort == "valor":
            notas_local.sort(key=lambda x: x.get('valor') or 0, reverse=rev)
        else:
            notas_local.sort(key=lambda x: x.get('dhEmi') or '', reverse=rev)
        emitidas = [n for n in notas_local if n.get('tipo') == 'emitida']
        recebidas = [n for n in notas_local if n.get('tipo') == 'recebida']

        # Sincroniza as emitidas para a tabela principal nfe (unifica a listagem, igual NFSe)
        for n in emitidas:
            chave = n.get('chaveAcesso')
            if not chave:
                continue
            existente = db.query(NFe).filter(NFe.chave_acesso == chave).first()
            if existente:
                # Backfill: preenche destinatário e vincula cliente se ainda vazio
                if not existente.destinatario_nome:
                    existente.destinatario_nome = n.get('destinatario_nome')
                if not existente.destinatario_cpf_cnpj:
                    existente.destinatario_cpf_cnpj = n.get('destinatario_cnpj')
                if not existente.cliente_id:
                    cli = _resolver_cliente_nfe(db, n.get('destinatario_cnpj'), n.get('destinatario_nome'))
                    if cli:
                        existente.cliente_id = cli.id
                continue
            nf = NFe()
            nf.chave_acesso = chave
            try:
                nf.numero = int(n.get('numero') or 0)
            except (ValueError, TypeError):
                nf.numero = 0
            nf.serie = 1
            nf.status = 'cancelled' if n.get('cancelada') else 'issued'
            nf.origem = 'importada'
            nf.valor_total = float(n.get('valor') or 0)
            nf.xml_text = n.get('xml')
            nf.destinatario_nome = n.get('destinatario_nome')
            nf.destinatario_cpf_cnpj = n.get('destinatario_cnpj')
            dh = n.get('dhEmi')
            if dh:
                for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                    try:
                        nf.data_emissao = datetime.strptime(dh[:19], fmt)
                        break
                    except Exception:
                        continue
            cli = _resolver_cliente_nfe(db, n.get('destinatario_cnpj'), n.get('destinatario_nome'))
            if cli:
                nf.cliente_id = cli.id
            db.add(nf)
        db.commit()

        # Emitidas SEFAZ ficam unificadas na listagem principal (/nfe);
        # recebidas têm página dedicada (/nfe/recebidas)
        ambiente = "Homologação" if service.tpAmb == 2 else "Produção"
        detalhe = ""
        if (len(emitidas) + len(recebidas)) == 0:
            dica = ""
            if service.ultimo_cstat == '656':
                dica = " | A SEFAZ bloqueia re-download completo: aguarde ~1h e use 'Reiniciar' (ou clique Buscar SEFAZ mais tarde para consulta incremental)"
            detalhe = (f" | SEFAZ cStat={service.ultimo_cstat or '-'} "
                       f"({service.ultimo_motivo or 'sem resposta'}) | Ambiente={ambiente} | "
                       f"cUFAutor=50 (MS - Mato Grosso do Sul, correto) | "
                       f"ultNSU={empresa.nfe_ultnsu or '000000000000000'}{dica}")
        if tipo == 'emitida':
            msg = f"SEFAZ: {len(emitidas)} NFe emitidas sincronizadas na listagem principal{detalhe}"
        elif tipo == 'recebida':
            msg = f"SEFAZ: {len(recebidas)} NFe recebidas importadas{detalhe}"
        else:
            msg = f"SEFAZ: {len(emitidas)} emitidas e {len(recebidas)} recebidas{detalhe}"
        if tipo == 'recebida' or retorno == 'recebidas':
            request.session["message"] = msg
            return RedirectResponse(url=f"/nfe/recebidas?data_inicio={data_inicio}&data_fim={data_fim}", status_code=303)
        request.session["message"] = msg
        return RedirectResponse(url="/nfe", status_code=303)
    except Exception as e:
        msg = str(e)
        if "Invalid password" in msg or "PKCS12" in msg:
            request.session["error"] = "Erro SEFAZ: Certificado digital inválido (senha incorreta ou arquivo corrompido). Verifique a senha do certificado A1 nas Configurações."
        else:
            request.session["error"] = f"Erro SEFAZ: {msg}"
        return RedirectResponse(url="/nfe", status_code=303)


@router.post("/importar-chave")
def importar_nfe_por_chave(
    request: Request, db: Session = Depends(get_db),
    chave_acesso: str = Form(...),
):
    """Importa uma NFe pela chave de acesso via SEFAZ (consChNFe)"""
    from services.nfe_distribuicao import NFeDistribuicaoService
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.cnpj:
        request.session["error"] = "Empresa sem CNPJ configurado"
        return RedirectResponse(url="/nfe", status_code=303)
    try:
        service = NFeDistribuicaoService(empresa, db=db)
        resultado = service.consultar_por_chave(empresa.cnpj, chave_acesso)
        request.session["message"] = f"NFe {resultado.get('numero', '')} importada com sucesso!"
    except Exception as e:
        request.session["error"] = f"Erro ao importar: {str(e)}"
    return RedirectResponse(url="/nfe/recebidas", status_code=303)


def _resolver_cliente_nfe(db, doc, nome):
    """Vincula um Cliente à NFe pelo CNPJ/CPF, com fallback pelo nome
    (caso o cadastro tenha divergência ou o cliente ainda não exista)."""
    doc_clean = re.sub(r'\D', '', doc or '')
    if doc_clean:
        cliente = db.query(Cliente).filter(
            Cliente.cpf_cnpj != None, Cliente.cpf_cnpj.like(f"%{doc_clean}%")
        ).first()
        if cliente:
            return cliente
    if nome:
        nome_l = re.sub(r'\s+', ' ', str(nome).strip().lower())
        for c in db.query(Cliente).filter(Cliente.nome != None).all():
            if re.sub(r'\s+', ' ', (c.nome or '').strip().lower()) == nome_l:
                return c
    return None


@router.post("/importar-xml")
def importar_nfe_xml(
    request: Request, db: Session = Depends(get_db),
    xml_file: UploadFile = File(...),
):
    """Importa uma NFe a partir do upload de um arquivo XML"""
    from services.nfe_distribuicao import NFeDistribuicaoService
    empresa = db.query(Empresa).first()
    if not empresa:
        request.session["error"] = "Empresa não configurada"
        return RedirectResponse(url="/nfe", status_code=303)
    try:
        xml_str = xml_file.file.read().decode('utf-8')

        # Evento de cancelamento (procEventoNFe): marca a NFe existente como cancelada
        if 'procEvento' in xml_str or 'infEvento' in xml_str:
            m_chave = re.search(r'<chNFe>(\d+)</chNFe>', xml_str)
            m_tipo = re.search(r'<tpEvento>(\d+)</tpEvento>', xml_str)
            if m_chave and m_tipo and m_tipo.group(1) == '110111':
                chave_cancel = m_chave.group(1)
                nf_cancel = db.query(NFe).filter(NFe.chave_acesso == chave_cancel).first()
                if nf_cancel:
                    nf_cancel.status = 'cancelado'
                    if not nf_cancel.origem:
                        nf_cancel.origem = 'importada'
                    db.commit()
                    request.session["message"] = f"NFe {nf_cancel.numero} marcada como cancelada (evento de cancelamento importado)"
                else:
                    request.session["error"] = f"NFe com chave {chave_cancel} não encontrada para cancelar"
                return RedirectResponse(url="/nfe", status_code=303)

        service = NFeDistribuicaoService(empresa, db=db)
        resultado = service.importar_xml(xml_str)

        # Salva/atualiza na tabela principal 'nfe' para aparecer na listagem
        chave = resultado.get('chaveAcesso')
        dest_nome = resultado.get('destinatario_nome')
        dest_doc = resultado.get('destinatario_cnpj')
        nf = db.query(NFe).filter(NFe.chave_acesso == chave).first() if chave else None
        if nf:
            if not nf.xml_text:
                nf.xml_text = xml_str
            if nf.status not in ('issued', 'cancelled'):
                nf.status = 'issued'
            if not nf.destinatario_nome:
                nf.destinatario_nome = dest_nome
            if not nf.destinatario_cpf_cnpj:
                nf.destinatario_cpf_cnpj = dest_doc
            nf.origem = 'importada'
            if not nf.cliente_id:
                cli = _resolver_cliente_nfe(db, dest_doc, dest_nome)
                if cli:
                    nf.cliente_id = cli.id
        else:
            nf = NFe()
            nf.chave_acesso = chave
            try:
                nf.numero = int(re.sub(r'\D', '', str(resultado.get('numero') or '')) or 0)
            except (ValueError, TypeError):
                nf.numero = 0
            nf.serie = 1
            nf.status = 'issued'
            nf.origem = 'importada'
            nf.valor_total = float(resultado.get('valor') or 0)
            nf.xml_text = xml_str
            nf.destinatario_nome = dest_nome
            nf.destinatario_cpf_cnpj = dest_doc
            dh = resultado.get('dhEmi')
            if dh:
                for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                    try:
                        nf.data_emissao = datetime.strptime(str(dh)[:19], fmt)
                        break
                    except Exception:
                        continue
            cli = _resolver_cliente_nfe(db, dest_doc, dest_nome)
            if cli:
                nf.cliente_id = cli.id
            db.add(nf)
        db.commit()
        request.session["message"] = f"NFe {resultado.get('numero', '')} importada do XML com sucesso!"
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro ao importar XML: {str(e)}"
    return RedirectResponse(url="/nfe", status_code=303)


@router.get("/dist-xml/{chave_acesso}")
def nfe_dist_xml(request: Request, chave_acesso: str, db: Session = Depends(get_db)):
    from fastapi.responses import Response
    from models import NFeDistribuida
    n = db.query(NFeDistribuida).filter(NFeDistribuida.chave_acesso == chave_acesso).first()
    if n and n.xml:
        return Response(content=n.xml, media_type="application/xml",
                        headers={"Content-Disposition": f"attachment; filename=nfe_{chave_acesso}.xml"})
    return Response(status_code=404, content="XML não encontrado")


@router.get("/dist-pdf/{chave_acesso}")
def nfe_dist_pdf(request: Request, chave_acesso: str, db: Session = Depends(get_db)):
    from models import NFeDistribuida
    import tempfile
    n = db.query(NFeDistribuida).filter(NFeDistribuida.chave_acesso == chave_acesso).first()
    if not n or not n.xml:
        raise HTTPException(status_code=404, detail="XML não encontrado")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
    try:
        from brazilfiscalreport.danfe import Danfe, DanfeConfig
        config = DanfeConfig(logo=None)
        danfe = Danfe(xml=n.xml, config=config)
        danfe.output(tmp.name)
        with open(tmp.name, 'rb') as f:
            pdf_bytes = f.read()
        return Response(content=pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": f"inline; filename=nfe_{n.numero or chave_acesso[:8]}.pdf"})
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
    return Response(status_code=500, content="Erro ao gerar PDF")


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
    if nfe.status in ("issued", "cancelled"):
        request.session["error"] = "NFe já foi transmitida"
        return RedirectResponse(url=f"/nfe/{nfe_id}", status_code=303)

    erros = _validar_rascunho(nfe, nfe.cliente or (nfe.pedido.cliente if nfe.pedido else None), empresa)
    return request.app.state.templates.TemplateResponse(request, 
        "nfe/previa.html",
        {"request": request, "nfe": nfe, "empresa": empresa,
         "erros": erros, "STATUS_LABELS": STATUS_LABELS,
         "messages": _get_messages(request)}
    )


@router.get("/{nfe_id}/editar")
def editar_nfe_form(request: Request, nfe_id: int, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.notaas_api_key:
        request.session["error"] = "API Key NotaAs não configurada"
        return RedirectResponse(url="/nfe/config", status_code=303)
    nfe = db.query(NFe).options(
        joinedload(NFe.itens), joinedload(NFe.cliente)
    ).filter(NFe.id == nfe_id).first()
    if not nfe:
        request.session["error"] = "NFe não encontrada"
        return RedirectResponse(url="/nfe", status_code=303)
    if nfe.status in ("issued", "cancelled"):
        request.session["error"] = "NFe já foi transmitida"
        return RedirectResponse(url=f"/nfe/{nfe_id}", status_code=303)

    itens_json = json.dumps([{
        "produto_id": i.produto_id,
        "descricao": i.descricao,
        "ncm": i.ncm,
        "unidade": i.unidade,
        "quantidade": float(i.quantidade) if i.quantidade else 0,
        "preco_unitario": float(i.preco_unitario) if i.preco_unitario else 0,
    } for i in nfe.itens])

    total_bruto = float(sum(i.preco_unitario * i.quantidade for i in nfe.itens))
    desconto = round(max(total_bruto - float(nfe.valor_total or 0), 0), 2)

    request.session["nfe_avulsa_cliente_id"] = nfe.cliente_id
    request.session["nfe_avulsa_cfop"] = nfe.cfop
    request.session["nfe_avulsa_natureza"] = nfe.natureza_operacao
    request.session["nfe_avulsa_itens"] = itens_json
    request.session["nfe_avulsa_desconto"] = desconto
    request.session["nfe_avulsa_data_emissao"] = nfe.data_emissao.strftime("%Y-%m-%d") if nfe.data_emissao else ""
    request.session["nfe_avulsa_hora_emissao"] = nfe.data_emissao.strftime("%H:%M") if nfe.data_emissao else ""
    request.session["nfe_avulsa_data_saida"] = nfe.data_saida.strftime("%Y-%m-%d") if nfe.data_saida else ""
    request.session["nfe_avulsa_hora_saida"] = nfe.data_saida.strftime("%H:%M") if nfe.data_saida else ""
    request.session["nfe_avulsa_finalidade"] = nfe.finalidade
    request.session["nfe_avulsa_indicador_presenca"] = nfe.indicador_presenca
    return RedirectResponse(url=f"/nfe/emitir/avulsa?editar={nfe.id}", status_code=303)


@router.post("/{nfe_id}/editar")
def editar_nfe_submit(
    request: Request, nfe_id: int, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    natureza_operacao: str = Form("Venda de mercadoria"),
    cfop: str = Form(None),
    itens_json: str = Form(...),
    desconto: float = Form(0.0),
    data_emissao: str = Form(""),
    hora_emissao: str = Form(""),
    data_saida: str = Form(""),
    hora_saida: str = Form(""),
    finalidade: str = Form("normal"),
    indicador_presenca: int = Form(1),
):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.notaas_api_key:
        request.session["error"] = "API Key NotaAs não configurada"
        return RedirectResponse(url="/nfe/config", status_code=303)

    nfe = db.query(NFe).options(joinedload(NFe.itens)).filter(NFe.id == nfe_id).first()
    if not nfe or nfe.status != "rascunho":
        request.session["error"] = "NFe não encontrada ou já transmitida"
        return RedirectResponse(url="/nfe", status_code=303)

    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        request.session["error"] = "Cliente não encontrado"
        return RedirectResponse(url=f"/nfe/{nfe_id}/editar", status_code=303)

    try:
        itens_data = json.loads(itens_json)
    except json.JSONDecodeError:
        request.session["error"] = "JSON de itens inválido"
        return RedirectResponse(url=f"/nfe/{nfe_id}/editar", status_code=303)

    if not itens_data:
        request.session["error"] = "Adicione pelo menos um item"
        return RedirectResponse(url=f"/nfe/{nfe_id}/editar", status_code=303)

    ie = _limpar_doc(cliente.inscricao_estadual) if hasattr(cliente, 'inscricao_estadual') else None
    if not ie and not cliente.isento_ie and cliente.indicador_ie != "nao_contribuinte":
        request.session["nfe_avulsa_editar_id"] = nfe_id
        request.session["nfe_avulsa_cliente_id"] = cliente_id
        request.session["nfe_avulsa_cfop"] = cfop
        request.session["nfe_avulsa_natureza"] = natureza_operacao
        request.session["nfe_avulsa_itens"] = itens_json
        request.session["nfe_avulsa_desconto"] = desconto
        request.session["nfe_avulsa_erro_ie"] = str(cliente.id)
        request.session["nfe_avulsa_data_emissao"] = data_emissao
        request.session["nfe_avulsa_hora_emissao"] = hora_emissao
        request.session["nfe_avulsa_data_saida"] = data_saida
        request.session["nfe_avulsa_hora_saida"] = hora_saida
        request.session["nfe_avulsa_finalidade"] = finalidade
        request.session["nfe_avulsa_indicador_presenca"] = indicador_presenca
        return RedirectResponse(url=f"/nfe/emitir/avulsa?editar={nfe_id}", status_code=303)

    itens_nfe = []
    for item in itens_data:
        prod_id = item.get("produto_id")
        origem = 0
        if prod_id:
            prod = db.query(Produto).filter(Produto.id == prod_id).first()
            if prod:
                origem = prod.origem or 0
        itens_nfe.append({
            "produto_id": prod_id,
            "descricao": item.get("descricao", ""),
            "ncm": item.get("ncm") or "99999999",
            "unidade": item.get("unidade", "UN"),
            "quantidade": Decimal(str(item.get("quantidade", 1))),
            "preco_unitario": Decimal(str(item.get("preco_unitario", 0))),
            "origem": origem,
        })

    if desconto > 0:
        total_bruto = sum(i["preco_unitario"] * i["quantidade"] for i in itens_nfe)
        if total_bruto > 0:
            fator = Decimal("1") - (Decimal(str(desconto)) / total_bruto)
            for item in itens_nfe:
                item["preco_unitario"] = round(item["preco_unitario"] * fator, 2)

    try:
        total = sum(i["preco_unitario"] * i["quantidade"] for i in itens_nfe)

        for old_item in nfe.itens:
            if old_item.produto_id:
                prod_old = db.query(Produto).filter(Produto.id == old_item.produto_id).first()
                if prod_old:
                    prod_old.estoque = (prod_old.estoque or 0) + float(old_item.quantidade)
            db.delete(old_item)

        nfe.cliente_id = cliente.id
        nfe.natureza_operacao = natureza_operacao
        nfe.cfop = cfop or empresa.cfop_padrao
        nfe.valor_total = total
        nfe.data_emissao = _parse_nfe_datetime(data_emissao, hora_emissao)
        nfe.data_saida = _parse_nfe_datetime(data_saida, hora_saida)
        nfe.finalidade = finalidade
        nfe.indicador_presenca = indicador_presenca
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
            if item.get("produto_id"):
                prod = db.query(Produto).filter(Produto.id == item["produto_id"]).first()
                if prod:
                    prod.estoque = (prod.estoque or 0) - float(item["quantidade"])

        db.commit()
        request.session["message"] = f"Rascunho NFe #{nfe.numero} atualizado!"
        return RedirectResponse(url=f"/nfe/{nfe.id}/previa", status_code=303)
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro ao atualizar rascunho: {str(e)}"
        return RedirectResponse(url=f"/nfe/{nfe_id}/editar", status_code=303)


@router.post("/{nfe_id}/gerar-cobranca")
def gerar_cobranca_nfe(request: Request, nfe_id: int, db: Session = Depends(get_db)):
    nfe = db.query(NFe).options(
        joinedload(NFe.pedido),
    ).filter(NFe.id == nfe_id).first()
    if not nfe:
        request.session["error"] = "NFe não encontrada"
        return RedirectResponse(url="/nfe", status_code=303)

    cobranca_existente = db.query(ContaReceber).filter(
        ContaReceber.observacao.like(f"%NFe #{nfe.id}%")
    ).first()
    if cobranca_existente:
        request.session["error"] = "Cobrança já existe para esta NFe"
        return RedirectResponse(url=f"/nfe/{nfe_id}", status_code=303)

    cliente_id = nfe.cliente_id
    if not cliente_id and nfe.pedido:
        cliente_id = nfe.pedido.cliente_id
    if not cliente_id:
        request.session["error"] = "Não foi possível identificar o cliente para gerar cobrança"
        return RedirectResponse(url=f"/nfe/{nfe_id}", status_code=303)

    cobranca = ContaReceber(
        cliente_id=cliente_id,
        descricao=f"NFe #{nfe.numero}",
        valor=nfe.valor_total or 0,
        data_vencimento=nfe.data_emissao.date() if nfe.data_emissao else date.today(),
        forma_pagamento="NFe",
        observacao=f"Gerado manualmente da NFe #{nfe.id}",
    )
    db.add(cobranca)
    db.commit()
    request.session["message"] = f"Cobrança gerada com sucesso para NFe #{nfe.numero}!"
    return RedirectResponse(url=f"/nfe/{nfe_id}", status_code=303)


@router.post("/{nfe_id}/excluir")
def excluir_nfe(request: Request, nfe_id: int, db: Session = Depends(get_db)):
    nfe = db.query(NFe).options(joinedload(NFe.itens)).filter(NFe.id == nfe_id).first()
    if not nfe:
        request.session["error"] = "NFe não encontrada"
        return RedirectResponse(url="/nfe", status_code=303)
    if nfe.status in ("issued", "cancelled"):
        request.session["error"] = "NFe já foi transmitida"
        return RedirectResponse(url=f"/nfe/{nfe_id}", status_code=303)

    for item in nfe.itens:
        if item.produto_id:
            prod = db.query(Produto).filter(Produto.id == item.produto_id).first()
            if prod:
                prod.estoque = (prod.estoque or 0) + float(item.quantidade)

    db.delete(nfe)
    db.commit()
    request.session["message"] = "NFe excluída com sucesso!"
    return RedirectResponse(url="/nfe", status_code=303)


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
    if nfe.status in ("issued", "cancelled"):
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
        "origem": getattr(db.query(Produto).filter(Produto.id == i.produto_id).first(), 'origem', 0) if i.produto_id else 0,
    } for i in nfe.itens]

    try:
        data_emissao_str = nfe.data_emissao.strftime("%Y-%m-%dT%H:%M:%S") if nfe.data_emissao else None
        data_saida_str = nfe.data_saida.strftime("%Y-%m-%dT%H:%M:%S") if nfe.data_saida else None

        payload = montar_payload_nfe(
            empresa, cliente, itens_nfe,
            numero_nfe=nfe.numero,
            serie=nfe.serie or empresa.serie_nfe or 1,
            natureza_operacao=nfe.natureza_operacao,
            cfop=nfe.cfop,
            finalidade=nfe.finalidade if hasattr(nfe, 'finalidade') else "normal",
            indicador_presenca=nfe.indicador_presenca if hasattr(nfe, 'indicador_presenca') else 1,
            data_emissao=data_emissao_str,
            data_saida=data_saida_str,
        )

        result = emitir_nfe(empresa, payload)
        invoice_id = result.get("invoiceId")

        nfe.invoice_id = invoice_id

        # Tenta capturar o motivo exato (cStat/xMotivo/protocolo) caso a
        # NotaAS/SEFAZ já devolva a rejeição de imediato. Não bloqueia a
        # emissão: se a consulta falhar ou ainda estiver processando, segue
        # como 'queued' e o webhook/Consultar Status atualiza depois.
        try:
            if invoice_id:
                status_data = consultar_status(empresa, invoice_id)
                erro_nfe = _extrair_erro_nfe(status_data)
                if erro_nfe and (status_data.get("status") in (None, "error", "cancelled")
                                  or status_data.get("cStat") not in (None, "100")):
                    nfe.status = "error"
                    nfe.mensagem_retorno = erro_nfe
                    nfe.protocolo = status_data.get("nProt") or nfe.protocolo
                    db.commit()
                    request.session["error"] = f"Erro ao transmitir NFe #{nfe.numero}: {erro_nfe}"
                    return RedirectResponse(url=f"/nfe/{nfe.id}", status_code=303)
        except Exception as e:
            logger.warning(f"Falha ao consultar status imediato da NFe: {e}")

        nfe.status = "queued"
        db.commit()

        # Baixa de estoque: venda de mercadoria (SAIDA_VENDA). Idempotente:
        # se o webhook issued chamar de novo, nao duplica.
        try:
            from services.estoque_service import baixar_nfe
            baixar_nfe(db, nfe)
        except Exception as e:
            logger.warning(f"Erro ao baixar estoque NFe: {e}")

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
        except Exception as e:
            logger.warning(f"Falha ao consultar status da NFe #{nfe_id}: {e}")
    cobranca = db.query(ContaReceber).filter(
        ContaReceber.observacao.like(f"%NFe #{nfe.id}%")
    ).first()

    return request.app.state.templates.TemplateResponse(request, 
        "nfe/detalhe.html",
        {"request": request, "nfe": nfe, "empresa": empresa,
         "STATUS_LABELS": STATUS_LABELS,
         "messages": _get_messages(request),
         "cobranca": cobranca}
    )


@router.get("/{nfe_id}/pdf")
def baixar_pdf_nfe(request: Request, nfe_id: int, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    nfe = db.query(NFe).filter(NFe.id == nfe_id).first()
    if not nfe or not (nfe.xml_text or nfe.invoice_id):
        raise HTTPException(status_code=404, detail="NFe não encontrada")

    def _local_xml_path():
        if nfe.xml_path and os.path.exists(f".{nfe.xml_path}"):
            return f".{nfe.xml_path}"
        busca = f"static/uploads/nfe/nfe_{nfe.numero}.xml"
        if os.path.exists(busca):
            return busca
        for fname in os.listdir("static/uploads/nfe"):
            if fname.endswith(".xml"):
                fpath = f"static/uploads/nfe/{fname}"
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        if nfe.invoice_id in f.read(500):
                            return fpath
                except Exception as e:
                    logger.warning(f"Erro ao ler XML NFe: {e}")
        return None

    def _gerar_danfe(xml_text):
        pdf_filename = f"danfe_{nfe.numero}.pdf"
        pdf_path = f"static/uploads/nfe/{pdf_filename}"
        os.makedirs("static/uploads/nfe", exist_ok=True)
        from services.nfe_danfe import gerar_danfe_pdf
        gerar_danfe_pdf(xml_text, pdf_path)
        nfe.pdf_path = f"/{pdf_path.replace(os.sep, '/')}"
        db.commit()
        return Response(content=open(pdf_path, 'rb').read(), media_type="application/pdf",
                        headers={"Content-Disposition": f"inline; filename={pdf_filename}"})

    # 0) Tenta XML persistido no banco (sobrevive a redeploys no Railway)
    if nfe.xml_text:
        try:
            return _gerar_danfe(nfe.xml_text)
        except Exception as e:
            logger.warning(f"Erro DANFE XML banco: {e}")

    # 1) Tenta XML local
    xml_path = _local_xml_path()
    if xml_path:
        try:
            with open(xml_path, 'r', encoding='utf-8') as f:
                xml_text = f.read()
                nfe.xml_text = xml_text
                db.commit()
                return _gerar_danfe(xml_text)
        except Exception as e:
            logger.warning(f"Erro DANFE XML local: {e}")

    # 2) Tenta baixar XML do NotaAs
    try:
        xml_text = baixar_xml(empresa, nfe.invoice_id)
        xml_path = f"static/uploads/nfe/nfe_{nfe.numero}.xml"
        os.makedirs("static/uploads/nfe", exist_ok=True)
        with open(xml_path, 'w', encoding='utf-8') as f:
            f.write(xml_text)
        nfe.xml_path = f"/{xml_path.replace(os.sep, '/')}"
        nfe.xml_text = xml_text
        db.commit()
        return _gerar_danfe(xml_text)
    except Exception as e:
        logger.warning(f"Erro DANFE NotaAs XML: {e}")

    # 3) Último fallback: PDF do NotaAs
    try:
        pdf_bytes = baixar_pdf(empresa, nfe.invoice_id)
        return Response(content=pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": f"inline; filename=nfe_{nfe.numero}.pdf"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter DANFE: {str(e)}")


@router.get("/{nfe_id}/xml")
def baixar_xml_nfe(request: Request, nfe_id: int, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    nfe = db.query(NFe).filter(NFe.id == nfe_id).first()
    if not nfe or not (nfe.xml_text or nfe.invoice_id):
        raise HTTPException(status_code=404, detail="NFe não encontrada")
    if nfe.xml_text:
        return Response(content=nfe.xml_text, media_type="application/xml",
                        headers={"Content-Disposition": f"attachment; filename=nfe_{nfe.numero}.xml"})
    try:
        xml_text = baixar_xml(empresa, nfe.invoice_id)
        nfe.xml_text = xml_text
        db.commit()
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
    redirect: bool = Query(False),
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
                    nfe.mensagem_retorno = None
                    _salvar_xml_nfe(empresa, nfe, db)
                else:
                    erro = _extrair_erro_nfe(data)
                    if erro:
                        nfe.mensagem_retorno = erro
                db.commit()
        if redirect and nfe:
            request.session["message"] = f"Status atualizado: {STATUS_LABELS.get(nfe.status, nfe.status)}"
            return RedirectResponse(url=f"/nfe/{nfe.id}", status_code=303)
        return JSONResponse(data)
    except Exception as e:
        if redirect and nfe:
            request.session["error"] = f"Erro ao consultar status: {str(e)}"
            return RedirectResponse(url=f"/nfe/{nfe.id}", status_code=303)
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
        "unidade": p.unidade or "UN", "preco": float(p.preco or 0),
        "estoque": p.estoque or 0, "codigo": p.codigo or "",
    } for p in results]})


@router.post("/webhook")
async def webhook_nfe(request: Request, db: Session = Depends(get_db)):
    webhook_secret = os.environ.get("WEBHOOK_NFE_SECRET", "")
    if not webhook_secret:
        logger.warning("WEBHOOK_NFE_SECRET não configurado - webhook bloqueado")
        return JSONResponse({"status": "error", "message": "Forbidden"}, status_code=403)
    provided = request.headers.get("X-Webhook-Secret") or request.query_params.get("secret") or ""
    if not secrets.compare_digest(provided, webhook_secret):
        return JSONResponse({"status": "error", "message": "Forbidden"}, status_code=403)
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
            empresa = db.query(Empresa).first()
            if empresa:
                _salvar_xml_nfe(empresa, nfe, db)
            # Dispara e-mail pos-autorizacao para o cliente da NFe (DANFE pronto)
            try:
                from services.email_service import enviar_documentos_cliente
                if empresa and getattr(empresa, "email_auto_enviar", False) and nfe.cliente:
                    enviar_documentos_cliente(db, nfe.cliente, nfes=[nfe], incluir_xml=False)
            except Exception as e:
                logger.warning(f"Erro ao disparar e-mail pos-autorizacao NFe: {e}")
            # Baixa de estoque: venda de mercadoria (SAIDA_VENDA)
            try:
                from services.estoque_service import baixar_nfe
                baixar_nfe(db, nfe)
            except Exception as e:
                logger.warning(f"Erro ao baixar estoque NFe: {e}")
        elif event in ("nfe.error", "nfce.error"):
            nfe.status = "error"
            nfe.mensagem_retorno = _extrair_erro_nfe(data) or json.dumps(data, ensure_ascii=False)
        elif event in ("nfe.cancelled", "nfce.cancelled"):
            nfe.status = "cancelled"
            nfe.mensagem_retorno = json.dumps(data)
        
        db.commit()
        return JSONResponse({"status": "updated", "invoice_id": invoice_id})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)



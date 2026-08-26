import os
import json
import re
import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date
from fastapi import APIRouter, Depends, Request, Form, Query, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse, JSONResponse, Response, FileResponse
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import desc, asc, or_
from database import get_db
from models import Cliente, Empresa, PedidoVenda, PedidoVendaItem, PedidoConsolidado, PedidoConsolidadoItem, Produto, ProdutoVariacao, ProdutoComposicao, ContaReceber, ContaPagar, StatusConta, StatusPedido, OrdemServico, Assinatura, Fornecedor
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

from app.core.config import settings
UPLOAD_DIR = os.path.join(settings.UPLOAD_DIR, "nfse")
os.makedirs(UPLOAD_DIR, exist_ok=True)
XML_DIR = os.path.join(settings.UPLOAD_DIR, "nfs")
os.makedirs(XML_DIR, exist_ok=True)


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


def _servico_contem_variacao(produto: Produto) -> bool:
    """True se o serviço possui variações próprias ou algum insumo (composição)
    com variação. Esses serviços não podem ser emitidos em NFS-e avulsa."""
    if getattr(produto, "variacoes", None):
        return True
    for comp in getattr(produto, "composicoes", []) or []:
        insumo = getattr(comp, "insumo", None)
        if insumo and getattr(insumo, "variacoes", None):
            return True
    return False


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
            NFSe.numero.ilike(f"%{busca}%") |
            NFSe.cliente.has(Cliente.nome.ilike(f"%{busca}%")) |
            NFSe.cliente.has(Cliente.fantasia.ilike(f"%{busca}%")) |
            NFSe.cliente.has(Cliente.cpf_cnpj.ilike(f"%{busca}%"))
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
    vincular: int = Query(0),
):
    """Lista as NFSe recebidas (somos o tomador), filtrÃ¡veis por perÃ­odo e nÂº, com ordenaÃ§Ã£o e paginaÃ§Ã£o."""
    # Auto-vinculo apos cadastrar o fornecedor a partir da NFSe (fluxo do cadastro)
    if vincular:
        rec_vinc = db.query(NFSeRecebida).filter(NFSeRecebida.id == vincular).first()
        if rec_vinc:
            fornecedor = _resolver_fornecedor_nfse_recebida(db, rec_vinc)
            if fornecedor:
                rec_vinc.fornecedor_id = fornecedor.id
                db.commit()
                request.session["message"] = f"Fornecedor '{fornecedor.nome}' cadastrado e vinculado à NFSe {rec_vinc.numero}."
        return RedirectResponse(url="/nfse/recebidas", status_code=303)
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
        like = f"%{busca}%"
        q = q.filter(
            or_(
                NFSeRecebida.numero.ilike(like),
                NFSeRecebida.emitente_nome.ilike(like),
                NFSeRecebida.emitente_cnpj.ilike(like),
            )
        )
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


def _normalizar_doc(doc):
    """Remove caracteres nao alfanumericos (pontos, barra, traco, espacos)."""
    return re.sub(r'[^A-Za-z0-9]', '', doc or '').upper()


def _resolver_fornecedor_nfse_recebida(db, rec, criar=False):
    """Busca o Fornecedor (prestador) da NFSe recebida pelo CNPJ do emitente.

    Reaproveita o vinculo ja existente em `rec.fornecedor_id` quando presente.
    A busca por CNPJ e feita com a versao NORMALIZADA (apenas digitos), pois o
    CNPJ do emitente (vindo da NFSe/ADN) pode estar em formato diferente do
    cadastrado (com/sem pontuacao), o que antes impedia o match e forçava um
    cadastro duplicado. Por padrao apenas BUSCA (nao cria). Retorna o Fornecedor ou None.
    """
    if rec.fornecedor_id:
        return db.query(Fornecedor).filter(Fornecedor.id == rec.fornecedor_id).first()
    empresa = db.query(Empresa).first()
    cnpj_empresa = _normalizar_doc(empresa.cnpj) if empresa and empresa.cnpj else ''
    cnpj_emit = _normalizar_doc(rec.emitente_cnpj)
    if not cnpj_emit or cnpj_emit == cnpj_empresa:
        return None
    for f in db.query(Fornecedor).filter(Fornecedor.cpf_cnpj.isnot(None), Fornecedor.cpf_cnpj != "").all():
        if _normalizar_doc(f.cpf_cnpj) == cnpj_emit:
            return f
    fornecedor = None
    if criar:
        fornecedor = Fornecedor(nome=rec.emitente_nome or 'Fornecedor', cpf_cnpj=rec.emitente_cnpj)
        db.add(fornecedor)
        db.flush()
    return fornecedor


def _emitente_valido_nfse_recebida(db, rec) -> bool:
    """True quando o emitente tem CNPJ valido e nao e a propria empresa."""
    empresa = db.query(Empresa).first()
    cnpj_empresa = re.sub(r'\D', '', empresa.cnpj) if empresa and empresa.cnpj else ''
    cnpj_emit = re.sub(r'\D', '', rec.emitente_cnpj or '')
    return bool(cnpj_emit) and cnpj_emit != cnpj_empresa


def _url_cadastro_fornecedor_nfse(rec) -> str:
    """Monta a URL do cadastro de fornecedor pre-preenchido com dados da NFSe."""
    from urllib.parse import urlencode
    dados = {"nome": rec.emitente_nome or "", "cpf_cnpj": rec.emitente_cnpj or ""}
    if dados["cpf_cnpj"]:
        dados["tipo_pessoa"] = "juridica" if len(re.sub(r'\D', '', dados["cpf_cnpj"])) > 11 else "fisica"
    params = {k: v for k, v in dados.items() if v}
    params["next"] = f"/nfse/recebidas?vincular={rec.id}"
    return "/fornecedores/novo?" + urlencode(params)


@router.post("/recebidas/{recebida_id}/vincular-fornecedor")
def vincular_fornecedor_nfse_recebida(request: Request, recebida_id: int, db: Session = Depends(get_db)):
    rec = db.query(NFSeRecebida).filter(NFSeRecebida.id == recebida_id).first()
    if not rec:
        request.session["error"] = "NFSe recebida não encontrada"
        return RedirectResponse(url="/nfse/recebidas", status_code=303)
    fornecedor = _resolver_fornecedor_nfse_recebida(db, rec)
    if not fornecedor:
        if _emitente_valido_nfse_recebida(db, rec):
            request.session["message"] = {
                "tipo": "warning",
                "texto": f"Fornecedor '{rec.emitente_nome or ''}' ainda não cadastrado. Preencha e salve o cadastro para vincular à NFSe.",
            }
            return RedirectResponse(url=_url_cadastro_fornecedor_nfse(rec), status_code=303)
        request.session["error"] = "Não foi possível vincular fornecedor (emitente sem CNPJ ou é a própria empresa)."
        return RedirectResponse(url="/nfse/recebidas", status_code=303)
    rec.fornecedor_id = fornecedor.id
    db.commit()
    request.session["message"] = f"Fornecedor '{fornecedor.nome}' vinculado à NFSe."
    return RedirectResponse(url="/nfse/recebidas", status_code=303)


@router.get("/recebidas/{recebida_id}/parcelas-info")
def parcelas_info_nfse_recebida(request: Request, recebida_id: int, db: Session = Depends(get_db)):
    """Retorna dados para o popup de geracao de conta a pagar da NFSe recebida.

    Diferente da NFe, a NFSe nao traz duplicatas/vencimento no padrao, entao o
    vencimento e informado manualmente pelo usuario no popup.
    """
    from services.nfse_service import extrair_fatura_nfse

    rec = db.query(NFSeRecebida).filter(NFSeRecebida.id == recebida_id).first()
    if not rec:
        return JSONResponse({"error": "NFSe recebida não encontrada"}, status_code=404)

    fornecedor = _resolver_fornecedor_nfse_recebida(db, rec)
    descricao = f"NFSe {rec.numero} - {rec.emitente_nome or ''}".strip()
    ja_existe = False
    if fornecedor:
        ja_existe = db.query(ContaPagar).filter(
            ContaPagar.descricao.like(descricao + "%"),
            ContaPagar.fornecedor_id == fornecedor.id,
            ContaPagar.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO]),
        ).first() is not None

    emissao = rec.data_emissao.date() if rec.data_emissao else date.today()

    faturas = extrair_fatura_nfse(rec.xml_text) if rec.xml_text else []
    fat_json = [
        {
            "numero": f["numero"],
            "vencimento": f["vencimento"].isoformat() if f["vencimento"] else "",
            "valor": float(f["valor"]) if f["valor"] else None,
        }
        for f in faturas
    ]

    return JSONResponse({
        "recebida_id": rec.id,
        "numero": rec.numero,
        "emitente": rec.emitente_nome or "",
        "valor_total": float(rec.valor_total or 0),
        "emissao": emissao.isoformat(),
        "fornecedor_cadastrado": fornecedor is not None,
        "fornecedor_vinculado": fornecedor is not None,
        "fornecedor_nome": fornecedor.nome if fornecedor else "",
        "cadastro_url": None if fornecedor else (
            _url_cadastro_fornecedor_nfse(rec) if _emitente_valido_nfse_recebida(db, rec) else None
        ),
        "emitente_invalido": fornecedor is None and not _emitente_valido_nfse_recebida(db, rec),
        "ja_existe_conta": ja_existe,
        "tem_duplicatas": len(fat_json) > 0,
        "duplicatas": fat_json,
    })


@router.post("/recebidas/{recebida_id}/gerar-conta")
def gerar_conta_nfse_recebida(
    request: Request,
    recebida_id: int,
    db: Session = Depends(get_db),
    parcela_numero: list[str] = Form(default=None),
    parcela_vencimento: list[str] = Form(default=None),
    parcela_valor: list[str] = Form(default=None),
    confirmar: str = Form(default=None),
):
    from services.parcelamento import gerar_contas_pagar_parcelas

    rec = db.query(NFSeRecebida).filter(NFSeRecebida.id == recebida_id).first()
    if not rec:
        request.session["error"] = "NFSe recebida não encontrada"
        return RedirectResponse(url="/nfse/recebidas", status_code=303)
    fornecedor = _resolver_fornecedor_nfse_recebida(db, rec)
    if not fornecedor:
        if _emitente_valido_nfse_recebida(db, rec):
            request.session["message"] = {
                "tipo": "warning",
                "texto": f"Fornecedor '{rec.emitente_nome or ''}' ainda não cadastrado. Cadastre-o para gerar a conta a pagar.",
            }
            return RedirectResponse(url=_url_cadastro_fornecedor_nfse(rec), status_code=303)
        request.session["error"] = "Não foi possível identificar o fornecedor (emitente sem CNPJ ou é a própria empresa)."
        return RedirectResponse(url="/nfse/recebidas", status_code=303)
    rec.fornecedor_id = fornecedor.id
    descricao = f"NFSe {rec.numero} - {rec.emitente_nome or ''}".strip()
    existente = db.query(ContaPagar).filter(
        ContaPagar.descricao.like(descricao + "%"),
        ContaPagar.fornecedor_id == fornecedor.id,
        ContaPagar.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO]),
    ).first()
    if existente and not confirmar:
        request.session["error"] = "Já existe conta a pagar para esta NFSe."
        db.commit()
        return RedirectResponse(url="/nfse/recebidas", status_code=303)

    emissao = rec.data_emissao.date() if rec.data_emissao else date.today()

    parcelas = []
    if parcela_valor:
        for i, valor_raw in enumerate(parcela_valor):
            valor_raw = (valor_raw or "").strip().replace(",", ".")
            if not valor_raw:
                continue
            try:
                valor = Decimal(valor_raw)
            except Exception:
                request.session["error"] = "Valor de parcela inválido."
                return RedirectResponse(url="/nfse/recebidas", status_code=303)
            venc_raw = (parcela_vencimento[i] if parcela_vencimento and i < len(parcela_vencimento) else "").strip()
            try:
                venc = datetime.strptime(venc_raw[:10], '%Y-%m-%d').date() if venc_raw else emissao
            except Exception:
                venc = emissao
            num_raw = (parcela_numero[i] if parcela_numero and i < len(parcela_numero) else "").strip()
            parcelas.append({"numero": num_raw or (i + 1), "valor": valor, "vencimento": venc})

    if not parcelas:
        parcelas = [{"numero": 1, "valor": Decimal(str(rec.valor_total or 0)), "vencimento": emissao}]

    contas = gerar_contas_pagar_parcelas(
        db,
        fornecedor_id=fornecedor.id,
        descricao=descricao,
        parcelas=parcelas,
        numero_documento=rec.numero,
    )
    db.commit()

    if len(contas) > 1:
        request.session["message"] = f"{len(contas)} parcelas (contas a pagar) criadas com sucesso!"
    else:
        request.session["message"] = "Conta a pagar criada com sucesso!"
    return RedirectResponse(url="/nfse/recebidas", status_code=303)


@router.get("/emitir/avulsa")
def emitir_avulsa_form(request: Request, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).first()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    servicos = db.query(Produto).options(
        selectinload(Produto.variacoes),
        selectinload(Produto.composicoes).selectinload(ProdutoComposicao.insumo).selectinload(Produto.variacoes),
    ).filter(
        Produto.tipo == "servico", Produto.situacao == "A"
    ).order_by(Produto.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "cpf_cnpj": c.cpf_cnpj,
                       "cidade": c.cidade, "estado": c.estado,
                       "iss_retido": bool(getattr(c, 'iss_retido', False))} for c in clientes]
    servicos_json = [{"id": p.id, "nome": p.nome, "preco": float(p.preco or 0),
                      "codigo_lc116": p.codigo_lc116 or "",
                      "codigo_tributacao_municipal": p.codigo_tributacao_municipal or "",
                      "unidade": p.unidade or "UN",
                      "tem_variacao": _servico_contem_variacao(p)} for p in servicos]
    aliquota_iss = float(getattr(empresa, 'aliquota_iss', None) or 2.0)
    return request.app.state.templates.TemplateResponse(request, 
        "nfse/emissao_avulsa.html",
        {"request": request, "empresa": empresa, "clientes": clientes,
         "servicos": servicos, "clientes_json": clientes_json,
         "servicos_json": servicos_json, "aliquota_iss": aliquota_iss,
         "messages": _get_messages(request)}
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
    forma_pagamento: str = Form(""),
    num_parcelas: int = Form(1),
    primeiro_vencimento: str = Form(""),
    intervalo_dias: int = Form(30),
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

    # OpÃ§Ã£o C: NFSe avulsa nÃ£o deve emitir serviÃ§os que contenham itens/insumos com variaÃ§Ã£o.
    produtos_ids = [i.get("produto_id") for i in itens_data if i.get("produto_id")]
    if produtos_ids:
        produtos_checados = db.query(Produto).options(
            selectinload(Produto.variacoes),
            selectinload(Produto.composicoes).selectinload(ProdutoComposicao.insumo).selectinload(Produto.variacoes),
        ).filter(Produto.id.in_(produtos_ids)).all()
        for p in produtos_checados:
            if _servico_contem_variacao(p):
                return JSONResponse({
                    "error": f"O serviÃ§o '{p.nome}' contÃ©m itens/insumos com variaÃ§Ã£o e nÃ£o pode ser emitido em NFS-e avulsa. Utilize um serviÃ§o sem variaÃ§Ã£o."
                }, status_code=400)

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
            variacao_id=item.get("variacao_id"),
            descricao=item.get("descricao", ""),
            quantidade=Decimal(str(item.get("quantidade", 1))),
            valor_unitario=Decimal(str(item.get("valor_unitario", 0))),
            valor_total=Decimal(str(item.get("valor_total", 0))),
            codigo_servico=item.get("codigo_lc116", ""),
            tributacao_municipal=item.get("codigo_tributacao_municipal", ""),
        )
        db.add(nfse_item)

    db.commit()

    # Gera cobranÃ§a automaticamente (se solicitado) â€” com suporte a parcelamento
    if gerar_cobranca:
        try:
            from services.parcelamento import gerar_contas_receber, emitir_boletos_contas
            try:
                venc = datetime.strptime(primeiro_vencimento, '%Y-%m-%d').date() if primeiro_vencimento else (
                    datetime.strptime(data_competencia, '%Y-%m-%d').date() if data_competencia else date.today())
            except ValueError:
                venc = date.today()
            contas_geradas = gerar_contas_receber(
                db,
                cliente_id=cliente_id,
                descricao=f"NFSe Avulsa #{nfse.numero}",
                valor_total=nfse.valor_liquido,
                primeiro_vencimento=venc,
                num_parcelas=num_parcelas,
                intervalo_dias=intervalo_dias,
                forma_pagamento=forma_pagamento or "NFSe",
                numero_documento=str(nfse.numero) if nfse.numero else None,
                observacao=f"Gerado automaticamente da NFSe #{nfse.id}",
                nfse_id=nfse.id,
            )
            db.commit()
            # EmissÃ£o imediata de TODOS os boletos das parcelas (Sicoob)
            if contas_geradas and forma_pagamento == "boleto":
                ok, erros = emitir_boletos_contas(db, contas_geradas)
                if erros:
                    request.session["error"] = f"{ok} boleto(s) emitido(s), com erro(s): " + "; ".join(erros)
        except Exception:
            logger.exception("Erro ao gerar cobranca da NFSe avulsa %s", nfse.id)

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
def emitir_nfse(request: Request, pedido_id: int, db: Session = Depends(get_db),
                num_parcelas: int = Form(1),
                primeiro_vencimento: str = Form(""),
                intervalo_dias: int = Form(30)):
    pedido = db.query(PedidoVenda).options(
        selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.produto)
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido nÃ£o encontrado")

    if pedido.consolidacao_id is not None:
        return JSONResponse(
            {"error": "Este pedido pertence a uma consolidação; fature pela consolidação."},
            status_code=400,
        )

    if pedido.status == StatusPedido.AGRUPADO:
        return JSONResponse(
            {"error": "Este pedido foi agrupado em outro pedido; a nota fiscal deve ser emitida pelo pedido agrupado para evitar duplicidade."},
            status_code=400,
        )

    nfse_existente = db.query(NFSe).filter(NFSe.pedido_id == pedido_id).first()
    if nfse_existente:
        return JSONResponse(
            {"error": "Este pedido já possui uma NFSe emitida. Cancele a nota existente para emitir uma nova."},
            status_code=400,
        )

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
                variacao_id=item.variacao_id,
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
                xml_path = os.path.join(XML_DIR, xml_filename)
                os.makedirs(os.path.dirname(xml_path), exist_ok=True)
                with open(xml_path, 'w', encoding='utf-8') as f:
                    f.write(dps_xml)
                nfse.xml_path = f"/{xml_path.replace(os.sep, '/')}"
                nfse.xml_text = dps_xml
        except Exception:
            pass

        db.commit()

        # NF emitida => pedido FATURADO (amarra o estado fiscal do pedido à
        # nota; antes o status ficava desacoplado da realidade fiscal).
        if pedido.status != StatusPedido.FATURADO:
            pedido.status = StatusPedido.FATURADO
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

        # Gera cobranÃ§a automaticamente â€” com suporte a parcelamento
        try:
            from services.parcelamento import gerar_contas_receber, contas_receber_existentes_para
            # Nao duplica cobranca: se o pedido/consolidacao ja gerou contas
            # (ex.: finalizado com "Gerar Cobranca" ou consolidacao finalizada),
            # apenas vincula a NFSe as contas existentes.
            existentes = contas_receber_existentes_para(db, pedido=pedido)
            if existentes:
                for conta in existentes:
                    if not conta.nfse_id:
                        conta.nfse_id = nfse.id
                logger.info(
                    "Pedido %s ja possui %s conta(s) a receber; NFSe %s apenas vinculada",
                    pedido.id, len(existentes), nfse.id,
                )
            else:
                try:
                    venc = datetime.strptime(primeiro_vencimento, '%Y-%m-%d').date() if primeiro_vencimento else (pedido.data or date.today())
                except ValueError:
                    venc = pedido.data or date.today()
                gerar_contas_receber(
                    db,
                    cliente_id=pedido.cliente_id,
                    descricao=f"NFSe Pedido #{pedido.numero or pedido.id}",
                    valor_total=nfse.valor_liquido,
                    primeiro_vencimento=venc,
                    num_parcelas=num_parcelas,
                    intervalo_dias=intervalo_dias,
                    forma_pagamento="NFSe",
                    numero_documento=str(nfse.numero) if nfse.numero else None,
                    observacao=f"Gerado automaticamente da NFSe #{nfse.id} (Pedido #{pedido.id})",
                    nfse_id=nfse.id,
                    pedido_id=pedido.id,
                    consolidacao_id=pedido.consolidacao_id,
                )
            db.commit()
        except Exception:
            logger.exception("Erro ao gerar cobranca da NFSe %s", nfse.id)

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
            os_id=os_id,
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

        import json
        # Monta os itens de serviço a partir dos serviços reais da OS
        servicos = []
        if os.servicos_executados:
            try:
                servicos = json.loads(os.servicos_executados)
                if not isinstance(servicos, list):
                    servicos = []
            except (json.JSONDecodeError, TypeError):
                servicos = []

        if not servicos:
            # Sem detalhamento: usa o valor total do serviço como item único
            servicos = [{"nome": "Serviço prestado", "qtd": 1, "preco": float(os.valor_servico or 0)}]

        # Validação: Dourados exige um único código LC116 por NFS-e
        codigos_lc116 = set()
        for s in servicos:
            pid = s.get("id")
            prod = db.query(Produto).filter(Produto.id == pid).first() if pid else None
            if prod and prod.codigo_lc116:
                codigos_lc116.add(prod.codigo_lc116)
        if len(codigos_lc116) > 1:
            db.rollback()
            request.session["error"] = (
                f"OS possui serviços com códigos LC116 diferentes: {', '.join(sorted(codigos_lc116))}. "
                f"A prefeitura de Dourados não aceita múltiplos códigos na mesma NFS-e. Separe em OS diferentes."
            )
            return RedirectResponse(url=f"/ordens-servico/{os_id}", status_code=303)

        valor_total = float(sum((float(s.get("qtd", 1) or 1) * float(s.get("preco", 0) or 0)) for s in servicos) or 0)
        if valor_total == 0:
            valor_total = float(os.valor_servico or 0)
        nfse.valor_total = valor_total

        for s in servicos:
            pid = s.get("id")
            prod = db.query(Produto).filter(Produto.id == pid).first() if pid else None
            qtd = float(s.get("qtd", 1) or 1)
            preco = float(s.get("preco", 0) or 0)
            nfse_item = NFSeItem(
                nfse_id=nfse.id,
                produto_id=pid,
                descricao=s.get("nome") or (prod.nome if prod else "Serviço prestado"),
                quantidade=qtd,
                valor_unitario=preco,
                valor_total=qtd * preco,
                codigo_servico=prod.codigo_lc116 if prod else "",
                tributacao_municipal=prod.codigo_tributacao_municipal if prod else "",
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
                variacao_id=item.get("variacao_id"),
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
                variacao_id=item.variacao_id,
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
    servicos_json = [{"id": p.id, "nome": p.nome, "preco": float(p.preco or 0),
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
        data_antiga = nfse.data_emissao
        nova_data = datetime.strptime(data_competencia, '%Y-%m-%d')
        nfse.data_emissao = nova_data.replace(
            hour=data_antiga.hour, minute=data_antiga.minute,
            second=data_antiga.second, microsecond=data_antiga.microsecond
        )

    for old_item in nfse.itens:
        db.delete(old_item)
    db.flush()

    for item in itens_data:
        nfse_item = NFSeItem(
            nfse_id=nfse.id,
            produto_id=item.get("produto_id") or None,
            variacao_id=item.get("variacao_id"),
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
        selectinload(NFSe.assinatura),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")

    cobranca = db.query(ContaReceber).filter(
        ContaReceber.observacao.like(f"%NFSe #{nfse.id}%")
    ).first()

    vencimento_sugerido = None
    if nfse.assinatura_id and nfse.assinatura and not cobranca:
        from routers.assinaturas import proximo_vencimento_para_cobranca
        vencimento_sugerido = proximo_vencimento_para_cobranca(db, nfse.assinatura)

    return request.app.state.templates.TemplateResponse(request, 
        "nfse/detalhe.html",
        {"request": request, "nfse": nfse, "STATUS_LABELS": STATUS_LABELS,
         "cobranca": cobranca, "vencimento_sugerido": vencimento_sugerido}
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


def _salvar_xml_arquivo(nfse: NFSe, xml: str) -> str | None:
    """Salva o XML em arquivo e retorna o path relativo."""
    try:
        xml_filename = f"nfse_{nfse.id}.xml"
        xml_path = os.path.join(XML_DIR, xml_filename)
        os.makedirs(os.path.dirname(xml_path), exist_ok=True)
        with open(xml_path, 'w', encoding='utf-8') as f:
            f.write(xml)
        return f"/{xml_path.replace(os.sep, '/')}"
    except Exception as e:
        logger.warning(f"Erro ao salvar XML: {e}")
        return None


def _salvar_xml_nacional_e_danfse(nfse: NFSe, db: Session, chave: str,
                                  cancelada: bool = False) -> bool:
    """Tenta obter o XML nacional (Ambiente Nacional) pela chave, armazena em
    nfse.xml_text/xml_path e gera o DANFSe padronizado. Retorna True se obteve o
    XML nacional (e o persiste); False caso contrário (ex.: ADN ainda não
    propagou). Não faz commit — o chamador deve commitar."""
    from services.nfse_pdf import gerar_danfse_pdf, is_xml_nfse_nacional
    if not chave:
        return False
    try:
        empresa = db.query(Empresa).first()
        svc = BethaNfseService(empresa=empresa)
        xml = svc.obter_xml_nacional_por_chave(chave, tentativas=3, intervalo=15)
    except Exception as e:
        logger.warning(f"Erro ao buscar XML nacional no ADN: {e}")
        return False
    if not (xml and is_xml_nfse_nacional(xml)):
        return False
    nfse.xml_text = xml
    nfse.xml_path = _salvar_xml_arquivo(nfse, xml)
    try:
        pdf_filename = f"danfse_{nfse.numero or nfse.id}.pdf"
        pdf_path = f"static/uploads/nfse/{pdf_filename}"
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        gerar_danfse_pdf(xml, pdf_path, cancelada=bool(cancelada))
        nfse.pdf_path = f"/{pdf_path.replace(os.sep, '/')}"
    except Exception as e:
        logger.warning(f"Erro ao gerar DANFSe padronizado: {e}")
    return True


def _baixar_pdf_via_url(url: str) -> bytes | None:
    """Baixa um PDF de uma URL externa."""
    import requests as req
    try:
        resp = req.get(url, timeout=30)
        if resp.status_code == 200 and ('pdf' in resp.headers.get('content-type', '').lower()
                                         or resp.content[:4] == b'%PDF'):
            return resp.content
    except Exception as e:
        logger.warning(f"Erro ao baixar PDF de {url}: {e}")
    return None


def _buscar_xml_nacional_adn(nfse: NFSe, db: Session = None):
    """Obtém o XML completo da NFS-e Nacional (infNFSe+DPS) no Ambiente Nacional
    (https://adn.nfse.gov.br), via distribuição DF-e.

    Notas emitidas por prefeituras proprietárias (ex.: Betha) persistem apenas
    a DPS no formato Betha; o XML nacional autorizado (fonte correta do DANFSe
    padronizado) vive no Ambiente Nacional e é obtido pela chave de acesso.
    Não busca nada da Betha nem do SEFIN.
    """
    chave = nfse.chave_acesso or nfse.codigo_verificacao
    if not chave:
        return None
    try:
        from services.nfse_betha import BethaNfseService
        empresa = db.query(Empresa).first() if db is not None else None
        service = BethaNfseService(empresa=empresa)
        xml = service.obter_xml_nacional_por_chave(chave, tentativas=3, intervalo=15)
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
    #    Fonte ÚNICA do XML: Ambiente Nacional (https://adn.nfse.gov.br).
    #    Nunca busca XML nem PDF da Betha.
    xml_nacional = None
    autorizada = (nfse.status or '').lower() in ('autorizada', 'cancelada')

    # 0a. Se o XML ja salvo e nacional, usa direto
    if nfse.xml_text and is_xml_nfse_nacional(nfse.xml_text):
        xml_nacional = nfse.xml_text

    # 0b. Busca o XML nacional na distribuição DF-e do ADN (pela chave de acesso)
    if not xml_nacional and autorizada:
        xml_nacional = _buscar_xml_nacional_adn(nfse, db)
        if xml_nacional:
            nfse.xml_text = xml_nacional
            nfse.xml_path = _salvar_xml_arquivo(nfse, xml_nacional)
            db.commit()

    # 0c. Gera o DANFSe padronizado se temos XML nacional
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
    cached = None
    if nfse.pdf_path and os.path.exists(f".{nfse.pdf_path}"):
        cached = nfse.pdf_path
    else:
        pattern = f"static/uploads/nfse/danfse_{nfse.numero or nfse.id}.pdf"
        if os.path.exists(pattern):
            cached = f"/{pattern.replace(os.sep, '/')}"
    if cached:
        return FileResponse(f".{cached}", media_type="application/pdf",
                            filename=f"nfse_{nfse.numero or nfse.id}.pdf")

    # 3. Fallback: gera PDF local (leiaute proprietario) a partir dos dados do banco
    empresa = db.query(Empresa).first()
    cliente = _cliente_para_nfse(nfse)
    if not cliente:
        raise HTTPException(status_code=400, detail="Cliente/Tomador nÃ£o encontrado para gerar PDF")
    itens = nfse.itens
    try:
        pdf_url = gerar_pdf_nfse(nfse, empresa, cliente, itens, STATUS_LABELS)
        nfse.pdf_path = pdf_url
        db.commit()
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
def gerar_cobranca_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db),
                        num_parcelas: int = Form(1),
                        primeiro_vencimento: str = Form(""),
                        intervalo_dias: int = Form(30)):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.pedido),
        selectinload(NFSe.assinatura),
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe nÃ£o encontrada")

    if nfse.status == "rascunho":
        request.session["error"] = "Não é possível gerar cobrança de uma NFSe em rascunho. Emita a nota primeiro."
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)

    cobranca_existente = db.query(ContaReceber).filter(
        (ContaReceber.nfse_id == nfse.id) |
        ContaReceber.observacao.like(f"%NFSe #{nfse.id}%")
    ).first()
    if cobranca_existente:
        request.session["error"] = "CobranÃ§a jÃ¡ existe para esta NFSe"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)

    from services.parcelamento import gerar_contas_receber, contas_receber_existentes_para
    if contas_receber_existentes_para(db, nfse=nfse):
        request.session["error"] = "Cobrança já existe para esta NFSe (ou para o pedido/consolidação vinculado)"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)

    cliente_id = None
    if nfse.pedido:
        cliente_id = nfse.pedido.cliente_id
    # Fallback: NFSe de OS/avulsa nÃ£o tem pedido — usa o cliente da prÃ³pria NFSe
    if not cliente_id:
        cliente_id = nfse.cliente_id

    if not cliente_id:
        request.session["error"] = "NÃ£o foi possÃ­vel identificar o cliente para gerar cobranÃ§a"
        return RedirectResponse(url=f"/nfse/detalhe/{nfse_id}", status_code=303)

    # Vencimento sugerido: se a NFSe veio de uma assinatura, usa o proximo
    # vencimento da assinatura (baseado na ultima cobranca + periodicidade).
    venc = None
    if primeiro_vencimento:
        try:
            venc = date.fromisoformat(primeiro_vencimento)
        except ValueError:
            venc = None
    if venc is None and nfse.assinatura_id and nfse.assinatura:
        from routers.assinaturas import proximo_vencimento_para_cobranca
        venc = proximo_vencimento_para_cobranca(db, nfse.assinatura)
    if venc is None:
        venc = date.today()

    observacao = f"Gerado da NFSe #{nfse.id}"
    if nfse.assinatura_id:
        observacao = f"CobranÃ§a automÃ¡tica - assinatura #{nfse.assinatura_id} (NFSe #{nfse.id})"

    contas = gerar_contas_receber(
        db,
        cliente_id=cliente_id,
        descricao=f"NFSe #{nfse.numero or nfse.id}",
        valor_total=nfse.valor_liquido,
        primeiro_vencimento=venc,
        num_parcelas=num_parcelas,
        intervalo_dias=intervalo_dias,
        forma_pagamento="NFSe",
        numero_documento=str(nfse.numero) if nfse.numero else None,
        observacao=observacao,
        nfse_id=nfse.id,
        pedido_id=nfse.pedido_id,
        consolidacao_id=nfse.consolidacao_id if nfse.consolidacao else None,
    )
    db.commit()
    request.session["message"] = f"{len(contas)} cobranÃ§a(s) gerada(s) com sucesso para NFSe #{nfse.numero}!"
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
        db.query(ContaReceber).filter(ContaReceber.nfse_id == nfse.id).update(
            {ContaReceber.nfse_id: None}, synchronize_session=False
        )
        db.query(Assinatura).filter(Assinatura.nfse_id == nfse.id).update(
            {Assinatura.nfse_id: None}, synchronize_session=False
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

        erros = resultado.get('erros', [])
        if erros:
            msg_erro = "; ".join(f"[{e.get('codigo','')}] {e.get('mensagem','')}" for e in erros)
            nfse.mensagem_retorno = msg_erro

        # Se DPS jÃ¡ foi recepcionada, o prÃ³prio emitir_rascunho jÃ¡ retentou
        # com sÃ©rie diferente. Se ainda hÃ¡ protocolo, tenta sync.
        tem_protocolo = bool(nfse.protocolo)

        if sp == 'sucesso':
            nfse.codigo_verificacao = resultado.get('codigo_verificacao')
            nfse.numero = resultado.get('numero') or nfse.numero
            nfse.status = "autorizada"
            nfse.mensagem_retorno = None
            # XML oficial deve ser o da NFS-e Nacional (Ambiente Nacional), fonte
            # exclusiva do DANFSe padronizado. Nunca usamos o XML da Betha.
            chave = (resultado.get('codigo_verificacao')
                     or nfse.chave_acesso or nfse.codigo_verificacao)
            if not _salvar_xml_nacional_e_danfse(nfse, db, chave):
                # ADN ainda não propagou: mantém a DPS como referência e gera PDF
                # proprietário temporário. O XML nacional poderá ser obtido depois
                # pela sincronização ou rota /pdf (que persiste na base).
                from services.nfse_betha import gerar_dps_xml_nfse
                from services.nfse_pdf import gerar_pdf_nfse
                numero = int(nfse.numero) if nfse.numero and nfse.numero.isdigit() else None
                if not nfse.xml_text:
                    dps_xml = gerar_dps_xml_nfse(nfse, db, 1, numero)
                    if dps_xml:
                        nfse.xml_path = _salvar_xml_arquivo(nfse, dps_xml)
                        nfse.xml_text = dps_xml
                empresa = db.query(Empresa).first()
                cliente = nfse.cliente or (nfse.pedido.cliente if nfse.pedido else None)
                if empresa and cliente:
                    nfse.pdf_path = gerar_pdf_nfse(nfse, empresa, cliente, nfse.itens, STATUS_LABELS)
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
        elif tem_protocolo:
            # DPS jÃ¡ recebida — tenta sincronizar com protocolo existente
            try:
                sync_result = sincronizar_nfse(nfse.protocolo, tpAmb=1)
                sp_sync = sync_result.get('status_processamento')
                if sp_sync == 'sucesso':
                    nfse.codigo_verificacao = sync_result.get('codigo_verificacao')
                    nfse.numero = sync_result.get('numero') or nfse.numero
                    nfse.status = "autorizada"
                    nfse.mensagem_retorno = None
                    chave = (sync_result.get('codigo_verificacao')
                             or nfse.chave_acesso or nfse.codigo_verificacao)
                    if not _salvar_xml_nacional_e_danfse(nfse, db, chave):
                        from services.nfse_betha import gerar_dps_xml_nfse
                        from services.nfse_pdf import gerar_pdf_nfse
                        numero = int(nfse.numero) if nfse.numero and nfse.numero.isdigit() else None
                        if not nfse.xml_text:
                            dps_xml = gerar_dps_xml_nfse(nfse, db, 1, numero)
                            if dps_xml:
                                nfse.xml_path = _salvar_xml_arquivo(nfse, dps_xml)
                                nfse.xml_text = dps_xml
                        empresa = db.query(Empresa).first()
                        cliente = nfse.cliente or (nfse.pedido.cliente if nfse.pedido else None)
                        if empresa and cliente:
                            nfse.pdf_path = gerar_pdf_nfse(nfse, empresa, cliente, nfse.itens, STATUS_LABELS)
                    db.commit()
                    request.session["message"] = f"NFSe #{nfse.numero} jÃ¡ estava processada! Autorizada com sucesso."
                    if background_tasks:
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
        resultado = sync_func(nfse.protocolo, tpAmb=1, numero_nfse=nfse.numero,
                             chave_acesso=nfse.chave_acesso)
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

            chave = nfse.chave_acesso or nfse.codigo_verificacao
            if not _salvar_xml_nacional_e_danfse(nfse, db, chave):
                # ADN ainda não propagou: mantém DPS como referência e PDF proprietário.
                from services.nfse_betha import gerar_dps_xml_nfse
                from services.nfse_pdf import gerar_pdf_nfse
                numero = int(nfse.numero) if nfse.numero and nfse.numero.isdigit() else None
                if not nfse.xml_text:
                    dps_xml = gerar_dps_xml_nfse(nfse, db, 1, numero)
                    if dps_xml:
                        nfse.xml_path = _salvar_xml_arquivo(nfse, dps_xml)
                        nfse.xml_text = dps_xml
                if not nfse.pdf_path:
                    empresa = db.query(Empresa).first()
                    cliente = nfse.cliente or (nfse.pedido.cliente if nfse.pedido else None)
                    if empresa and cliente:
                        nfse.pdf_path = gerar_pdf_nfse(nfse, empresa, cliente, nfse.itens, STATUS_LABELS)

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
                # Backfill do XML nacional: notas emitidas via Betha ficam só com
                # a DPS (formato Betha). O XML padrão nacional (infNFSe+DPS) vem do
                # ADN e é a fonte do DANFSe. Atualiza se ainda não for nacional.
                if n.get('xml') and is_xml_nfse_nacional(n['xml']) and \
                   (not existente.xml_text or not is_xml_nfse_nacional(existente.xml_text)):
                    existente.xml_text = n['xml']
                    if not existente.chave_acesso:
                        existente.chave_acesso = chave
                    atualizados += 1
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
        if not xml_nacional or not is_xml_nfse_nacional(xml_nacional):
            try:
                empresa = db.query(Empresa).first()
                service = BethaNfseService(empresa=empresa)
                xml_nacional = service.obter_xml_nacional_por_chave(chave_acesso)
                if xml_nacional and is_xml_nfse_nacional(xml_nacional):
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

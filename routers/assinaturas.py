import logging
from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import (Assinatura, AssinaturaHistorico, Cliente, Fornecedor, Empresa, Produto,
                   ContaReceber, StatusConta, get_safe_day)
from models_nfe import NFSe, NFSeItem
from app.core.security import confirma_senha_usuario
from services.audit import registrar_auditoria

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assinaturas", tags=["Assinaturas"])

PERIODICIDADE_LABELS = {
    1: "Mensal",
    2: "Bimestral",
    3: "Trimestral",
    4: "Semestral",
    5: "Anual",
    6: "Bianual",
    7: "Trianual",
}

SITUACAO_LABELS = {
    0: "Inativo",
    1: "Ativo",
    2: "Baixado",
    3: "Isento",
    4: "Em Avaliação",
}

SITUACAO_OPCOES = [(0, "Inativo"), (1, "Ativo"), (2, "Baixado"), (3, "Isento"), (4, "Em Avaliação")]

PERIODICIDADE_OPCOES = [
    (1, "Mensal"),
    (2, "Bimestral"),
    (3, "Trimestral"),
    (4, "Semestral"),
    (5, "Anual"),
    (6, "Bianual"),
    (7, "Trianual"),
]


def _proximo_vencimento(assinatura: Assinatura) -> date | None:
    if assinatura.situacao != 1:
        return None
    hoje = date.today()
    dia = min(assinatura.dia_vencimento, 28)
    if assinatura.mes_vencimento == 1:
        if hoje.month == 12:
            data = date(hoje.year + 1, 1, dia)
        else:
            data = date(hoje.year, hoje.month + 1, dia)
    else:
        data = date(hoje.year, hoje.month, dia)
    # Avanca o periodo ate cair em uma data futura (nao mostrar vencimento passado)
    while data < hoje:
        data = _add_months(data, assinatura.periodicidade)
    return data


def proximo_vencimento_para_cobranca(db: Session, assinatura: Assinatura) -> date:
    """Proximo vencimento para gerar a cobranca da assinatura.

    Baseia-se na ultima cobranca ja gerada para esta assinatura (observacao
    contendo 'assinatura #id') avancando pela periodicidade; se nao houver
    cobranca anterior, usa o proximo vencimento calendario.
    """
    ultima = db.query(ContaReceber).filter(
        ContaReceber.cliente_id == assinatura.cliente_id,
        ContaReceber.observacao.like(f"%assinatura #{assinatura.id}%"),
        ContaReceber.status != StatusConta.CANCELADO,
    ).order_by(ContaReceber.data_vencimento.desc()).first()
    if ultima:
        return get_safe_day(_add_months(ultima.data_vencimento, assinatura.periodicidade), assinatura.dia_vencimento)
    return _proximo_vencimento(assinatura) or date.today()


@router.get("/")
def listar_assinaturas(
    request: Request, db: Session = Depends(get_db),
    periodicidade: str = Query(""), status_filtro: str = Query(""), busca: str = Query(""),
    vencimento_dias: str = Query(""), sort: str = Query(""), ordem: str = Query("")
):
    from sqlalchemy.orm import joinedload
    query = db.query(Assinatura).options(joinedload(Assinatura.cliente), joinedload(Assinatura.fornecedor)).join(Cliente)
    if periodicidade:
        try:
            query = query.filter(Assinatura.periodicidade == int(periodicidade))
        except ValueError:
            pass
    if status_filtro:
        try:
            query = query.filter(Assinatura.situacao == int(status_filtro))
        except ValueError:
            pass
    if busca:
        query = query.filter(Cliente.nome.ilike(f"%{busca}%") | Cliente.fantasia.ilike(f"%{busca}%") | Cliente.cpf_cnpj.ilike(f"%{busca}%"))

    # Ordenação por colunas (cliente, descricao, revenda, data_inicio)
    # "vencimento" e ordenado em Python pela data real de vencimento (ver abaixo),
    # pois dependeria de _proximo_vencimento (logica em Python) e nao do dia isolado.
    sort_map = {
        "cliente": Cliente.nome,
        "descricao": Assinatura.descricao,
        "revenda": Fornecedor.nome,
        "data_inicio": Assinatura.data_inicio,
    }
    if sort in sort_map:
        order_col = sort_map[sort]
        descendente = (ordem != "asc")
        query = query.order_by(order_col.desc() if descendente else order_col.asc())
    else:
        query = query.order_by(Assinatura.data_inicio.asc())

    if vencimento_dias:
        try:
            dias = int(vencimento_dias)
            hoje = date.today()
            fim = hoje + timedelta(days=dias)
            query = query.filter(
                (Assinatura.data_fim == None) | (Assinatura.data_fim >= hoje),
                Assinatura.data_inicio <= fim
            )
            assinaturas = query.all()
            assinaturas = [a for a in assinaturas if _proximo_vencimento(a) and hoje <= _proximo_vencimento(a) <= fim]
        except ValueError:
            assinaturas = query.all()
    else:
        assinaturas = query.all()
    
    for a in assinaturas:
        prox = _proximo_vencimento(a)
        a.prox_data = prox
        if prox:
            a.proximo_vencimento = prox.strftime("%d/%m/%Y")
            dias = (prox - date.today()).days
            a.vencendo_15 = 0 <= dias <= 15
            a.vencendo_30 = 15 < dias <= 30
        else:
            a.proximo_vencimento = None
            a.vencendo_15 = False
            a.vencendo_30 = False

    # Ordena por vencimento real (data completa), nao apenas pelo dia
    if sort == "vencimento":
        descendente = (ordem != "asc")
        assinaturas.sort(
            key=lambda a: (a.prox_data is None, a.prox_data),
            reverse=descendente
        )

    lucro_total = sum(
        a.valor - (a.valor_revenda or 0)
        for a in assinaturas
    )

    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    
    empresa = db.query(Empresa).first()
    categoria_padrao_id = getattr(empresa, 'categoria_servico_padrao_id', None)
    query_servicos = db.query(Produto).filter(Produto.tipo == 'servico')
    if categoria_padrao_id:
        query_servicos = query_servicos.filter(Produto.categoria_id == categoria_padrao_id)
    servicos = query_servicos.order_by(Produto.nome).all()
    
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    servicos_json = [{"id": s.id, "nome": s.nome, "codigo_lc116": s.codigo_lc116 or '', "preco": float(s.preco or 0), "fornecedor_id": s.fornecedor_id} for s in servicos]
    
    return request.app.state.templates.TemplateResponse(
        "assinaturas/listar.html",
        {"request": request, "assinaturas": assinaturas, "clientes": clientes,
         "fornecedores": fornecedores, "servicos": servicos,
         "clientes_json": clientes_json, "fornecedores_json": fornecedores_json, "servicos_json": servicos_json,
         "periodicidade": periodicidade, "status_filtro": status_filtro,
         "busca": busca, "vencimento_dias": vencimento_dias, "SITUACAO_LABELS": SITUACAO_LABELS,
         "sort": sort, "ordem": ordem,
         "PERIODICIDADE_LABELS": PERIODICIDADE_LABELS, "lucro_total": lucro_total}
    )


@router.post("/novo")
def criar_assinatura(
    request: Request, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    periodicidade: int = Form(1),
    servico_id: str = Form(""),
    descricao: str = Form(None),
    valor: float = Form(...),
    quantidade: str = Form(""),
    data_inicio: str = Form(...),
    data_fim: str = Form(None),
    dia_vencimento: int = Form(...),
    mes_vencimento: int = Form(0),
    situacao: int = Form(1),
    fornecedor_id: str = Form(""),
    valor_revenda: str = Form(""),
    numero_contrato: str = Form(None),
    observacao: str = Form(None),
    travar_cobranca: str = Form(""),
):
    # Tolerancia a campos int/float enviados vazios pelos selects ("Selecione...")
    servico_id = int(servico_id) if str(servico_id or "").strip() else None
    fornecedor_id = int(fornecedor_id) if str(fornecedor_id or "").strip() else None
    valor_revenda = float(valor_revenda) if str(valor_revenda or "").strip() else None
    quantidade = int(quantidade) if str(quantidade or "").strip() else None
    try:
        inicio = date.fromisoformat(data_inicio)
        fim = date.fromisoformat(data_fim) if data_fim else None
        
        servico = db.query(Produto).filter(Produto.id == servico_id).first() if servico_id else None
        descricao_final = descricao
        produto_id_final = servico_id
        fornecedor_id_final = fornecedor_id
        if servico:
            if not descricao:
                descricao_final = servico.nome
                produto_id_final = servico.id
            if not fornecedor_id and servico.fornecedor_id:
                fornecedor_id_final = servico.fornecedor_id
        
        assinatura =         Assinatura(
            cliente_id=cliente_id, periodicidade=periodicidade, descricao=descricao_final,
            valor=valor, quantidade=quantidade,
            data_inicio=inicio, data_fim=fim,
            dia_vencimento=dia_vencimento,
            mes_vencimento=mes_vencimento,
            situacao=situacao,
            fornecedor_id=fornecedor_id_final,
            valor_revenda=valor_revenda,
            numero_contrato=numero_contrato,
            observacao=observacao,
            travar_cobranca=(travar_cobranca == "1"),
            produto_id=produto_id_final,
            bling_pending_sync=True,
        )
        db.add(assinatura)
        db.commit()
        _gerar_cobranca(db, assinatura)
        return RedirectResponse(url="/assinaturas", status_code=303)
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


def _add_months(source_date, months):
    month = source_date.month - 1 + months
    year = source_date.year + month // 12
    month = month % 12 + 1
    day = min(source_date.day, 28)
    return date(year, month, day)


def _gerar_cobranca(db: Session, assinatura: Assinatura, gerar_proximas: int = 3):
    # Trava: cobranca da assinatura eh feita pela NFS-e (nao pela assinatura)
    if assinatura.travar_cobranca:
        return
    hoje = date.today()
    label = PERIODICIDADE_LABELS.get(assinatura.periodicidade, "Mensal")
    dia = assinatura.dia_vencimento

    ultima_conta = db.query(ContaReceber).filter(
        ContaReceber.cliente_id == assinatura.cliente_id,
        ContaReceber.observacao.like(f"%assinatura #{assinatura.id}%")
    ).order_by(ContaReceber.data_vencimento.desc()).first()

    if ultima_conta:
        data_base = _add_months(ultima_conta.data_vencimento, assinatura.periodicidade)
    else:
        m = hoje.month + 1 if assinatura.mes_vencimento == 1 else hoje.month
        a = hoje.year if m <= 12 else hoje.year + 1
        m = m if m <= 12 else 1
        data_base = date(a, m, 1)

    for i in range(gerar_proximas):
        data_venc = get_safe_day(data_base, dia)
        if assinatura.periodicidade >= 5:
            desc = f"{label} - {assinatura.descricao} - {data_venc.year}"
        else:
            desc = f"{label} - {assinatura.descricao} - {data_venc.month:02d}/{data_venc.year}"

        existente = db.query(ContaReceber).filter(
            ContaReceber.cliente_id == assinatura.cliente_id,
            ContaReceber.descricao == desc,
            ContaReceber.status != StatusConta.CANCELADO
        ).first()

        if not existente and data_venc >= hoje:
            conta = ContaReceber(
                cliente_id=assinatura.cliente_id,
                descricao=desc,
                valor=assinatura.valor,
                data_vencimento=data_venc,
                observacao=f"Cobrança automática - assinatura #{assinatura.id}"
            )
            db.add(conta)

        data_base = _add_months(data_base, assinatura.periodicidade)

    db.commit()


def _salvar_historico(db: Session, assinatura: Assinatura, valor, valor_revenda, quantidade, dia_vencimento):
    alterou = False
    vals = {}
    if assinatura.valor != valor:
        vals["valor_anterior"] = assinatura.valor
        vals["valor_novo"] = valor
        alterou = True
    if assinatura.valor_revenda != valor_revenda:
        vals["valor_revenda_anterior"] = assinatura.valor_revenda
        vals["valor_revenda_novo"] = valor_revenda
        alterou = True
    if assinatura.quantidade != quantidade:
        vals["quantidade_anterior"] = assinatura.quantidade
        vals["quantidade_novo"] = quantidade
        alterou = True
    if assinatura.dia_vencimento != dia_vencimento:
        vals["dia_vencimento_anterior"] = assinatura.dia_vencimento
        vals["dia_vencimento_novo"] = dia_vencimento
        alterou = True
    if alterou:
        historico = AssinaturaHistorico(assinatura_id=assinatura.id, **vals)
        db.add(historico)


@router.post("/{assinatura_id}/gerar-cobranca")
def gerar_cobranca(request: Request, assinatura_id: int, db: Session = Depends(get_db), quantidade: int = Form(3)):
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if not assinatura:
        request.session["error"] = "Assinatura não encontrada"
    elif assinatura.travar_cobranca:
        request.session["error"] = "Cobrança desta assinatura está travada: gere a cobrança a partir da NFS-e."
    else:
        try:
            qtd = max(1, min(int(quantidade), 24))
        except (ValueError, TypeError):
            qtd = 3
        _gerar_cobranca(db, assinatura, gerar_proximas=qtd)
        request.session["message"] = f"{qtd} cobrança(s) gerada(s) para a assinatura #{assinatura_id}"
    return RedirectResponse(url="/assinaturas", status_code=303)


@router.post("/{assinatura_id}/gerar-nfse")
def gerar_nfse_assinatura(request: Request, assinatura_id: int, db: Session = Depends(get_db)):
    assinatura = db.query(Assinatura).options(
        joinedload(Assinatura.cliente), joinedload(Assinatura.produto)
    ).filter(Assinatura.id == assinatura_id).first()
    if not assinatura:
        request.session["error"] = "Assinatura não encontrada"
        return RedirectResponse(url="/assinaturas", status_code=303)

    empresa = db.query(Empresa).first()
    if not empresa:
        request.session["error"] = "Empresa não configurada"
        return RedirectResponse(url="/assinaturas", status_code=303)

    try:
        numero_nfse = str((empresa.ultimo_numero_nfse or 0) + 1)
        empresa.ultimo_numero_nfse = int(numero_nfse)

        iss_retido = getattr(assinatura.cliente, 'iss_retido', False) or False
        nfse = NFSe(
            numero=numero_nfse,
            cliente_id=assinatura.cliente_id,
            origem="assinatura",
            status="rascunho",
            valor_total=assinatura.valor,
            data_emissao=datetime.now(),
            iss_retido=iss_retido,
            aliquota_iss=empresa.aliquota_iss or 2.0,
            aliquota_federal=empresa.aliquota_federal or 0.0,
            aliquota_estadual=empresa.aliquota_estadual or 0.0,
            aliquota_municipal=empresa.aliquota_municipal or 0.0,
            observacoes=assinatura.observacao or "",
        )
        db.add(nfse)
        db.flush()
        # Vincula a NFS-e a assinatura de origem para rastrear o vencimento na cobranca
        nfse.assinatura_id = assinatura.id
        assinatura.nfse_id = nfse.id

        servico = assinatura.produto

        nome_servico = servico.nome if servico else ""
        descricao_completa = f"{nome_servico} - {assinatura.descricao}" if nome_servico else assinatura.descricao
        if assinatura.quantidade:
            descricao_completa += f" ({assinatura.quantidade} pessoas)"
        nfse_item = NFSeItem(
            nfse_id=nfse.id,
            produto_id=servico.id if servico else None,
            descricao=descricao_completa,
            quantidade=1,
            valor_unitario=assinatura.valor,
            valor_total=assinatura.valor,
            codigo_servico=servico.codigo_lc116 if servico else "",
            tributacao_municipal=servico.codigo_tributacao_municipal if servico else "",
        )
        db.add(nfse_item)

        db.commit()
        request.session["message"] = f"Rascunho NFSe #{numero_nfse} gerado para assinatura! Revise antes de emitir."
        return RedirectResponse(url=f"/nfse/detalhe/{nfse.id}", status_code=303)
    except Exception as e:
        db.rollback()
        request.session["error"] = f"Erro ao gerar NFSe: {str(e)}"
        return RedirectResponse(url="/assinaturas", status_code=303)


@router.get("/{assinatura_id}/editar")
def editar_assinatura(request: Request, assinatura_id: int, db: Session = Depends(get_db)):
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if not assinatura:
        return RedirectResponse(url="/assinaturas", status_code=303)
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    historico = db.query(AssinaturaHistorico).filter(
        AssinaturaHistorico.assinatura_id == assinatura_id
    ).order_by(AssinaturaHistorico.data_alteracao.desc()).all()
    empresa = db.query(Empresa).first()
    categoria_padrao_id = getattr(empresa, 'categoria_servico_padrao_id', None)
    query_servicos = db.query(Produto).filter(Produto.tipo == 'servico')
    if categoria_padrao_id:
        query_servicos = query_servicos.filter(Produto.categoria_id == categoria_padrao_id)
    servicos = query_servicos.order_by(Produto.nome).all()
    
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    servicos_json = [{"id": s.id, "nome": s.nome, "codigo_lc116": s.codigo_lc116 or '',
                  "preco": float(s.preco or 0), "fornecedor_id": s.fornecedor_id} for s in servicos]
    
    return request.app.state.templates.TemplateResponse(
        "assinaturas/form.html",
        {"request": request, "assinatura": assinatura, "clientes": clientes,
         "fornecedores": fornecedores, "servicos": servicos,
         "historico": historico,
         "clientes_json": clientes_json, "fornecedores_json": fornecedores_json, "servicos_json": servicos_json,
         "senha_definida": True}
    )


@router.post("/{assinatura_id}/editar")
def atualizar_assinatura(
    request: Request, assinatura_id: int, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    periodicidade: int = Form(1),
    servico_id: str = Form(""),
    descricao: str = Form(None),
    valor: float = Form(...),
    quantidade: str = Form(""),
    data_inicio: str = Form(...),
    data_fim: str = Form(""),
    dia_vencimento: int = Form(...),
    mes_vencimento: int = Form(0),
    situacao: int = Form(1),
    fornecedor_id: str = Form(""),
    valor_revenda: str = Form(""),
    numero_contrato: str = Form(""),
    observacao: str = Form(""),
    travar_cobranca: str = Form(""),
):
    # Tolerancia a campos int/float enviados vazios pelos selects ("Selecione...")
    servico_id = int(servico_id) if str(servico_id or "").strip() else None
    fornecedor_id = int(fornecedor_id) if str(fornecedor_id or "").strip() else None
    valor_revenda = float(valor_revenda) if str(valor_revenda or "").strip() else None
    quantidade = int(quantidade) if str(quantidade or "").strip() else 0
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if not assinatura:
        return RedirectResponse(url="/assinaturas", status_code=303)

    # Só permite alterações se a assinatura estiver ativa
    if assinatura.situacao != 1:
        return RedirectResponse(url="/assinaturas", status_code=303)

    servico = db.query(Produto).filter(Produto.id == servico_id).first() if servico_id else None
    descricao_final = descricao or (servico.nome if servico else '')

    dia_antigo = assinatura.dia_vencimento

    _salvar_historico(db, assinatura, valor, valor_revenda, quantidade if quantidade else None, dia_vencimento)

    assinatura.cliente_id = cliente_id
    assinatura.periodicidade = periodicidade
    assinatura.descricao = descricao_final
    assinatura.valor = valor
    assinatura.quantidade = quantidade if quantidade else None
    assinatura.data_inicio = date.fromisoformat(data_inicio)
    assinatura.data_fim = date.fromisoformat(data_fim) if data_fim else None
    assinatura.dia_vencimento = dia_vencimento
    assinatura.mes_vencimento = mes_vencimento
    assinatura.situacao = situacao
    fornecedor_id_final = fornecedor_id if fornecedor_id else (servico.fornecedor_id if servico and servico.fornecedor_id else None)
    assinatura.fornecedor_id = fornecedor_id_final
    assinatura.produto_id = servico_id if servico_id else None
    assinatura.valor_revenda = valor_revenda if valor_revenda else None
    assinatura.numero_contrato = numero_contrato if numero_contrato else None
    assinatura.observacao = observacao
    assinatura.travar_cobranca = (travar_cobranca == "1")
    assinatura.updated_at = datetime.now()
    assinatura.bling_pending_sync = True

    # Ajusta o vencimento recorrente nas cobrancas futuras ja geradas
    # quando o dia de vencimento da assinatura e alterado
    if dia_antigo != dia_vencimento:
        hoje = date.today()
        contas_futuras = db.query(ContaReceber).filter(
            ContaReceber.cliente_id == assinatura.cliente_id,
            ContaReceber.observacao.like(f"%assinatura #{assinatura.id}%"),
            ContaReceber.data_vencimento >= hoje,
            ContaReceber.status.notin_([
                StatusConta.PAGO, StatusConta.CANCELADO,
                StatusConta.BAIXA_SOLICITADA, StatusConta.EXCLUIDO
            ])
        ).all()
        for c in contas_futuras:
            c.data_vencimento = get_safe_day(c.data_vencimento, dia_vencimento)

    db.commit()
    return RedirectResponse(url="/assinaturas", status_code=303)


@router.post("/{assinatura_id}/historico/{historico_id}/excluir")
def excluir_historico(
    request: Request, assinatura_id: int, historico_id: int,
    db: Session = Depends(get_db),
    senha: str = Form(""),
):
    if not confirma_senha_usuario(request, db, senha):
        return JSONResponse({"erro": "Senha inválida ou usuário não autorizado"}, status_code=403)
    historico = db.query(AssinaturaHistorico).filter(
        AssinaturaHistorico.id == historico_id
    ).first()
    if historico:
        db.delete(historico)
        db.commit()
        registrar_auditoria(
            db, request.session.get("user_id"), "excluir",
            "assinatura_historico", historico_id,
            f"Assinatura #{assinatura_id} - Histórico #{historico_id}",
            request.client.host if request.client else None
        )
    return RedirectResponse(url=f"/assinaturas/{assinatura_id}/editar", status_code=303)


@router.get("/{assinatura_id}/cancelar")
def cancelar_assinatura(request: Request, assinatura_id: int, db: Session = Depends(get_db)):
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if assinatura:
        assinatura.situacao = 0
        db.commit()
    return RedirectResponse(url="/assinaturas", status_code=303)


@router.post("/{assinatura_id}/excluir")
def excluir_assinatura(request: Request, assinatura_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    if not confirma_senha_usuario(request, db, senha):
        return JSONResponse({"success": False, "error": "Senha inválida ou usuário não autorizado"}, status_code=403)
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if not assinatura:
        return JSONResponse({"success": False, "error": "Assinatura não encontrada"}, status_code=404)
    try:
        db.delete(assinatura)
        db.commit()
        registrar_auditoria(
            db, request.session.get("user_id"), "excluir",
            "assinatura", assinatura_id,
            f"Assinatura #{assinatura_id}",
            request.client.host if request.client else None
        )
        return JSONResponse({"success": True, "message": "Assinatura excluída com sucesso"})
    except Exception:
        db.rollback()
        logger.exception("Erro ao excluir assinatura %s", assinatura_id)
        return JSONResponse({"success": False, "error": "Erro interno ao excluir a assinatura"}, status_code=500)

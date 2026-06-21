import enum
from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import (Assinatura, AssinaturaHistorico, Cliente, Fornecedor, Empresa,
                   ContaReceber, StatusConta, get_safe_day)

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
    if assinatura.mes_vencimento == 1:
        if hoje.month == 12:
            data = date(hoje.year + 1, 1, min(assinatura.dia_vencimento, 28))
        else:
            data = date(hoje.year, hoje.month + 1, min(assinatura.dia_vencimento, 28))
    else:
        data = date(hoje.year, hoje.month, min(assinatura.dia_vencimento, 28))
    return data


@router.get("/")
def listar_assinaturas(
    request: Request, db: Session = Depends(get_db),
    periodicidade: str = Query(""), status_filtro: str = Query(""), busca: str = Query(""),
    vencimento_dias: str = Query("")
):
    query = db.query(Assinatura).join(Cliente)
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
    if vencimento_dias:
        try:
            dias = int(vencimento_dias)
            hoje = date.today()
            fim = hoje + timedelta(days=dias)
            query = query.filter(
                (Assinatura.data_fim == None) | (Assinatura.data_fim >= hoje),
                Assinatura.data_inicio <= fim
            )
            assinaturas = query.order_by(Assinatura.data_inicio.desc()).all()
            assinaturas = [a for a in assinaturas if _proximo_vencimento(a) and hoje <= _proximo_vencimento(a) <= fim]
        except ValueError:
            assinaturas = query.order_by(Assinatura.data_inicio.desc()).all()
    else:
        assinaturas = query.order_by(Assinatura.data_inicio.desc()).all()
    
    for a in assinaturas:
        prox = _proximo_vencimento(a)
        if prox:
            a.proximo_vencimento = prox.strftime("%d/%m/%Y")
        else:
            a.proximo_vencimento = None

    lucro_total = sum(
        a.valor - (a.valor_revenda or 0)
        for a in assinaturas
    )

    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    return request.app.state.templates.TemplateResponse(
        "assinaturas/listar.html",
        {"request": request, "assinaturas": assinaturas, "clientes": clientes,
         "fornecedores": fornecedores, "clientes_json": clientes_json, "fornecedores_json": fornecedores_json,
         "periodicidade": periodicidade, "status_filtro": status_filtro,
         "busca": busca, "vencimento_dias": vencimento_dias, "SITUACAO_LABELS": SITUACAO_LABELS,
         "PERIODICIDADE_LABELS": PERIODICIDADE_LABELS, "lucro_total": lucro_total}
    )


@router.post("/novo")
def criar_assinatura(
    request: Request, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    periodicidade: int = Form(1),
    descricao: str = Form(...),
    valor: float = Form(...),
    quantidade: int = Form(0),
    data_inicio: str = Form(...),
    data_fim: str = Form(""),
    dia_vencimento: int = Form(...),
    mes_vencimento: int = Form(0),
    fornecedor_id: int = Form(0),
    valor_revenda: float = Form(0),
    numero_contrato: str = Form(""),
    observacao: str = Form(""),
):
    inicio = date.fromisoformat(data_inicio)
    fim = date.fromisoformat(data_fim) if data_fim else None
    assinatura = Assinatura(
        cliente_id=cliente_id, periodicidade=periodicidade, descricao=descricao,
        valor=valor, quantidade=quantidade if quantidade else None,
        data_inicio=inicio, data_fim=fim,
        dia_vencimento=dia_vencimento,
        mes_vencimento=mes_vencimento,
        fornecedor_id=fornecedor_id if fornecedor_id else None,
        valor_revenda=valor_revenda if valor_revenda else None,
        numero_contrato=numero_contrato if numero_contrato else None,
        observacao=observacao,
    )
    db.add(assinatura)
    db.commit()

    _gerar_cobranca(db, assinatura)
    return RedirectResponse(url="/assinaturas", status_code=303)


def _add_months(source_date, months):
    month = source_date.month - 1 + months
    year = source_date.year + month // 12
    month = month % 12 + 1
    day = min(source_date.day, 28)
    return date(year, month, day)


def _gerar_cobranca(db: Session, assinatura: Assinatura, gerar_proximas: int = 3):
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


@router.get("/{assinatura_id}/gerar-cobranca")
def gerar_cobranca(request: Request, assinatura_id: int, db: Session = Depends(get_db)):
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if assinatura:
        _gerar_cobranca(db, assinatura)
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
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    fornecedores_json = [{"id": f.id, "nome": f.nome, "fantasia": f.fantasia or '', "cpf_cnpj": f.cpf_cnpj} for f in fornecedores]
    return request.app.state.templates.TemplateResponse(
        "assinaturas/form.html",
        {"request": request, "assinatura": assinatura, "clientes": clientes,
         "fornecedores": fornecedores, "historico": historico,
         "clientes_json": clientes_json, "fornecedores_json": fornecedores_json,
         "senha_definida": bool(empresa and empresa.senha_admin)}
    )


@router.post("/{assinatura_id}/editar")
def atualizar_assinatura(
    request: Request, assinatura_id: int, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    periodicidade: int = Form(1),
    descricao: str = Form(...),
    valor: float = Form(...),
    quantidade: int = Form(0),
    data_inicio: str = Form(...),
    data_fim: str = Form(""),
    dia_vencimento: int = Form(...),
    mes_vencimento: int = Form(0),
    situacao: int = Form(1),
    fornecedor_id: int = Form(0),
    valor_revenda: float = Form(0),
    numero_contrato: str = Form(""),
    observacao: str = Form(""),
):
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if not assinatura:
        return RedirectResponse(url="/assinaturas", status_code=303)

    # Só permite alterações se a assinatura estiver ativa
    if assinatura.situacao != 1:
        return RedirectResponse(url="/assinaturas", status_code=303)

    _salvar_historico(db, assinatura, valor, valor_revenda, quantidade if quantidade else None, dia_vencimento)

    assinatura.cliente_id = cliente_id
    assinatura.periodicidade = periodicidade
    assinatura.descricao = descricao
    assinatura.valor = valor
    assinatura.quantidade = quantidade if quantidade else None
    assinatura.data_inicio = date.fromisoformat(data_inicio)
    assinatura.data_fim = date.fromisoformat(data_fim) if data_fim else None
    assinatura.dia_vencimento = dia_vencimento
    assinatura.mes_vencimento = mes_vencimento
    assinatura.situacao = situacao
    assinatura.fornecedor_id = fornecedor_id if fornecedor_id else None
    assinatura.valor_revenda = valor_revenda if valor_revenda else None
    assinatura.numero_contrato = numero_contrato if numero_contrato else None
    assinatura.observacao = observacao
    assinatura.updated_at = datetime.now()
    db.commit()
    return RedirectResponse(url="/assinaturas", status_code=303)


@router.post("/{assinatura_id}/historico/{historico_id}/excluir")
def excluir_historico(
    request: Request, assinatura_id: int, historico_id: int,
    db: Session = Depends(get_db),
    senha: str = Form(""),
):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.senha_admin or senha != empresa.senha_admin:
        return JSONResponse({"erro": "Senha inválida"}, status_code=403)
    historico = db.query(AssinaturaHistorico).filter(
        AssinaturaHistorico.id == historico_id,
        AssinaturaHistorico.assinatura_id == assinatura_id
    ).first()
    if historico:
        db.delete(historico)
        db.commit()
    return RedirectResponse(url=f"/assinaturas/{assinatura_id}", status_code=303)


@router.get("/{assinatura_id}/cancelar")
def cancelar_assinatura(request: Request, assinatura_id: int, db: Session = Depends(get_db)):
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if assinatura:
        assinatura.situacao = 0
        db.commit()
    return RedirectResponse(url="/assinaturas", status_code=303)


@router.post("/{assinatura_id}/excluir")
def excluir_assinatura(request: Request, assinatura_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.senha_admin or senha != empresa.senha_admin:
        return JSONResponse({"erro": "Senha inválida"}, status_code=403)
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if assinatura:
        db.delete(assinatura)
        db.commit()
    return RedirectResponse(url="/assinaturas", status_code=303)
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime, date

from database import get_db
from models import (Assinatura, AssinaturaHistorico, Cliente, Fornecedor, Empresa,
                     ContaReceber, StatusConta)

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


@router.get("/")
def listar_assinaturas(
    request: Request, db: Session = Depends(get_db),
    periodicidade: str = Query(""), status_filtro: str = Query(""), busca: str = Query("")
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
        query = query.filter(Cliente.nome.ilike(f"%{busca}%"))
    assinaturas = query.order_by(Assinatura.data_inicio.desc()).all()

    lucro_total = sum(
        a.valor - (a.valor_revenda or 0)
        for a in assinaturas
    )

    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    return request.app.state.templates.TemplateResponse(
        "assinaturas/listar.html",
        {"request": request, "assinaturas": assinaturas, "clientes": clientes,
         "fornecedores": fornecedores, "periodicidade": periodicidade, "status_filtro": status_filtro,
         "busca": busca, "SITUACAO_LABELS": SITUACAO_LABELS,
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
    fornecedor_id: int = Form(0),
    valor_revenda: float = Form(0),
    observacao: str = Form(""),
):
    inicio = date.fromisoformat(data_inicio)
    fim = date.fromisoformat(data_fim) if data_fim else None
    assinatura = Assinatura(
        cliente_id=cliente_id, periodicidade=periodicidade, descricao=descricao,
        valor=valor, quantidade=quantidade if quantidade else None,
        data_inicio=inicio, data_fim=fim,
        dia_vencimento=dia_vencimento,
        fornecedor_id=fornecedor_id if fornecedor_id else None,
        valor_revenda=valor_revenda if valor_revenda else None,
        observacao=observacao,
        bling_pending_sync=True
    )
    db.add(assinatura)
    db.commit()

    _gerar_cobranca(db, assinatura)
    return RedirectResponse(url="/assinaturas", status_code=303)


def _gerar_cobranca(db: Session, assinatura: Assinatura):
    hoje = date.today()
    mes_cobranca = hoje.month
    ano_cobranca = hoje.year

    if assinatura.dia_vencimento > 28:
        dia = 28
    else:
        dia = assinatura.dia_vencimento

    label = PERIODICIDADE_LABELS.get(assinatura.periodicidade, "Mensal")

    if assinatura.periodicidade >= 5:
        desc = f"{label} - {assinatura.descricao} - {ano_cobranca}"
        data_venc = date(ano_cobranca, 1, dia)
    else:
        desc = f"{label} - {assinatura.descricao} - {mes_cobranca:02d}/{ano_cobranca}"
        data_venc = date(ano_cobranca, mes_cobranca, dia)

    existente = db.query(ContaReceber).filter(
        ContaReceber.cliente_id == assinatura.cliente_id,
        ContaReceber.descricao == desc,
        ContaReceber.status != StatusConta.CANCELADO
    ).first()

    if not existente:
        conta = ContaReceber(
            cliente_id=assinatura.cliente_id,
            descricao=desc,
            valor=assinatura.valor,
            data_vencimento=data_venc,
            observacao=f"Cobrança automática - assinatura #{assinatura.id}"
        )
        db.add(conta)
        db.commit()


def _salvar_historico(db: Session, assinatura: Assinatura, valor, valor_revenda, quantidade):
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
    return request.app.state.templates.TemplateResponse(
        "assinaturas/form.html",
        {"request": request, "assinatura": assinatura, "clientes": clientes,
         "fornecedores": fornecedores, "historico": historico,
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
    situacao: int = Form(1),
    fornecedor_id: int = Form(0),
    valor_revenda: float = Form(0),
    observacao: str = Form(""),
):
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if not assinatura:
        return RedirectResponse(url="/assinaturas", status_code=303)

    _salvar_historico(db, assinatura, valor, valor_revenda, quantidade if quantidade else None)

    assinatura.cliente_id = cliente_id
    assinatura.periodicidade = periodicidade
    assinatura.descricao = descricao
    assinatura.valor = valor
    assinatura.quantidade = quantidade if quantidade else None
    assinatura.data_inicio = date.fromisoformat(data_inicio)
    assinatura.data_fim = date.fromisoformat(data_fim) if data_fim else None
    assinatura.dia_vencimento = dia_vencimento
    assinatura.situacao = situacao
    assinatura.fornecedor_id = fornecedor_id if fornecedor_id else None
    assinatura.valor_revenda = valor_revenda if valor_revenda else None
    assinatura.observacao = observacao
    assinatura.updated_at = datetime.now()
    assinatura.bling_pending_sync = True
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
    return RedirectResponse(url=f"/assinaturas/{assinatura_id}/editar", status_code=303)


@router.get("/{assinatura_id}/cancelar")
def cancelar_assinatura(request: Request, assinatura_id: int, db: Session = Depends(get_db)):
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if assinatura:
        assinatura.situacao = 0
        db.commit()
    return RedirectResponse(url="/assinaturas", status_code=303)


@router.get("/{assinatura_id}/excluir")
def excluir_assinatura(request: Request, assinatura_id: int, db: Session = Depends(get_db)):
    assinatura = db.query(Assinatura).filter(Assinatura.id == assinatura_id).first()
    if assinatura:
        db.delete(assinatura)
        db.commit()
    return RedirectResponse(url="/assinaturas", status_code=303)

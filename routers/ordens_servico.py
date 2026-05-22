from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, date

from database import get_db
from models import OrdemServico, Cliente, Empresa, StatusOS

router = APIRouter(prefix="/ordens-servico", tags=["Ordens de Serviço"])


@router.get("/")
def listar_ordens(
    request: Request, db: Session = Depends(get_db),
    status_filtro: str = Query(""), busca: str = Query("")
):
    query = db.query(OrdemServico).join(Cliente)
    if status_filtro:
        query = query.filter(OrdemServico.status == status_filtro)
    if busca:
        query = query.filter(
            OrdemServico.equipamento.ilike(f"%{busca}%") |
            Cliente.nome.ilike(f"%{busca}%") |
            OrdemServico.defeito_relatado.ilike(f"%{busca}%")
        )
    ordens = query.order_by(OrdemServico.data_entrada.desc()).all()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    return request.app.state.templates.TemplateResponse(
        "ordens_servico/listar.html",
        {"request": request, "ordens": ordens, "clientes": clientes,
         "status_filtro": status_filtro, "busca": busca, "StatusOS": StatusOS}
    )


@router.get("/nova")
def nova_ordem(request: Request, db: Session = Depends(get_db)):
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    return request.app.state.templates.TemplateResponse(
        "ordens_servico/form.html",
        {"request": request, "ordem": None, "clientes": clientes}
    )


@router.post("/nova")
def criar_ordem(
    request: Request, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    equipamento: str = Form(...),
    marca: str = Form(""),
    modelo: str = Form(""),
    numero_serie: str = Form(""),
    defeito_relatado: str = Form(""),
    tecnico: str = Form(""),
    autorizado_por: str = Form(""),
    numero_requisicao: str = Form(""),
    observacao: str = Form(""),
):
    ordem = OrdemServico(
        cliente_id=cliente_id, equipamento=equipamento,
        marca=marca, modelo=modelo, numero_serie=numero_serie,
        defeito_relatado=defeito_relatado, tecnico=tecnico,
        autorizado_por=autorizado_por, numero_requisicao=numero_requisicao,
        observacao=observacao,
        bling_pending_sync=True
    )
    db.add(ordem)
    db.commit()
    return RedirectResponse(url="/ordens-servico", status_code=303)


@router.get("/{ordem_id}")
def detalhe_ordem(request: Request, ordem_id: int, db: Session = Depends(get_db)):
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    return request.app.state.templates.TemplateResponse(
        "ordens_servico/detalhe.html",
        {"request": request, "ordem": ordem, "clientes": clientes, "StatusOS": StatusOS}
    )


@router.post("/{ordem_id}/editar")
def atualizar_ordem(
    request: Request, ordem_id: int, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    equipamento: str = Form(...),
    marca: str = Form(""),
    modelo: str = Form(""),
    numero_serie: str = Form(""),
    defeito_relatado: str = Form(""),
    servicos_executados: str = Form(""),
    pecas_utilizadas: str = Form(""),
    valor_servico: float = Form(0),
    valor_pecas: float = Form(0),
    valor_total: float = Form(0),
    data_entrada: str = Form(""),
    data_saida: str = Form(""),
    status: str = Form(...),
    tecnico: str = Form(""),
    autorizado_por: str = Form(""),
    numero_requisicao: str = Form(""),
    observacao: str = Form(""),
):
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)
    ordem.cliente_id = cliente_id
    ordem.equipamento = equipamento
    ordem.marca = marca
    ordem.modelo = modelo
    ordem.numero_serie = numero_serie
    ordem.defeito_relatado = defeito_relatado
    ordem.servicos_executados = servicos_executados
    ordem.pecas_utilizadas = pecas_utilizadas
    ordem.valor_servico = valor_servico
    ordem.valor_pecas = valor_pecas
    ordem.valor_total = valor_total
    ordem.data_entrada = date.fromisoformat(data_entrada) if data_entrada else ordem.data_entrada
    ordem.data_saida = date.fromisoformat(data_saida) if data_saida else None
    ordem.status = status
    ordem.tecnico = tecnico
    ordem.autorizado_por = autorizado_por
    ordem.numero_requisicao = numero_requisicao
    ordem.observacao = observacao
    ordem.updated_at = datetime.now()
    ordem.bling_pending_sync = True
    db.commit()
    return RedirectResponse(url=f"/ordens-servico/{ordem_id}", status_code=303)


@router.get("/{ordem_id}/imprimir")
def imprimir_ordem(request: Request, ordem_id: int, db: Session = Depends(get_db)):
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)
    empresa = db.query(Empresa).first()
    from datetime import datetime
    return request.app.state.templates.TemplateResponse(
        "ordens_servico/imprimir.html",
        {"request": request, "ordem": ordem, "empresa": empresa, "datetime": datetime}
    )


@router.get("/{ordem_id}/excluir")
def excluir_ordem(request: Request, ordem_id: int, db: Session = Depends(get_db)):
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if ordem:
        db.delete(ordem)
        db.commit()
    return RedirectResponse(url="/ordens-servico", status_code=303)

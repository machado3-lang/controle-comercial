from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime, date
from sqlalchemy import or_

from database import get_db
from models import OrdemServico, Cliente, Empresa, StatusOS, Produto, MarcaProduto, CategoriaProduto

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
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    servicos = db.query(Produto).filter(Produto.tipo == 'servico').order_by(Produto.nome).all()
    pecas = db.query(Produto).filter(Produto.tipo == 'produto').order_by(Produto.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    marcas_json = [{"id": m.id, "nome": m.nome} for m in marcas]
    servicos_json = [{"id": s.id, "nome": s.nome, "codigo_lc116": s.codigo_lc116 or '', "preco": s.preco} for s in servicos]
    pecas_json = [{"id": p.id, "nome": p.nome, "preco": p.preco} for p in pecas]
    return request.app.state.templates.TemplateResponse(
        "ordens_servico/listar.html",
        {"request": request, "ordens": ordens, "clientes": clientes, "marcas": marcas,
         "servicos": servicos, "pecas": pecas,
         "clientes_json": clientes_json, "marcas_json": marcas_json, "servicos_json": servicos_json, "pecas_json": pecas_json,
         "status_filtro": status_filtro, "busca": busca, "StatusOS": StatusOS}
    )


@router.get("/nova")
def nova_ordem(request: Request, db: Session = Depends(get_db)):
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    servicos = db.query(Produto).filter(Produto.tipo == 'servico').order_by(Produto.nome).all()
    pecas = db.query(Produto).filter(Produto.tipo == 'produto').order_by(Produto.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    marcas_json = [{"id": m.id, "nome": m.nome} for m in marcas]
    servicos_json = [{"id": s.id, "nome": s.nome, "codigo_lc116": s.codigo_lc116 or '', "preco": s.preco} for s in servicos]
    pecas_json = [{"id": p.id, "nome": p.nome, "preco": p.preco} for p in pecas]
    return request.app.state.templates.TemplateResponse(
        "ordens_servico/form.html",
        {"request": request, "ordem": None, "clientes": clientes, "marcas": marcas,
         "servicos": servicos, "pecas": pecas, "clientes_json": clientes_json, 
         "marcas_json": marcas_json, "servicos_json": servicos_json, "pecas_json": pecas_json}
    )


@router.post("/nova")
def criar_ordem(
    request: Request, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    equipamento: str = Form(...),
    marca_id: int = Form(0),
    modelo: str = Form(""),
    numero_serie: str = Form(""),
    defeito_relatado: str = Form(""),
    tecnico: str = Form(""),
    autorizado_por: str = Form(""),
    numero_requisicao: str = Form(""),
    observacao: str = Form(""),
):
    marca = None
    if marca_id:
        m = db.query(MarcaProduto).get(marca_id)
        marca = m.nome if m else None
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
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    servicos = db.query(Produto).filter(Produto.tipo == 'servico').order_by(Produto.nome).all()
    pecas = db.query(Produto).filter(Produto.tipo == 'produto').order_by(Produto.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    marcas_json = [{"id": m.id, "nome": m.nome} for m in marcas]
    servicos_json = [{"id": s.id, "nome": s.nome, "codigo_lc116": s.codigo_lc116 or '', "preco": s.preco} for s in servicos]
    pecas_json = [{"id": p.id, "nome": p.nome, "preco": p.preco} for p in pecas]
    return request.app.state.templates.TemplateResponse(
        "ordens_servico/detalhe.html",
        {"request": request, "ordem": ordem, "clientes": clientes, "marcas": marcas,
         "servicos": servicos, "pecas": pecas, "StatusOS": StatusOS,
         "clientes_json": clientes_json, "marcas_json": marcas_json,
         "servicos_json": servicos_json, "pecas_json": pecas_json}
    )


@router.post("/{ordem_id}/editar")
def atualizar_ordem(
    request: Request, ordem_id: int, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    equipamento: str = Form(...),
    marca_id: int = Form(0),
    modelo: str = Form(""),
    numero_serie: str = Form(""),
    defeito_relatado: str = Form(""),
    servicos_ids: str = Form(""),
    pecas_ids: str = Form(""),
    valor_servico: float = Form(0),
    valor_pecas: float = Form(0),
    valor_total: float = Form(0),
    data_entrada: str = Form(""),
    data_saida: str = Form(None),
    status: str = Form(...),
    tecnico: str = Form(""),
    autorizado_por: str = Form(""),
    numero_requisicao: str = Form(""),
    observacao: str = Form(""),
):
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)
    
    marca = None
    if marca_id:
        m = db.query(MarcaProduto).get(marca_id)
        marca = m.nome if m else None
    
    ordem.cliente_id = cliente_id
    ordem.equipamento = equipamento
    ordem.marca = marca
    ordem.modelo = modelo
    ordem.numero_serie = numero_serie
    ordem.defeito_relatado = defeito_relatado
    
    # Montar serviços executados da lista
    if servicos_ids:
        ids_servicos = [int(s) for s in servicos_ids.split(',') if s.isdigit()]
        servicos = db.query(Produto).filter(Produto.id.in_(ids_servicos), Produto.tipo == 'servico').all()
        ordem.servicos_executados = ', '.join([s.nome for s in servicos])
        if not ordem.valor_servico or ordem.valor_servico == 0:
            ordem.valor_servico = sum([s.preco for s in servicos])
    
    # Montar peças utilizadas da lista
    if pecas_ids:
        ids_pecas = [int(p) for p in pecas_ids.split(',') if p.isdigit()]
        pecas = db.query(Produto).filter(Produto.id.in_(ids_pecas), Produto.tipo == 'produto').all()
        ordem.pecas_utilizadas = ', '.join([p.nome for p in pecas])
        if not ordem.valor_pecas or ordem.valor_pecas == 0:
            ordem.valor_pecas = sum([p.preco for p in pecas])
    
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
def imprimir_ordem(request: Request, ordem_id: int, db: Session = Depends(get_db), tipo: str = Query("comum")):
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)
    empresa = db.query(Empresa).first()
    from datetime import datetime
    return request.app.state.templates.TemplateResponse(
        "ordens_servico/imprimir.html",
        {"request": request, "ordem": ordem, "empresa": empresa, "datetime": datetime, "tipo_impressao": tipo}
    )


@router.post("/{ordem_id}/excluir")
def excluir_ordem(request: Request, ordem_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    empresa = db.query(Empresa).first()
    if not empresa or not empresa.senha_admin or senha != empresa.senha_admin:
        return JSONResponse({"erro": "Senha inválida"}, status_code=403)
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if ordem:
        db.delete(ordem)
        db.commit()
    return RedirectResponse(url="/ordens-servico", status_code=303)

import json
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime, date
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from database import get_db
from models import OrdemServico, Cliente, Empresa, StatusOS, Produto, MarcaProduto, CategoriaProduto
from app.core.security import confirma_senha_usuario
from services.audit import registrar_auditoria

router = APIRouter(prefix="/ordens-servico", tags=["Ordens de Serviço"])


def _normalizar_status(valor):
    """Converte o valor vindo do formulario para o membro StatusOS correto.
    O form antigo envia 'CONCLUIDA' (legado) mas o Enum usa FINALIZADA; o
    banco armazena em minusculas, entao atribuir a string maiuscula direto
    quebra o INSERT/UPDATE (InvalidTextRepresentation)."""
    ALIASES = {
        "CONCLUIDA": "FINALIZADA", "CONCLUIDO": "FINALIZADA",
        "FECHADA": "FINALIZADA", "FECHADO": "FINALIZADA",
        "ABERTA": "ABERTA", "CANCELADA": "CANCELADA",
        "EM_ANDAMENTO": "EM_ANDAMENTO", "FINALIZADA": "FINALIZADA",
        "ANDAMENTO": "EM_ANDAMENTO",
    }
    if valor is None:
        return StatusOS.ABERTA
    if isinstance(valor, StatusOS):
        return valor
    chave = str(valor).strip().upper()
    nome = ALIASES.get(chave, chave)
    try:
        return StatusOS[nome]
    except KeyError:
        try:
            return StatusOS(valor)
        except ValueError:
            return StatusOS.ABERTA


@router.get("/")
def listar_ordens(
    request: Request, db: Session = Depends(get_db),
    status_filtro: str = Query(""), busca: str = Query(""),
    page: int = Query(1), per_page: int = Query(20),
    sort: str = Query(""), ordem: str = Query(""),
):
    query = db.query(OrdemServico).options(selectinload(OrdemServico.cliente)).join(Cliente)
    if status_filtro:
        try:
            status_enum = StatusOS(status_filtro)
            query = query.filter(OrdemServico.status == status_enum.name)
        except ValueError:
            query = query.filter(OrdemServico.status == status_filtro)
    if busca:
        query = query.filter(
            OrdemServico.equipamento.ilike(f"%{busca}%") |
            Cliente.nome.ilike(f"%{busca}%") |
            OrdemServico.defeito_relatado.ilike(f"%{busca}%")
        )

    # Ordenação por colunas principais
    sort_map = {
        "cliente": Cliente.nome,
        "equipamento": OrdemServico.equipamento,
        "entrada": OrdemServico.data_entrada,
        "saida": OrdemServico.data_saida,
        "valor": OrdemServico.valor_total,
        "tecnico": OrdemServico.tecnico,
        "autorizado": OrdemServico.autorizado_por,
        "requisicao": OrdemServico.numero_requisicao,
        "status": OrdemServico.status,
    }
    order_col = sort_map.get(sort, OrdemServico.data_entrada)
    descendente = (ordem != "asc")
    query = query.order_by(order_col.desc() if descendente else order_col.asc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    ordens = query.offset(offset).limit(per_page).all()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    servicos = db.query(Produto).filter(Produto.tipo == 'servico').order_by(Produto.nome).all()
    pecas = db.query(Produto).filter(Produto.tipo == 'produto').order_by(Produto.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    marcas_json = [{"id": m.id, "nome": m.nome} for m in marcas]
    servicos_json = [{"id": s.id, "nome": s.nome, "codigo_lc116": s.codigo_lc116 or '', "preco": s.preco} for s in servicos]
    pecas_json = [{"id": p.id, "nome": p.nome, "preco": p.preco} for p in pecas]
    return request.app.state.templates.TemplateResponse(request, 
        "ordens_servico/listar.html",
        {"request": request, "ordens": ordens, "clientes": clientes, "marcas": marcas,
         "servicos": servicos, "pecas": pecas,
         "clientes_json": clientes_json, "marcas_json": marcas_json, "servicos_json": servicos_json, "pecas_json": pecas_json,
          "status_filtro": status_filtro, "busca": busca, "StatusOS": StatusOS,
          "sort": sort, "ordem": ordem,
          "page": page, "per_page": per_page, "total_pages": total_pages, "total_count": total}
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
    return request.app.state.templates.TemplateResponse(request, 
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
    return request.app.state.templates.TemplateResponse(request, 
        "ordens_servico/detalhe.html",
        {"request": request, "ordem": ordem, "clientes": clientes, "marcas": marcas,
         "servicos": servicos, "pecas": pecas, "StatusOS": StatusOS,
         "clientes_json": clientes_json, "marcas_json": marcas_json,
         "servicos_json": servicos_json, "pecas_json": pecas_json,
         "os_pecas": ordem.os_pecas if hasattr(ordem, "os_pecas") else []}
    )


@router.get("/{ordem_id}/editar")
def editar_ordem_form(request: Request, ordem_id: int, db: Session = Depends(get_db)):
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    categorias = db.query(CategoriaProduto).order_by(CategoriaProduto.nome).all()
    servicos = db.query(Produto).filter(
        Produto.tipo == 'servico', Produto.situacao == 'A'
    ).order_by(Produto.nome).all()
    pecas = db.query(Produto).filter(
        Produto.tipo == 'produto', Produto.situacao == 'A'
    ).order_by(Produto.nome).all()
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    marcas_json = [{"id": m.id, "nome": m.nome} for m in marcas]
    categorias_json = [{"id": c.id, "nome": c.nome} for c in categorias]
    servicos_json = [{"id": s.id, "nome": s.nome, "codigo_lc116": s.codigo_lc116 or '', "preco": s.preco, "categoria_id": s.categoria_id} for s in servicos]
    pecas_json = [{"id": p.id, "nome": p.nome, "preco": p.preco, "categoria_id": p.categoria_id} for p in pecas]

    # Parse existing items data (JSON or fallback to text)
    servicos_existentes = []
    if ordem.servicos_executados:
        try:
            servicos_existentes = json.loads(ordem.servicos_executados)
            if isinstance(servicos_existentes, str):
                servicos_existentes = []
        except (json.JSONDecodeError, TypeError):
            servicos_existentes = []

    pecas_existentes = []
    if ordem.pecas_utilizadas:
        try:
            pecas_existentes = json.loads(ordem.pecas_utilizadas)
            if isinstance(pecas_existentes, str):
                pecas_existentes = []
        except (json.JSONDecodeError, TypeError):
            pecas_existentes = []

    return request.app.state.templates.TemplateResponse(request, 
        "ordens_servico/editar.html",
        {"request": request, "ordem": ordem, "clientes": clientes, "marcas": marcas,
         "categorias": categorias, "servicos": servicos, "pecas": pecas,
         "clientes_json": clientes_json, "marcas_json": marcas_json,
         "categorias_json": categorias_json,
         "servicos_json": servicos_json, "pecas_json": pecas_json,
         "servicos_existentes": json.dumps(servicos_existentes, ensure_ascii=False),
         "pecas_existentes": json.dumps(pecas_existentes, ensure_ascii=False),
         "StatusOS": StatusOS}
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
    servicos_json_data: str = Form(""),
    pecas_json_data: str = Form(""),
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
    
    # Montar serviços executados da lista (JSON estruturado)
    if servicos_json_data:
        try:
            itens_servico = json.loads(servicos_json_data)
            ordem.servicos_executados = json.dumps([{
                "id": s.get("id"), "nome": s.get("nome", ""),
                "qtd": float(s.get("qtd", 1)), "preco": float(s.get("preco", 0))
            } for s in itens_servico], ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Montar peças utilizadas da lista (JSON estruturado)
    if pecas_json_data:
        try:
            itens_pecas = json.loads(pecas_json_data)
            ordem.pecas_utilizadas = json.dumps([{
                "id": p.get("id"), "nome": p.get("nome", ""),
                "qtd": float(p.get("qtd", 1)), "preco": float(p.get("preco", 0))
            } for p in itens_pecas], ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass
    
    ordem.valor_servico = valor_servico
    ordem.valor_pecas = valor_pecas
    ordem.valor_total = valor_total
    ordem.data_entrada = date.fromisoformat(data_entrada) if data_entrada else ordem.data_entrada
    ordem.data_saida = date.fromisoformat(data_saida) if data_saida else None
    ordem.status = _normalizar_status(status)
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
    return request.app.state.templates.TemplateResponse(request, 
        "ordens_servico/imprimir.html",
        {"request": request, "ordem": ordem, "empresa": empresa, "datetime": datetime, "tipo_impressao": tipo}
    )


@router.post("/{ordem_id}/excluir")
def excluir_ordem(request: Request, ordem_id: int, db: Session = Depends(get_db), senha: str = Form("")):
    if not confirma_senha_usuario(request, db, senha):
        return JSONResponse({"erro": "Senha inválida ou usuário não autorizado"}, status_code=403)
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if ordem:
        ordem_descricao = ordem.equipamento or f"OS #{ordem_id}"
        ordem.status = StatusOS.CANCELADA
        db.commit()
        registrar_auditoria(
            db, request.session.get("user_id"), "excluir",
            "ordem_servico", ordem_id, f"OS: {ordem_descricao}",
            request.client.host if request.client else None
        )
    return RedirectResponse(url="/ordens-servico", status_code=303)

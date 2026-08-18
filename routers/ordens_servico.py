import json
from decimal import Decimal
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime, date
from sqlalchemy import or_, and_, text as sa_text
from sqlalchemy.orm import selectinload

from database import get_db
from models import OrdemServico, Cliente, Empresa, StatusOS, Produto, MarcaProduto, CategoriaProduto, ContaReceber, StatusConta
from models_nfe import NFe, NFSe
from app.core.security import confirma_senha_usuario
from services.audit import registrar_auditoria
from services.parcelamento import gerar_contas_receber
from services.email_service import enviar_email

router = APIRouter(prefix="/ordens-servico", tags=["Ordens de Serviço"])

# Transicoes de status permitidas. O fluxo e dirigido:
#   aberta -> em_andamento -> finalizada -> concluida (entregue)
# 'cancelada' e terminal e pode ser alcancada a partir de qualquer estado ativo.
# Nao se permite retroceder (ex.: concluida -> finalizada) nem reabrir uma
# OS cancelada. Para corrigir uma OS concluida errada, o procedimento e
# cancelar (apos void das notas/cobrancas) e abrir uma nova.
TRANSICOES_STATUS_VALIDAS = {
    "aberta": {"em_andamento", "cancelada"},
    "em_andamento": {"finalizada", "cancelada"},
    "finalizada": {"concluida", "cancelada"},
    "concluida": {"cancelada"},
    "cancelada": set(),
}

_STATUS_EMITIDOS_NFE = {"issued", "queued", "pendente"}
_STATUS_EMITIDOS_NFSE = {"autorizada", "pendente", "em_processamento"}

# Formas de pagamento que NAO exigem nota fiscal transmitida: permitem gerar a
# cobranca (recibo/fatura) direto da OS, para PIX, dinheiro, cartao, a vista,
# a prazo, garantia, etc. A cobranca agrupada tradicional (boleto vinculado a
# nota) continua funcionando quando ha nota transmitida.
_FORMAS_SEM_NOTA = {
    "pix", "dinheiro", "cartao_credito", "cartao_debito", "avista",
    "aprazo", "garantia",
}


def _garantir_valor_enum(tipo_enum, valor):
    """Garante que 'valor' exista no tipo ENUM nativo do PostgreSQL.

    Resolve o erro 'invalid input value for enum' ao concluir uma OS cujo
    label (ex.: 'concluida') ainda nao foi adicionado ao banco (cenarios de
    restore de dump, migracao que nao rodou ou pool de conexoes que cacheou o
    enum antigo). Usa uma conexao autocommit separada, pois ALTER TYPE ADD
    VALUE nao pode rodar dentro de uma transacao.
    """
    try:
        from database import engine
        if engine.dialect.name != "postgresql":
            return
        raw = engine.connect()
        conn = raw.execution_options(isolation_level="AUTOCOMMIT")
        try:
            existe = conn.execute(
                sa_text(
                    "SELECT 1 FROM pg_enum WHERE enumtypid = "
                    "(SELECT oid FROM pg_type WHERE typname=:t) AND enumlabel=:v"
                ).bindparams(t=tipo_enum, v=valor)
            ).scalar()
            if not existe:
                conn.execute(
                    sa_text(f"ALTER TYPE {tipo_enum} ADD VALUE IF NOT EXISTS '{valor}'")
                )
        finally:
            conn.close()
            raw.close()
    except Exception as e:
        print(f"[ENUM] nao foi possivel garantir {tipo_enum}.{valor}: {e}")


def _normalizar_status(valor):
    """Converte o valor vindo do formulario para o membro StatusOS correto.
    O form antigo envia 'CONCLUIDA' (legado) mas o Enum usa FINALIZADA; o
    banco armazena em minusculas, entao atribuir a string maiuscula direto
    quebra o INSERT/UPDATE (InvalidTextRepresentation)."""
    ALIASES = {
        "CONCLUIDA": "CONCLUIDA", "CONCLUIDO": "CONCLUIDA",
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


def _os_tem_itens(ordem):
    """Verifica se a OS possui servicos e/ou pecas lancados (para nao permitir
    cobranca/recibo de uma OS vazia). Considera tanto os JSONs de servicos/pecas
    quanto as OSPeca (baixa de estoque)."""
    def _json_com_itens(texto):
        if not texto:
            return False
        if isinstance(texto, (list, dict)):
            return len(texto) > 0
        t = str(texto).strip()
        return t not in ("", "[]", "null", "None")
    if _json_com_itens(getattr(ordem, "servicos_executados", None)):
        return True
    if _json_com_itens(getattr(ordem, "pecas_utilizadas", None)):
        return True
    os_pecas = getattr(ordem, "os_pecas", None)
    try:
        if os_pecas is not None and len(os_pecas) > 0:
            return True
    except TypeError:
        pass
    return False


def _buscar_pecas(db):
    """Retorna os produtos que são peças (tipo='produto'), restritos à categoria
    'Peças' quando ela existir, para o seletor da OS não listar itens que não
    são peças (insumos, materiais etc.)."""
    query = db.query(Produto).filter(Produto.tipo == 'produto')
    cat = db.query(CategoriaProduto).filter(CategoriaProduto.nome.ilike("%peça%")).first()
    if cat:
        query = query.filter(Produto.categoria_id == cat.id)
    return query.order_by(Produto.nome).all()


@router.get("/")
def listar_ordens(
    request: Request, db: Session = Depends(get_db),
    status_filtro: str = Query(""), busca: str = Query(""),
    page: int = Query(1), per_page: int = Query(20),
    sort: str = Query(""), ordem: str = Query(""),
):
    query = db.query(OrdemServico).options(selectinload(OrdemServico.cliente)).join(Cliente)
    if status_filtro:
        status_enum = None
        try:
            status_enum = StatusOS(status_filtro)  # casa por valor ("aberta")
        except ValueError:
            status_enum = StatusOS.__members__.get(status_filtro.upper())  # por nome
        if status_enum is not None:
            query = query.filter(OrdemServico.status == status_enum)
        else:
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
    pecas = _buscar_pecas(db)
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    marcas_json = [{"id": m.id, "nome": m.nome} for m in marcas]
    servicos_json = [{"id": s.id, "nome": s.nome, "codigo_lc116": s.codigo_lc116 or '', "preco": float(s.preco or 0)} for s in servicos]
    pecas_json = [{"id": p.id, "nome": p.nome, "preco": float(p.preco or 0)} for p in pecas]
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
    pecas = _buscar_pecas(db)
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    marcas_json = [{"id": m.id, "nome": m.nome} for m in marcas]
    servicos_json = [{"id": s.id, "nome": s.nome, "codigo_lc116": s.codigo_lc116 or '', "preco": float(s.preco or 0)} for s in servicos]
    pecas_json = [{"id": p.id, "nome": p.nome, "preco": float(p.preco or 0)} for p in pecas]
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
    marca_id: str = Form(""),
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
        m = db.query(MarcaProduto).get(int(marca_id))
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
    ordem = db.query(OrdemServico).options(
        selectinload(OrdemServico.nfes),
        selectinload(OrdemServico.nfses),
    ).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    marcas = db.query(MarcaProduto).order_by(MarcaProduto.nome).all()
    servicos = db.query(Produto).filter(Produto.tipo == 'servico').order_by(Produto.nome).all()
    pecas = _buscar_pecas(db)
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    marcas_json = [{"id": m.id, "nome": m.nome} for m in marcas]
    servicos_json = [{"id": s.id, "nome": s.nome, "codigo_lc116": s.codigo_lc116 or '', "preco": float(s.preco or 0)} for s in servicos]
    pecas_json = [{"id": p.id, "nome": p.nome, "preco": float(p.preco or 0)} for p in pecas]

    # Cobrancas vinculadas as notas desta OS (agrupadas ou separadas) e os recibos
    # gerados direto da OS sem nota (nfe_id/nfse_id nulos, mas os_id preenchido).
    ids_nfe = [n.id for n in (ordem.nfes or [])]
    ids_nfse = [n.id for n in (ordem.nfses or [])]
    cobrancas = []
    filtros_cob = []
    if ids_nfe:
        filtros_cob.append(ContaReceber.nfe_id.in_(ids_nfe))
    if ids_nfse:
        filtros_cob.append(ContaReceber.nfse_id.in_(ids_nfse))
    # Recibos/faturas da OS sem nota fiscal (PIX, dinheiro, garantia, etc.)
    filtros_cob.append(
        and_(ContaReceber.os_id == ordem.id,
             ContaReceber.nfe_id.is_(None),
             ContaReceber.nfse_id.is_(None))
    )
    if filtros_cob:
        cobrancas = db.query(ContaReceber).options(
            selectinload(ContaReceber.nfe), selectinload(ContaReceber.nfse),
        ).filter(or_(*filtros_cob)).order_by(ContaReceber.data_vencimento).all()

    pode_gerar_cobranca = (
        any(n.status in _STATUS_EMITIDOS_NFE for n in (ordem.nfes or []))
        or any(n.status in _STATUS_EMITIDOS_NFSE for n in (ordem.nfses or []))
    )

    return request.app.state.templates.TemplateResponse(request, 
        "ordens_servico/detalhe.html",
        {"request": request, "ordem": ordem, "clientes": clientes, "marcas": marcas,
         "servicos": servicos, "pecas": pecas, "StatusOS": StatusOS,
         "cobrancas": cobrancas, "pode_gerar_cobranca": pode_gerar_cobranca,
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
    pecas = _buscar_pecas(db)
    clientes_json = [{"id": c.id, "nome": c.nome, "fantasia": c.fantasia or '', "cpf_cnpj": c.cpf_cnpj} for c in clientes]
    marcas_json = [{"id": m.id, "nome": m.nome} for m in marcas]
    categorias_json = [{"id": c.id, "nome": c.nome} for c in categorias]
    servicos_json = [{"id": s.id, "nome": s.nome, "codigo_lc116": s.codigo_lc116 or '', "preco": float(s.preco or 0), "categoria_id": s.categoria_id} for s in servicos]
    pecas_json = [{"id": p.id, "nome": p.nome, "preco": float(p.preco or 0), "categoria_id": p.categoria_id} for p in pecas]

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
          "os_pecas": ordem.os_pecas if hasattr(ordem, "os_pecas") else [],
          "StatusOS": StatusOS}
    )


def _sincronizar_pecas_estoque_os(db: Session, ordem, itens):
    """Unifica pecas da OS com o estoque: a partir do unico campo 'Pecas' da edicao,
    cria/atualiza OSPeca e faz baixa (ou estorna ao remover). Evita ter dois campos
    de pecas diferentes para a mesma OS."""
    from models_estoque import OSPeca
    from models import Produto
    desejados = {}
    for it in (itens or []):
        pid = it.get("id")
        if not pid:
            continue
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        desejados[pid] = {
            "qtd": float(it.get("qtd", 1) or 1),
            "preco": float(it.get("preco", 0) or 0),
        }
    existentes = {op.produto_id: op for op in ordem.os_pecas}
    # Remove os que nao estao mais na lista (estorna estoque)
    for pid, op in existentes.items():
        if pid not in desejados:
            prod = db.query(Produto).filter(Produto.id == pid).first()
            if prod:
                prod.estoque = float(prod.estoque or 0) + float(op.quantidade or 0)
            db.delete(op)
    # Cria/atualiza os desejados (baixa o delta no estoque)
    for pid, d in desejados.items():
        prod = db.query(Produto).filter(Produto.id == pid).first()
        op = existentes.get(pid)
        if op:
            delta = d["qtd"] - float(op.quantidade or 0)
            op.quantidade = d["qtd"]
            op.valor_unitario = d["preco"]
            if prod:
                prod.estoque = float(prod.estoque or 0) - delta
        else:
            qtd_baixa = d["qtd"]
            if prod:
                estoque_atual = float(prod.estoque or 0)
                if qtd_baixa > estoque_atual:
                    qtd_baixa = estoque_atual  # nao deixa estoque negativo na criacao
                prod.estoque = estoque_atual - qtd_baixa
            db.add(OSPeca(os_id=ordem.id, produto_id=pid,
                          quantidade=qtd_baixa, valor_unitario=d["preco"]))
    db.flush()


@router.post("/{ordem_id}/editar")
def atualizar_ordem(
    request: Request, ordem_id: int, db: Session = Depends(get_db),
    cliente_id: int = Form(...),
    equipamento: str = Form(...),
    marca_id: str = Form(""),
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
    cobranca_separada: str = Form(""),
):
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)
    
    marca = None
    if marca_id:
        m = db.query(MarcaProduto).get(int(marca_id))
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
    
    # Montar peças utilizadas da lista (JSON estruturado) e fazer baixa de estoque
    itens_pecas = []
    if pecas_json_data:
        try:
            itens_pecas = json.loads(pecas_json_data)
        except (json.JSONDecodeError, TypeError):
            itens_pecas = []
    ordem.pecas_utilizadas = json.dumps([{
        "id": p.get("id"), "nome": p.get("nome", ""),
        "qtd": float(p.get("qtd", 1)), "preco": float(p.get("preco", 0))
    } for p in itens_pecas], ensure_ascii=False)
    # Unico campo de pecas: a baixa de estoque eh feita a partir daqui
    try:
        _sincronizar_pecas_estoque_os(db, ordem, itens_pecas)
    except Exception:
        pass
    
    ordem.valor_servico = valor_servico
    ordem.valor_pecas = valor_pecas
    ordem.valor_total = (valor_servico or 0) + (valor_pecas or 0)
    ordem.data_entrada = date.fromisoformat(data_entrada) if data_entrada else ordem.data_entrada
    # Status: respeita o fluxo dirigido (mesmas regras do endpoint /status), para
    # que a edição não reverta uma OS concluída para "aberta" nem permita
    # transições inválidas. Se a transição não for permitida, mantém o status
    # atual e apenas alerta (os demais campos são salvos normalmente).
    novo_status = _normalizar_status(status)
    if ordem.status != novo_status:
        permitidos = TRANSICOES_STATUS_VALIDAS.get(ordem.status.value, set())
        if novo_status.value not in permitidos:
            request.session["error"] = (
                f"Transição de status inválida: não é possível mudar de "
                f"'{ordem.status.value}' para '{novo_status.value}'. Status mantido."
            )
            novo_status = ordem.status
        else:
            ordem.status = novo_status
            if novo_status == StatusOS.CONCLUIDA and not ordem.data_saida:
                ordem.data_saida = date.today()
    else:
        ordem.status = novo_status
    ordem.data_saida = date.fromisoformat(data_saida) if data_saida else ordem.data_saida
    ordem.tecnico = tecnico
    ordem.autorizado_por = autorizado_por
    ordem.numero_requisicao = numero_requisicao
    ordem.observacao = observacao
    ordem.cobranca_separada = bool(cobranca_separada)
    ordem.updated_at = datetime.now()
    ordem.bling_pending_sync = True
    db.commit()
    return RedirectResponse(url=f"/ordens-servico/{ordem_id}", status_code=303)


@router.post("/{ordem_id}/gerar-cobranca")
def gerar_cobranca_os(
    request: Request, ordem_id: int, db: Session = Depends(get_db),
    cobranca_separada: str = Form(""),
    forma_pagamento: str = Form("boleto"),
    num_parcelas: int = Form(1),
    primeiro_vencimento: str = Form(""),
    intervalo_dias: int = Form(30),
):
    """Gera a cobrança (ContaReceber) a partir das notas fiscais da OS.

    - Com notas transmitidas (padrao): cobranca agrupada (UMA ContaReceber
      referenciando NFe + NFSe, valor = NFe.valor_total + NFSe.valor_liquido) ou
      separada (uma por nota). Se forma_pagamento = boleto, boleto unico.
    - SEM notas transmitidas (recibo/fatura): quando a forma de pagamento esta em
      _FORMAS_SEM_NOTA (pix, dinheiro, cartao, avista, aprazo, garantia), gera-se
      um recibo a partir de ordem.valor_total, sem vincular NFe/NFSe. Cobre casos
      de PIX/dinheiro direto ou conserto em garantia (sem emissao de NFS).
    - Garantia: valor da cobranca = 0 (sem onus ao cliente), apenas registro.
    A preferencia (cobranca_separada) e persistida na OS para proximas emissoes.
    Retorna JSON quando o header Accept for 'application/json' (usado pelo modal
    de conclusao), ou RedirectResponse em navegacao normal.
    """
    aceita_json = (request.headers.get("accept") or "").startswith("application/json")

    def _resposta(status_code, ok, mensagem, redirect=None):
        if aceita_json:
            return JSONResponse(
                {"ok": ok, "mensagem": mensagem, "redirect": redirect or f"/ordens-servico/{ordem_id}"},
                status_code=status_code,
            )
        if ok:
            request.session["message"] = mensagem
        else:
            request.session["error"] = mensagem
        return RedirectResponse(url=f"/ordens-servico/{ordem_id}", status_code=303)

    ordem = db.query(OrdemServico).options(
        selectinload(OrdemServico.nfes),
        selectinload(OrdemServico.nfses),
    ).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)

    nfe = next((n for n in ordem.nfes if n.status in _STATUS_EMITIDOS_NFE), None)
    nfse = next((n for n in ordem.nfses if n.status in _STATUS_EMITIDOS_NFSE), None)
    sem_notas = (not nfe and not nfse)

    if sem_notas and forma_pagamento not in _FORMAS_SEM_NOTA:
        return _resposta(
            400, False,
            "Não há notas fiscais transmitidas para esta OS. Emita a NFe/NFSe primeiro "
            "ou escolha uma forma de pagamento sem nota (PIX, dinheiro, cartão, à vista, "
            "a prazo ou garantia)."
        )

    # Recibo sem nota: a OS precisa ter servicos/pecas lancados e (para formas
    # com cobranca) valor total > 0. Nao permitir gerar recibo zerado de OS vazia.
    if sem_notas:
        if not _os_tem_itens(ordem):
            return _resposta(
                400, False,
                "A OS não possui serviços nem peças lançados. Lance ao menos um "
                "serviço ou peça antes de gerar a cobrança/recibo."
            )
        if forma_pagamento == "garantia":
            valor_total = Decimal("0")
        else:
            valor_total = Decimal(str(ordem.valor_total or 0))
            if valor_total <= 0:
                return _resposta(
                    400, False,
                    "O valor total da OS está zerado. Informe os valores de serviços/peças "
                    "(ou use a forma 'Garantia' para conserto sem cobrança)."
                )
    else:
        valor_total = None  # calculado abaixo com base nas notas

    # Guarda anti-duplicacao:
    # - Com notas: bloqueia se ja houver cobranca ativa vinculada a qualquer nota.
    # - Sem notas (recibo): bloqueia se ja houver recibo (os_id = OS, sem nfe/nfse).
    if sem_notas:
        cobranca_existente = db.query(ContaReceber).filter(
            ContaReceber.status.notin_([StatusConta.CANCELADO, StatusConta.EXCLUIDO]),
            ContaReceber.os_id == ordem.id,
            ContaReceber.nfe_id.is_(None),
            ContaReceber.nfse_id.is_(None),
        ).first()
        if cobranca_existente:
            return _resposta(400, False, "Já existe um recibo/cobrança para esta OS.")
    else:
        ids_nfe = [n.id for n in ordem.nfes]
        ids_nfse = [n.id for n in ordem.nfses]
        filtros_existentes = []
        if ids_nfe:
            filtros_existentes.append(ContaReceber.nfe_id.in_(ids_nfe))
        if ids_nfse:
            filtros_existentes.append(ContaReceber.nfse_id.in_(ids_nfse))
        if filtros_existentes:
            cobranca_existente = db.query(ContaReceber).filter(
                ContaReceber.status.notin_([StatusConta.CANCELADO, StatusConta.EXCLUIDO]),
                or_(*filtros_existentes),
            ).first()
            if cobranca_existente:
                return _resposta(400, False, "Já existe cobrança para as notas desta OS.")

    # Persiste a preferencia para futuras emissoes
    ordem.cobranca_separada = bool(cobranca_separada)
    db.flush()

    try:
        venc = date.fromisoformat(primeiro_vencimento) if primeiro_vencimento else (ordem.data_saida or date.today())
    except ValueError:
        venc = ordem.data_saida or date.today()

    if sem_notas:
        # Recibo/fatura sem nota fiscal (PIX, dinheiro, cartao, garantia, etc.)
        # valor_total ja validado acima (0 apenas para garantia).
        forma = forma_pagamento or "dinheiro"
        descricao = f"Recibo OS #{ordem_id} ({forma})" + (" — sem nota fiscal" if forma != "garantia" else " — garantia")
        contas = gerar_contas_receber(
            db, cliente_id=ordem.cliente_id,
            descricao=descricao,
            valor_total=valor_total,
            primeiro_vencimento=venc, num_parcelas=num_parcelas,
            intervalo_dias=intervalo_dias,
            forma_pagamento=forma,
            observacao=f"Cobrança/recibo da OS #{ordem_id} sem emissão de NFS-e/NFe.",
            nfe_id=None, nfse_id=None, os_id=ordem.id,
        )
        msg = f"{len(contas)} recibo(s) gerado(s) para a OS #{ordem_id} (sem nota fiscal)."
    elif bool(cobranca_separada):
        # Uma conta por nota (cliente quer separado)
        contas = []
        if nfe:
            contas += gerar_contas_receber(
                db, cliente_id=ordem.cliente_id,
                descricao=f"NFe #{nfe.numero or nfe.id}",
                valor_total=nfe.valor_total or 0,
                primeiro_vencimento=venc, num_parcelas=num_parcelas,
                intervalo_dias=intervalo_dias,
                forma_pagamento=forma_pagamento or "NFe",
                observacao=f"Gerado da NFe #{nfe.id} (OS #{ordem_id})",
                numero_documento=str(nfe.numero) if nfe.numero else None,
                nfe_id=nfe.id, os_id=ordem.id,
            )
        if nfse:
            contas += gerar_contas_receber(
                db, cliente_id=ordem.cliente_id,
                descricao=f"NFSe #{nfse.numero or nfse.id}",
                valor_total=nfse.valor_liquido,
                primeiro_vencimento=venc, num_parcelas=num_parcelas,
                intervalo_dias=intervalo_dias,
                forma_pagamento=forma_pagamento or "NFSe",
                observacao=f"Gerado da NFSe #{nfse.id} (OS #{ordem_id})",
                nfse_id=nfse.id, os_id=ordem.id,
            )
        msg = f"{len(contas)} cobrança(s) separada(s) gerada(s) para a OS #{ordem_id}."
    else:
        # Cobrança única agrupando NFe + NFSe (e um único boleto se boleto)
        valor_total = Decimal("0")
        if nfe:
            valor_total += Decimal(str(nfe.valor_total or 0))
        if nfse:
            valor_total += Decimal(str(nfse.valor_liquido))
        if nfe and nfse:
            descricao = f"Cobrança OS #{ordem_id} (NFe #{nfe.numero or nfe.id} + NFSe #{nfse.numero or nfse.id})"
        elif nfe:
            descricao = f"NFe #{nfe.numero or nfe.id} (OS #{ordem_id})"
        else:
            descricao = f"NFSe #{nfse.numero or nfse.id} (OS #{ordem_id})"
        contas = gerar_contas_receber(
            db, cliente_id=ordem.cliente_id,
            descricao=descricao,
            valor_total=valor_total,
            primeiro_vencimento=venc, num_parcelas=num_parcelas,
            intervalo_dias=intervalo_dias,
            forma_pagamento=forma_pagamento or "boleto",
            observacao=f"Cobrança agrupada da OS #{ordem_id}",
            nfe_id=nfe.id if nfe else None,
            nfse_id=nfse.id if nfse else None,
            os_id=ordem.id,
        )
        msg = f"{len(contas)} cobrança(s) agrupada(s) gerada(s) para a OS #{ordem_id}."

    db.commit()
    return _resposta(200, True, msg)


@router.get("/{ordem_id}/recibo/{conta_id}")
def recibo_ordem(request: Request, ordem_id: int, conta_id: int, db: Session = Depends(get_db), tipo: str = Query("a4")):
    """Visualiza/imprime o recibo (cobranca sem nota fiscal) gerado da OS.
    Suporta tipo='a4' (padrao) e tipo='termica' (80mm), espelhando o padrao
    de impressao da Ordem de Servico."""
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)
    conta = db.query(ContaReceber).options(
        selectinload(ContaReceber.cliente),
    ).filter(ContaReceber.id == conta_id, ContaReceber.os_id == ordem_id).first()
    if not conta:
        return RedirectResponse(url=f"/ordens-servico/{ordem_id}", status_code=303)
    empresa = db.query(Empresa).first()
    template = "ordens_servico/recibo_termica.html" if tipo == "termica" else "ordens_servico/recibo.html"
    return request.app.state.templates.TemplateResponse(request,
        template,
        {"request": request, "ordem": ordem, "conta": conta,
         "empresa": empresa, "cliente": conta.cliente,
         "now": datetime.now(), "email": False}
    )


@router.post("/{ordem_id}/recibo/{conta_id}/enviar-email")
def enviar_email_recibo(request: Request, ordem_id: int, conta_id: int, db: Session = Depends(get_db)):
    """Envia o recibo (cobranca sem nota fiscal) por e-mail ao cliente."""
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)
    conta = db.query(ContaReceber).options(
        selectinload(ContaReceber.cliente),
    ).filter(ContaReceber.id == conta_id, ContaReceber.os_id == ordem_id).first()
    if not conta:
        request.session["error"] = "Recibo não encontrado para esta OS."
        return RedirectResponse(url=f"/ordens-servico/{ordem_id}", status_code=303)
    cliente = conta.cliente
    if not cliente or not cliente.email:
        request.session["error"] = "O cliente não possui e-mail cadastrado."
        return RedirectResponse(url=f"/ordens-servico/{ordem_id}", status_code=303)
    empresa = db.query(Empresa).first()
    try:
        corpo = request.app.state.templates.env.get_template("ordens_servico/recibo.html").render(
            request=request, ordem=ordem, conta=conta, empresa=empresa,
            cliente=cliente, now=datetime.now(), email=True,
        )
    except Exception as e:
        request.session["error"] = f"Erro ao montar o recibo: {e}"
        return RedirectResponse(url=f"/ordens-servico/{ordem_id}", status_code=303)
    resultado = enviar_email(
        cliente.email,
        f"Recibo da Ordem de Serviço #{ordem_id}",
        corpo, db=db,
    )
    if resultado.get("success"):
        conta.email_enviado = True
        conta.data_envio_email = datetime.now()
        db.commit()
        request.session["message"] = f"Recibo enviado para {cliente.email}."
    else:
        request.session["error"] = f"Falha ao enviar e-mail: {resultado.get('error')}"
    return RedirectResponse(url=f"/ordens-servico/{ordem_id}", status_code=303)


@router.get("/{ordem_id}/imprimir")
def imprimir_ordem(request: Request, ordem_id: int, db: Session = Depends(get_db), tipo: str = Query("a4")):
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return RedirectResponse(url="/ordens-servico", status_code=303)
    empresa = db.query(Empresa).first()

    def _parse_itens(texto):
        if not texto:
            return []
        try:
            dados = json.loads(texto)
            if isinstance(dados, list):
                return [
                    {
                        "nome": (i.get("nome") or "") if isinstance(i, dict) else str(i),
                        "qtd": float((i.get("qtd") if isinstance(i, dict) else 1) or 1),
                        "preco": float((i.get("preco") if isinstance(i, dict) else 0) or 0),
                    }
                    for i in dados
                ]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return None  # texto livre (legado) -> renderizado como paragrafo

    servicos = _parse_itens(ordem.servicos_executados)
    pecas = _parse_itens(ordem.pecas_utilizadas)
    # Fallback: pecas vinculadas via baixa de estoque quando o campo JSON esta vazio
    if (pecas is None or len(pecas) == 0) and getattr(ordem, "os_pecas", None):
        pecas = [
            {
                "nome": (p.produto.nome if p.produto else "Peça"),
                "qtd": float(p.quantidade or 1),
                "preco": float(p.valor_unitario or 0),
            }
            for p in ordem.os_pecas
        ]

    templates_por_tipo = {
        "a4": "ordens_servico/imprimir_a4.html",
        "orcamento": "ordens_servico/imprimir_a4.html",
        "termica": "ordens_servico/imprimir_termica.html",
    }
    eh_orcamento = tipo == "orcamento"
    template = templates_por_tipo.get(tipo, "ordens_servico/imprimir_a4.html")
    return request.app.state.templates.TemplateResponse(request, template, {
        "request": request, "ordem": ordem, "empresa": empresa,
        "servicos": servicos, "pecas": pecas,
        "tipo_impressao": tipo, "eh_orcamento": eh_orcamento, "now": datetime.now(),
    })


@router.post("/{ordem_id}/excluir")
def excluir_ordem(
    request: Request, ordem_id: int, db: Session = Depends(get_db),
    senha: str = Form(""), excluir_contas: str = Form(""),
):
    if not confirma_senha_usuario(request, db, senha):
        return JSONResponse({"erro": "Senha inválida ou usuário não autorizado"}, status_code=403)
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return JSONResponse({"erro": "OS não encontrada"}, status_code=404)
    try:
        ordem_descricao = ordem.equipamento or f"OS #{ordem_id}"
        # NFe possui FK para a OS sem cascade: excluir definitivamente quebra o
        # integridade. Nesse caso, cancelamos a OS em vez de apagar.
        if ordem.nfes:
            ordem.status = StatusOS.CANCELADA
            db.commit()
            registrar_auditoria(
                db, request.session.get("user_id"), "cancelar",
                "ordem_servico", ordem_id, f"OS: {ordem_descricao} (NFe vinculada)",
                request.client.host if request.client else None
            )
            return JSONResponse({
                "ok": True, "redirect": "/ordens-servico",
                "message": "OS cancelada (possui NFe emitida e não pode ser excluída).",
            })
        db.delete(ordem)
        db.commit()
        registrar_auditoria(
            db, request.session.get("user_id"), "excluir",
            "ordem_servico", ordem_id, f"OS: {ordem_descricao}",
            request.client.host if request.client else None
        )
        return JSONResponse({"ok": True, "redirect": "/ordens-servico", "message": "OS excluída."})
    except Exception:
        db.rollback()
        return JSONResponse({"erro": "Erro interno ao excluir a OS"}, status_code=500)


@router.post("/{ordem_id}/status")
def atualizar_status_ordem(
    request: Request, ordem_id: int, db: Session = Depends(get_db),
    novo_status: str = Form(...), data_saida: str = Form(None),
):
    """Transiciona o status da OS no fluxo de finalizacao (Aberta -> Em Andamento
    -> Finalizada -> Concluida, ou Cancelada). Ao concluir (entregar), registra a
    data de saida. Apenas usuarios autenticados."""
    ordem = db.query(OrdemServico).filter(OrdemServico.id == ordem_id).first()
    if not ordem:
        return JSONResponse({"erro": "OS não encontrada"}, status_code=404)
    # Normaliza o valor vindo do formulario (pode chegar em minusculas ou
    # maiusculas/legado) para o membro StatusOS correto. Nunca grava o NOME
    # do enum (ex.: 'CONCLUIDA') nem um valor ausente no banco.
    status = _normalizar_status(novo_status)
    if status is None or not isinstance(status, StatusOS):
        return JSONResponse({"erro": "Status inválido"}, status_code=400)

    # Transicao de status invalida (ex.: concluida -> finalizada, ou reabrir cancelada)
    if ordem.status != status:
        permitidos = TRANSICOES_STATUS_VALIDAS.get(ordem.status.value, set())
        if status.value not in permitidos:
            return JSONResponse(
                {"erro": f"Transição inválida: não é possível mudar o status de "
                         f"'{ordem.status.value}' para '{status.value}'."},
                status_code=400,
            )

    # Cancelar uma OS concluida/existente exige antes void das notas e cobrancas
    if status == StatusOS.CANCELADA and ordem.status != StatusOS.CANCELADA:
        nfes_os = db.query(NFe).filter(NFe.os_id == ordem.id).all()
        nfses_os = db.query(NFSe).filter(NFSe.os_id == ordem.id).all()
        notas_emitidas = [n for n in nfes_os if n.status in _STATUS_EMITIDOS_NFE]
        notas_emitidas += [n for n in nfses_os if n.status in _STATUS_EMITIDOS_NFSE]
        if notas_emitidas:
            return JSONResponse(
                {"erro": "Não é possível cancelar: a OS possui nota(s) fiscal(is) emitida(s). "
                         "Cancele a(s) nota(s) primeiro."},
                status_code=400,
            )
        ids_nfe = [n.id for n in nfes_os]
        ids_nfse = [n.id for n in nfses_os]
        cobranca_ativa = db.query(ContaReceber).filter(
            ContaReceber.status.notin_([StatusConta.CANCELADO, StatusConta.EXCLUIDO]),
            or_(ContaReceber.nfe_id.in_(ids_nfe), ContaReceber.nfse_id.in_(ids_nfse)),
        ).first()
        if cobranca_ativa:
            return JSONResponse(
                {"erro": "Não é possível cancelar: a OS possui cobrança(s) ativa(s). "
                         "Cancele a(s) cobrança(s) primeiro."},
                status_code=400,
            )

    if ordem.status == status and not (status == StatusOS.CONCLUIDA and data_saida):
        return JSONResponse({"ok": True, "status": status.value, "redirect": f"/ordens-servico/{ordem_id}"})

    # Garante que o label do enum existe no banco antes de gravar (ex.:
    # 'concluida'), evitando o erro 'invalid input value for enum' ao concluir.
    if status == StatusOS.CONCLUIDA:
        _garantir_valor_enum("statusos", "concluida")
        # Libera a conexao atual (pode ter cacheado o enum antigo) e forca
        # reconexao para enxergar o novo label.
        try:
            db.rollback()
            from database import engine
            engine.dispose()
        except Exception:
            pass

    ordem.status = status
    # Ao concluir (entregar), registra a data de saida: a informada ou hoje.
    if status == StatusOS.CONCLUIDA:
        if data_saida:
            try:
                ordem.data_saida = date.fromisoformat(data_saida)
            except (ValueError, TypeError):
                if not ordem.data_saida:
                    ordem.data_saida = date.today()
        elif not ordem.data_saida:
            ordem.data_saida = date.today()
    ordem.updated_at = datetime.now()
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return JSONResponse(
            {"erro": f"Não foi possível atualizar o status da OS: {str(e)}"},
            status_code=500,
        )
    try:
        registrar_auditoria(
            db, request.session.get("user_id"), "alterar_status",
            "ordem_servico", ordem_id, f"Status -> {status.value}",
            request.client.host if request.client else None
        )
    except Exception:
        pass
    return JSONResponse({"ok": True, "status": status.value, "redirect": f"/ordens-servico/{ordem_id}"})

from fastapi import FastAPI, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date as date_func, datetime
import bcrypt

from database import engine, Base, get_db
from models import Cliente, Fornecedor, ContaPagar, ContaReceber, Assinatura, OrdemServico, Empresa, StatusConta, StatusOS, Produto, PedidoVenda, Usuario

Base.metadata.create_all(bind=engine)

# migrations for new columns on existing tables
with engine.connect() as conn:
    from sqlalchemy import text
    for table, col, dtype in [
        ("clientes", "bling_id", "INTEGER"),
        ("clientes", "bling_updated_at", "DATETIME"),
        ("clientes", "bling_pending_sync", "BOOLEAN"),
        ("fornecedores", "bling_id", "INTEGER"),
        ("fornecedores", "bling_updated_at", "DATETIME"),
        ("fornecedores", "bling_pending_sync", "BOOLEAN"),
        ("assinaturas", "bling_id", "INTEGER"),
        ("assinaturas", "bling_updated_at", "DATETIME"),
        ("assinaturas", "bling_pending_sync", "BOOLEAN"),
        ("assinaturas", "periodicidade", "INTEGER"),
        ("assinaturas", "situacao", "INTEGER"),
        ("ordens_servico", "bling_id", "INTEGER"),
        ("ordens_servico", "bling_updated_at", "DATETIME"),
        ("ordens_servico", "bling_pending_sync", "BOOLEAN"),
        ("empresa", "bling_token", "VARCHAR(200)"),
        ("empresa", "bling_client_id", "VARCHAR(200)"),
        ("empresa", "bling_client_secret", "VARCHAR(200)"),
        ("empresa", "bling_refresh_token", "VARCHAR(200)"),
        ("empresa", "bling_token_expires_at", "DATETIME"),
        ("empresa", "bling_webhook_secret", "VARCHAR(100)"),
        ("empresa", "bling_api_key_v2", "VARCHAR(100)"),
        ("clientes", "fantasia", "VARCHAR(200)"),
        ("clientes", "inscricao_estadual", "VARCHAR(20)"),
        ("clientes", "inscricao_municipal", "VARCHAR(20)"),
        ("fornecedores", "fantasia", "VARCHAR(200)"),
        ("fornecedores", "inscricao_estadual", "VARCHAR(20)"),
        ("fornecedores", "inscricao_municipal", "VARCHAR(20)"),
        ("clientes", "situacao", "VARCHAR(1)"),
        ("fornecedores", "situacao", "VARCHAR(1)"),
        ("contas_receber", "nosso_numero", "VARCHAR(30)"),
        ("contas_receber", "boleto_emitido", "BOOLEAN"),
        ("contas_receber", "boleto_url", "VARCHAR(500)"),
        ("contas_receber", "boleto_txid", "VARCHAR(50)"),
        ("empresa", "sicoob_client_id", "VARCHAR(200)"),
        ("empresa", "sicoob_token", "VARCHAR(300)"),
        ("empresa", "sicoob_conta_corrente", "VARCHAR(30)"),
        ("empresa", "sicoob_cert_path", "VARCHAR(500)"),
        ("empresa", "sicoob_cert_key_path", "VARCHAR(500)"),
        ("empresa", "sicoob_cert_password", "VARCHAR(100)"),
        ("empresa", "sicoob_beneficiario", "VARCHAR(20)"),
("contas_receber", "motivo_baixa", "VARCHAR(100)"),
         ("assinaturas", "mes_vencimento", "INTEGER"),
         ("produtos", "id", "INTEGER"),
         ("produtos", "codigo", "VARCHAR(50)"),
         ("produtos", "nome", "VARCHAR(200)"),
         ("produtos", "descricao", "TEXT"),
         ("produtos", "preco", "FLOAT"),
         ("produtos", "preco_custo", "FLOAT"),
         ("produtos", "ncm", "VARCHAR(10)"),
("produtos", "unidade", "VARCHAR(10)"),
           ("produtos", "categoria_id", "INTEGER"),
           ("produtos", "foto", "VARCHAR(500)"),
          ("produtos", "fornecedor_id", "INTEGER"),
          ("produtos", "marca", "VARCHAR(100)"),
          ("produtos", "peso_liq", "FLOAT"),
          ("produtos", "peso_bruto", "FLOAT"),
          ("produtos", "altura", "FLOAT"),
          ("produtos", "largura", "FLOAT"),
          ("produtos", "profundidade", "FLOAT"),
("produtos", "unidade_medida", "VARCHAR(20)"),
           ("produtos", "estoque", "FLOAT"),
           ("produtos", "estoque_minimo", "FLOAT"),
           ("produtos", "bling_id", "INTEGER"),
           ("produtos", "bling_updated_at", "DATETIME"),
           ("produtos", "bling_pending_sync", "BOOLEAN"),
           ("pedidos_venda", "id", "INTEGER"),
         ("pedidos_venda", "cliente_id", "INTEGER"),
         ("pedidos_venda", "numero", "VARCHAR(50)"),
         ("pedidos_venda", "data", "DATE"),
         ("pedidos_venda", "status", "VARCHAR(20)"),
         ("pedidos_venda", "total", "FLOAT"),
         ("pedidos_venda_itens", "id", "INTEGER"),
         ("pedidos_venda_itens", "pedido_id", "INTEGER"),
         ("pedidos_venda_itens", "produto_id", "INTEGER"),
         ("pedidos_venda_itens", "descricao", "VARCHAR(300)"),
         ("pedidos_venda_itens", "quantidade", "FLOAT"),
         ("pedidos_venda_itens", "preco_unitario", "FLOAT"),
         ("pedidos_venda_itens", "total", "FLOAT"),
("pedidos_venda_itens", "fornecedor_id", "INTEGER"),
            ("pedidos_venda", "tipo_pedido", "VARCHAR(20)"),
            ("pedidos_venda", "forma_pagamento", "VARCHAR(20)"),
("pedidos_venda", "gerar_boleto", "BOOLEAN"),
            ("pedidos_venda", "terminos_boleto", "TEXT"),
            ("pedidos_venda", "pedido_agrupado_id", "INTEGER"),
            ("categorias_produto", "id", "INTEGER"),
          ("categorias_produto", "nome", "VARCHAR(100)"),
     ]:
        try:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}"))
            conn.commit()
        except Exception:
            pass
    for table, idx_name in [("clientes", "ix_clientes_bling_id"), ("fornecedores", "ix_fornecedores_bling_id"),
                            ("assinaturas", "ix_assinaturas_bling_id"), ("ordens_servico", "ix_ordens_servico_bling_id"),
                            ("produtos", "ix_produtos_bling_id")]:
        try:
            conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {idx_name} ON {table}(bling_id)"))
            conn.commit()
        except Exception:
            pass
    # migrar dados das colunas tipo (antiga) e status (antigo)
    try:
        conn.execute(text(
            "UPDATE assinaturas SET periodicidade = 1 WHERE tipo = 'mensalidade' AND periodicidade IS NULL"
        ))
        conn.execute(text(
            "UPDATE assinaturas SET periodicidade = 5 WHERE tipo = 'anuidade' AND periodicidade IS NULL"
        ))
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute(text(
            "UPDATE assinaturas SET situacao = 1 WHERE status = 'ativa' AND situacao IS NULL"
        ))
        conn.execute(text(
            "UPDATE assinaturas SET situacao = 1 WHERE status = 'inadimplente' AND situacao IS NULL"
        ))
        conn.execute(text(
            "UPDATE assinaturas SET situacao = 0 WHERE status = 'cancelada' AND situacao IS NULL"
        ))
        conn.execute(text(
            "UPDATE assinaturas SET situacao = 2 WHERE status = 'encerrada' AND situacao IS NULL"
        ))
        conn.commit()
    except Exception:
        pass

import re

templates = Jinja2Templates(directory="templates")


def format_cpf_cnpj(value):
    if not value:
        return ""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    elif len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return value


templates.env.filters["format_cpf_cnpj"] = format_cpf_cnpj

app = FastAPI(title="Controle Comercial")
app.state.templates = templates
app.add_middleware(SessionMiddleware, secret_key="controle-comercial-secret-key-2024")

app.mount("/static", StaticFiles(directory="static"), name="static")

from routers import clientes, fornecedores, contas, assinaturas, ordens_servico, configuracoes, bling, sicoob, produtos, pedidos, auth
app.include_router(auth.router)
app.include_router(clientes.router)
app.include_router(fornecedores.router)
app.include_router(contas.router)
app.include_router(assinaturas.router)
app.include_router(ordens_servico.router)
app.include_router(configuracoes.router)
app.include_router(bling.router)
app.include_router(sicoob.router)
app.include_router(produtos.router)
app.include_router(pedidos.router)

@app.middleware("http")
async def auth_middleware(request, call_next):
    path = request.url.path.rstrip("/")
    public_paths = ["", "/auth/login", "/auth/logout", "/static", "/favicon.ico"]
    if path in public_paths or any(path.startswith(p) for p in public_paths):
        return await call_next(request)
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login")
    return await call_next(request)


def create_default_admin(db: Session):
    admin = db.query(Usuario).filter(Usuario.email == "admin@controle.com").first()
    if not admin:
        senha = bcrypt.hashpw("admin123".encode(), bcrypt.gensalt()).decode()
        admin = Usuario(email="admin@controle.com", senha=senha, nome="Administrador", ativo=True)
        db.add(admin)
        db.commit()


@app.on_event("startup")
def startup_event():
    from sqlalchemy import text
    db = next(get_db())
    try:
        db.execute(text("SELECT 1 FROM usuarios"))
    except:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, email VARCHAR(200) UNIQUE, senha VARCHAR(200), nome VARCHAR(200), ativo BOOLEAN, created_at DATETIME)"))
            conn.commit()
    create_default_admin(db)


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    hoje = date_func.today()
    
    total_clientes = db.query(func.count(Cliente.id)).scalar()
    total_fornecedores = db.query(func.count(Fornecedor.id)).scalar()
    total_produtos = db.query(func.count(Produto.id)).scalar()
    total_pedidos = db.query(func.count(PedidoVenda.id)).scalar()

    contas_pagar_pendentes = db.query(func.sum(ContaPagar.valor)).filter(
        ContaPagar.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    ).scalar() or 0

    contas_receber_pendentes = db.query(func.sum(ContaReceber.valor)).filter(
        ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    ).scalar() or 0

    contas_vencidas = db.query(func.count(ContaPagar.id)).filter(
        ContaPagar.data_vencimento < hoje,
        ContaPagar.status == StatusConta.PENDENTE
    ).scalar() or 0

    assinaturas_ativas = db.query(func.count(Assinatura.id)).filter(
        Assinatura.situacao == 1
    ).scalar()

    ordens_abertas = db.query(func.count(OrdemServico.id)).filter(
        OrdemServico.status.in_([StatusOS.ABERTA, StatusOS.EM_ANDAMENTO])
    ).scalar()

    clientes_rapidos = db.query(Cliente).order_by(Cliente.nome).all()
    produtos_rapidos = db.query(Produto).order_by(Produto.nome).all()
    ultima_os = db.query(OrdemServico).order_by(OrdemServico.created_at.desc()).limit(5).all()
    
    bling_pending = db.query(func.count(Cliente.id)).filter(Cliente.bling_pending_sync == True).scalar() or 0
    bling_pending += db.query(func.count(Fornecedor.id)).filter(Fornecedor.bling_pending_sync == True).scalar() or 0
    bling_pending += db.query(func.count(Assinatura.id)).filter(Assinatura.bling_pending_sync == True).scalar() or 0
    bling_pending += db.query(func.count(OrdemServico.id)).filter(OrdemServico.bling_pending_sync == True).scalar() or 0
    bling_pending += db.query(func.count(Produto.id)).filter(Produto.bling_pending_sync == True).scalar() or 0
    empresa_config = db.query(Empresa).first()
    bling_token_configurado = bool(empresa_config and empresa_config.bling_token)
    
    contas_pagar_proximas = db.query(ContaPagar).filter(
        ContaPagar.status == StatusConta.PENDENTE
    ).order_by(ContaPagar.data_vencimento).limit(5).all()
    contas_receber_proximas = db.query(ContaReceber).filter(
        ContaReceber.status == StatusConta.PENDENTE
    ).order_by(ContaReceber.data_vencimento).limit(5).all()
    
    proximo_pedido = db.query(func.max(PedidoVenda.numero)).scalar()
    try:
        proximo_pedido = str(int(proximo_pedido) + 1) if proximo_pedido else "1"
    except:
        proximo_pedido = "1"
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "total_clientes": total_clientes,
        "total_fornecedores": total_fornecedores,
        "total_produtos": total_produtos,
        "total_pedidos": total_pedidos,
        "contas_pagar_pendentes": contas_pagar_pendentes,
        "contas_receber_pendentes": contas_receber_pendentes,
        "contas_vencidas": contas_vencidas,
        "assinaturas_ativas": assinaturas_ativas,
        "ordens_abertas": ordens_abertas,
        "ultimas_ordens": ultima_os,
        "contas_pagar_proximas": contas_pagar_proximas,
        "contas_receber_proximas": contas_receber_proximas,
        "bling_pending": bling_pending,
        "bling_token_configurado": bling_token_configurado,
        "StatusOS": StatusOS, "StatusConta": StatusConta,
        "clientes": clientes_rapidos,
        "produtos": produtos_rapidos,
        "hoje": hoje,
        "proximo_pedido": proximo_pedido,
    })
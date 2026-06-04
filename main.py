import re
import os
from fastapi import FastAPI, Depends, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date as date_func, datetime

from database import engine, Base, get_db
from models import Cliente, Fornecedor, ContaPagar, ContaReceber, Assinatura, OrdemServico, Empresa, StatusConta, StatusOS, Produto, PedidoVenda, Usuario

# Executar migrations no startup
def run_migrations():
    from sqlalchemy import text
    migrations = [
        "ALTER TABLE contas_receber ADD COLUMN IF NOT EXISTS data_emissao DATE",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS telefone_whatsapp VARCHAR(20)",
        "ALTER TABLE produtos ADD COLUMN IF NOT EXISTS codigo_barras VARCHAR(50)",
    ]
    try:
        with engine.connect() as conn:
            for m in migrations:
                conn.execute(text(m))
            conn.commit()
    except Exception as e:
        print(f"Migration error: {e}")

run_migrations()
Base.metadata.create_all(bind=engine)

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
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

app.mount("/static", StaticFiles(directory="static"), name="static")

from routers import clientes, fornecedores, contas, assinaturas, ordens_servico, configuracoes, bling, sicoob, produtos, pedidos, auth


class ProxyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.headers.get("x-forwarded-proto") == "https":
            request.scope["scheme"] = "https"
        response = await call_next(request)
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        public_paths = ["/auth/login", "/auth/setup", "/static", "/favicon.ico"]
        path = request.url.path
        if any(path.startswith(p) for p in public_paths):
            return await call_next(request)
        if not request.session.get("user_id"):
            request.session["message"] = {"tipo": "danger", "texto": "Faça login para acessar"}
            return RedirectResponse(url="/auth/login")
        try:
            return await call_next(request)
        except Exception:
            request.session.clear()
            request.session["message"] = {"tipo": "danger", "texto": "Erro no servidor."}
            return RedirectResponse(url="/auth/login", status_code=303)


app.add_middleware(ProxyMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "controle-comercial-secret-key-2024"))
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
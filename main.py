from fastapi import FastAPI, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime

from database import engine, Base, get_db
from models import Cliente, Fornecedor, ContaPagar, ContaReceber, Assinatura, OrdemServico, Empresa, StatusConta, StatusOS

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
        ("clientes", "fantasia", "VARCHAR(200)"),
        ("clientes", "inscricao_estadual", "VARCHAR(20)"),
        ("clientes", "inscricao_municipal", "VARCHAR(20)"),
        ("fornecedores", "fantasia", "VARCHAR(200)"),
        ("fornecedores", "inscricao_estadual", "VARCHAR(20)"),
        ("fornecedores", "inscricao_municipal", "VARCHAR(20)"),
        ("clientes", "situacao", "VARCHAR(1)"),
        ("fornecedores", "situacao", "VARCHAR(1)"),
    ]:
        try:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}"))
            conn.commit()
        except Exception:
            pass
    for table, idx_name in [("clientes", "ix_clientes_bling_id"), ("fornecedores", "ix_fornecedores_bling_id"),
                            ("assinaturas", "ix_assinaturas_bling_id"), ("ordens_servico", "ix_ordens_servico_bling_id")]:
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

app = FastAPI(title="Controle de Serviços")
app.state.templates = templates
app.add_middleware(SessionMiddleware, secret_key="controle-servicos-secret-key-2024")

app.mount("/static", StaticFiles(directory="static"), name="static")

from routers import clientes, fornecedores, contas, assinaturas, ordens_servico, configuracoes, bling
app.include_router(clientes.router)
app.include_router(fornecedores.router)
app.include_router(contas.router)
app.include_router(assinaturas.router)
app.include_router(ordens_servico.router)
app.include_router(configuracoes.router)
app.include_router(bling.router)


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    hoje = date.today()

    total_clientes = db.query(func.count(Cliente.id)).scalar()
    total_fornecedores = db.query(func.count(Fornecedor.id)).scalar()

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

    ultimas_ordens = db.query(OrdemServico).order_by(OrdemServico.created_at.desc()).limit(5).all()
    contas_pagar_proximas = db.query(ContaPagar).filter(
        ContaPagar.status == StatusConta.PENDENTE
    ).order_by(ContaPagar.data_vencimento).limit(5).all()
    contas_receber_proximas = db.query(ContaReceber).filter(
        ContaReceber.status == StatusConta.PENDENTE
    ).order_by(ContaReceber.data_vencimento).limit(5).all()

    bling_pending = db.query(func.count(Cliente.id)).filter(Cliente.bling_pending_sync == True).scalar() or 0
    bling_pending += db.query(func.count(Fornecedor.id)).filter(Fornecedor.bling_pending_sync == True).scalar() or 0
    bling_pending += db.query(func.count(Assinatura.id)).filter(Assinatura.bling_pending_sync == True).scalar() or 0
    bling_pending += db.query(func.count(OrdemServico.id)).filter(OrdemServico.bling_pending_sync == True).scalar() or 0
    empresa_config = db.query(Empresa).first()
    bling_token_configurado = bool(empresa_config and empresa_config.bling_token)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "total_clientes": total_clientes,
        "total_fornecedores": total_fornecedores,
        "contas_pagar_pendentes": contas_pagar_pendentes,
        "contas_receber_pendentes": contas_receber_pendentes,
        "contas_vencidas": contas_vencidas,
        "assinaturas_ativas": assinaturas_ativas,
        "ordens_abertas": ordens_abertas,
        "ultimas_ordens": ultimas_ordens,
        "contas_pagar_proximas": contas_pagar_proximas,
        "contas_receber_proximas": contas_receber_proximas,
        "bling_pending": bling_pending,
        "bling_token_configurado": bling_token_configurado,
        "StatusOS": StatusOS, "StatusConta": StatusConta,
    })

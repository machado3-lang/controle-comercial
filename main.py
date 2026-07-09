import json
import re
import os
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, Depends, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload
from sqlalchemy import func
from datetime import date as date_func, datetime, timedelta

from database import engine, Base, get_db
from models import Cliente, Fornecedor, ContaPagar, ContaReceber, Assinatura, OrdemServico, Empresa, StatusConta, StatusOS, Produto, PedidoVenda, Usuario, MarcaProduto
import models_nfe

def get_current_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        raise RedirectResponse(url="/auth/login")
    return db.query(Usuario).filter(Usuario.id == user_id).first()

# Executar migrations no startup
def run_migrations():
    from sqlalchemy import text, inspect
    migrations = [
        "ALTER TABLE contas_receber ADD COLUMN IF NOT EXISTS data_emissao DATE",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS telefone_whatsapp VARCHAR(20)",
        "ALTER TABLE produtos ADD COLUMN IF NOT EXISTS codigo_barras VARCHAR(50)",
        "ALTER TABLE produtos ADD COLUMN IF NOT EXISTS tipo VARCHAR(20) DEFAULT 'produto'",
        "CREATE TABLE IF NOT EXISTS marcas_produto (id SERIAL PRIMARY KEY, nome VARCHAR(100) NOT NULL UNIQUE, created_at TIMESTAMP DEFAULT NOW())",
        "ALTER TABLE produtos ADD COLUMN IF NOT EXISTS marca_id INTEGER REFERENCES marcas_produto(id)",
        "ALTER TABLE produtos ADD COLUMN IF NOT EXISTS foto VARCHAR(500)",
        "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE",
        "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS permissoes TEXT",
        "ALTER TABLE pedidos_venda_itens ADD COLUMN IF NOT EXISTS variacao_id INTEGER REFERENCES produto_variacoes(id)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS senha_admin VARCHAR(100)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS senha_lembrete VARCHAR(200)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS bling_refresh_token VARCHAR(200)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS bling_token_expires_at TIMESTAMP",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS bling_webhook_secret VARCHAR(100)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS bling_api_key_v2 VARCHAR(100)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS sicoob_token VARCHAR(3000)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS sicoob_conta_corrente VARCHAR(30)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS sicoob_beneficiario VARCHAR(20)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS sicoob_cert_path VARCHAR(500)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS sicoob_cert_key_path VARCHAR(500)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS sicoob_cert_password VARCHAR(100)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS sicoob_cert_base64 TEXT",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS sicoob_cert_key_base64 TEXT",
        "ALTER TABLE assinaturas ADD COLUMN IF NOT EXISTS produto_id INTEGER REFERENCES produtos(id)",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS categoria_servico_padrao_id INTEGER REFERENCES categorias_produto(id)",
        "ALTER TABLE nfe ADD COLUMN IF NOT EXISTS origem VARCHAR(10) DEFAULT 'avulsa'",
        "ALTER TABLE nfe ADD COLUMN IF NOT EXISTS finalidade VARCHAR(30) DEFAULT 'normal'",
        "ALTER TABLE nfe ADD COLUMN IF NOT EXISTS indicador_presenca INTEGER DEFAULT 1",
        "ALTER TABLE produtos ADD COLUMN IF NOT EXISTS origem INTEGER DEFAULT 0",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS indicador_ie VARCHAR(20) DEFAULT 'contribuidor'",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS ultimo_numero_nfse INTEGER DEFAULT 0",
        "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS aliquota_iss FLOAT DEFAULT 2.0",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS iss_retido BOOLEAN DEFAULT FALSE",
        "ALTER TABLE nfse ADD COLUMN IF NOT EXISTS iss_retido BOOLEAN DEFAULT FALSE",
        "ALTER TABLE nfse ADD COLUMN IF NOT EXISTS aliquota_iss FLOAT DEFAULT 2.0",
        "ALTER TABLE nfse ADD COLUMN IF NOT EXISTS cliente_id INTEGER REFERENCES clientes(id)",
        "ALTER TABLE nfse ADD COLUMN IF NOT EXISTS protocolo VARCHAR(50)",
        "ALTER TABLE nfse ALTER COLUMN numero TYPE VARCHAR(50)",
        "ALTER TABLE nfse ALTER COLUMN codigo_verificacao TYPE VARCHAR(100)",
    ]
    # SQLite não suporta IF NOT EXISTS em ADD COLUMN, usar PRAGMA para verificar
    if "sqlite" in str(engine.url):
        conn = engine.connect()
        inspector = inspect(engine)
        cols = inspector.get_columns("usuarios")
        existing_cols = {c['name'] for c in cols}
        if "is_admin" not in existing_cols:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN is_admin BOOLEAN DEFAULT FALSE"))
            conn.commit()
        if "permissoes" not in existing_cols:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN permissoes TEXT"))
            conn.commit()
        # Migration pedidos_venda_itens
        cols_itens = inspector.get_columns("pedidos_venda_itens")
        existing_itens = {c['name'] for c in cols_itens}
        if "variacao_id" not in existing_itens:
            conn.execute(text("ALTER TABLE pedidos_venda_itens ADD COLUMN variacao_id INTEGER"))
            conn.commit()
        if "item_pai_id" not in existing_itens:
            conn.execute(text("ALTER TABLE pedidos_venda_itens ADD COLUMN item_pai_id INTEGER"))
            conn.commit()
        # Check and add missing columns to produto_variacoes
        cols_var = inspector.get_columns("produto_variacoes")
        existing_var = {c['name'] for c in cols_var}
        if "created_at" not in existing_var:
            conn.execute(text("ALTER TABLE produto_variacoes ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
            conn.commit()
        if "updated_at" not in existing_var:
            conn.execute(text("ALTER TABLE produto_variacoes ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
            conn.commit()
        if "estoque_atual" not in existing_var:
            conn.execute(text("ALTER TABLE produto_variacoes ADD COLUMN estoque_atual REAL DEFAULT 0"))
            conn.commit()
        if "estoque_minimo" not in existing_var:
            conn.execute(text("ALTER TABLE produto_variacoes ADD COLUMN estoque_minimo REAL DEFAULT 0"))
            conn.commit()
        # Check and add missing column to produto_composicao
        cols_comp = inspector.get_columns("produto_composicao")
        existing_comp = {c['name'] for c in cols_comp}
        if "created_at" not in existing_comp:
            conn.execute(text("ALTER TABLE produto_composicao ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
            conn.commit()
        if "insumo_id" not in existing_comp:
            # Não há dados importantes - recriar tabela limpa
            conn.execute(text("DROP TABLE IF EXISTS produto_composicao"))
            conn.execute(text("""
                CREATE TABLE produto_composicao (
                    id INTEGER PRIMARY KEY,
                    produto_pai_id INTEGER NOT NULL REFERENCES produtos(id),
                    insumo_id INTEGER NOT NULL REFERENCES produtos(id),
                    quantidade_padrao REAL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
        # Check and add missing columns to assinaturas_historico
        cols_hist = inspector.get_columns("assinaturas_historico")
        existing_hist = {c['name'] for c in cols_hist}
        if "dia_vencimento_anterior" not in existing_hist:
            conn.execute(text("ALTER TABLE assinaturas_historico ADD COLUMN dia_vencimento_anterior INTEGER"))
            conn.commit()
        if "dia_vencimento_novo" not in existing_hist:
            conn.execute(text("ALTER TABLE assinaturas_historico ADD COLUMN dia_vencimento_novo INTEGER"))
            conn.commit()
# Check and add missing columns to empresa
        cols_emp = inspector.get_columns("empresa")
        existing_emp = {c['name'] for c in cols_emp}
        if "senha_admin" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN senha_admin TEXT"))
            conn.commit()
        if "senha_lembrete" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN senha_lembrete TEXT"))
            conn.commit()
        if "sicoob_cert_base64" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN sicoob_cert_base64 TEXT"))
            conn.commit()
        if "sicoob_cert_key_base64" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN sicoob_cert_key_base64 TEXT"))
            conn.commit()
        if "notaas_api_key" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN notaas_api_key TEXT"))
            conn.commit()
        if "notaas_ambiente" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN notaas_ambiente VARCHAR(1) DEFAULT '2'"))
            conn.commit()
        if "serie_nfe" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN serie_nfe INTEGER DEFAULT 1"))
            conn.commit()
        if "ultimo_numero_nfe" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN ultimo_numero_nfe INTEGER DEFAULT 0"))
            conn.commit()
        if "ultimo_numero_nfse" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN ultimo_numero_nfse INTEGER DEFAULT 0"))
            conn.commit()
        if "aliquota_iss" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN aliquota_iss FLOAT DEFAULT 2.0"))
            conn.commit()
        if "cfop_padrao" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN cfop_padrao VARCHAR(4) DEFAULT '5102'"))
            conn.commit()
        if "bling_desabilitado" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN bling_desabilitado BOOLEAN DEFAULT 0"))
            conn.commit()
        if "codigo_ibge" not in existing_emp:
            conn.execute(text("ALTER TABLE empresa ADD COLUMN codigo_ibge VARCHAR(7)"))
            conn.commit()
        cols_nfe = inspector.get_columns("nfe")
        existing_nfe = {c['name'] for c in cols_nfe}
        if "cliente_id" not in existing_nfe:
            conn.execute(text("ALTER TABLE nfe ADD COLUMN cliente_id INTEGER REFERENCES clientes(id)"))
            conn.commit()
        if "origem" not in existing_nfe:
            conn.execute(text("ALTER TABLE nfe ADD COLUMN origem VARCHAR(10) DEFAULT 'avulsa'"))
            conn.commit()
        if "finalidade" not in existing_nfe:
            conn.execute(text("ALTER TABLE nfe ADD COLUMN finalidade VARCHAR(30) DEFAULT 'normal'"))
            conn.commit()
        if "indicador_presenca" not in existing_nfe:
            conn.execute(text("ALTER TABLE nfe ADD COLUMN indicador_presenca INTEGER DEFAULT 1"))
            conn.commit()
        cols_ass = inspector.get_columns("assinaturas")
        existing_ass = {c['name'] for c in cols_ass}
        if "produto_id" not in existing_ass:
            conn.execute(text("ALTER TABLE assinaturas ADD COLUMN produto_id INTEGER"))
            conn.commit()
        # Check codigo_ibge in clientes
        cols_cli = inspector.get_columns("clientes")
        existing_cli = {c['name'] for c in cols_cli}
        if "codigo_ibge" not in existing_cli:
            conn.execute(text("ALTER TABLE clientes ADD COLUMN codigo_ibge VARCHAR(7)"))
            conn.commit()
        if "isento_ie" not in existing_cli:
            conn.execute(text("ALTER TABLE clientes ADD COLUMN isento_ie BOOLEAN DEFAULT 0"))
            conn.commit()
        if "indicador_ie" not in existing_cli:
            conn.execute(text("ALTER TABLE clientes ADD COLUMN indicador_ie VARCHAR(20) DEFAULT 'contribuidor'"))
            conn.commit()
        if "iss_retido" not in existing_cli:
            conn.execute(text("ALTER TABLE clientes ADD COLUMN iss_retido BOOLEAN DEFAULT 0"))
            conn.commit()
        cols_prod = inspector.get_columns("produtos")
        existing_prod = {c['name'] for c in cols_prod}
        if "origem" not in existing_prod:
            conn.execute(text("ALTER TABLE produtos ADD COLUMN origem INTEGER DEFAULT 0"))
            conn.commit()
        cols_nfse = inspector.get_columns("nfse")
        existing_nfse = {c['name'] for c in cols_nfse}
        if "natureza_operacao" not in existing_nfse:
            conn.execute(text("ALTER TABLE nfse ADD COLUMN natureza_operacao VARCHAR(100)"))
            conn.commit()
        if "regime_especial" not in existing_nfse:
            conn.execute(text("ALTER TABLE nfse ADD COLUMN regime_especial VARCHAR(100)"))
            conn.commit()
        if "municipio_codigo" not in existing_nfse:
            conn.execute(text("ALTER TABLE nfse ADD COLUMN municipio_codigo VARCHAR(10)"))
            conn.commit()
        if "municipio_nome" not in existing_nfse:
            conn.execute(text("ALTER TABLE nfse ADD COLUMN municipio_nome VARCHAR(100)"))
            conn.commit()
        cols_nfse_itens = inspector.get_columns("nfse_itens")
        existing_nfse_itens = {c['name'] for c in cols_nfse_itens}
        if "codigo_servico" not in existing_nfse_itens:
            conn.execute(text("ALTER TABLE nfse_itens ADD COLUMN codigo_servico VARCHAR(20)"))
            conn.commit()
        if "tributacao_municipal" not in existing_nfse_itens:
            conn.execute(text("ALTER TABLE nfse_itens ADD COLUMN tributacao_municipal VARCHAR(20)"))
            conn.commit()
        conn.close()
    else:
        try:
            with engine.connect() as conn:
                conn.execute(text("""
                    DO $$ BEGIN
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='contas_receber' AND column_name='data_emissao') THEN ALTER TABLE contas_receber ADD COLUMN data_emissao DATE; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='clientes' AND column_name='telefone_whatsapp') THEN ALTER TABLE clientes ADD COLUMN telefone_whatsapp VARCHAR(20); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='clientes' AND column_name='codigo_ibge') THEN ALTER TABLE clientes ADD COLUMN codigo_ibge VARCHAR(7); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='clientes' AND column_name='isento_ie') THEN ALTER TABLE clientes ADD COLUMN isento_ie BOOLEAN DEFAULT FALSE; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='produtos' AND column_name='codigo_barras') THEN ALTER TABLE produtos ADD COLUMN codigo_barras VARCHAR(50); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='produtos' AND column_name='tipo') THEN ALTER TABLE produtos ADD COLUMN tipo VARCHAR(20) DEFAULT 'produto'; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='produtos' AND column_name='marca_id') THEN ALTER TABLE produtos ADD COLUMN marca_id INTEGER; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='produtos' AND column_name='foto') THEN ALTER TABLE produtos ADD COLUMN foto VARCHAR(500); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='usuarios' AND column_name='is_admin') THEN ALTER TABLE usuarios ADD COLUMN is_admin BOOLEAN DEFAULT FALSE; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='usuarios' AND column_name='permissoes') THEN ALTER TABLE usuarios ADD COLUMN permissoes TEXT; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pedidos_venda_itens' AND column_name='variacao_id') THEN ALTER TABLE pedidos_venda_itens ADD COLUMN variacao_id INTEGER; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pedidos_venda_itens' AND column_name='item_pai_id') THEN ALTER TABLE pedidos_venda_itens ADD COLUMN item_pai_id INTEGER; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='senha_admin') THEN ALTER TABLE empresa ADD COLUMN senha_admin VARCHAR(100); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='senha_lembrete') THEN ALTER TABLE empresa ADD COLUMN senha_lembrete VARCHAR(200); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='bling_refresh_token') THEN ALTER TABLE empresa ADD COLUMN bling_refresh_token VARCHAR(200); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='bling_token_expires_at') THEN ALTER TABLE empresa ADD COLUMN bling_token_expires_at TIMESTAMP; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='bling_webhook_secret') THEN ALTER TABLE empresa ADD COLUMN bling_webhook_secret VARCHAR(100); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='bling_api_key_v2') THEN ALTER TABLE empresa ADD COLUMN bling_api_key_v2 VARCHAR(100); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='sicoob_token') THEN ALTER TABLE empresa ADD COLUMN sicoob_token VARCHAR(3000); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='sicoob_conta_corrente') THEN ALTER TABLE empresa ADD COLUMN sicoob_conta_corrente VARCHAR(30); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='sicoob_beneficiario') THEN ALTER TABLE empresa ADD COLUMN sicoob_beneficiario VARCHAR(20); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='sicoob_cert_path') THEN ALTER TABLE empresa ADD COLUMN sicoob_cert_path VARCHAR(500); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='sicoob_cert_key_path') THEN ALTER TABLE empresa ADD COLUMN sicoob_cert_key_path VARCHAR(500); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='sicoob_cert_password') THEN ALTER TABLE empresa ADD COLUMN sicoob_cert_password VARCHAR(100); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='sicoob_cert_base64') THEN ALTER TABLE empresa ADD COLUMN sicoob_cert_base64 TEXT; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='sicoob_cert_key_base64') THEN ALTER TABLE empresa ADD COLUMN sicoob_cert_key_base64 TEXT; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='categoria_servico_padrao_id') THEN ALTER TABLE empresa ADD COLUMN categoria_servico_padrao_id INTEGER; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='notaas_api_key') THEN ALTER TABLE empresa ADD COLUMN notaas_api_key TEXT; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='notaas_ambiente') THEN ALTER TABLE empresa ADD COLUMN notaas_ambiente VARCHAR(1) DEFAULT '2'; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='serie_nfe') THEN ALTER TABLE empresa ADD COLUMN serie_nfe INTEGER DEFAULT 1; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='ultimo_numero_nfe') THEN ALTER TABLE empresa ADD COLUMN ultimo_numero_nfe INTEGER DEFAULT 0; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='ultimo_numero_nfse') THEN ALTER TABLE empresa ADD COLUMN ultimo_numero_nfse INTEGER DEFAULT 0; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='aliquota_iss') THEN ALTER TABLE empresa ADD COLUMN aliquota_iss FLOAT DEFAULT 2.0; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='cfop_padrao') THEN ALTER TABLE empresa ADD COLUMN cfop_padrao VARCHAR(4) DEFAULT '5102'; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='bling_desabilitado') THEN ALTER TABLE empresa ADD COLUMN bling_desabilitado BOOLEAN DEFAULT FALSE; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='empresa' AND column_name='codigo_ibge') THEN ALTER TABLE empresa ADD COLUMN codigo_ibge VARCHAR(7); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='nfe' AND column_name='cliente_id') THEN ALTER TABLE nfe ADD COLUMN cliente_id INTEGER; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='assinaturas' AND column_name='produto_id') THEN ALTER TABLE assinaturas ADD COLUMN produto_id INTEGER; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='nfe' AND column_name='origem') THEN ALTER TABLE nfe ADD COLUMN origem VARCHAR(10) DEFAULT 'avulsa'; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='nfe' AND column_name='finalidade') THEN ALTER TABLE nfe ADD COLUMN finalidade VARCHAR(30) DEFAULT 'normal'; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='nfe' AND column_name='indicador_presenca') THEN ALTER TABLE nfe ADD COLUMN indicador_presenca INTEGER DEFAULT 1; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='nfse' AND column_name='natureza_operacao') THEN ALTER TABLE nfse ADD COLUMN natureza_operacao VARCHAR(100); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='nfse' AND column_name='regime_especial') THEN ALTER TABLE nfse ADD COLUMN regime_especial VARCHAR(100); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='nfse' AND column_name='municipio_codigo') THEN ALTER TABLE nfse ADD COLUMN municipio_codigo VARCHAR(10); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='nfse' AND column_name='municipio_nome') THEN ALTER TABLE nfse ADD COLUMN municipio_nome VARCHAR(100); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='produtos' AND column_name='origem') THEN ALTER TABLE produtos ADD COLUMN origem INTEGER DEFAULT 0; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='clientes' AND column_name='indicador_ie') THEN ALTER TABLE clientes ADD COLUMN indicador_ie VARCHAR(20) DEFAULT 'contribuidor'; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='clientes' AND column_name='iss_retido') THEN ALTER TABLE clientes ADD COLUMN iss_retido BOOLEAN DEFAULT FALSE; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='produto_variacoes' AND column_name='created_at') THEN ALTER TABLE produto_variacoes ADD COLUMN created_at TIMESTAMP DEFAULT NOW(); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='produto_variacoes' AND column_name='updated_at') THEN ALTER TABLE produto_variacoes ADD COLUMN updated_at TIMESTAMP DEFAULT NOW(); END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='produto_variacoes' AND column_name='estoque_atual') THEN ALTER TABLE produto_variacoes ADD COLUMN estoque_atual REAL DEFAULT 0; END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='produto_variacoes' AND column_name='estoque_minimo') THEN ALTER TABLE produto_variacoes ADD COLUMN estoque_minimo REAL DEFAULT 0; END IF;
                    END $$;
                """))
                # CREATE TABLE IF NOT EXISTS for marcas_produto
                conn.execute(text("CREATE TABLE IF NOT EXISTS marcas_produto (id SERIAL PRIMARY KEY, nome VARCHAR(100) NOT NULL UNIQUE, created_at TIMESTAMP DEFAULT NOW())"))
                conn.execute(text("CREATE TABLE IF NOT EXISTS cfop_natureza (id SERIAL PRIMARY KEY, cfop VARCHAR(4) NOT NULL, natureza VARCHAR(200) NOT NULL, created_at TIMESTAMP DEFAULT NOW())"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_cfop_natureza_cfop ON cfop_natureza(cfop)"))
                # Fix sequences after restore from backup
                if "postgresql" in str(engine.url):
                    seq_tables = [
                        ('contas_receber_id_seq', 'contas_receber'),
                        ('assinaturas_id_seq', 'assinaturas'),
                        ('clientes_id_seq', 'clientes'),
                        ('fornecedores_id_seq', 'fornecedores'),
                        ('pedidos_venda_id_seq', 'pedidos_venda'),
                    ]
                    for seq, table in seq_tables:
                        try:
                            conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id)+1 FROM {table}), 1), false)"))
                        except:
                            pass
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
templates.env.filters["fromjson"] = lambda v: json.loads(v) if isinstance(v, str) else v
templates.env.globals["now"] = lambda: date_func.today()

app = FastAPI(title="Controle Comercial")
app.state.templates = templates
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "controle-comercial-secret-key-2024"))

from routers import clientes, fornecedores, nfse, contas, assinaturas, ordens_servico, configuracoes, bling, sicoob, produtos, pedidos, auth, servicos, nfe
app.include_router(auth.router)
app.include_router(clientes.router)
app.include_router(fornecedores.router)
app.include_router(contas.router)
app.include_router(nfse.router)
app.include_router(assinaturas.router)
app.include_router(ordens_servico.router)
app.include_router(configuracoes.router)
app.include_router(bling.router)
app.include_router(sicoob.router)
app.include_router(produtos.router)
app.include_router(pedidos.router)
app.include_router(servicos.router)
app.include_router(nfe.router)


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login")
    hoje = date_func.today()
    
    total_clientes = db.query(func.count(Cliente.id)).scalar()
    total_fornecedores = db.query(func.count(Fornecedor.id)).scalar()
    total_produtos = db.query(func.count(Produto.id)).scalar()
    produtos_estoque_baixo = db.query(func.count(Produto.id)).filter(
        Produto.estoque > 0,
        Produto.estoque_minimo > 0,
        Produto.estoque < Produto.estoque_minimo
    ).scalar() or 0
    produtos_estoque_zerado = db.query(func.count(Produto.id)).filter(
        Produto.estoque <= 0
    ).scalar() or 0
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

    # Indicadores extras
    inicio_mes = date_func.today().replace(day=1)
    faturamento_mes = db.query(func.sum(ContaReceber.valor)).filter(
        ContaReceber.status == StatusConta.PAGO,
        ContaReceber.data_recebimento >= inicio_mes
    ).scalar() or 0

    # Contas vencendo em 30 dias
    contas_pagar_vencendo = db.query(func.sum(ContaPagar.valor)).filter(
        ContaPagar.status == StatusConta.PENDENTE,
        ContaPagar.data_vencimento >= hoje,
        ContaPagar.data_vencimento <= hoje + timedelta(days=30)
    ).scalar() or 0

    contas_receber_vencendo = db.query(func.sum(ContaReceber.valor)).filter(
        ContaReceber.status == StatusConta.PENDENTE,
        ContaReceber.data_vencimento >= hoje,
        ContaReceber.data_vencimento <= hoje + timedelta(days=30)
    ).scalar() or 0

    assinaturas_vencendo = db.query(func.count(Assinatura.id)).filter(
        Assinatura.situacao == 1,
        Assinatura.data_fim >= hoje,
        Assinatura.data_fim <= hoje + timedelta(days=30)
    ).scalar() or 0

    ordens_abertas = db.query(func.count(OrdemServico.id)).filter(
        OrdemServico.status.in_([StatusOS.ABERTA, StatusOS.EM_ANDAMENTO])
    ).scalar()

    clientes_rapidos = db.query(Cliente).order_by(Cliente.nome).all()
    produtos_rapidos = db.query(Produto).order_by(Produto.nome).all()
    ultima_os = db.query(OrdemServico).order_by(OrdemServico.created_at.desc()).limit(5).all()
    assinaturas_proximas = db.query(Assinatura).options(joinedload(Assinatura.cliente)).filter(
        Assinatura.situacao == 1,
        Assinatura.data_fim >= hoje,
        Assinatura.data_fim <= hoje + timedelta(days=30)
    ).order_by(Assinatura.data_fim).limit(5).all()

    # Inadimplência total
    inadimplente_total = db.query(func.sum(ContaReceber.valor)).filter(
        ContaReceber.data_vencimento < hoje,
        ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    ).scalar() or 0

    bling_pending = db.query(func.count(Cliente.id)).filter(Cliente.bling_pending_sync == True).scalar() or 0
    bling_pending += db.query(func.count(Fornecedor.id)).filter(Fornecedor.bling_pending_sync == True).scalar() or 0
    bling_pending += db.query(func.count(Assinatura.id)).filter(Assinatura.bling_pending_sync == True).scalar() or 0
    bling_pending += db.query(func.count(OrdemServico.id)).filter(OrdemServico.bling_pending_sync == True).scalar() or 0
    bling_pending += db.query(func.count(Produto.id)).filter(Produto.bling_pending_sync == True).scalar() or 0
    empresa_config = db.query(Empresa).first()
    bling_token_configurado = bool(empresa_config and empresa_config.bling_token)
    sicoob_token_configurado = bool(empresa_config and empresa_config.sicoob_client_id)
    
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
        "produtos_estoque_baixo": produtos_estoque_baixo,
        "produtos_estoque_zerado": produtos_estoque_zerado,
        "total_pedidos": total_pedidos,
        "contas_pagar_pendentes": contas_pagar_pendentes,
        "contas_receber_pendentes": contas_receber_pendentes,
"contas_vencidas": contas_vencidas,
        "assinaturas_ativas": assinaturas_ativas,
        "assinaturas_vencendo": assinaturas_vencendo,
        "faturamento_mes": faturamento_mes,
        "contas_pagar_vencendo": contas_pagar_vencendo,
        "contas_receber_vencendo": contas_receber_vencendo,
        "inadimplente_total": inadimplente_total,
        "ordens_abertas": ordens_abertas,
        "ultimas_ordens": ultima_os,
        "assinaturas_proximas": assinaturas_proximas,
        "contas_pagar_proximas": contas_pagar_proximas,
        "contas_receber_proximas": contas_receber_proximas,
        "bling_pending": bling_pending,
        "bling_token_configurado": bling_token_configurado,
        "sicoob_token_configurado": sicoob_token_configurado,
        "StatusOS": StatusOS, "StatusConta": StatusConta,
        "clientes": clientes_rapidos,
        "produtos": produtos_rapidos,
        "hoje": hoje,
        "proximo_pedido": proximo_pedido,
    })
"""
Application lifespan events for Controle Comercial.
"""
from contextlib import asynccontextmanager
import secrets
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware as StarletteSessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker, Session, joinedload
from database import engine, Base, SessionLocal, get_db
from models import TipoDocumento, PlanoDeContas, Cliente, Fornecedor, ContaPagar, ContaReceber, Assinatura, OrdemServico, Empresa, StatusConta, StatusOS, Produto, PedidoVenda
import models_nfe  # registra modelos NFe/NFSe no Base.metadata para migração automática
import models_estoque  # registra modelos de estoque (movimentacoes, os_pecas)

from datetime import date as date_func, timedelta
from app.core.config import settings
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.trustedhost import TrustedHostMiddleware
from slowapi.middleware import SlowAPIMiddleware

from urllib.parse import parse_qs
import json
from itsdangerous import TimestampSigner
from base64 import b64encode, b64decode

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    print("[STARTUP] Iniciando aplicação...")
    run_migrations()
    print("[STARTUP] Migrações executadas")
    yield
    # Shutdown
    # Any cleanup if needed


def run_migrations():
    """Create tables if they don't exist. For migrations, use: alembic upgrade head"""
    Base.metadata.create_all(bind=engine)

    # Auto-migrate: add missing columns to existing tables
    try:
        _add_missing_columns()
    except Exception as e:
        print(f"[MIGRATION] Warning: could not auto-migrate columns: {e}")

    # Auto-migrate: widen String columns when the model declares a larger length
    try:
        _widen_string_columns()
    except Exception as e:
        print(f"[MIGRATION] Warning: could not widen columns: {e}")

    # Auto-migrate: adiciona valores ausentes em tipos ENUM nativos do PostgreSQL
    # (ex.: EXCLUIDO em StatusConta). O create_all e o _add_missing_columns nao
    # alteram ENUMs existentes, entao valores novos no modelo precisam ser
    # acrescentados manualmente ao tipo do banco, caso contrario o commit falha.
    try:
        _add_missing_enum_values()
    except Exception as e:
        print(f"[MIGRATION] Warning: could not migrate enum values: {e}")

    # Auto-migrate: adiciona unique constraints que faltam
    try:
        _add_unique_constraints()
    except Exception as e:
        print(f"[MIGRATION] Warning: could not add unique constraints: {e}")

    # Auto-migrate: adiciona indexes faltantes em colunas FK
    try:
        _add_missing_indexes()
    except Exception as e:
        print(f"[MIGRATION] Warning: could not add missing indexes: {e}")

    # Limpeza de PDFs temporários antigos
    try:
        _cleanup_old_pdfs()
    except Exception as e:
        print(f"[MIGRATION] Warning: could not cleanup old PDFs: {e}")

    # Auto-migrate: converte colunas Float para Numeric quando o modelo mudar
    try:
        _fix_float_columns()
    except Exception as e:
        print(f"[MIGRATION] Warning: could not fix float columns: {e}")

    # Backfill: corrige origem de NFe/NFSe já existentes (default 'avulsa')
    try:
        _backfill_origem()
    except Exception as e:
        print(f"[MIGRATION] Warning: could not backfill origem: {e}")


def _add_missing_columns():
    """Add missing columns to existing tables for schema evolution.
    Compara todas as colunas declaradas nos modelos (Base.metadata) com o
    banco e cria as que faltam. Cobre qualquer nova coluna sem lista hardcoded.
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text
    from sqlalchemy.types import (
        String, Integer, BigInteger, Boolean, Float, Numeric,
        Date, DateTime, Time, JSON, Text, LargeBinary,
    )

    _TYPE_MAP = {
        Text: lambda c: "TEXT",
        String: lambda c: f"VARCHAR({c.type.length or 255})",
        Integer: lambda c: "INTEGER",
        BigInteger: lambda c: "BIGINT",
        Boolean: lambda c: "BOOLEAN",
        Float: lambda c: "DOUBLE PRECISION",
        Numeric: lambda c: f"NUMERIC({getattr(c.type, 'precision', 12) or 12},{getattr(c.type, 'scale', 2) or 2})",
        Date: lambda c: "DATE",
        DateTime: lambda c: "TIMESTAMP",
        Time: lambda c: "TIME",
        JSON: lambda c: "JSONB",
        LargeBinary: lambda c: "BYTEA",
    }

    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table_name, table in Base.metadata.tables.items():
        # Tabela ainda não existe: create_all ja cuida; aqui só colunas faltantes
        if table_name not in existing_tables:
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name in existing_cols:
                continue
            col_type = None
            for base, fn in _TYPE_MAP.items():
                if isinstance(col.type, base):
                    col_type = fn(col)
                    break
            if col_type is None:
                col_type = "TEXT"
            # Auto-migration sempre adiciona como nullable para nao falhar em
            # tabelas ja populadas (PostgreSQL nao permite ADD COLUMN NOT NULL
            # com linhas existentes sem DEFAULT). O modelo mantem a restricao.
            nullable = ""
            default = ""
            try:
                with engine.connect() as conn:
                    conn.execute(sa_text(
                        f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}{nullable}{default}"
                    ))
                    conn.commit()
                    print(f"[MIGRATION] Added column {table_name}.{col.name} ({col_type})")
            except Exception as e:
                print(f"[MIGRATION] Could not add {table_name}.{col.name}: {e}")

    # Corrige colunas Text que possam ter sido criadas como VARCHAR devido a
    # ordem do _TYPE_MAP (Text eh subclasse de String no SQLAlchemy).
    try:
        _fix_text_columns()
    except Exception as e:
        print(f"[MIGRATION] Warning: could not fix text columns: {e}")


def _widen_string_columns():
    """Widens existing VARCHAR columns when the model declares a larger length
    (PostgreSQL only; increasing VARCHAR length is metadata-only and safe)."""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text
    from sqlalchemy.types import String

    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue
        try:
            cols = inspector.get_columns(table_name)
        except Exception:
            continue
        existing = {c["name"]: c for c in cols}
        for col in table.columns:
            if col.name not in existing:
                continue
            if not isinstance(col.type, String):
                continue
            model_len = col.type.length or 255
            db_len = getattr(existing[col.name]["type"], "length", None)
            if db_len and model_len > db_len:
                try:
                    with engine.connect() as conn:
                        conn.execute(sa_text(
                            f"ALTER TABLE {table_name} ALTER COLUMN {col.name} TYPE VARCHAR({model_len})"
                        ))
                        conn.commit()
                        print(f"[MIGRATION] Widened {table_name}.{col.name} to VARCHAR({model_len})")
                except Exception as e:
                    print(f"[MIGRATION] Could not widen {table_name}.{col.name}: {e}")


def _backfill_origem():
    """Corrige a coluna origem de NFe/NFSe já existentes que ficaram com o
    valor default 'avulsa'. Idempotente: só atualiza onde necessário."""
    from sqlalchemy import text as sa_text

    updates = [
        # NFe importadas via SEFAZ/XML
        "UPDATE nfe SET origem='importada' WHERE origem='sefaz'",
        # NFe de assinatura (pedido vinculado a uma assinatura)
        "UPDATE nfe SET origem='assinatura' WHERE pedido_id IN "
        "(SELECT id FROM pedidos_venda WHERE assinatura_id IS NOT NULL) "
        "AND origem IN ('pedido','avulsa')",
        # NFe de pedido
        "UPDATE nfe SET origem='pedido' WHERE pedido_id IS NOT NULL AND origem='avulsa'",
        # NFe de ordem de servico
        "UPDATE nfe SET origem='os' WHERE os_id IS NOT NULL AND origem='avulsa'",
        # NFe importada via XML sem invoice_id
        "UPDATE nfe SET origem='importada' WHERE xml_text IS NOT NULL "
        "AND invoice_id IS NULL AND origem='avulsa'",
        # NFSe importada via ADN
        "UPDATE nfse SET origem='importada' WHERE origem='adn'",
        # NFSe de assinatura
        "UPDATE nfse SET origem='assinatura' WHERE pedido_id IN "
        "(SELECT id FROM pedidos_venda WHERE assinatura_id IS NOT NULL) "
        "AND origem IN ('pedido','avulsa')",
        # NFSe de pedido
        "UPDATE nfse SET origem='pedido' WHERE pedido_id IS NOT NULL AND origem='avulsa'",
        # NFSe de consolidação
        "UPDATE nfse SET origem='consolidacao' WHERE consolidacao_id IS NOT NULL AND origem='avulsa'",
    ]
    with engine.connect() as conn:
        for sql in updates:
            try:
                conn.execute(sa_text(sql))
            except Exception as e:
                print(f"[MIGRATION] Backfill origem falhou ({sql[:40]}...): {e}")
        conn.commit()

    # Create audit_log table if it doesn't exist
    if "audit_log" not in existing_tables:
        try:
            with engine.connect() as conn:
                conn.execute(sa_text("""
                    CREATE TABLE audit_log (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES usuarios(id),
                        acao VARCHAR(100) NOT NULL,
                        entidade VARCHAR(100),
                        entidade_id INTEGER,
                        detalhes TEXT,
                        ip VARCHAR(50),
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """))
                conn.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_audit_log_acao ON audit_log(acao)"))
                conn.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at)"))
                conn.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log(user_id)"))
                conn.commit()
                print("[MIGRATION] Created audit_log table")
        except Exception as e:
            print(f"[MIGRATION] Could not create audit_log table: {e}")


def _fix_text_columns():
    """Converte colunas declaradas como Text no modelo, mas criadas como
    VARCHAR no banco, para TEXT (evita truncamento de XML/notas longas)."""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text
    from sqlalchemy.types import Text

    inspector = sa_inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        if table_name not in set(inspector.get_table_names()):
            continue
        for col in table.columns:
            if not isinstance(col.type, Text):
                continue
            try:
                cols = {c["name"]: c for c in inspector.get_columns(table_name)}
                db_col = cols.get(col.name)
                if not db_col:
                    continue
                udt = str(db_col["type"]).upper()
                if "VARCHAR" in udt or "CHAR" in udt:
                    with engine.connect() as conn:
                        conn.execute(sa_text(
                            f"ALTER TABLE {table_name} ALTER COLUMN {col.name} TYPE TEXT"
                        ))
                        conn.commit()
                        print(f"[MIGRATION] Converted {table_name}.{col.name} to TEXT")
            except Exception as e:
                print(f"[MIGRATION] Could not convert {table_name}.{col.name}: {e}")


def _fix_float_columns():
    """Converte colunas Float para Numeric quando o modelo muda de tipo.
    Ex.: PedidoVendaItem.quantidade passou de Float para Numeric(12,3)."""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text
    from sqlalchemy.types import Float, Numeric

    inspector = sa_inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        if table_name not in set(inspector.get_table_names()):
            continue
        for col in table.columns:
            if not isinstance(col.type, Numeric):
                continue
            try:
                cols = {c["name"]: c for c in inspector.get_columns(table_name)}
                db_col = cols.get(col.name)
                if not db_col:
                    continue
                db_type = str(db_col["type"]).upper()
                if "DOUBLE" in db_type or "FLOAT" in db_type or "REAL" in db_type:
                    p = getattr(col.type, "precision", 12) or 12
                    s = getattr(col.type, "scale", 2) or 2
                    with engine.connect() as conn:
                        conn.execute(sa_text(
                            f"ALTER TABLE {table_name} ALTER COLUMN {col.name} "
                            f"TYPE NUMERIC({p},{s}) USING {col.name}::numeric"
                        ))
                        conn.commit()
                        print(f"[MIGRATION] Converted {table_name}.{col.name} from {db_type} to NUMERIC({p},{s})")
            except Exception as e:
                print(f"[MIGRATION] Could not convert {table_name}.{col.name}: {e}")


def _add_missing_enum_values():
    """Adiciona valores ausentes em tipos ENUM nativos do PostgreSQL.

    O create_all e o _add_missing_columns so criam tabelas/colunas, mas no
    alteram ENUMs existentes. Valores novos adicionados ao modelo (ex.: o
    EXCLUIDO de StatusConta) precisam ser acrescentados ao tipo do banco, senao
    qualquer commit que os use falha com 'invalid input value for enum' (500).
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text
    from sqlalchemy.types import Enum as SAEnum

    if engine.dialect.name != "postgresql":
        return

    inspector = sa_inspect(engine)
    existing_enums = {e["name"]: e for e in inspector.get_enums()}

    with engine.connect() as raw:
        # ALTER TYPE ADD VALUE nao pode rodar dentro de um bloco de transacao
        # em versoes antigas do PostgreSQL; usamos autocommit por seguranca.
        conn = raw.execution_options(isolation_level="AUTOCOMMIT")
        for table in Base.metadata.tables.values():
            for col in table.columns:
                if not isinstance(col.type, SAEnum) or not getattr(col.type, "native_enum", False):
                    continue
                enum_class = getattr(col.type, "enum_class", None)
                if not enum_class:
                    continue
                type_name = getattr(col.type, "name", None)
                if not type_name or type_name not in existing_enums:
                    continue
                existing_labels = set(existing_enums[type_name].get("labels", []))
                for member in enum_class:
                    val = member.value if hasattr(member, "value") else str(member)
                    if val in existing_labels:
                        continue
                    # Valor vem do modelo (controlado), mas sanitizamos por defesa.
                    if not all(c.isalnum() or c == "_" for c in str(val)):
                        continue
                    try:
                        conn.execute(
                            sa_text(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{val}'")
                        )
                        print(f"[MIGRATION] Added enum value {type_name}.{val}")
                    except Exception as e:
                        print(f"[MIGRATION] Could not add enum value {type_name}.{val}: {e}")


def _add_unique_constraints():
    """Adiciona unique constraints em colunas que devem ser únicas,
    desde que não existam duplicatas no banco."""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text

    if engine.dialect.name != "postgresql":
        return

    constraints = [
        ("nfse", "uq_nfse_chave_acesso", "chave_acesso"),
        ("nfse_recebida", "uq_nfse_recebida_chave_acesso", "chave_acesso"),
        ("clientes", "uq_clientes_cpf_cnpj", "cpf_cnpj"),
        ("fornecedores", "uq_fornecedores_cpf_cnpj", "cpf_cnpj"),
    ]

    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())
    existing_constraints = set()
    for table in existing_tables:
        for idx in inspector.get_unique_constraints(table):
            existing_constraints.add(idx["name"])

    with engine.connect() as conn:
        for table, constraint_name, column in constraints:
            if table not in existing_tables:
                continue
            if constraint_name in existing_constraints:
                continue
            # Verifica se há duplicatas antes de adicionar a constraint
            dup = conn.execute(sa_text(
                f"SELECT COUNT(*) FROM (SELECT {column} FROM {table} "
                f"WHERE {column} IS NOT NULL AND {column} != '' "
                f"GROUP BY {column} HAVING COUNT(*) > 1) sub"
            )).scalar()
            if dup and dup > 0:
                print(f"[MIGRATION] WARNING: {dup} {column} duplicados em {table}, "
                      f"unique constraint não adicionada automaticamente")
                continue
            try:
                conn.execute(sa_text(
                    f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} UNIQUE ({column})"
                ))
                conn.commit()
                print(f"[MIGRATION] Added unique constraint {constraint_name} on {table}({column})")
            except Exception as e:
                print(f"[MIGRATION] Could not add {constraint_name}: {e}")


def _add_missing_indexes():
    """Adiciona indexes em colunas FK que foram declaradas no model com
    index=True mas cujo index ainda nao existe no banco."""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text

    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue
        existing_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
        for col in table.columns:
            if not col.index:
                continue
            # Nome padrao que o SQLAlchemy usaria se criar a tabela do zero
            idx_name = f"ix_{table_name}_{col.name}"
            if idx_name in existing_indexes:
                continue
            # Pula primary keys (ja tem index implicito)
            if col.primary_key:
                continue
            try:
                with engine.connect() as conn:
                    conn.execute(sa_text(
                        f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name} ({col.name})"
                    ))
                    conn.commit()
                    print(f"[MIGRATION] Created index {idx_name} on {table_name}({col.name})")
            except Exception as e:
                print(f"[MIGRATION] Could not create index {idx_name}: {e}")


def _cleanup_old_pdfs():
    """Remove PDFs temporários com mais de 30 dias das pastas de upload.
    Os DANFSe/DANFE serão regenerados sob demanda se necessário."""
    import os
    import shutil
    import time

    dirs = ["static/uploads/nfse", "static/uploads/nfe"]
    cutoff = time.time() - 30 * 86400
    removed = 0
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if not fname.lower().endswith(".pdf"):
                continue
            fpath = os.path.join(d, fname)
            try:
                mtime = os.path.getmtime(fpath)
                if mtime < cutoff:
                    os.remove(fpath)
                    removed += 1
            except Exception:
                pass
    if removed:
        print(f"[MIGRATION] Removed {removed} old PDF(s) (>30 days)")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    from fastapi import Depends
    from decimal import Decimal

    app = FastAPI(
        title="Controle Comercial API",
        description="Sistema de controle comercial com integração Bling ERP, NFe, NFSe, Sicoob",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Rate limiter (uses module-level instance for consistency with router imports)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Disable rate limiter completely in testing mode
    if settings.ENVIRONMENT != "testing":
        # Only add rate limiter middleware in production
        app.add_middleware(SlowAPIMiddleware)
    else:
        # In testing, set default limits to very high
        limiter._default_limits = ["1000000/minute"]

    # Templates
    from pathlib import Path
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent.parent / "templates"))

    # Add custom template filters
    def format_cpf_cnpj(value):
        if not value:
            return ""
        digits = "".join(c for c in str(value) if c.isdigit())
        if len(digits) == 11:
            return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
        elif len(digits) == 14:
            return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
        return value

    templates.env.filters["format_cpf_cnpj"] = format_cpf_cnpj

    def format_reais(value):
        if value is None:
            return "0,00"
        return f"{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    templates.env.filters["format_reais"] = format_reais

    class _DecimalEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return super().default(obj)

    from markupsafe import Markup

    def tojson_filter(value, indent=None):
        result = json.dumps(value, cls=_DecimalEncoder, indent=indent)
        return Markup(result)

    templates.env.filters["tojson"] = tojson_filter
    templates.env.globals["now"] = date_func.today
    app.state.templates = templates

    # ---- CSRF + Auth middleware ----
    _csrf_skip_regen = frozenset({
        "/clientes/buscar", "/produtos/buscar", "/fornecedores/buscar",
        "/static/", "/docs", "/redoc", "/openapi.json", "/api/",
    })
    _public_prefixes = frozenset({
        "/auth/login", "/auth/logout", "/auth/setup", "/auth/migrate",
        "/static/", "/api/", "/bling/webhook", "/sicoob/webhook",
        "/nfe/webhook", "/docs", "/redoc", "/openapi.json",
    })

    class _CSRFAndAuthMiddleware:
        def __init__(self, app: ASGIApp):
            self.app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            # Session will be populated by StarletteSessionMiddleware before reaching us
            # since we're added AFTER it (we're inner, it's outer)

            # Buffer body for CSRF validation
            method = scope.get("method", "GET")
            full_body = b""
            has_body = method in ("POST", "PUT", "PATCH", "DELETE")
            
            if has_body:
                chunks, more = [], True
                while more:
                    msg = await receive()
                    if msg["type"] == "http.request":
                        chunks.append(msg.get("body", b""))
                        more = msg.get("more_body", False)
                    elif msg["type"] == "http.disconnect":
                        return
                full_body = b"".join(chunks)

            path = scope.get("path", "/")
            is_public = any(path.startswith(p) for p in _public_prefixes)

            session = scope.get("session", {})

            # CSRF validation on write methods
            if has_body and not is_public:
                session_tok = session.get("_csrf_token", "")
                form_tok = ""

                def _extract_csrf_from_multipart(body: bytes, content_type: str) -> str:
                    """Extrai o campo 'csrf_token' de um corpo multipart/form-data."""
                    try:
                        boundary = None
                        for part in content_type.split(";"):
                            part = part.strip()
                            if part.startswith("boundary="):
                                boundary = part[len("boundary="):].strip().strip('"')
                        if not boundary:
                            return ""
                        delimiter = b"--" + boundary.encode("utf-8")
                        for part in body.split(delimiter):
                            if b'name="csrf_token"' in part:
                                idx = part.find(b"\r\n\r\n")
                                sep = 4
                                if idx == -1:
                                    idx = part.find(b"\n\n")
                                    sep = 2
                                if idx != -1:
                                    val = part[idx + sep:]
                                    val = val.rstrip(b"\r\n")
                                    if val.endswith(b"--"):
                                        val = val[:-2].rstrip(b"\r\n")
                                    return val.decode("utf-8", "ignore")
                    except Exception:
                        pass
                    return ""

                # Try URL-encoded form body first
                if full_body:
                    try:
                        params = parse_qs(full_body.decode("utf-8"))
                        form_tok = params.get("csrf_token", [""])[0]
                    except Exception:
                        pass
                # Try multipart/form-data body (forms com upload de arquivos)
                if not form_tok:
                    ctype = ""
                    for hdr_name, hdr_val in scope.get("headers", []):
                        if hdr_name == b"content-type":
                            ctype = hdr_val.decode("utf-8", "ignore")
                            break
                    if "multipart/form-data" in ctype:
                        form_tok = _extract_csrf_from_multipart(full_body, ctype)
                # Fallback to query string (used by JS fetch uploads)
                if not form_tok:
                    qs = scope.get("query_string", b"").decode("utf-8")
                    if qs:
                        qparams = parse_qs(qs)
                        form_tok = qparams.get("csrf_token", [""])[0]
                if not session_tok or not form_tok or not secrets.compare_digest(session_tok, form_tok):
                    session["message"] = {"tipo": "danger", "texto": "Token de segurança inválido. Tente novamente."}
                    session["_csrf_token"] = secrets.token_hex(32)
                    # Persist session and redirect manually
                    signer = TimestampSigner(settings.SECRET_KEY)
                    data = b64encode(json.dumps(session).encode("utf-8"))
                    signed = signer.sign(data)
                    redirect_to = "/assinaturas"
                    for hdr_name, hdr_val in scope.get("headers", []):
                        if hdr_name == b"referer":
                            redirect_to = hdr_val.decode("utf-8")
                            break
                    headers = [(b"location", redirect_to.encode("utf-8"))]
                    headers.append((b"set-cookie", f"session={signed.decode('utf-8')}; path=/; httponly; samesite=lax".encode("utf-8")))
                    await send({"type": "http.response.start", "status": 303, "headers": headers})
                    await send({"type": "http.response.body", "body": b""})
                    return

            # Auth check
            if not is_public:
                # Garantir que a sessão tenha sempre um token CSRF válido
                if session.get("user_id") and not session.get("_csrf_token"):
                    session["_csrf_token"] = secrets.token_hex(32)
                if not session.get("user_id"):
                    if path.startswith("/api/"):
                        await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]})
                        await send({"type": "http.response.body", "body": b'{"detail": "Unauthorized"}'})
                        return
                    session["message"] = {"tipo": "danger", "texto": "Faça login primeiro"}
                    signer = TimestampSigner(settings.SECRET_KEY)
                    data = b64encode(json.dumps(session).encode("utf-8"))
                    signed = signer.sign(data)
                    headers = [(b"location", b"/auth/login")]
                    headers.append((b"set-cookie", f"session={signed.decode('utf-8')}; path=/; httponly; samesite=lax".encode("utf-8")))
                    await send({"type": "http.response.start", "status": 303, "headers": headers})
                    await send({"type": "http.response.body", "body": b""})
                    return

            # Replay body
            done = False
            async def replay_receive():
                nonlocal done
                if not done:
                    done = True
                    return {"type": "http.request", "body": full_body, "more_body": False}
                return {"type": "http.disconnect"}

            await self.app(scope, replay_receive, send)

    # Register middlewares (LIFO: LAST added runs FIRST on request)
    # We need SessionMiddleware to run FIRST on request to populate scope["session"]
    # So it must be added LAST (after CSRF middleware)
    app.add_middleware(CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list)
    app.add_middleware(_CSRFAndAuthMiddleware)  # Added third = runs first on request
    app.add_middleware(StarletteSessionMiddleware,  # Added last = runs last on request
        secret_key=settings.SECRET_KEY,
        max_age=60 * 60 * 24 * 7,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )

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

        inicio_mes = date_func.today().replace(day=1)
        faturamento_mes = db.query(func.sum(ContaReceber.valor)).filter(
            ContaReceber.status == StatusConta.PAGO,
            ContaReceber.data_recebimento >= inicio_mes
        ).scalar() or 0

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

    # Error handlers
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request, exc):
        from starlette.responses import JSONResponse
        if exc.status_code in (302, 303) and exc.detail:
            return RedirectResponse(url=exc.detail, status_code=303)
        if request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        try:
            return request.app.state.templates.TemplateResponse(request, "erro.html", {
                "request": request, "status_code": exc.status_code,
                "mensagem": exc.detail
            }, status_code=exc.status_code)
        except Exception:
            return RedirectResponse(url="/auth/login", status_code=303)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request, exc):
        import traceback
        tb = traceback.format_exc()
        print(f"[500] {request.url.path}: {exc}\n{tb}")
        if request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
            from starlette.responses import JSONResponse
            return JSONResponse(status_code=500, content={"detail": "Erro interno do servidor"})
        try:
            return request.app.state.templates.TemplateResponse(request, "erro.html", {
                "request": request, "status_code": 500,
                "mensagem": "Erro interno do servidor",
                "detalhes": tb if settings.is_development else None,
            }, status_code=500)
        except Exception:
            return RedirectResponse(url="/auth/login", status_code=303)

    # Add CSRF token and flash messages to all template contexts via context processor
    def add_to_context(request):
        messages = []
        if "message" in request.session:
            raw = request.session.pop("message")
            if isinstance(raw, dict):
                messages.append({"type": raw.get("tipo", "success"), "text": raw.get("texto", str(raw))})
            else:
                messages.append({"type": "success", "text": raw})
        if "error" in request.session:
            raw = request.session.pop("error")
            if isinstance(raw, dict):
                messages.append({"type": "error", "text": raw.get("texto", str(raw))})
            else:
                messages.append({"type": "error", "text": raw})
        token = request.session.get("_csrf_token", "")
        def _csrf_token():
            return request.session.get("_csrf_token", token)
        return {"messages": messages, "csrf_token": _csrf_token}

    templates.context_processors.append(add_to_context)

    # Static files
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # Include routers
    from routers import (
        clientes, fornecedores, nfse, contas, assinaturas,
        ordens_servico, configuracoes, bling, sicoob, produtos,
        pedidos, auth, nfe, tipos_documento, planocontas, consolidacoes,
        estoque, consultas
    )

    app.include_router(auth.router)
    app.include_router(clientes.router)
    app.include_router(fornecedores.router)
    app.include_router(nfse.router)
    app.include_router(contas.router)
    app.include_router(assinaturas.router)
    app.include_router(ordens_servico.router)
    app.include_router(configuracoes.router)
    app.include_router(bling.router)
    app.include_router(sicoob.router)
    app.include_router(produtos.router)
    app.include_router(pedidos.router)
    app.include_router(nfe.router)
    app.include_router(tipos_documento.router)
    app.include_router(planocontas.router)
    app.include_router(consolidacoes.router)
    app.include_router(estoque.router)
    app.include_router(consultas.router)

    return app
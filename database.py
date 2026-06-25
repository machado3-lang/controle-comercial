import os
import logging
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_raw_url = os.environ.get("DATABASE_URL", "")

if not _raw_url:
    logger.warning(
        "DATABASE_URL is not set — falling back to SQLite (controle.db). "
        "Data will NOT persist across deploys. Set DATABASE_URL to use PostgreSQL."
    )
    SQLALCHEMY_DATABASE_URL = "sqlite:///./controle.db"
else:
    # Heroku / Railway may supply the legacy 'postgres://' scheme; SQLAlchemy
    # requires 'postgresql://' for the psycopg2 dialect.
    if _raw_url.startswith("postgres://"):
        _raw_url = _raw_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URL = _raw_url
    # Log the URL with credentials redacted for safety
    try:
        from urllib.parse import urlparse, urlunparse
        _parsed = urlparse(SQLALCHEMY_DATABASE_URL)
        _safe = _parsed._replace(netloc=f"****:****@{_parsed.hostname}:{_parsed.port}")
        logger.info("DATABASE_URL resolved to: %s", urlunparse(_safe))
    except Exception:
        logger.info("DATABASE_URL is set (could not parse for safe logging)")

connect_args = {}
if "sqlite" in SQLALCHEMY_DATABASE_URL:
    connect_args = {"check_same_thread": False}

engine_kwargs = {"connect_args": connect_args} if connect_args else {}
if "postgresql" in SQLALCHEMY_DATABASE_URL:
    engine_kwargs["pool_size"] = 10
    engine_kwargs["pool_pre_ping"] = True

try:
    engine = create_engine(SQLALCHEMY_DATABASE_URL, **engine_kwargs)
    # Verify the connection is reachable at startup so failures are visible
    # immediately in the logs rather than on the first request.
    with engine.connect() as _conn:
        _conn.execute(text("SELECT 1"))
    logger.info(
        "Database connection established successfully (%s)",
        "PostgreSQL" if "postgresql" in SQLALCHEMY_DATABASE_URL else "SQLite",
    )
except Exception as exc:
    logger.error(
        "Failed to connect to the database at startup: %s", exc, exc_info=True
    )
    raise

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

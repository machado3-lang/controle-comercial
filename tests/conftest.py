"""
Pytest fixtures and configuration for Controle Comercial tests.
"""
import os
import asyncio
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Set test environment
os.environ["ENVIRONMENT"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from database import Base, get_db
from main import app
from models import Cliente, Fornecedor, Empresa, Usuario


# Use SQLite in-memory for tests
TEST_DATABASE_URL = "sqlite:///./test.db"

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def test_engine():
    """Create test database engine"""
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def db_session(test_engine):
    """Create test database session"""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="function")
def override_get_db(db_session):
    """Override get_db dependency"""
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
async def async_client(override_get_db):
    """Async HTTP client for testing"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture(scope="function")
def test_user(db_session):
    """Create a test user"""
    from routers.auth import hash_senha
    user = Usuario(
        email="test@test.com",
        senha=hash_senha("test123"),
        nome="Test User",
        ativo=True,
        is_admin=True
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture(scope="function")
def test_empresa(db_session):
    """Create test empresa"""
    empresa = Empresa(
        razao_social="Teste Ltda",
        nome_fantasia="Teste",
        cnpj="12345678000195",
        endereco="Rua Teste, 123",
        cidade="São Paulo",
        estado="SP",
        cep="01234567"
    )
    db_session.add(empresa)
    db_session.commit()
    db_session.refresh(empresa)
    return empresa


@pytest.fixture(scope="function")
async def authenticated_client(async_client, test_user):
    """Client with authenticated session"""
    # Login to get session
    response = await async_client.post("/auth/login", data={
        "email": "test@test.com",
        "senha": "test123"
    })
    assert response.status_code in (200, 302, 303)
    return async_client


# Helper functions
def criar_cliente_teste(db_session, **kwargs):
    """Helper to create test cliente"""
    defaults = {
        "nome": "Cliente Teste",
        "cpf_cnpj": "12345678901",
        "tipo_pessoa": "fisica",
        "email": "cliente@test.com",
        "telefone": "11999999999",
        "endereco": "Rua Cliente, 123",
        "bairro": "Centro",
        "cidade": "São Paulo",
        "estado": "SP",
        "cep": "01234567",
        "situacao": "A"
    }
    defaults.update(kwargs)
    cliente = Cliente(**defaults)
    db_session.add(cliente)
    db_session.commit()
    db_session.refresh(cliente)
    return cliente


def criar_fornecedor_teste(db_session, **kwargs):
    """Helper to create test fornecedor"""
    defaults = {
        "nome": "Fornecedor Teste",
        "cpf_cnpj": "12345678000195",
        "tipo_pessoa": "juridica",
        "email": "fornecedor@test.com",
        "telefone": "11988888888",
        "endereco": "Rua Fornecedor, 456",
        "bairro": "Industrial",
        "cidade": "São Paulo",
        "estado": "SP",
        "cep": "01234567",
        "situacao": "A"
    }
    defaults.update(kwargs)
    fornecedor = Fornecedor(**defaults)
    db_session.add(fornecedor)
    db_session.commit()
    db_session.refresh(fornecedor)
    return fornecedor
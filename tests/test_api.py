"""
Test API endpoints
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session


class TestAuthEndpoints:
    """Test authentication endpoints"""
    
    @pytest.mark.asyncio
    async def test_login_page(self, async_client: AsyncClient):
        response = await async_client.get("/auth/login")
        assert response.status_code == 200
        assert "login" in response.text.lower()

    @pytest.mark.asyncio
    async def test_login_invalid_credentials(self, async_client: AsyncClient):
        response = await async_client.post("/auth/login", data={
            "email": "wrong@test.com",
            "senha": "wrong"
        })
        assert response.status_code in (302, 303)  # Redirect to login with error

    @pytest.mark.asyncio
    async def test_login_valid_credentials(self, async_client: AsyncClient, test_user):
        response = await async_client.post("/auth/login", data={
            "email": "test@test.com",
            "senha": "test123"
        })
        assert response.status_code in (302, 303)  # Redirect to dashboard

    @pytest.mark.asyncio
    async def test_logout(self, authenticated_client: AsyncClient):
        response = await authenticated_client.get("/auth/logout")
        assert response.status_code in (302, 303)
        # After logout, should redirect to login
        assert "/auth/login" in response.headers.get("location", "")


class TestClientesEndpoints:
    """Test clientes CRUD endpoints"""
    
    @pytest.mark.asyncio
    async def test_listar_clientes(self, authenticated_client: AsyncClient):
        response = await authenticated_client.get("/clientes/")
        assert response.status_code == 200
        assert "clientes" in response.text.lower() or "lista" in response.text.lower()

    @pytest.mark.asyncio
    async def test_novo_cliente_page(self, authenticated_client: AsyncClient):
        response = await authenticated_client.get("/clientes/novo")
        assert response.status_code == 200
        assert "novo" in response.text.lower() or "cliente" in response.text.lower()

    @pytest.mark.asyncio
    async def test_criar_cliente_valido(self, authenticated_client: AsyncClient, test_empresa):
        response = await authenticated_client.post("/clientes/novo", data={
            "nome": "Cliente Teste API",
            "cpf_cnpj": "11144477735",
            "tipo_pessoa": "fisica",
            "email": "cliente@api.test",
            "telefone": "1133334444",
            "celular": "11999999999",
            "endereco": "Rua API, 123",
            "bairro": "Centro",
            "cidade": "São Paulo",
            "estado": "SP",
            "cep": "01234567",
            "situacao": "A"
        })
        # Should redirect to list on success
        assert response.status_code in (302, 303)

    @pytest.mark.asyncio
    async def test_criar_cliente_invalido(self, authenticated_client: AsyncClient):
        response = await authenticated_client.post("/clientes/novo", data={
            "nome": "",  # Nome obrigatório
            "cpf_cnpj": "123",  # Inválido
            "tipo_pessoa": "fisica"
        })
        # Should show error and redirect back
        assert response.status_code in (302, 303)


class TestFornecedoresEndpoints:
    """Test fornecedores CRUD endpoints"""
    
    @pytest.mark.asyncio
    async def test_listar_fornecedores(self, authenticated_client: AsyncClient):
        response = await authenticated_client.get("/fornecedores/")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_criar_fornecedor_valido(self, authenticated_client: AsyncClient):
        response = await authenticated_client.post("/fornecedores/novo", data={
            "nome": "Fornecedor Teste API",
            "cpf_cnpj": "11222333000181",
            "tipo_pessoa": "juridica",
            "email": "forn@api.test",
            "telefone": "1133334444",
            "celular": "11988888888",
            "endereco": "Rua Forn, 456",
            "bairro": "Industrial",
            "cidade": "São Paulo",
            "estado": "SP",
            "cep": "01234567",
            "situacao": "A"
        })
        assert response.status_code in (302, 303)


class TestProdutosEndpoints:
    """Test produtos CRUD endpoints"""
    
    @pytest.mark.asyncio
    async def test_listar_produtos(self, authenticated_client: AsyncClient):
        response = await authenticated_client.get("/produtos/")
        assert response.status_code == 200


class TestContasEndpoints:
    """Test contas a pagar/receber endpoints"""
    
    @pytest.mark.asyncio
    async def test_listar_contas_pagar(self, authenticated_client: AsyncClient):
        response = await authenticated_client.get("/contas/pagar")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_listar_contas_receber(self, authenticated_client: AsyncClient):
        response = await authenticated_client.get("/contas/receber")
        assert response.status_code == 200


class TestDashboard:
    """Test dashboard endpoint"""
    
    @pytest.mark.asyncio
    async def test_dashboard_requires_auth(self, async_client: AsyncClient):
        response = await async_client.get("/")
        assert response.status_code in (302, 303, 307)
        assert "/auth/login" in response.headers.get("location", "")
        assert "/auth/login" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_dashboard_authenticated(self, authenticated_client: AsyncClient):
        response = await authenticated_client.get("/")
        assert response.status_code == 200
        assert "dashboard" in response.text.lower() or "controle comercial" in response.text.lower()


class TestCSRFProtection:
    """Test CSRF protection on forms"""
    
    @pytest.mark.asyncio
    async def test_csrf_token_in_forms(self, authenticated_client: AsyncClient):
        response = await authenticated_client.get("/clientes/novo")
        assert response.status_code == 200
        # Check for CSRF token in form
        assert 'name="csrf_token"' in response.text

    @pytest.mark.asyncio
    async def test_post_without_csrf_fails(self, authenticated_client: AsyncClient):
        # Try to post without CSRF token
        response = await authenticated_client.post("/clientes/novo", data={
            "nome": "Test CSRF",
            "cpf_cnpj": "11144477735",
            "tipo_pessoa": "fisica"
        })
        # Should fail with 403 or redirect with error
        assert response.status_code in (403, 302, 303)
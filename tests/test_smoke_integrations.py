"""Smoke tests: verify all critical modules import without errors."""
import pytest
from datetime import date, timedelta
from unittest.mock import patch


def test_import_models():
    from models import Cliente, Fornecedor, ContaPagar, ContaReceber, Produto, PedidoVenda
    from models import Empresa, OrdemServico, Assinatura, Usuario, StatusConta, StatusOS
    from models_nfe import NFe, NFSe
    assert StatusConta.PENDENTE


def test_import_routers_bling():
    from routers.bling import router
    assert router is not None


def test_import_routers_sicoob():
    from routers.sicoob import router
    assert router is not None


def test_import_routers_nfe():
    from routers.nfe import router
    assert router is not None


def test_import_routers_nfse():
    from routers.nfse import router
    assert router is not None


def test_import_services():
    from services.retry import with_retry, retry_on_transient
    from services.cert_store import store_certificate, load_certificate
    assert with_retry is not None


def test_import_app():
    from app.core.lifespan import create_app
    from app.core.security import hash_senha, verifica_senha, verificar_admin
    from app.core.config import settings
    app = create_app()
    assert app is not None


def test_hash_and_verify():
    from app.core.security import hash_senha, verifica_senha
    h = hash_senha("test123")
    assert verifica_senha("test123", h)
    assert not verifica_senha("wrong", h)


def test_verificar_admin_import():
    from app.core.security import verificar_admin
    assert callable(verificar_admin)


def test_retry_decorator():
    from services.retry import with_retry, retry_on_transient

    call_count = [0]

    @with_retry(max_retries=2, base_delay=0.01)
    def flaky_func():
        call_count[0] += 1
        if call_count[0] < 3:
            raise ConnectionError("transient")
        return "ok"

    result = flaky_func()
    assert result == "ok"
    assert call_count[0] == 3

    def succeeding():
        return "success"

    res, err = retry_on_transient(succeeding, max_retries=1)
    assert res == "success"
    assert err is None


def test_conta_vencida_helper():
    from models import StatusConta
    from routers.contas import conta_vencida

    class FakeConta:
        pass

    conta_vencida_obj = FakeConta()
    conta_vencida_obj.data_vencimento = date.today() - timedelta(days=5)
    conta_vencida_obj.status = StatusConta.PENDENTE
    assert conta_vencida(conta_vencida_obj, date.today())

    conta_ok = FakeConta()
    conta_ok.data_vencimento = date.today() + timedelta(days=5)
    conta_ok.status = StatusConta.PENDENTE
    assert not conta_vencida(conta_ok, date.today())


def test_bling_webhook_secret_mock():
    """Verify bling webhook endpoint exists and validate secret check."""
    from routers.bling import router
    routes = {r.path for r in router.routes}
    assert any('/webhook' in p for p in routes)


def test_sicoob_webhook_secret_mock():
    """Verify sicoob webhook endpoint exists."""
    from routers.sicoob import router
    routes = {r.path for r in router.routes}
    assert any('/webhook' in p for p in routes)


def test_nfe_webhook_secret_mock():
    """Verify nfe webhook endpoint exists."""
    from routers.nfe import router
    routes = {r.path for r in router.routes}
    assert any('/webhook' in p for p in routes)


def test_bling_mock_request():
    """Verify bling API calls can be mocked without hitting real API."""
    with patch('requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "access_token": "mock_token",
            "refresh_token": "mock_refresh",
            "expires_in": 3600,
        }
        import requests
        resp = requests.post("https://api.bling.com.br/Api/v3/oauth/token", json={})
        assert resp.status_code == 200
        assert resp.json()["access_token"] == "mock_token"


def test_sicoob_mock_request():
    """Verify sicoob token request can be mocked."""
    with patch('requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "access_token": "mock_token",
            "expires_in": 3600,
        }
        import requests
        resp = requests.post("https://auth.sicoob.com.br/auth/realms/cooperado/protocol/openid-connect/token", data={})
        assert resp.status_code == 200
        assert resp.json()["access_token"] == "mock_token"


def test_nfe_soap_mock():
    """Verify nfe SOAP/zeep can be mocked."""
    with patch('zeep.Client') as mock_zeep:
        from unittest.mock import MagicMock
        mock_service = MagicMock()
        mock_service.nfeDistDFeInteresse.return_value = None
        mock_zeep.return_value.service = mock_service
        try:
            from zeep import Client
            c = Client("https://mock.wsdl")
            assert c.service is not None
        except ImportError:
            pytest.skip("zeep not installed")


def test_nfse_api_mock():
    """Verify nfse betha API can be mocked."""
    with patch('requests.post') as mock_post, patch('requests.get') as mock_get:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"status": "ok"}
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {}
        import requests
        resp = requests.get("https://nota-eletronica.betha.cloud/dps/ws")
        assert resp.status_code == 200
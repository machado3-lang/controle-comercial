"""Smoke test da tela de boletos (abas e endpoints de consulta)."""
import pytest


@pytest.mark.asyncio
async def test_tela_boletos_renderiza_aba_conferencia(authenticated_client, db_session, test_empresa):
    resp = await authenticated_client.get("/sicoob/boletos")

    assert resp.status_code == 200
    assert "Conferência (abertos no Sicoob)" in resp.text
    assert "conferirAbertos()" in resp.text


@pytest.mark.asyncio
async def test_conferencia_exige_periodo(authenticated_client, db_session, test_empresa):
    resp = await authenticated_client.get("/sicoob/api/conferencia-abertos")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "período" in data["error"]

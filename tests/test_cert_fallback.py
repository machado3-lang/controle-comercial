"""Testes do fallback de certificado A1 da empresa.

Cobrem o caso em que o cofre do banco não abre (banco restaurado de outro
ambiente / CERT_MASTER_KEY diferente) e o sistema precisa usar o arquivo
CERT_PATH do .env sem aceitar blob inválido como certificado.
"""
import base64
import datetime
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from services.cert_store import resolver_certificado_empresa

SENHA = "senha-teste"


def _gerar_pfx(senha: str = SENHA) -> bytes:
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CERTIFICADO TESTE")])
    agora = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - datetime.timedelta(days=1))
        .not_valid_after(agora + datetime.timedelta(days=30))
        .sign(chave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"teste", chave, cert, None, serialization.BestAvailableEncryption(senha.encode())
    )


def _empresa(**kwargs):
    dados = {
        "cert_id": None,
        "cert_base64": None,
        "cert_path": None,
        "cert_password": None,
    }
    dados.update(kwargs)
    return SimpleNamespace(**dados)


@pytest.fixture
def pfx_valido():
    return _gerar_pfx()


def test_fallback_para_arquivo_quando_blob_do_banco_e_invalido(tmp_path, monkeypatch, pfx_valido):
    arquivo = tmp_path / "certificado.pfx"
    arquivo.write_bytes(pfx_valido)
    monkeypatch.setenv("CERT_PATH", str(arquivo))
    monkeypatch.setenv("CERT_PASSWORD", SENHA)

    empresa = _empresa(cert_base64=base64.b64encode(b"conteudo-cifrado-invalido").decode())

    pfx, senha = resolver_certificado_empresa(empresa)
    assert pfx == pfx_valido
    assert senha == SENHA


def test_prefere_base64_valido_ao_arquivo(tmp_path, monkeypatch, pfx_valido):
    arquivo = tmp_path / "certificado.pfx"
    arquivo.write_bytes(b"nao-e-pfx")
    monkeypatch.setenv("CERT_PATH", str(arquivo))
    monkeypatch.setenv("CERT_PASSWORD", "outra-senha")

    empresa = _empresa(
        cert_base64=base64.b64encode(pfx_valido).decode(),
        cert_password=SENHA,
    )

    pfx, senha = resolver_certificado_empresa(empresa)
    assert pfx == pfx_valido
    assert senha == SENHA


def test_sem_certificado_utilizavel_retorna_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CERT_PATH", str(tmp_path / "inexistente.pfx"))
    empresa = _empresa(cert_base64=base64.b64encode(b"lixo").decode())
    assert resolver_certificado_empresa(empresa) is None


def test_senha_errada_no_blob_nao_impede_arquivo_valido(tmp_path, monkeypatch, pfx_valido):
    arquivo = tmp_path / "certificado.pfx"
    arquivo.write_bytes(pfx_valido)
    monkeypatch.setenv("CERT_PATH", str(arquivo))
    monkeypatch.setenv("CERT_PASSWORD", SENHA)

    empresa = _empresa(
        cert_base64=base64.b64encode(pfx_valido).decode(),
        cert_password="senha-errada",
    )

    pfx, senha = resolver_certificado_empresa(empresa)
    assert pfx == pfx_valido
    assert senha == SENHA

"""
Certificate Storage Service
Stores certificates encrypted in the PostgreSQL database (column cert_base64 /
sicoob_cert_base64 / sicoob_cert_key_base64 on the empresa table) instead of the
filesystem. Uses AES-256-GCM encryption with a master key from environment.
Persisting in the DB keeps certificates available across Railway redeploys
(filesystem is ephemeral).
"""
import os
import base64
import logging
from typing import Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.serialization import pkcs12

logger = logging.getLogger(__name__)

# Mapeia o tipo de certificado para a coluna da tabela empresa
_CERT_COLUMN = {
    "empresa": "cert_base64",
    "sicoob": "sicoob_cert_base64",
    "sicoob_key": "sicoob_cert_key_base64",
}

_MASTER_KEY = None


def _get_master_key() -> bytes:
    """Derive master encryption key from environment variable."""
    global _MASTER_KEY
    if _MASTER_KEY is not None:
        return _MASTER_KEY

    secret = os.environ.get("CERT_MASTER_KEY")
    if not secret:
        dev_secret = os.environ.get("SECRET_KEY", "dev-only-change-in-production")
        secret = dev_secret
        logger.warning("CERT_MASTER_KEY não definida. Usando SECRET_KEY como fallback (apenas desenvolvimento).")

    salt = b"controle-comercial-cert-salt-v1"
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000)
    _MASTER_KEY = kdf.derive(secret.encode())
    return _MASTER_KEY


def _encrypt(data: bytes) -> Tuple[bytes, bytes]:
    key = _get_master_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return nonce, ciphertext


def _decrypt(nonce: bytes, ciphertext: bytes) -> bytes:
    key = _get_master_key()
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def _get_session():
    from database import SessionLocal
    return SessionLocal()


def _get_empresa(db, cert_id: int):
    from models import Empresa
    return db.query(Empresa).filter(Empresa.id == cert_id).first()


def store_certificate(cert_type: str, cert_id: int, pfx_data: bytes, password: str) -> dict:
    """
    Store a certificate (PFX or PEM) encrypted in the database.
    Returns dict with metadata.
    """
    column = _CERT_COLUMN.get(cert_type)
    if not column:
        raise ValueError(f"Tipo de certificado desconhecido: {cert_type}")

    try:
        private_key, cert, _ = pkcs12.load_key_and_certificates(
            pfx_data, password.encode() if password else None
        )
        not_after = cert.not_valid_after
        subject = cert.subject.rfc4514_string()
    except Exception as e:
        logger.warning(f"Could not parse certificate (pode ser PEM): {e}")
        not_after = None
        subject = ""

    nonce, encrypted = _encrypt(pfx_data)
    blob = base64.b64encode(nonce + encrypted).decode()

    db = _get_session()
    try:
        empresa = _get_empresa(db, cert_id)
        if not empresa:
            raise ValueError(f"Empresa {cert_id} não encontrada")
        setattr(empresa, column, blob)
        db.commit()
    finally:
        db.close()

    return {"column": column, "validade": not_after, "subject": subject}


def load_certificate(cert_type: str, cert_id: int) -> Optional[bytes]:
    """Load and decrypt a certificate from the database."""
    column = _CERT_COLUMN.get(cert_type)
    if not column:
        logger.error(f"Tipo de certificado desconhecido: {cert_type}")
        return None

    db = _get_session()
    try:
        empresa = _get_empresa(db, cert_id)
        blob = getattr(empresa, column) if empresa else None
    finally:
        db.close()

    if not blob:
        return None

    try:
        raw = base64.b64decode(blob)
        nonce = raw[:12]
        ciphertext = raw[12:]
        return _decrypt(nonce, ciphertext)
    except Exception as e:
        logger.error(f"Erro ao descriptografar certificado ({cert_type}): {e}")
        return None


def delete_certificate(cert_type: str, cert_id: int) -> bool:
    """Delete a stored certificate (clears the DB column)."""
    column = _CERT_COLUMN.get(cert_type)
    if not column:
        return False
    db = _get_session()
    try:
        empresa = _get_empresa(db, cert_id)
        if empresa and getattr(empresa, column):
            setattr(empresa, column, None)
            db.commit()
            return True
    except Exception as e:
        logger.error(f"Erro ao excluir certificado ({cert_type}): {e}")
    finally:
        db.close()
    return False


def get_certificate_path(cert_type: str, cert_id: int) -> Optional[str]:
    """Kept for API compatibility. DB-backed certs have no filesystem path."""
    return None


def extract_cert_info(pfx_data: bytes, password: str) -> dict:
    """Extract certificate metadata without storing."""
    try:
        private_key, cert, _ = pkcs12.load_key_and_certificates(
            pfx_data, password.encode() if password else None
        )
        return {
            "valida": cert.not_valid_after,
            "subject": cert.subject.rfc4514_string(),
            "issuer": cert.issuer.rfc4514_string(),
            "serial": str(cert.serial_number),
        }
    except Exception as e:
        logger.error(f"Erro ao extrair info do certificado: {e}")
        return {}


def create_temp_pfx(cert_type: str, cert_id: int) -> Optional[str]:
    """Create a temporary decrypted PFX/PEM file for external tools (zeep, requests)."""
    pfx_data = load_certificate(cert_type, cert_id)
    if not pfx_data:
        return None
    import tempfile
    suffix = ".pem" if cert_type in ("sicoob", "sicoob_key") else ".pfx"
    temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    temp_file.write(pfx_data)
    temp_file.close()
    return temp_file.name

"""
Certificate Storage Service
Stores certificates as encrypted files on disk instead of database.
Uses AES-256-GCM encryption with a master key from environment.
"""
import os
import base64
import logging
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.serialization import pkcs12
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

CERT_DIR = Path("certs")
CERT_DIR.mkdir(exist_ok=True)

_MASTER_KEY = None


def _get_master_key() -> bytes:
    """Derive master encryption key from environment variable."""
    global _MASTER_KEY
    if _MASTER_KEY is not None:
        return _MASTER_KEY
    
    secret = os.environ.get("CERT_MASTER_KEY")
    if not secret:
        # In development, generate a persistent key from SECRET_KEY
        dev_secret = os.environ.get("SECRET_KEY", "dev-only-change-in-production")
        secret = dev_secret
        logger.warning("CERT_MASTER_KEY não definida. Usando SECRET_KEY como fallback (apenas desenvolvimento).")
    
    # Derive 32-byte key using PBKDF2
    salt = b"controle-comercial-cert-salt-v1"  # Fixed salt for deterministic derivation
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600000,
    )
    _MASTER_KEY = kdf.derive(secret.encode())
    return _MASTER_KEY


def _encrypt(data: bytes) -> Tuple[bytes, bytes]:
    """Encrypt data with AES-256-GCM. Returns (nonce, ciphertext)."""
    key = _get_master_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return nonce, ciphertext


def _decrypt(nonce: bytes, ciphertext: bytes) -> bytes:
    """Decrypt data with AES-256-GCM."""
    key = _get_master_key()
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def store_certificate(cert_type: str, cert_id: int, pfx_data: bytes, password: str) -> dict:
    """
    Store a PFX certificate securely.
    Returns dict with file paths and metadata.
    """
    # Extract certificate info
    try:
        private_key, cert, _ = pkcs12.load_key_and_certificates(
            pfx_data, password.encode() if password else None
        )
        not_after = cert.not_valid_after
        subject = cert.subject.rfc4514_string()
    except Exception as e:
        logger.warning(f"Could not parse certificate: {e}")
        not_after = None
        subject = ""
    
    # Encrypt the PFX
    nonce, encrypted = _encrypt(pfx_data)
    
    # Save encrypted file
    filename = f"{cert_type}_{cert_id}.pfx.enc"
    filepath = CERT_DIR / filename
    
    with open(filepath, "wb") as f:
        f.write(nonce + encrypted)
    
    # Set restrictive permissions (owner read/write only)
    os.chmod(filepath, 0o600)
    
    return {
        "path": str(filepath),
        "filename": filename,
        "validade": not_after,
        "subject": subject,
    }


def load_certificate(cert_type: str, cert_id: int) -> Optional[bytes]:
    """Load and decrypt a PFX certificate."""
    filename = f"{cert_type}_{cert_id}.pfx.enc"
    filepath = CERT_DIR / filename
    
    if not filepath.exists():
        return None
    
    try:
        with open(filepath, "rb") as f:
            data = f.read()
        
        nonce = data[:12]
        ciphertext = data[12:]
        return _decrypt(nonce, ciphertext)
    except Exception as e:
        logger.error(f"Erro ao descriptografar certificado {filename}: {e}")
        return None


def delete_certificate(cert_type: str, cert_id: int) -> bool:
    """Delete a stored certificate."""
    filename = f"{cert_type}_{cert_id}.pfx.enc"
    filepath = CERT_DIR / filename
    
    try:
        if filepath.exists():
            filepath.unlink()
            return True
    except Exception as e:
        logger.error(f"Erro ao excluir certificado {filename}: {e}")
    return False


def get_certificate_path(cert_type: str, cert_id: int) -> Optional[str]:
    """Get the filesystem path for a certificate (for external tools)."""
    filename = f"{cert_type}_{cert_id}.pfx.enc"
    filepath = CERT_DIR / filename
    return str(filepath) if filepath.exists() else None


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
    """Create a temporary decrypted PFX file for external tools (zeep, requests)."""
    pfx_data = load_certificate(cert_type, cert_id)
    if not pfx_data:
        return None
    
    import tempfile
    temp_file = tempfile.NamedTemporaryFile(suffix=".pfx", delete=False)
    temp_file.write(pfx_data)
    temp_file.close()
    return temp_file.name
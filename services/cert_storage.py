"""
Certificate Storage Service - Armazena certificados A1 como arquivos criptografados
em vez de base64 no banco de dados.
"""
import os
import base64
import secrets
import tempfile
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


CERT_DIR = os.path.join(tempfile.gettempdir(), "controle_certs")
os.makedirs(CERT_DIR, exist_ok=True)


class CertificateStorage:
    """Gerencia armazenamento seguro de certificados A1 (PFX)."""
    
    def __init__(self, master_key: str = None):
        """
        Inicializa o storage.
        
        Args:
            master_key: Chave mestra para criptografia. Se None, usa variável de ambiente
                       CERT_MASTER_KEY ou gera uma temporária (dev only).
        """
        self.master_key = master_key or os.environ.get("CERT_MASTER_KEY")
        if not self.master_key:
            import sys
            is_dev = os.environ.get("ENVIRONMENT", "development") == "development"
            if is_dev:
                self.master_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
                print("AVISO: CERT_MASTER_KEY não definida. Usando chave temporária (dev only).")
            else:
                raise RuntimeError("CERT_MASTER_KEY é obrigatória em produção. Defina no ambiente.")
        
        # Deriva chave Fernet da master_key
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"controle_cert_salt_v1",
            iterations=100000,
        )
        self.fernet = Fernet(base64.urlsafe_b64encode(kdf.derive(self.master_key.encode())))
    
    def _cert_path(self, identifier: str) -> str:
        """Retorna caminho do arquivo do certificado."""
        safe_id = "".join(c for c in identifier if c.isalnum() or c in "-_")
        return os.path.join(CERT_DIR, f"cert_{safe_id}.enc")
    
    def store(self, identifier: str, pfx_data: bytes, password: str = None) -> str:
        """
        Armazena certificado PFX criptografado.
        
        Args:
            identifier: Identificador único (ex: "empresa_1", "sicoob_1")
            pfx_data: Bytes do arquivo PFX
            password: Senha do PFX (opcional, armazenada junto criptografada)
        
        Returns:
            Caminho do arquivo armazenado
        """
        import json
        payload = {
            "pfx": base64.b64encode(pfx_data).decode(),
            "password": password or "",
        }
        encrypted = self.fernet.encrypt(json.dumps(payload).encode())
        
        path = self._cert_path(identifier)
        with open(path, "wb") as f:
            f.write(encrypted)
        
        return path
    
    def load(self, identifier: str) -> tuple[bytes, str]:
        """
        Carrega certificado PFX descriptografado.
        
        Returns:
            Tupla (pfx_bytes, password)
        """
        import json
        path = self._cert_path(identifier)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Certificado não encontrado: {identifier}")
        
        with open(path, "rb") as f:
            encrypted = f.read()
        
        decrypted = self.fernet.decrypt(encrypted)
        payload = json.loads(decrypted.decode())
        
        pfx_bytes = base64.b64decode(payload["pfx"])
        password = payload.get("password", "")
        
        return pfx_bytes, password
    
    def load_to_temp_file(self, identifier: str) -> str:
        """
        Carrega certificado e salva em arquivo temporário para uso com requests.
        
        Returns:
            Caminho do arquivo temporário PFX
        """
        pfx_bytes, _ = self.load(identifier)
        tmp_path = os.path.join(tempfile.gettempdir(), f"cert_{identifier}_{secrets.token_hex(8)}.pfx")
        with open(tmp_path, "wb") as f:
            f.write(pfx_bytes)
        return tmp_path
    
    def exists(self, identifier: str) -> bool:
        """Verifica se certificado existe."""
        return os.path.exists(self._cert_path(identifier))
    
    def delete(self, identifier: str) -> bool:
        """Remove certificado armazenado."""
        path = self._cert_path(identifier)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
    
    def get_pem_combined(self, identifier: str) -> str:
        """
        Carrega PFX e retorna caminho de arquivo PEM combinado (key + cert)
        para uso com requests/zeep.
        """
        from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
        
        pfx_bytes, password = self.load(identifier)
        private_key, cert, _ = pkcs12.load_key_and_certificates(
            pfx_bytes,
            password=password.encode() if password else None
        )
        
        combined = private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=NoEncryption()
        ) + cert.public_bytes(Encoding.PEM)
        
        tmp_path = os.path.join(tempfile.gettempdir(), f"cert_combined_{identifier}_{secrets.token_hex(8)}.pem")
        with open(tmp_path, "wb") as f:
            f.write(combined)
        return tmp_path


# Instância global para uso simples
_default_storage = None

def get_certificate_storage() -> CertificateStorage:
    """Retorna instância singleton do storage."""
    global _default_storage
    if _default_storage is None:
        _default_storage = CertificateStorage()
    return _default_storage
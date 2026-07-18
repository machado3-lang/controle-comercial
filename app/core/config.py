"""
Configuration management for Controle Comercial.
"""
import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    
    # Database
    DATABASE_URL: str = "sqlite:///./controle.db"
    
    # Security
    SECRET_KEY: str
    CERT_MASTER_KEY: str = ""
    CSRF_SECRET_KEY: str = ""
    
    # Environment
    ENVIRONMENT: str = "development"
    ALLOWED_HOSTS: str = "*"
    
    # CORS
    CORS_ORIGINS: str = ""
    
    # Rate limiting
    RATE_LIMIT_LOGIN: str = "5/minute"
    RATE_LIMIT_WEBHOOK: str = "100/minute"
    RATE_LIMIT_API: str = "1000/hour"
    
    # Session
    SESSION_COOKIE_SECURE: bool = False
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "lax"
    
    # Setup token for /auth/setup (optional)
    SETUP_TOKEN: str = ""
    
    # External APIs
    BLING_API_URL: str = "https://api.bling.com.br/Api/v3"
    BLING_AUTH_URL: str = "https://www.bling.com.br/Api/v3/oauth/authorize"
    BLING_TOKEN_URL: str = "https://www.bling.com.br/Api/v3/oauth/token"
    
    NOTAAS_API_URL: str = "https://platform.notaas.com.br/api/v1"
    
    SICOOB_API_URL: str = "https://api.sicoob.com.br/cobranca-bancaria/v3"
    SICOOB_AUTH_URL: str = "https://auth.sicoob.com.br/auth/realms/cooperado/protocol/openid-connect/token"
    
    # NFSe
    BETHA_NFSE_URL: str = "https://nota-eletronica.betha.cloud/dps/ws"
    BETHA_USUARIO: str = ""
    BETHA_SENHA: str = ""
    BETHA_NFSE_DPS_URL: str = "https://nota-eletronica.betha.cloud/dps/ws"
    BETHA_NFSE_CANCEL_URL: str = "https://e-gov.betha.com.br/e-nota/contribuinte/services/NfseService"
    BETHA_CNPJ: str = ""
    BETHA_CERT_PATH: str = "./certs/certificado.pfx"
    BETHA_CERT_PASSWORD: str = ""
    MUNICIPIO_CODIGO: str = "5003702"
    REQUEST_TIMEOUT: int = 120
    
    # NFSe Rest
    BETHA_NFSE_REST_URL: str = "https://nota-eletronica.betha.cloud/api/v1/nfse"
    
    # ADN (Ambiente Nacional)
    ADN_NFSE_URL: str = "https://sefin.nfse.gov.br/SefinNacional"
    ADN_DANFSE_URL: str = "https://adn.nfse.gov.br/danfse"
    ADN_DFE_URL: str = "https://adn.nfse.gov.br/contribuintes/dfe"
    
    # SEFAZ NFe
    NFE_DIST_URL_PROD: str = "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
    NFE_DIST_URL_HOMOL: str = "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
    
    # Certificates
    CERT_DIR: str = "certs"
    CERT_PATH: str = "./certs/certificado.pfx"
    CERT_PASSWORD: str = ""
    
    # Static files
    STATIC_DIR: str = "static"
    UPLOAD_DIR: str = "static/uploads"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # Pagination
    DEFAULT_PAGE_SIZE: int = 50
    MAX_PAGE_SIZE: int = 200
    
    @property
    def allowed_hosts_list(self) -> List[str]:
        hosts = [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]
        railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if railway_domain:
            hosts.append(railway_domain)
            hosts.append(f"*.{railway_domain.split('.', 1)[-1]}")
        return hosts
    
    @property
    def cors_origins_list(self) -> List[str]:
        if not self.CORS_ORIGINS:
            return ["*"]
        return [h.strip() for h in self.CORS_ORIGINS.split(",")]
    
    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"
    
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"
    
    @property
    def session_cookie_secure(self) -> bool:
        if self.is_production:
            return True
        return self.SESSION_COOKIE_SECURE

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._validate()

    def _validate(self) -> None:
        if not self.SECRET_KEY or len(self.SECRET_KEY) < 16:
            raise ValueError("SECRET_KEY must be set with at least 16 characters")
        if not self.CSRF_SECRET_KEY:
            raise ValueError("CSRF_SECRET_KEY must be set (must not be empty)")
        if self.is_production:
            if self.ALLOWED_HOSTS == "*" or "*" in self.allowed_hosts_list:
                raise ValueError("ALLOWED_HOSTS must not contain '*' in production")
            if not self.allowed_hosts_list and not os.getenv("RAILWAY_PUBLIC_DOMAIN"):
                raise ValueError("ALLOWED_HOSTS must be set in production (or set RAILWAY_PUBLIC_DOMAIN)")
            origins = self.cors_origins_list
            if not origins or origins == ["*"] or "*" in origins:
                raise ValueError("CORS_ORIGINS must not be empty or contain '*' in production")


# Global settings instance
settings = Settings()


def get_cert_dir() -> Path:
    """Get certificate directory path."""
    path = Path(settings.CERT_DIR)
    path.mkdir(exist_ok=True)
    return path


def get_upload_dir() -> Path:
    """Get upload directory path."""
    path = Path(settings.UPLOAD_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path
"""
Security utilities for Controle Comercial.
"""
import hashlib
import logging
import secrets
from fastapi import Request
from sqlalchemy.orm import Session
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from starlette.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

PBKDF2_ITERATIONS = 600000


class CsrfSettings(BaseModel):
    """CSRF protection settings."""
    secret_key: str
    cookie_samesite: str = "lax"
    cookie_secure: bool = False
    header_name: str = "X-CSRF-Token"
    header_type: str = ""
    cookie_name: str = "csrf_token"
    cookie_path: str = "/"
    methods: tuple = ("POST", "PUT", "PATCH", "DELETE")
    token_location: str = "body"
    token_key: str = "csrf_token"


def get_csrf_config() -> CsrfSettings:
    """Get CSRF configuration from environment."""
    from app.core.config import settings
    return CsrfSettings(
        secret_key=settings.CSRF_SECRET_KEY,
        cookie_samesite="lax",
        cookie_secure=settings.session_cookie_secure,
    )


def init_csrf(app) -> None:
    """Initialize CSRF protection for the FastAPI app."""
    @CsrfProtect.load_config
    def get_csrf_settings():
        return get_csrf_config()


def hash_senha(senha: str) -> str:
    """Hash password using PBKDF2 with SHA-256."""
    salt = secrets.token_hex(16)
    hash_val = hashlib.pbkdf2_hmac(
        'sha256', senha.encode(), salt.encode(), PBKDF2_ITERATIONS
    ).hex()
    return f"2:{PBKDF2_ITERATIONS}:{salt}:{hash_val}"


def _verifica_legado(senha: str, hash_armazenado: str) -> bool:
    """Tenta verificar senhas em formatos legados (pré-PBKDF2).

    Formatos suportados:
      - "salt:sha256hex"            (git history: sha256(salt + senha))
      - "sha256hex:salt"            (variante)
      - "md5hex:salt"               (variante mais antiga)
    """
    try:
        if ":" not in hash_armazenado:
            return False
        left, right = hash_armazenado.split(":", 1)
        # caso "salt:hash"
        if len(left) >= 16 and len(right) == 64:
            salt, stored = left, right
            if hashlib.sha256((salt + senha).encode()).hexdigest() == stored:
                return True
            if hashlib.sha256((senha + salt).encode()).hexdigest() == stored:
                return True
        # caso "hash:salt" (hash de 32 = md5 ou 64 = sha256)
        if len(left) in (32, 64) and len(right) <= 32:
            stored, salt = left, right
            if len(stored) == 64:
                if hashlib.sha256((salt + senha).encode()).hexdigest() == stored:
                    return True
                if hashlib.sha256((senha + salt).encode()).hexdigest() == stored:
                    return True
            else:  # md5
                if hashlib.md5((salt + senha).encode()).hexdigest() == stored:
                    return True
                if hashlib.md5((senha + salt).encode()).hexdigest() == stored:
                    return True
                if hashlib.md5(senha.encode()).hexdigest() == stored:
                    return True
    except Exception:
        return False
    return False


def verifica_senha(senha: str, hash_armazenado: str) -> bool:
    """Verify password against stored hash (PBKDF2 ou formatos legados)."""
    if hash_armazenado.startswith("2:"):
        parts = hash_armazenado.split(":")
        if len(parts) >= 4 and parts[0] == "2":
            iterations = int(parts[1])
            salt = parts[2]
            hash_val = parts[3]
            computed = hashlib.pbkdf2_hmac(
                'sha256', senha.encode(), salt.encode(), iterations
            ).hex()
            return secrets.compare_digest(computed, hash_val)
        logger.warning("Formato de hash inválido")
        return False
    # Formatos legados: aceita para permitir migração automática no login.
    return _verifica_legado(senha, hash_armazenado)


def verifica_senha_admin(senha: str, hash_armazenado: str | None) -> bool:
    """Verify the shared 'senha_admin' (used to authorize deletions) against
    its stored hash. Returns False if there is no hash configured or the
    hash is in the legacy plaintext format (never matches, forcing re-save)."""
    if not senha or not hash_armazenado:
        return False
    if not hash_armazenado.startswith("2:"):
        # Legacy plaintext value - never treat as a valid match for security.
        return False
    return verifica_senha(senha, hash_armazenado)


def generate_csrf_token(request) -> str:
    """Generate CSRF token for request."""
    csrf = CsrfProtect()
    return csrf.generate_token(request)


async def csrf_exception_handler(request, exc: CsrfProtectError):
    """Handle CSRF validation errors."""
    if request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
        return JSONResponse(status_code=403, content={"detail": "CSRF token inválido ou ausente"})
    from starlette.requests import Request
    request.session["message"] = {"tipo": "danger", "texto": "Token de segurança inválido. Tente novamente."}
    return RedirectResponse(url=request.url.path, status_code=303)


def confirma_senha_usuario(request: Request, db: Session, senha: str) -> bool:
    """Verifica se a senha informada corresponde à senha do usuário logado.
    O usuário deve ser admin (is_admin=True). Retorna True se válido."""
    from models import Usuario
    user_id = request.session.get("user_id")
    if not user_id:
        return False
    user = db.query(Usuario).filter(Usuario.id == user_id).first()
    if not user or not user.is_admin:
        return False
    if not user.senha or not verifica_senha(senha, user.senha):
        return False
    return True


def verificar_admin(request: Request, db: Session) -> bool:
    """Returns True if current user is admin, otherwise sets error message and returns False."""
    from models import Usuario
    user_id = request.session.get("user_id")
    if not user_id:
        request.session["message"] = {"tipo": "danger", "texto": "Faça login primeiro"}
        return False
    user = db.query(Usuario).filter(Usuario.id == user_id).first()
    if not user or not user.is_admin:
        request.session["message"] = {"tipo": "danger", "texto": "Acesso negado: apenas administradores"}
        return False
    return True
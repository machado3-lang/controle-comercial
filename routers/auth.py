import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from models import Usuario
from app.core.config import settings
from app.core.lifespan import limiter
from app.core.security import hash_senha, verifica_senha, verificar_admin
from services.audit import registrar_auditoria

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])

_reset_tokens: dict = {}  # token -> (user_id, expires_at)


@router.get("/import-backup")
def import_backup(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    user = db.query(Usuario).filter(Usuario.id == request.session.get("user_id")).first()
    if not user or not user.is_admin:
        logger.warning(f"Tentativa de import-backup por usuário não-admin: {request.session.get('user_id')}")
        return {"success": False, "error": "Acesso negado"}
    logger.info(f"Import-backup iniciado por user_id={request.session.get('user_id')}")
    backup_dir = os.environ.get("BACKUP_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backups"))
    backup_path = os.path.join(backup_dir, "restore.sql")
    try:
        with open(backup_path, "r") as f:
            sql_content = f.read()
        # Executar em blocos pequenos
        statements = [s.strip() for s in sql_content.split(';') if s.strip() and s.strip().startswith('INSERT')]
        count = 0
        for stmt in statements[:100]:  # Limite seguro
            try:
                db.execute(text(stmt))
                count += 1
            except:
                pass
        db.commit()
        return {"success": True, "message": f"{count} registros importados"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/setup")
def setup_admin(request: Request, db: Session = Depends(get_db)):
    setup_token = os.environ.get("SETUP_TOKEN", "")
    if setup_token:
        provided = request.query_params.get("token") or ""
        if not secrets.compare_digest(provided, setup_token):
            return JSONResponse({"error": "Forbidden"}, status_code=403)
    else:
        logger.warning("SETUP_TOKEN não configurado - /auth/setup está acessível sem token!")
    admin = db.query(Usuario).filter(Usuario.email == "admin@controle.com").first()
    if not admin:
        senha = hash_senha("admin123")
        admin = Usuario(email="admin@controle.com", senha=senha, nome="Administrador", ativo=True, is_admin=True)
        db.add(admin)
        db.commit()
        return "Admin criado: admin@controle.com / admin123"
    admin.is_admin = True
    db.commit()
    return "Admin atualizado com is_admin=True"


@router.get("/migrate")
def migrate_check(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)
    user = db.query(Usuario).filter(Usuario.id == request.session.get("user_id")).first()
    if not user or not user.is_admin:
        request.session["message"] = {"tipo": "danger", "texto": "Acesso negado"}
        return RedirectResponse(url="/", status_code=303)
    try:
        count = db.query(Usuario).count()
        return f"Banco conectado. Total usuários: {count}."
    except Exception as e:
        return f"Erro: {str(e)}"


@router.get("/database/migrate")
def run_migrations(request: Request, db: Session = Depends(get_db)):
    if not verificar_admin(request, db):
        return {"success": False, "error": "Acesso negado: apenas administradores"}
    try:
        db.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE"))
        db.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS permissoes TEXT"))
        db.commit()
        return {"success": True, "message": "Migration executada com sucesso"}
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}


@router.get("/login")
def login_page(request: Request):
    message = request.session.pop("message", None)
    return request.app.state.templates.TemplateResponse(request, "auth/login.html", {"request": request, "message": message})


@router.post("/login")
@limiter.limit("5/minute") if not settings.ENVIRONMENT == "testing" else (lambda f: f)
def login(request: Request, db: Session = Depends(get_db), email: str = Form(""), senha: str = Form("")):
    try:
        usuario = db.query(Usuario).filter(Usuario.email == email, Usuario.ativo == True).first()
        if usuario and verifica_senha(senha, usuario.senha):
            # Upgrade hash legado para PBKDF2 automaticamente
            if not usuario.senha.startswith("2:"):
                usuario.senha = hash_senha(senha)
                db.commit()
            request.session["user_id"] = usuario.id
            request.session["user_nome"] = usuario.nome
            request.session["_csrf_token"] = secrets.token_hex(32)
            return RedirectResponse(url="/", status_code=303, headers={"Cache-Control": "no-cache, no-store"})
        request.session["message"] = {"tipo": "danger", "texto": "Email ou senha inválidos"}
        return RedirectResponse(url="/auth/login", status_code=303, headers={"Cache-Control": "no-cache, no-store"})
    except Exception as e:
        print(f"Login error: {e}")
        raise


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)


@router.get("/esqueci-senha")
def esqueci_senha_page(request: Request):
    message = request.session.pop("message", None)
    return request.app.state.templates.TemplateResponse(request, "auth/esqueci_senha.html", {"request": request, "message": message})

@router.post("/esqueci-senha")
@limiter.limit("3/minute")
def esqueci_senha(request: Request, db: Session = Depends(get_db), email: str = Form("")):
    usuario = db.query(Usuario).filter(Usuario.email == email).first()
    if usuario:
        token = secrets.token_hex(32)
        expires = datetime.utcnow() + timedelta(hours=1)
        _reset_tokens[token] = (usuario.id, expires)
        reset_url = str(request.base_url).rstrip("/") + f"/auth/reset-senha?token={token}"
        try:
            from services.email_service import enviar_email, render_email_template
            corpo = render_email_template("reset_senha.html", {
                "usuario_nome": usuario.nome or usuario.email,
                "reset_url": reset_url,
                "validade": "1 hora",
                "ano": datetime.now().year,
            })
            result = enviar_email(usuario.email, "Redefinição de Senha", corpo, db=db)
            if not result["success"]:
                logger.warning(f"Falha ao enviar email de reset: {result.get('error', 'desconhecido')}")
        except Exception as e:
            logger.warning(f"Erro ao enviar email de reset: {e}")
    request.session["message"] = {"tipo": "success", "texto": "Se o email existir, instruções de redefinição serão enviadas"}
    return RedirectResponse(url="/auth/esqueci-senha", status_code=303)


@router.get("/reset-senha")
def reset_senha_page(request: Request, token: str = Query("")):
    entry = _reset_tokens.get(token)
    if not entry:
        request.session["message"] = {"tipo": "danger", "texto": "Link inválido ou expirado"}
        return RedirectResponse(url="/auth/login", status_code=303)
    user_id, expires = entry
    if datetime.utcnow() > expires:
        _reset_tokens.pop(token, None)
        request.session["message"] = {"tipo": "danger", "texto": "Link expirado. Solicite novamente"}
        return RedirectResponse(url="/auth/login", status_code=303)
    return request.app.state.templates.TemplateResponse(request, "auth/reset_senha.html", {"request": request, "token": token})


@router.post("/reset-senha")
def reset_senha(request: Request, db: Session = Depends(get_db), token: str = Form(""), nova_senha: str = Form(""), confirmar_senha: str = Form("")):
    entry = _reset_tokens.get(token)
    if not entry:
        request.session["message"] = {"tipo": "danger", "texto": "Token inválido"}
        return RedirectResponse(url="/auth/login", status_code=303)
    user_id, expires = entry
    if datetime.utcnow() > expires:
        _reset_tokens.pop(token, None)
        request.session["message"] = {"tipo": "danger", "texto": "Token expirado"}
        return RedirectResponse(url="/auth/login", status_code=303)
    if nova_senha != confirmar_senha:
        return request.app.state.templates.TemplateResponse(request, "auth/reset_senha.html",
            {"request": request, "token": token, "message": {"tipo": "danger", "texto": "Senhas não conferem"}})
    if len(nova_senha) < 6:
        return request.app.state.templates.TemplateResponse(request, "auth/reset_senha.html",
            {"request": request, "token": token, "message": {"tipo": "danger", "texto": "Senha deve ter pelo menos 6 caracteres"}})
    usuario = db.query(Usuario).filter(Usuario.id == user_id).first()
    if not usuario:
        request.session["message"] = {"tipo": "danger", "texto": "Usuário não encontrado"}
        return RedirectResponse(url="/auth/login", status_code=303)
    usuario.senha = hash_senha(nova_senha)
    db.commit()
    _reset_tokens.pop(token, None)
    request.session["message"] = {"tipo": "success", "texto": "Senha redefinida com sucesso"}
    return RedirectResponse(url="/auth/login", status_code=303)


@router.get("/usuarios")
def listar_usuarios(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)
    user = db.query(Usuario).filter(Usuario.id == request.session.get("user_id")).first()
    if not user or not user.is_admin:
        request.session["message"] = {"tipo": "danger", "texto": "Acesso negado"}
        return RedirectResponse(url="/", status_code=303)
    message = request.session.pop("message", None)
    usuarios = db.query(Usuario).all()
    return request.app.state.templates.TemplateResponse(request, "auth/usuarios.html", {"request": request, "usuarios": usuarios, "message": message})


@router.get("/usuarios/novo")
def novo_usuario_page(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)
    user = db.query(Usuario).filter(Usuario.id == request.session.get("user_id")).first()
    if not user or not user.is_admin:
        request.session["message"] = {"tipo": "danger", "texto": "Acesso negado: apenas administradores"}
        return RedirectResponse(url="/auth/usuarios", status_code=303)
    return request.app.state.templates.TemplateResponse(request, "auth/usuario_form.html", {"request": request})


@router.post("/usuarios/novo")
def criar_usuario(request: Request, db: Session = Depends(get_db), nome: str = Form(""), email: str = Form(""), senha: str = Form(""), is_admin: str = Form(None), permissoes: str = Form("")):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)
    user = db.query(Usuario).filter(Usuario.id == request.session.get("user_id")).first()
    if not user or not user.is_admin:
        request.session["message"] = {"tipo": "danger", "texto": "Acesso negado"}
        return RedirectResponse(url="/auth/usuarios", status_code=303)
    if db.query(Usuario).filter(Usuario.email == email).first():
        request.session["message"] = {"tipo": "danger", "texto": "Email já cadastrado"}
        return RedirectResponse(url="/auth/usuarios", status_code=303)
    senha_hash = hash_senha(senha)
    usuario = Usuario(email=email, senha=senha_hash, nome=nome, ativo=True, is_admin=(is_admin == "on"), permissoes=permissoes)
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    registrar_auditoria(
        db, request.session.get("user_id"), "criar_usuario",
        "usuario", usuario.id, f"Usuário: {usuario.nome}, admin={usuario.is_admin}",
        request.client.host if request.client else None
    )
    request.session["message"] = {"tipo": "success", "texto": f"Usuário {usuario.nome} criado"}
    return RedirectResponse(url="/auth/usuarios", headers={"Cache-Control": "no-cache, no-store"}, status_code=303)


@router.get("/usuarios/{uid}/editar")
def editar_usuario_page(request: Request, uid: int, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)
    user = db.query(Usuario).filter(Usuario.id == request.session.get("user_id")).first()
    if not user or not user.is_admin:
        request.session["message"] = {"tipo": "danger", "texto": "Acesso negado: apenas administradores"}
        return RedirectResponse(url="/auth/usuarios", status_code=303)
    usuario = db.query(Usuario).filter(Usuario.id == uid).first()
    if not usuario:
        return RedirectResponse(url="/auth/usuarios", status_code=303)
    return request.app.state.templates.TemplateResponse(request, "auth/usuario_form.html", {"request": request, "usuario": usuario})


@router.post("/usuarios/{uid}/editar")
def editar_usuario(request: Request, uid: int, db: Session = Depends(get_db), nome: str = Form(""), email: str = Form(""), senha: str = Form(""), ativo: str = Form(None), is_admin: str = Form(None), permissoes: str = Form("")):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)
    user = db.query(Usuario).filter(Usuario.id == request.session.get("user_id")).first()
    if not user or not user.is_admin:
        request.session["message"] = {"tipo": "danger", "texto": "Acesso negado"}
        return RedirectResponse(url="/auth/usuarios", status_code=303)
    usuario = db.query(Usuario).filter(Usuario.id == uid).first()
    if usuario:
        usuario_old = {"nome": usuario.nome, "admin": usuario.is_admin, "ativo": usuario.ativo}
        usuario.nome = nome
        usuario.ativo = ativo == "on"
        usuario.is_admin = is_admin == "on"
        usuario.permissoes = permissoes
        if senha:
            usuario.senha = hash_senha(senha)
        db.commit()
        registrar_auditoria(
            db, request.session.get("user_id"), "editar_usuario",
            "usuario", uid,
            f"De: {usuario_old['nome']}(admin={usuario_old['admin']}) → Para: {usuario.nome}(admin={usuario.is_admin})",
            request.client.host if request.client else None
        )
        request.session["message"] = {"tipo": "success", "texto": "Usuário atualizado"}
    return RedirectResponse(url="/auth/usuarios", status_code=303)
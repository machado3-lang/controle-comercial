import hashlib
import secrets
import json
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import engine, get_db
from models import Usuario

router = APIRouter(prefix="/auth", tags=["Auth"])


def hash_senha(senha: str) -> str:
    salt = secrets.token_hex(16)
    return f"{salt}:{hashlib.sha256((salt + senha).encode()).hexdigest()}"


def verifica_senha(senha: str, hash_armazenado: str) -> bool:
    if ":" not in hash_armazenado:
        return hash_armazenado == hashlib.sha256(senha.encode()).hexdigest()
    salt, hash_val = hash_armazenado.split(":", 1)
    return hashlib.sha256((salt + senha).encode()).hexdigest() == hash_val


@router.get("/import-backup")
def import_backup(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    try:
        from sqlalchemy import text
        with open("restore.sql", "r") as f:
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
    try:
        count = db.query(Usuario).count()
        return f"Banco conectado. Total usuários: {count}."
    except Exception as e:
        return f"Erro: {str(e)}"


@router.get("/database/migrate")
def run_migrations(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    try:
        from sqlalchemy import text
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
    return request.app.state.templates.TemplateResponse("auth/login.html", {"request": request, "message": message})


@router.post("/login")
def login(request: Request, db: Session = Depends(get_db), email: str = Form(""), senha: str = Form("")):
    try:
        usuario = db.query(Usuario).filter(Usuario.email == email, Usuario.ativo == True).first()
        if usuario and verifica_senha(senha, usuario.senha):
            request.session["user_id"] = usuario.id
            request.session["user_nome"] = usuario.nome
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
    return request.app.state.templates.TemplateResponse("auth/esqueci_senha.html", {"request": request, "message": message})

@router.post("/esqueci-senha")
def esqueci_senha(request: Request, db: Session = Depends(get_db), email: str = Form("")):
    usuario = db.query(Usuario).filter(Usuario.email == email).first()
    request.session["message"] = {"tipo": "success", "texto": "Se o email existir, instruções serão enviadas"}
    return RedirectResponse(url="/auth/esqueci-senha", status_code=303)


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
    return request.app.state.templates.TemplateResponse("auth/usuarios.html", {"request": request, "usuarios": usuarios, "message": message})


@router.get("/usuarios/novo")
def novo_usuario_page(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)
    user = db.query(Usuario).filter(Usuario.id == request.session.get("user_id")).first()
    if not user or not user.is_admin:
        request.session["message"] = {"tipo": "danger", "texto": "Acesso negado: apenas administradores"}
        return RedirectResponse(url="/auth/usuarios", status_code=303)
    return request.app.state.templates.TemplateResponse("auth/usuario_form.html", {"request": request})


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
    return request.app.state.templates.TemplateResponse("auth/usuario_form.html", {"request": request, "usuario": usuario})


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
        usuario.nome = nome
        usuario.ativo = ativo == "on"
        usuario.is_admin = is_admin == "on"
        usuario.permissoes = permissoes
        if senha:
            usuario.senha = hash_senha(senha)
        db.commit()
        request.session["message"] = {"tipo": "success", "texto": "Usuário atualizado"}
    return RedirectResponse(url="/auth/usuarios", status_code=303)
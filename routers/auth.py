import hashlib
import secrets
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


@router.get("/setup")
def setup_admin(request: Request, db: Session = Depends(get_db)):
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, email VARCHAR(200) UNIQUE, senha VARCHAR(200), nome VARCHAR(200), ativo BOOLEAN, created_at DATETIME)"))
        conn.commit()
    admin = db.query(Usuario).filter(Usuario.email == "admin@controle.com").first()
    if not admin:
        senha = hash_senha("admin123")
        admin = Usuario(email="admin@controle.com", senha=senha, nome="Administrador", ativo=True)
        db.add(admin)
        db.commit()
        return "Admin criado: admin@controle.com / admin123"
    return "Admin já existe"


@router.get("/login")
def login_page(request: Request):
    message = request.session.pop("message", None)
    return request.app.state.templates.TemplateResponse("auth/login.html", {"request": request, "message": message})


@router.post("/login")
def login(request: Request, db: Session = Depends(get_db), email: str = Form(""), senha: str = Form("")):
    usuario = db.query(Usuario).filter(Usuario.email == email, Usuario.ativo == True).first()
    if usuario and verifica_senha(senha, usuario.senha):
        request.session["user_id"] = usuario.id
        request.session["user_nome"] = usuario.nome
        request.session["message"] = {"tipo": "success", "texto": f"Bem-vindo, {usuario.nome}!"}
        return RedirectResponse(url="/", status_code=303)
    request.session["message"] = {"tipo": "danger", "texto": "Email ou senha inválidos"}
    return RedirectResponse(url="/auth/login", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)


@router.get("/usuarios")
def listar_usuarios(request: Request, db: Session = Depends(get_db)):
    message = request.session.pop("message", None)
    usuarios = db.query(Usuario).all()
    return request.app.state.templates.TemplateResponse("auth/usuarios.html", {"request": request, "usuarios": usuarios, "message": message})


@router.get("/usuarios/novo")
def novo_usuario_page(request: Request):
    return request.app.state.templates.TemplateResponse("auth/usuario_form.html", {"request": request})


@router.post("/usuarios/novo")
def criar_usuario(request: Request, db: Session = Depends(get_db), nome: str = Form(""), email: str = Form(""), senha: str = Form("")):
    if db.query(Usuario).filter(Usuario.email == email).first():
        request.session["message"] = {"tipo": "danger", "texto": "Email já cadastrado"}
        return RedirectResponse(url="/auth/usuarios", status_code=303)
    senha_hash = hash_senha(senha)
    usuario = Usuario(email=email, senha=senha_hash, nome=nome, ativo=True)
    db.add(usuario)
    db.commit()
    request.session["message"] = {"tipo": "success", "texto": "Usuário criado"}
    return RedirectResponse(url="/auth/usuarios", status_code=303)


@router.get("/usuarios/{uid}/editar")
def editar_usuario_page(request: Request, uid: int, db: Session = Depends(get_db)):
    usuario = db.query(Usuario).filter(Usuario.id == uid).first()
    if not usuario:
        return RedirectResponse(url="/auth/usuarios", status_code=303)
    return request.app.state.templates.TemplateResponse("auth/usuario_form.html", {"request": request, "usuario": usuario})


@router.post("/usuarios/{uid}/editar")
def editar_usuario(request: Request, uid: int, db: Session = Depends(get_db), nome: str = Form(""), email: str = Form(""), senha: str = Form(""), ativo: str = Form(None)):
    usuario = db.query(Usuario).filter(Usuario.id == uid).first()
    if usuario:
        usuario.nome = nome
        usuario.ativo = ativo == "on"
        if senha:
            usuario.senha = hash_senha(senha)
        db.commit()
        request.session["message"] = {"tipo": "success", "texto": "Usuário atualizado"}
    return RedirectResponse(url="/auth/usuarios", status_code=303)
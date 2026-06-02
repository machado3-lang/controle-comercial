import bcrypt
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Usuario

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.get("/login")
def login_page(request: Request):
    return request.app.state.templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login")
def login(request: Request, db: Session = Depends(get_db), email: str = Form(""), senha: str = Form("")):
    usuario = db.query(Usuario).filter(Usuario.email == email, Usuario.ativo == True).first()
    if usuario and bcrypt.checkpw(senha.encode(), usuario.senha.encode()):
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
    usuarios = db.query(Usuario).all()
    return request.app.state.templates.TemplateResponse("auth/usuarios.html", {"request": request, "usuarios": usuarios})


@router.get("/usuarios/novo")
def novo_usuario_page(request: Request):
    return request.app.state.templates.TemplateResponse("auth/usuario_form.html", {"request": request})


@router.post("/usuarios/novo")
def criar_usuario(request: Request, db: Session = Depends(get_db), nome: str = Form(""), email: str = Form(""), senha: str = Form("")):
    if db.query(Usuario).filter(Usuario.email == email).first():
        request.session["message"] = {"tipo": "danger", "texto": "Email já cadastrado"}
        return RedirectResponse(url="/auth/usuarios", status_code=303)
    senha_hash = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
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
            usuario.senha = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
        db.commit()
        request.session["message"] = {"tipo": "success", "texto": "Usuário atualizado"}
    return RedirectResponse(url="/auth/usuarios", status_code=303)
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import Produto
from models_servico import Servico, ServicoInsumo

router = APIRouter(prefix="/servicos", tags=["Servicos"])

def get_messages(request: Request):
    message = request.session.pop("message", None)
    return [message] if message else []


@router.get("")
def listar_servicos(request: Request, db: Session = Depends(get_db)):
    messages = get_messages(request)
    servicos = db.query(Servico).all()
    return request.app.state.templates.TemplateResponse(
        "servicos/listar.html",
        {"request": request, "servicos": servicos, "messages": messages}
    )


@router.get("/novo")
def novo_servico_page(request: Request, db: Session = Depends(get_db)):
    produtos = db.query(Produto).all()
    produtos_json = [{"id": p.id, "nome": p.nome} for p in produtos]
    return request.app.state.templates.TemplateResponse(
        "servicos/form.html",
        {"request": request, "produtos": produtos, "produtos_json": produtos_json}
    )


@router.post("/novo")
def criar_servico(
    request: Request, db: Session = Depends(get_db),
    nome: str = Form(...),
    descricao: str = Form(""),
    preco_padrao: float = Form(0),
    insumos: str = Form("")
):
    servico = Servico(nome=nome, descricao=descricao, preco_padrao=preco_padrao)
    db.add(servico)
    db.commit()
    db.refresh(servico)
    
    if insumos:
        import json
        for item in json.loads(insumos):
            db.add(ServicoInsumo(servico_id=servico.id, produto_id=item["produto_id"], quantidade=item["quantidade"]))
        db.commit()
    
    request.session["message"] = {"tipo": "success", "texto": f"Serviço {servico.nome} criado"}
    return RedirectResponse(url="/servicos", status_code=303)


@router.get("/{servico_id}/editar")
def editar_servico_page(request: Request, servico_id: int, db: Session = Depends(get_db)):
    servico = db.query(Servico).filter(Servico.id == servico_id).first()
    produtos = db.query(Produto).all()
    produtos_json = [{"id": p.id, "nome": p.nome} for p in produtos]
    return request.app.state.templates.TemplateResponse(
        "servicos/form.html",
        {"request": request, "servico": servico, "produtos": produtos, "produtos_json": produtos_json}
    )


@router.post("/{servico_id}/editar")
def atualizar_servico(
    request: Request, servico_id: int, db: Session = Depends(get_db),
    nome: str = Form(...),
    descricao: str = Form(""),
    preco_padrao: float = Form(0),
    insumos: str = Form("")
):
    servico = db.query(Servico).filter(Servico.id == servico_id).first()
    if servico:
        servico.nome = nome
        servico.descricao = descricao
        servico.preco_padrao = preco_padrao
        
        db.query(ServicoInsumo).filter(ServicoInsumo.servico_id == servico_id).delete()
        if insumos:
            import json
            for item in json.loads(insumos):
                db.add(ServicoInsumo(servico_id=servico_id, produto_id=item["produto_id"], quantidade=item["quantidade"]))
        db.commit()
    
    return RedirectResponse(url="/servicos", status_code=303)


@router.get("/{servico_id}/insumos/json")
def insumos_json(request: Request, servico_id: int, db: Session = Depends(get_db)):
    insumos = db.query(ServicoInsumo).filter(ServicoInsumo.servico_id == servico_id).all()
    return [{"produto_id": i.produto_id, "quantidade": i.quantidade} for i in insumos]
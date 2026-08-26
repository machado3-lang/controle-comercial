import logging
import re
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models import Empresa, Transportadora, Fornecedor
from app.core.security import verificar_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transportadoras", tags=["Transportadoras"])


@router.get("")
def listar_transportadoras(request: Request, db: Session = Depends(get_db)):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/dashboard", status_code=303)
    empresa = db.query(Empresa).first()
    transportadoras = db.query(Transportadora).options(joinedload(Transportadora.fornecedor)).order_by(Transportadora.nome).all()
    fornecedores = db.query(Fornecedor).order_by(Fornecedor.nome).all()
    fornecedores_json = [
        {
            "id": f.id, "nome": f.nome or "", "cpf_cnpj": f.cpf_cnpj or "",
            "inscricao_estadual": f.inscricao_estadual or "", "endereco": f.endereco or "",
            "cidade": f.cidade or "", "estado": f.estado or "", "cep": f.cep or "",
        }
        for f in fornecedores
    ]
    return request.app.state.templates.TemplateResponse(request,
        "transportadoras/listar.html",
        {"request": request, "empresa": empresa, "transportadoras": transportadoras,
         "fornecedores": fornecedores, "fornecedores_json": fornecedores_json}
    )


def _resolver_fornecedor_transportadora(db, fornecedor_id, cpf_cnpj):
    """Resolve o Fornecedor a vincular a uma transportadora.

    Usa o fornecedor_id informado no formulario; se nao houver, tenta achar um
    fornecedor ja cadastrado com o mesmo CNPJ (normalizado), evitando cadastrar
    a transportadora em duplicidade com um fornecedor existente.
    """
    if fornecedor_id:
        try:
            f = db.query(Fornecedor).filter(Fornecedor.id == int(fornecedor_id)).first()
            if f:
                return f
        except (ValueError, TypeError):
            pass
    cpf_cnpj_num = re.sub(r'\D', '', cpf_cnpj or '')
    if cpf_cnpj_num:
        for f in db.query(Fornecedor).filter(Fornecedor.cpf_cnpj.isnot(None), Fornecedor.cpf_cnpj != "").all():
            if re.sub(r'\D', '', f.cpf_cnpj or '') == cpf_cnpj_num:
                return f
    return None


@router.post("/criar")
def criar_transportadora(
    request: Request, db: Session = Depends(get_db),
    nome: str = Form(...),
    cpf_cnpj: str = Form(""),
    inscricao_estadual: str = Form(""),
    endereco: str = Form(""),
    cidade: str = Form(""),
    estado: str = Form(""),
    cep: str = Form(""),
    fornecedor_id: str = Form(""),
):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/dashboard", status_code=303)
    empresa = db.query(Empresa).first()
    fornecedor = _resolver_fornecedor_transportadora(db, fornecedor_id, cpf_cnpj)
    t = Transportadora(
        empresa_id=empresa.id if empresa else None,
        nome=nome,
        cpf_cnpj=cpf_cnpj or None,
        inscricao_estadual=inscricao_estadual or None,
        endereco=endereco or None,
        cidade=cidade or None,
        estado=estado or None,
        cep=cep or None,
        fornecedor_id=fornecedor.id if fornecedor else None,
    )
    db.add(t)
    db.commit()
    if fornecedor:
        request.session["message"] = f"Transportadora '{nome}' cadastrada e vinculada ao fornecedor '{fornecedor.nome}'!"
    else:
        request.session["message"] = f"Transportadora '{nome}' cadastrada!"
    return RedirectResponse(url="/transportadoras", status_code=303)


@router.post("/{transportadora_id}/editar")
def editar_transportadora(
    request: Request, transportadora_id: int, db: Session = Depends(get_db),
    nome: str = Form(...),
    cpf_cnpj: str = Form(""),
    inscricao_estadual: str = Form(""),
    endereco: str = Form(""),
    cidade: str = Form(""),
    estado: str = Form(""),
    cep: str = Form(""),
    fornecedor_id: str = Form(""),
):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/dashboard", status_code=303)
    t = db.query(Transportadora).filter(Transportadora.id == transportadora_id).first()
    if t:
        fornecedor = _resolver_fornecedor_transportadora(db, fornecedor_id, cpf_cnpj)
        t.nome = nome
        t.cpf_cnpj = cpf_cnpj or None
        t.inscricao_estadual = inscricao_estadual or None
        t.endereco = endereco or None
        t.cidade = cidade or None
        t.estado = estado or None
        t.cep = cep or None
        t.fornecedor_id = fornecedor.id if fornecedor else None
        db.commit()
        request.session["message"] = "Transportadora atualizada!"
    return RedirectResponse(url="/transportadoras", status_code=303)


@router.post("/{transportadora_id}/excluir")
def excluir_transportadora(request: Request, transportadora_id: int, db: Session = Depends(get_db)):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/dashboard", status_code=303)
    t = db.query(Transportadora).filter(Transportadora.id == transportadora_id).first()
    if t:
        db.delete(t)
        db.commit()
        request.session["message"] = "Transportadora excluída!"
    return RedirectResponse(url="/transportadoras", status_code=303)

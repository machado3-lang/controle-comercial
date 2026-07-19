"""Rotas de controle de estoque: ajuste de inventario, historico e pecas da OS."""
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from database import get_db
from app.core.security import verificar_admin
from services.audit import registrar_auditoria
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/estoque", tags=["estoque"])


@router.post("/produto/{produto_id}/ajustar")
def ajustar_estoque(
    request: Request, produto_id: int, db: Session = Depends(get_db),
    quantidade_fisica: float = Form(...), motivo: str = Form(""),
):
    from services.estoque_service import ajuste_inventario
    from models import Produto

    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)

    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if not produto:
        request.session["error"] = "Produto nao encontrado"
        return RedirectResponse(url=request.headers.get("Referer", "/produtos"), status_code=303)

    usuario_id = request.session.get("usuario_id")
    ok = ajuste_inventario(db, produto_id, quantidade_fisica, usuario_id=usuario_id, motivo=motivo or "Ajuste de inventario")
    if ok:
        request.session["message"] = f"Estoque de {produto.nome} ajustado para {quantidade_fisica}"
        try:
            registrar_auditoria(db, usuario_id, "ajuste_estoque", "produto", produto_id,
                                detalhes=f"qtd_fisica={quantidade_fisica}; motivo={motivo}")
        except Exception:
            pass
    else:
        request.session["message"] = "Nenhuma alteracao necessaria (saldo ja conferia) ou produto invalido"
    return RedirectResponse(url=request.headers.get("Referer", "/produtos"), status_code=303)


@router.get("/produto/{produto_id}/movimentacoes")
def historico_estoque(request: Request, produto_id: int, db: Session = Depends(get_db)):
    from models_estoque import MovimentacaoEstoque
    from models import Produto

    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)

    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    movs = db.query(MovimentacaoEstoque).filter(
        MovimentacaoEstoque.produto_id == produto_id
    ).order_by(MovimentacaoEstoque.data.desc()).all()
    return request.app.state.templates.TemplateResponse(request,
        "estoque/historico.html",
        {"request": request, "produto": produto, "movs": movs,
         "messages": _get_messages(request)}
    )


@router.post("/os/{os_id}/peca/adicionar")
def adicionar_peca_os(
    request: Request, os_id: int, db: Session = Depends(get_db),
    produto_id: int = Form(...), quantidade: float = Form(1),
):
    from models_estoque import OSPeca
    from models import OrdemServico, Produto

    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)

    os_obj = db.query(OrdemServico).filter(OrdemServico.id == os_id).first()
    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if not os_obj or not produto:
        request.session["error"] = "OS ou produto invalido"
        return RedirectResponse(url=request.headers.get("Referer", "/ordens-servico"), status_code=303)

    peca = OSPeca(os_id=os_id, produto_id=produto_id, quantidade=quantidade,
                   valor_unitario=produto.preco_custo or produto.preco)
    db.add(peca)
    db.commit()
    request.session["message"] = f"Peca {produto.nome} vinculada a OS #{os_id}"
    return RedirectResponse(url=request.headers.get("Referer", f"/ordens-servico/{os_id}"), status_code=303)


@router.post("/os/{os_id}/peca/{peca_id}/remover")
def remover_peca_os(request: Request, os_id: int, peca_id: int, db: Session = Depends(get_db)):
    from models_estoque import OSPeca
    if not verificar_admin(request, db):
        return RedirectResponse(url="/login", status_code=303)
    peca = db.query(OSPeca).filter(OSPeca.id == peca_id, OSPeca.os_id == os_id).first()
    if peca:
        db.delete(peca)
        db.commit()
        request.session["message"] = "Peca removida da OS"
    return RedirectResponse(url=request.headers.get("Referer", f"/ordens-servico/{os_id}"), status_code=303)


def _get_messages(request):
    msgs = []
    for key in ("message", "error"):
        if request.session.get(key):
            msgs.append({"tipo": "success" if key == "message" else "danger", "texto": request.session.pop(key)})
    return msgs

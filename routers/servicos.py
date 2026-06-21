from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

router = APIRouter(prefix="/servicos", tags=["Servicos"])


@router.get("")
def redirect_servicos(request: Request):
    return RedirectResponse(url="/produtos?tipo_filtro=servico", status_code=303)


@router.get("/novo")
def redirect_novo_servico(request: Request):
    return RedirectResponse(url="/produtos/novo", status_code=303)


@router.get("/{servico_id}/editar")
def redirect_editar_servico(request: Request, servico_id: int):
    return RedirectResponse(url=f"/produtos/{servico_id}/editar", status_code=303)
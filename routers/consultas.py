"""Rotas de consulta externa: CEP (ViaCEP) e CNPJ (publica.cnpj.ws)."""
import logging
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from database import get_db
from app.core.security import verificar_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/consultas", tags=["consultas"])


@router.get("/cep")
def consultar_cep(request: Request, cep: str = Query("")):
    if not verificar_admin(request, get_db()):
        return JSONResponse({"erro": "nao autenticado"}, status_code=403)
    from services.cep_service import buscar_cep
    resultado = buscar_cep(cep)
    if resultado is None:
        return JSONResponse({"erro": "CEP nao encontrado"}, status_code=404)
    return JSONResponse(resultado)


@router.get("/cnpj")
def consultar_cnpj(request: Request, cnpj: str = Query("")):
    if not verificar_admin(request, get_db()):
        return JSONResponse({"erro": "nao autenticado"}, status_code=403)
    from services.cnpj_service import buscar_cnpj
    resultado = buscar_cnpj(cnpj)
    if "erro" in resultado:
        return JSONResponse(resultado, status_code=404)
    return JSONResponse(resultado)

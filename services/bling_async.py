"""
Bling API async client - substitui ThreadPoolExecutor por httpx.AsyncClient
"""
import asyncio
import time
from typing import Optional, List, Dict, Any
import httpx
from sqlalchemy.orm import Session

from models import Cliente, Fornecedor, Assinatura, OrdemServico, Produto, CategoriaProduto, MarcaProduto
from database import get_db

BLING_API = "https://api.bling.com.br/Api/v3"
BLING_AUTH = "https://www.bling.com.br/Api/v3/oauth/authorize"
BLING_TOKEN = "https://www.bling.com.br/Api/v3/oauth/token"

_tipos_contato_cache = {"cliente": 1, "fornecedor": 2}


async def call_bling_async(token: str, method: str, path: str, json_body: dict = None, timeout: float = 30.0) -> dict:
    """Versão async de call_bling usando httpx.AsyncClient"""
    url = f"{BLING_API}/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, f"{BLING_API}/{path}", headers=headers, json=json_body)
            payload = {}
            try:
                payload = resp.json()
            except Exception:
                pass
            return {"status": resp.status_code, "data": payload.get("data"), "body": payload}
    except Exception as e:
        return {"status": 0, "data": None, "body": {"error": str(e)}}


async def fetch_all_pages_async(token: str, endpoint: str, limit: int = 100, max_pages: int = 100) -> List[Dict]:
    """Busca todas as páginas de um endpoint de forma assíncrona"""
    all_items = []
    pagina = 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        while pagina <= max_pages:
            resp = await client.get(f"{BLING_API}/{endpoint}?pagina={pagina}&limite={limit}", headers=headers)
            if resp.status_code != 200:
                break
            try:
                payload = resp.json()
            except Exception:
                break
            items = payload.get("data", [])
            if not items:
                break
            all_items.extend(items)
            pagina += 1
    return all_items


async def fetch_items_detail_async(token: str, ids: List[int], endpoint: str, max_concurrent: int = 5) -> List[Dict]:
    """Busca detalhes de múltiplos itens com semáforo para limitar concorrência"""
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def fetch_one(cid: int) -> tuple[int, Dict | None, str | None]:
        async with semaphore:
            for tentativa in range(3):
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.get(
                            f"{BLING_API}/{endpoint}/{cid}",
                            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
                        )
                        if resp.status_code == 200:
                            return cid, resp.json().get("data"), None
                        if resp.status_code == 429:
                            await asyncio.sleep(2 ** tentativa)
                            continue
                except Exception:
                    pass
            return cid, None, f"Erro ao buscar {endpoint} {cid} após 3 tentativas"
    
    tasks = [fetch_one(cid) for cid in ids]
    results = await asyncio.gather(*tasks)
    
    items = []
    for cid, data, err in results:
        if data:
            items.append(data)
    return items


async def _carregar_tipos_contato_async(token: str):
    """Carrega tipos de contato do Bling (async)"""
    global _tipos_contato_cache
    result = await call_bling_async(token, "GET", "contatos/tipos")
    if result["status"] != 200:
        return
    items = result["data"]
    if not isinstance(items, list):
        return
    for t in items:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        desc = (t.get("descricao") or "").lower()
        nome = (t.get("nome") or "").lower()
        if "cliente" in desc or "cliente" in nome:
            _tipos_contato_cache["cliente"] = tid
        elif "fornecedor" in desc or "fornecedor" in nome:
            _tipos_contato_cache["fornecedor"] = tid


async def importar_contatos_async(db: Session, token: str, errors: list, tipo_filtro: str = None) -> tuple:
    """Importa contatos do Bling de forma assíncrona"""
    imported_clientes = 0
    updated_clientes = 0
    imported_fornecedores = 0
    updated_fornecedores = 0

    # Busca todos os IDs
    todos_ids = []
    pagina = 1
    while True:
        result = await call_bling_async(token, "GET", f"contatos?pagina={pagina}&limite=100")
        if result["status"] != 200:
            errors.append(f"Erro ao buscar contatos (página {pagina}): {result['body']}")
            break
        items = result["data"]
        if not items:
            break
        for item in items:
            bling_id = item.get("id")
            if bling_id:
                todos_ids.append(bling_id)
        pagina += 1

    # Carrega tipos de contato
    await _carregar_tipos_contato_async(token)

    # Busca detalhes de forma assíncrona
    items_detalhados = await fetch_items_detail_async(token, todos_ids, "contatos", max_concurrent=3)

    for item in items_detalhados:
        bling_id = item.get("id")
        if not bling_id:
            continue

        eh_fornecedor = is_fornecedor(item)
        eh_cliente = not eh_fornecedor

        if tipo_filtro == "cliente" and not eh_cliente:
            continue
        if tipo_filtro == "fornecedor" and not eh_fornecedor:
            continue

        mapped = contato_to_cliente(item, eh_fornecedor=eh_fornecedor)

        try:
            if eh_cliente:
                entity = db.query(Cliente).filter(Cliente.bling_id == bling_id).first()
                if entity:
                    for k, v in mapped.items():
                        if k == "created_at" and v is None and getattr(entity, k) is not None:
                            continue
                        setattr(entity, k, v)
                    entity.bling_pending_sync = False
                    updated_clientes += 1
                else:
                    entity = Cliente(bling_id=bling_id, **mapped)
                    db.add(entity)
                    imported_clientes += 1

            if eh_fornecedor:
                entity = db.query(Fornecedor).filter(Fornecedor.bling_id == bling_id).first()
                if entity:
                    for k, v in mapped.items():
                        if k == "created_at" and v is None and getattr(entity, k) is not None:
                            continue
                        setattr(entity, k, v)
                    entity.bling_pending_sync = False
                    updated_fornecedores += 1
                else:
                    entity = Fornecedor(bling_id=bling_id, **mapped)
                    db.add(entity)
                    imported_fornecedores += 1

            db.commit()
        except Exception as e:
            db.rollback()
            errors.append(f"Erro ao processar {'fornecedor' if eh_fornecedor else 'cliente'} {bling_id}: {str(e)[:200]}")

    return imported_clientes, updated_clientes, imported_fornecedores, updated_fornecedores
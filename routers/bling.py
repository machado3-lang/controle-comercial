import json
import httpx
import re
import secrets
import urllib.parse
from datetime import datetime, timedelta, date
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Cliente, Fornecedor, Empresa, Assinatura, OrdemServico, StatusOS

router = APIRouter(prefix="/bling", tags=["Bling"])

BLING_API = "https://api.bling.com.br/Api/v3"
BLING_AUTH = "https://www.bling.com.br/Api/v3/oauth/authorize"
BLING_TOKEN = "https://www.bling.com.br/Api/v3/oauth/token"


class BlingWebhookPayload(BaseModel):
    evento: str
    id: int
    dataHora: Optional[str] = None
    idEmpresa: Optional[int] = None


# ─── helpers ──────────────────────────────────────────────────────

def get_empresa(db: Session) -> Empresa | None:
    return db.query(Empresa).first()


def get_token(db: Session) -> str | None:
    emp = get_empresa(db)
    if not emp or not emp.bling_token:
        return None
    # check expiration
    if emp.bling_token_expires_at and datetime.now() >= emp.bling_token_expires_at:
        if emp.bling_refresh_token and emp.bling_client_id and emp.bling_client_secret:
            _refresh_token(db, emp)
            return emp.bling_token
        return None
    return emp.bling_token


def _refresh_token(db: Session, emp: Empresa):
    """Refresh the access token using the refresh token."""
    try:
        import base64
        creds = base64.b64encode(f"{emp.bling_client_id}:{emp.bling_client_secret}".encode()).decode()
        with httpx.Client(timeout=15) as client:
            resp = client.post(BLING_TOKEN,
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/json",
                    "Accept": "1.0",
                },
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": emp.bling_refresh_token,
                },
            )
            if resp.status_code != 200:
                emp.bling_token = None
                emp.bling_refresh_token = None
                emp.bling_token_expires_at = None
                db.commit()
                return
            data = resp.json()
            emp.bling_token = data.get("access_token")
            emp.bling_refresh_token = data.get("refresh_token", emp.bling_refresh_token)
            expires_in = data.get("expires_in", 21600)
            emp.bling_token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            db.commit()
    except Exception:
        emp.bling_token = None
        emp.bling_refresh_token = None
        emp.bling_token_expires_at = None
        db.commit()


def call_bling(token: str, method: str, path: str, json_body: dict = None) -> dict:
    url = f"{BLING_API}/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.request(method, url, headers=headers, json=json_body)
            payload = {}
            try:
                payload = resp.json()
            except Exception:
                pass
            return {"status": resp.status_code, "data": payload.get("data"), "body": payload}
    except Exception as e:
        return {"status": 0, "data": None, "body": {"error": str(e)}}


# ─── contatos data mapping ────────────────────────────────────────

def contato_to_cliente(item: dict, eh_fornecedor: bool = False) -> dict:
    parsed_date = None
    data_criacao = item.get("dataCriacao")
    if data_criacao:
        try:
            parsed_date = datetime.strptime(data_criacao[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            try:
                parsed_date = datetime.fromisoformat(data_criacao.replace("Z", ""))
            except (ValueError, TypeError):
                pass

    codigo = ""
    bling_codigo = item.get("codigo", "")
    if bling_codigo:
        prefixo = "FOR" if eh_fornecedor else "CLI"
        try:
            num = int(bling_codigo)
            codigo = f"{prefixo}-{num:04d}"
        except (ValueError, TypeError):
            codigo = f"{prefixo}-{bling_codigo}"

    endereco = ""
    bairro = ""
    cidade = ""
    estado = ""
    cep = ""
    email = item.get("email") or ""
    if isinstance(item.get("endereco"), dict):
        addr = item["endereco"].get("geral", {}) if isinstance(item["endereco"], dict) else {}
        if addr:
            end_part = addr.get('endereco', '') or ''
            num_part = addr.get('numero', '') or ''
            endereco = f"{end_part}, {num_part}" if end_part and num_part else (end_part or num_part)
            bairro = addr.get("bairro", "") or ""
            cidade = addr.get("municipio", "") or ""
            estado = addr.get("uf", "") or ""
            cep = addr.get("cep", "") or ""

    telefone = item.get("telefone") or ""
    celular = item.get("celular") or ""
    if not telefone and not celular:
        pessoas = item.get("pessoasContato") or []
        for p in pessoas:
            if isinstance(p, dict):
                desc = (p.get("descricao") or "").lower()
                valor = p.get("contato") or ""
                if "celular" in desc or "whats" in desc:
                    celular = valor
                elif "telefone" in desc or "fone" in desc:
                    telefone = valor
        if not telefone and not celular and pessoas:
            celular = pessoas[0].get("contato", "")

    return {
        "nome": item.get("nome", ""),
        "codigo": codigo,
        "tipo_pessoa": "fisica" if item.get("tipo") == "F" else "juridica" if item.get("tipo") == "J" else "",
        "cpf_cnpj": re.sub(r"\D", "", item.get("numeroDocumento") or ""),
        "endereco": endereco,
        "bairro": bairro,
        "cidade": cidade,
        "estado": estado,
        "cep": cep,
        "telefone": telefone,
        "celular": celular,
        "email": email,
        "contato": "",
        "fantasia": item.get("fantasia", "") or "",
        "inscricao_estadual": item.get("inscricaoEstadual", "") or "",
        "inscricao_municipal": item.get("inscricaoMunicipal", "") or "",
        "situacao": item.get("situacao") or "A",
        "observacao": "",
        "created_at": parsed_date,
    }


def cliente_to_contato(entity) -> dict:
    end = (entity.endereco or "").strip()
    numero = ""
    endereco = end

    if "," in end:
        parts = [p.strip() for p in end.rsplit(",", 1)]
        endereco = parts[0]
        numero = parts[1] if len(parts) > 1 else ""
    else:
        m = re.search(r",?\s*(?:n[º°]?\s*|numero\s*|n\.?\s*)?(\d+(?:\s*[-/]\s*\d+)?)\s*$", end, re.IGNORECASE)
        if m:
            numero = m.group(1).strip()
            endereco = end[:m.start()].strip().rstrip(",").strip()

    pessoas = []
    if entity.celular:
        pessoas.append({"descricao": "Celular", "contato": entity.celular, "whatsapp": True})
    if entity.telefone:
        pessoas.append({"descricao": "Telefone", "contato": entity.telefone, "whatsapp": False})

    data_criacao = ""
    if entity.created_at:
        try:
            data_criacao = entity.created_at.strftime("%Y-%m-%d")
        except Exception:
            pass

    return {
        "nome": entity.nome or "",
        "situacao": entity.situacao or "A",
        "tipo": "F" if entity.tipo_pessoa and entity.tipo_pessoa.lower().startswith("f") else "J" if entity.tipo_pessoa and entity.tipo_pessoa.lower().startswith("j") else None,
        "numeroDocumento": entity.cpf_cnpj or "",
        "fantasia": entity.fantasia or entity.contato or "",
        "inscricaoEstadual": entity.inscricao_estadual or "",
        "inscricaoMunicipal": entity.inscricao_municipal or "",
        "email": entity.email or "",
        "dataCriacao": data_criacao,
        "endereco": {
            "geral": {
                "endereco": endereco,
                "numero": numero,
                "bairro": entity.bairro or "",
                "municipio": entity.cidade or "",
                "uf": entity.estado or "",
                "cep": entity.cep or "",
            }
        },
        "pessoasContato": pessoas,
    }


def is_fornecedor(item: dict) -> bool:
    tipos = item.get("tiposContato") or []
    for t in tipos:
        if isinstance(t, dict):
            desc = (t.get("descricao") or "").lower()
            nome = (t.get("nome") or "").lower()
            if "fornecedor" in desc or "fornecedor" in nome:
                return True
    return False


_tipos_contato_cache = {"cliente": 1, "fornecedor": 2}


def _carregar_tipos_contato(token: str):
    global _tipos_contato_cache
    result = call_bling(token, "GET", "contatos/tipos")
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


def _validar_contato(entity) -> list:
    erros = []
    if not entity.nome or not entity.nome.strip():
        erros.append("Nome é obrigatório")
    if entity.cpf_cnpj:
        digits = re.sub(r"\D", "", entity.cpf_cnpj)
        if entity.tipo_pessoa and entity.tipo_pessoa.lower().startswith("j"):
            if len(digits) not in (0, 14):
                erros.append("CNPJ deve ter 14 dígitos")
        elif entity.tipo_pessoa and entity.tipo_pessoa.lower().startswith("f"):
            if len(digits) not in (0, 11):
                erros.append("CPF deve ter 11 dígitos")
    if entity.email and "@" not in entity.email:
        erros.append("Email inválido")
    return erros


# ─── OAuth 2.0 flow ───────────────────────────────────────────────

@router.get("/")
def pagina_bling(request: Request, db: Session = Depends(get_db)):
    empresa = get_empresa(db)
    messages = []
    msg = request.session.pop("message", None)
    if msg:
        messages.append(msg)

    token_valido = bool(get_token(db))
    client_id = empresa.bling_client_id if empresa else ""
    client_secret = empresa.bling_client_secret if empresa else ""
    webhook_secret = empresa.bling_webhook_secret if empresa else ""

    base_url = str(request.base_url).rstrip("/")
    callback_url = f"{base_url}/bling/callback"

    pending_clientes = db.query(Cliente).filter(Cliente.bling_pending_sync == True).count()
    pending_fornecedores = db.query(Fornecedor).filter(Fornecedor.bling_pending_sync == True).count()
    pending_assinaturas = db.query(Assinatura).filter(Assinatura.bling_pending_sync == True).count()
    pending_ordens = db.query(OrdemServico).filter(OrdemServico.bling_pending_sync == True).count()
    synced_clientes = db.query(Cliente).filter(Cliente.bling_id.isnot(None)).count()
    synced_fornecedores = db.query(Fornecedor).filter(Fornecedor.bling_id.isnot(None)).count()

    return request.app.state.templates.TemplateResponse(
        "bling/index.html", {
            "request": request,
            "messages": messages,
            "token_valido": token_valido,
            "bling_client_id": client_id,
            "bling_client_secret": client_secret,
            "webhook_secret": webhook_secret,
            "callback_url": callback_url,
            "base_url": base_url,
            "pending_clientes": pending_clientes,
            "pending_fornecedores": pending_fornecedores,
            "pending_assinaturas": pending_assinaturas,
            "pending_ordens": pending_ordens,
            "synced_clientes": synced_clientes,
            "synced_fornecedores": synced_fornecedores,
        }
    )


@router.post("/salvar-credenciais")
async def salvar_credenciais(
    request: Request, db: Session = Depends(get_db),
    bling_client_id: str = Form(""),
    bling_client_secret: str = Form(""),
):
    request.session["message"] = {"tipo": "info", "texto": "Credenciais devem ser configuradas em Configurações > Empresa."}
    return RedirectResponse(url="/configuracoes", status_code=303)


@router.get("/autorizar")
def autorizar_bling(request: Request, db: Session = Depends(get_db)):
    empresa = get_empresa(db)
    if not empresa or not empresa.bling_client_id:
        request.session["message"] = {"tipo": "danger", "texto": "Configure o Client ID primeiro!"}
        return RedirectResponse(url="/bling", status_code=303)

    base_url = str(request.base_url).rstrip("/")
    callback = f"{base_url}/bling/callback"
    state = secrets.token_hex(16)

    # store state in session to verify on callback
    request.session["bling_oauth_state"] = state

    auth_url = (
        f"{BLING_AUTH}?response_type=code"
        f"&client_id={urllib.parse.quote(empresa.bling_client_id)}"
        f"&redirect_uri={urllib.parse.quote(callback)}"
        f"&state={state}"
    )
    return RedirectResponse(url=auth_url)


@router.get("/callback")
def callback_bling(
    request: Request, db: Session = Depends(get_db),
    code: str = None, state: str = None,
):
    if not code:
        request.session["message"] = {"tipo": "danger", "texto": "Autorização negada ou código ausente."}
        return RedirectResponse(url="/bling", status_code=303)

    saved_state = request.session.pop("bling_oauth_state", None)
    if saved_state and state and state != saved_state:
        request.session["message"] = {"tipo": "danger", "texto": "State inválido. Possível CSRF."}
        return RedirectResponse(url="/bling", status_code=303)

    empresa = get_empresa(db)
    if not empresa or not empresa.bling_client_id or not empresa.bling_client_secret:
        request.session["message"] = {"tipo": "danger", "texto": "Credenciais não configuradas."}
        return RedirectResponse(url="/bling", status_code=303)

    base_url = str(request.base_url).rstrip("/")
    callback = f"{base_url}/bling/callback"

    try:
        import base64
        creds = base64.b64encode(f"{empresa.bling_client_id}:{empresa.bling_client_secret}".encode()).decode()
        with httpx.Client(timeout=15) as client:
            resp = client.post(BLING_TOKEN,
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/json",
                    "Accept": "1.0",
                },
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": callback,
                },
            )
            if resp.status_code != 200:
                request.session["message"] = {"tipo": "danger", "texto": f"Erro ao obter token: {resp.text}"}
                return RedirectResponse(url="/bling", status_code=303)

            data = resp.json()
            empresa.bling_token = data.get("access_token")
            empresa.bling_refresh_token = data.get("refresh_token")
            expires_in = data.get("expires_in", 21600)
            empresa.bling_token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            db.commit()

            request.session["message"] = {"tipo": "success", "texto": "Autorizado com sucesso! Token válido por 6h."}
            return RedirectResponse(url="/bling", status_code=303)
    except Exception as e:
        request.session["message"] = {"tipo": "danger", "texto": f"Erro de conexão: {str(e)}"}
        return RedirectResponse(url="/bling", status_code=303)


# ─── sync engine ──────────────────────────────────────────────────

def importar_contatos(db: Session, token: str, errors: list, tipo_filtro: str = None) -> tuple:
    """tipo_filtro: None = todos, 'cliente', 'fornecedor'"""
    imported_clientes = 0
    updated_clientes = 0
    imported_fornecedores = 0
    updated_fornecedores = 0

    todos_ids = []
    pagina = 1
    while True:
        result = call_bling(token, "GET", f"contatos?pagina={pagina}&limite=100")
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

    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def fetch_one(cid):
        for tentativa in range(3):
            r = call_bling(token, "GET", f"contatos/{cid}")
            if r["status"] == 200:
                return cid, r["data"], None
            if r["status"] == 429:
                time.sleep(2 ** tentativa)
                continue
            if tentativa == 2:
                return cid, None, f"Erro ao buscar contato {cid}: {r['body']}"
        return cid, None, f"Erro ao buscar contato {cid} após 3 tentativas"

    items_detalhados = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_map = {pool.submit(fetch_one, cid): cid for cid in todos_ids}
        for fut in as_completed(fut_map):
            cid, data, err = fut.result()
            if err:
                errors.append(err)
            elif data:
                items_detalhados.append(data)

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

        if eh_cliente:
            entity = db.query(Cliente).filter(Cliente.bling_id == bling_id).first()
            if entity:
                for k, v in mapped.items():
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
                    setattr(entity, k, v)
                entity.bling_pending_sync = False
                updated_fornecedores += 1
            else:
                entity = Fornecedor(bling_id=bling_id, **mapped)
                db.add(entity)
                imported_fornecedores += 1

        db.commit()

    return imported_clientes, updated_clientes, imported_fornecedores, updated_fornecedores


def push_cliente(db: Session, cliente: Cliente, token: str) -> str | None:
    erros = _validar_contato(cliente)
    if erros:
        return f"Cliente '{cliente.nome}': validação falhou - {'; '.join(erros)}"

    _carregar_tipos_contato(token)

    data = cliente_to_contato(cliente)
    if not data.get("tipo"):
        data["tipo"] = "F"
    data["tiposContato"] = [{"id": _tipos_contato_cache["cliente"]}]
    if cliente.bling_id:
        result = call_bling(token, "PUT", f"contatos/{cliente.bling_id}", data)
        if result["status"] in (200, 204):
            cliente.bling_pending_sync = False
            db.commit()
            return None
        return f"Erro ao atualizar cliente #{cliente.id} ('{cliente.nome}'): {result['body']}"
    else:
        result = call_bling(token, "POST", "contatos", data)
        if result["status"] in (200, 201):
            bling_id = result["data"].get("id")
            if bling_id:
                cliente.bling_id = bling_id
            cliente.bling_pending_sync = False
            db.commit()
            return None
        return f"Erro ao criar cliente '{cliente.nome}': {result['body']}"


def push_fornecedor(db: Session, fornecedor: Fornecedor, token: str) -> str | None:
    erros = _validar_contato(fornecedor)
    if erros:
        return f"Fornecedor '{fornecedor.nome}': validação falhou - {'; '.join(erros)}"

    _carregar_tipos_contato(token)

    data = cliente_to_contato(fornecedor)
    if not data.get("tipo"):
        data["tipo"] = "J"
    data["tiposContato"] = [{"id": _tipos_contato_cache["fornecedor"]}]
    if fornecedor.bling_id:
        result = call_bling(token, "PUT", f"contatos/{fornecedor.bling_id}", data)
        if result["status"] in (200, 204):
            fornecedor.bling_pending_sync = False
            db.commit()
            return None
        return f"Erro ao atualizar fornecedor #{fornecedor.id} ('{fornecedor.nome}'): {result['body']}"
    else:
        result = call_bling(token, "POST", "contatos", data)
        if result["status"] in (200, 201):
            bling_id = result["data"].get("id")
            if bling_id:
                fornecedor.bling_id = bling_id
            fornecedor.bling_pending_sync = False
            db.commit()
            return None
        return f"Erro ao criar fornecedor '{fornecedor.nome}': {result['body']}"


# ─── webhook receiver ─────────────────────────────────────────────

@router.get("/webhook")
def webhook_get():
    return JSONResponse({"status": "ok", "message": "Webhook endpoint ativo."})


@router.post("/webhook")
def webhook_receiver(
    payload: BlingWebhookPayload,
    request: Request,
    db: Session = Depends(get_db)
):
    empresa = get_empresa(db)
    if not empresa or not empresa.bling_webhook_secret:
        return JSONResponse({"error": "Webhook não configurado"}, status_code=403)

    secret = request.query_params.get("secret")
    if not secret or secret != empresa.bling_webhook_secret:
        return JSONResponse({"error": "Assinatura inválida"}, status_code=403)

    evento = payload.evento
    bling_id = payload.id

    token = get_token(db)
    if not token:
        return JSONResponse({"error": "Token não configurado"}, status_code=400)

    # fetch contato details
    result = call_bling(token, "GET", f"contatos/{bling_id}")
    if result["status"] != 200:
        return JSONResponse({"error": f"Falha ao buscar dados: {result['body']}"}, status_code=502)

    item = result["data"]
    if not item:
        return JSONResponse({"error": "Dados não encontrados"}, status_code=404)

    eh_fornecedor = is_fornecedor(item)
    mapped = contato_to_cliente(item, eh_fornecedor=eh_fornecedor)

    if "excluir" in evento:
        for model_class in [Cliente, Fornecedor]:
            entity = db.query(model_class).filter(model_class.bling_id == bling_id).first()
            if entity:
                entity.bling_id = None
                entity.bling_updated_at = None
                entity.bling_pending_sync = False
        db.commit()
        return JSONResponse({"ok": True})

    if eh_fornecedor:
        entity = db.query(Fornecedor).filter(Fornecedor.bling_id == bling_id).first()
        if not entity:
            entity = Fornecedor(bling_id=bling_id)
            db.add(entity)
        for k, v in mapped.items():
            setattr(entity, k, v)
        entity.bling_pending_sync = False
    else:
        entity = db.query(Cliente).filter(Cliente.bling_id == bling_id).first()
        if not entity:
            entity = Cliente(bling_id=bling_id)
            db.add(entity)
        for k, v in mapped.items():
            setattr(entity, k, v)
        entity.bling_pending_sync = False

    db.commit()
    return JSONResponse({"ok": True})


# ─── manual sync endpoints ────────────────────────────────────────

@router.post("/importar-contatos")
def importar_contatos_route(request: Request, db: Session = Depends(get_db)):
    token = get_token(db)
    if not token:
        request.session["message"] = {"tipo": "danger", "texto": "Token inválido ou expirado. Reautorize no Bling."}
        return RedirectResponse(url="/bling", status_code=303)
    errors = []
    ic, uc, fi, fu = importar_contatos(db, token, errors)
    msg = f"Clientes: {ic} importado(s), {uc} atualizado(s) | Fornecedores: {fi} importado(s), {fu} atualizado(s)"
    if errors:
        msg += f" | Erros: {'; '.join(errors)}"
    request.session["message"] = {"texto": msg, "tipo": "success" if not errors else "warning"}
    return RedirectResponse(url="/bling", status_code=303)


@router.post("/sincronizar-pendentes")
def sincronizar_pendentes(request: Request, db: Session = Depends(get_db)):
    token = get_token(db)
    if not token:
        request.session["message"] = {"tipo": "danger", "texto": "Token inválido ou expirado. Reautorize no Bling."}
        return RedirectResponse(url="/bling", status_code=303)

    errors = []
    clientes_ok = 0
    fornecedores_ok = 0
    assinaturas_ok = 0
    ordens_ok = 0
    for c in db.query(Cliente).filter(Cliente.bling_pending_sync == True).all():
        err = push_cliente(db, c, token)
        if err:
            errors.append(err)
        else:
            clientes_ok += 1
    for f in db.query(Fornecedor).filter(Fornecedor.bling_pending_sync == True).all():
        err = push_fornecedor(db, f, token)
        if err:
            errors.append(err)
        else:
            fornecedores_ok += 1
    for a in db.query(Assinatura).filter(Assinatura.bling_pending_sync == True).all():
        err = push_assinatura(db, a, token)
        if err:
            errors.append(err)
        else:
            assinaturas_ok += 1
    for o in db.query(OrdemServico).filter(OrdemServico.bling_pending_sync == True).all():
        err = push_ordem(db, o, token)
        if err:
            errors.append(err)
        else:
            ordens_ok += 1

    partes = []
    if clientes_ok:
        partes.append(f"{clientes_ok} cliente(s) OK")
    if fornecedores_ok:
        partes.append(f"{fornecedores_ok} fornecedor(es) OK")
    if assinaturas_ok:
        partes.append(f"{assinaturas_ok} assinatura(s) OK")
    if ordens_ok:
        partes.append(f"{ordens_ok} OS(s) OK")
    if errors:
        partes.append(f"{len(errors)} erro(s)")
    msg = " | ".join(partes) if partes else "Nenhum pendente"
    if errors:
        msg += f": {'; '.join(errors)}"
    request.session["message"] = {"texto": msg, "tipo": "success" if not errors else "warning"}
    return RedirectResponse(url="/bling", status_code=303)


@router.post("/limpar-importar")
def limpar_importar(request: Request, db: Session = Depends(get_db)):
    token = get_token(db)
    if not token:
        request.session["message"] = {"tipo": "danger", "texto": "Token inválido ou expirado. Reautorize no Bling."}
        return RedirectResponse(url="/bling", status_code=303)

    db.query(Cliente).delete()
    db.query(Fornecedor).delete()
    db.commit()

    errors = []
    ic, uc, fi, fu = importar_contatos(db, token, errors)
    msg = f"Clientes: {ic} importado(s), {uc} atualizado(s) | Fornecedores: {fi} importado(s), {fu} atualizado(s)"
    if errors:
        msg += f" | Erros: {'; '.join(errors)}"
    request.session["message"] = {"texto": msg, "tipo": "success" if not errors else "warning"}
    return RedirectResponse(url="/bling", status_code=303)


@router.post("/gerar-webhook-secret")
def gerar_webhook_secret(request: Request, db: Session = Depends(get_db)):
    empresa = get_empresa(db)
    if not empresa:
        empresa = Empresa()
        db.add(empresa)
    empresa.bling_webhook_secret = secrets.token_hex(16)
    db.commit()
    request.session["message"] = {"texto": "Webhook Secret gerado!", "tipo": "success"}
    return RedirectResponse(url="/bling", status_code=303)


# ─── Contratos (Assinaturas) sync ───────────────────────────

def assinatura_to_contrato(assinatura: Assinatura) -> dict:
    cliente_bling_id = assinatura.cliente.bling_id if assinatura.cliente else None
    data_inicio = assinatura.data_inicio.isoformat() if assinatura.data_inicio else ""
    data_fim = assinatura.data_fim.strftime("%Y-%m") if assinatura.data_fim else ""
    body = {
        "descricao": assinatura.descricao or "",
        "data": data_inicio,
        "numero": str(assinatura.id),
        "valor": assinatura.valor,
        "situacao": assinatura.situacao,
        "observacoes": assinatura.observacao or "",
    }

    body["tipoManutencao"] = 1
    body["emitirOrdemServico"] = False
    body["descISSTotalNota"] = False

    if cliente_bling_id:
        body["contato"] = {"id": cliente_bling_id}

    if data_fim:
        body["dataFim"] = data_fim

    body["cobranca"] = {
        "dataBase": data_inicio,
        "vencimento": {
            "tipo": 1,
            "dia": assinatura.dia_vencimento,
            "periodicidade": assinatura.periodicidade,
        }
    }
    if cliente_bling_id:
        body["cobranca"]["contato"] = {"id": cliente_bling_id}

    return body


def contrato_to_assinatura(item: dict) -> dict:
    parsed_date = None
    data_criacao = item.get("data")
    if data_criacao:
        try:
            parsed_date = datetime.strptime(data_criacao, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass

    data_fim = None
    raw_fim = item.get("dataFim")
    if raw_fim:
        try:
            data_fim = datetime.strptime(raw_fim, "%Y-%m").date()
        except (ValueError, TypeError):
            pass

    cobranca = item.get("cobranca") or {}
    vencimento = cobranca.get("vencimento") or {}
    dia_venc = vencimento.get("dia", 1)
    periodicidade = vencimento.get("periodicidade", 1)

    return {
        "descricao": item.get("descricao", ""),
        "valor": item.get("valor", 0),
        "dia_vencimento": dia_venc,
        "periodicidade": periodicidade,
        "data_inicio": parsed_date or date.today(),
        "data_fim": data_fim,
        "situacao": item.get("situacao", 1),
        "observacao": item.get("observacoes", ""),
    }


def push_assinatura(db: Session, assinatura: Assinatura, token: str) -> str | None:
    erros = _validar_contato(assinatura.cliente)
    if erros:
        return f"Assinatura #{assinatura.id}: cliente inválido - {'; '.join(erros)}"
    if not assinatura.cliente.bling_id:
        return f"Assinatura #{assinatura.id}: cliente sem bling_id, sincronize o cliente primeiro"

    if assinatura.bling_id:
        existing = call_bling(token, "GET", f"contratos/{assinatura.bling_id}")
        if existing["status"] == 200:
            base = existing["data"]
        else:
            base = {}
        data = assinatura_to_contrato(assinatura)
        merged = {**base, **data}
        merged.pop("id", None)
        result = call_bling(token, "PUT", f"contratos/{assinatura.bling_id}", merged)
        if result["status"] in (200, 204):
            assinatura.bling_pending_sync = False
            db.commit()
            return None
        return f"Erro ao atualizar contrato #{assinatura.bling_id}: {result['body']}"
    else:
        data = assinatura_to_contrato(assinatura)
        result = call_bling(token, "POST", "contratos", data)
        if result["status"] in (200, 201):
            bling_id = result["data"].get("id")
            if bling_id:
                assinatura.bling_id = bling_id
            assinatura.bling_pending_sync = False
            db.commit()
            return None
        return f"Erro ao criar contrato: {result['body']}"


def importar_contratos(db: Session, token: str, errors: list) -> tuple:
    imported = 0
    updated = 0

    todos_ids = []
    pagina = 1
    while True:
        result = call_bling(token, "GET", f"contratos?pagina={pagina}&limite=100")
        if result["status"] != 200:
            errors.append(f"Erro ao buscar contratos (página {pagina}): {result['body']}")
            break
        items = result["data"]
        if not items:
            break
        for item in items:
            bling_id = item.get("id")
            if bling_id:
                todos_ids.append(bling_id)
        pagina += 1

    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def fetch_one(cid):
        for tentativa in range(3):
            r = call_bling(token, "GET", f"contratos/{cid}")
            if r["status"] == 200:
                return cid, r["data"], None
            if r["status"] == 429:
                time.sleep(2 ** tentativa)
                continue
            if tentativa == 2:
                return cid, None, f"Erro ao buscar contrato {cid}: {r['body']}"
        return cid, None, f"Erro ao buscar contrato {cid} após 3 tentativas"

    items_detalhados = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_map = {pool.submit(fetch_one, cid): cid for cid in todos_ids}
        for fut in as_completed(fut_map):
            cid, data, err = fut.result()
            if err:
                errors.append(err)
            elif data:
                items_detalhados.append(data)

    for item in items_detalhados:
        bling_id = item.get("id")
        if not bling_id:
            continue

        mapped = contrato_to_assinatura(item)
        contato = item.get("contato") or {}
        contato_bling_id = contato.get("id")

        if contato_bling_id:
            cliente = db.query(Cliente).filter(Cliente.bling_id == contato_bling_id).first()
        else:
            cliente = None

        entity = db.query(Assinatura).filter(Assinatura.bling_id == bling_id).first()
        if entity:
            for k, v in mapped.items():
                setattr(entity, k, v)
            if cliente:
                entity.cliente_id = cliente.id
            entity.bling_pending_sync = False
            updated += 1
        else:
            if not cliente:
                errors.append(f"Contrato {bling_id}: cliente (bling_id={contato_bling_id}) não encontrado localmente")
                continue
            entity = Assinatura(
                bling_id=bling_id,
                cliente_id=cliente.id,
                quantidade=None,
                fornecedor_id=None,
                valor_revenda=None,
                **mapped
            )
            db.add(entity)
            imported += 1

        db.commit()

    return imported, updated


# ─── Ordens de Serviço sync ─────────────────────────────────

_STATUS_OS_PARA_SITUACAO = {
    StatusOS.ABERTA: 0,
    StatusOS.EM_ANDAMENTO: 0,
    StatusOS.FINALIZADA: 2,
    StatusOS.CANCELADA: 3,
}

# Tentativa de reversão razoável
_SITUACAO_PARA_STATUS_OS = {
    0: StatusOS.ABERTA,
    1: StatusOS.EM_ANDAMENTO,
    2: StatusOS.FINALIZADA,
    3: StatusOS.CANCELADA,
}


def ordem_to_ordem_servico(ordem: OrdemServico) -> dict:
    cliente_bling_id = ordem.cliente.bling_id if ordem.cliente else None
    situacao = _STATUS_OS_PARA_SITUACAO.get(ordem.status, 0)

    body = {
        "data": ordem.data_entrada.isoformat() if ordem.data_entrada else "",
        "situacao": situacao,
        "observacoes": ordem.observacao or "",
    }

    descricao_parts = [ordem.equipamento]
    if ordem.marca:
        descricao_parts.append(f"Marca: {ordem.marca}")
    if ordem.modelo:
        descricao_parts.append(f"Modelo: {ordem.modelo}")
    if ordem.numero_serie:
        descricao_parts.append(f"N/S: {ordem.numero_serie}")
    if ordem.defeito_relatado:
        descricao_parts.append(f"Defeito: {ordem.defeito_relatado}")

    body["descricao"] = " | ".join(descricao_parts)

    if cliente_bling_id:
        body["contato"] = {"id": cliente_bling_id}

    if ordem.tecnico:
        body["vendedor"] = {"nome": ordem.tecnico}

    if ordem.valor_total:
        body["valor"] = ordem.valor_total

    itens = []
    if ordem.servicos_executados:
        itens.append({
            "descricao": ordem.servicos_executados,
            "valor": ordem.valor_servico or 0,
            "quantidade": 1,
        })
    if ordem.pecas_utilizadas:
        itens.append({
            "descricao": f"Peças: {ordem.pecas_utilizadas}",
            "valor": ordem.valor_pecas or 0,
            "quantidade": 1,
        })
    if itens:
        body["itens"] = itens

    return body


def ordem_servico_to_ordem(item: dict) -> dict:
    parsed_date = None
    raw_data = item.get("data")
    if raw_data:
        try:
            parsed_date = datetime.strptime(raw_data, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass

    data_saida = None
    raw_fim = item.get("dataFim")
    if raw_fim:
        try:
            data_saida = datetime.strptime(raw_fim, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass

    descricao = item.get("descricao", "")
    situacao = item.get("situacao", 0)
    status = _SITUACAO_PARA_STATUS_OS.get(situacao, StatusOS.ABERTA)

    # Parse descricao to extract equipment info
    equipamento = descricao
    marca = ""
    modelo = ""
    numero_serie = ""
    defeito_relatado = ""

    parts = descricao.split(" | ")
    if parts:
        equipamento = parts[0]
    for p in parts[1:]:
        if p.startswith("Marca: "):
            marca = p.replace("Marca: ", "")
        elif p.startswith("Modelo: "):
            modelo = p.replace("Modelo: ", "")
        elif p.startswith("N/S: "):
            numero_serie = p.replace("N/S: ", "")
        elif p.startswith("Defeito: "):
            defeito_relatado = p.replace("Defeito: ", "")

    # Extract services/parts from itens
    itens = item.get("itens") or []
    servicos_executados = ""
    pecas_utilizadas = ""
    valor_servico = 0
    valor_pecas = 0
    for i in itens:
        if isinstance(i, dict):
            desc = i.get("descricao", "")
            valor = i.get("valor", 0)
            if desc.startswith("Peças:"):
                pecas_utilizadas = desc.replace("Peças: ", "")
                valor_pecas = valor
            else:
                servicos_executados = desc
                valor_servico = valor

    return {
        "equipamento": equipamento,
        "marca": marca,
        "modelo": modelo,
        "numero_serie": numero_serie,
        "defeito_relatado": defeito_relatado,
        "servicos_executados": servicos_executados,
        "pecas_utilizadas": pecas_utilizadas,
        "valor_servico": valor_servico,
        "valor_pecas": valor_pecas,
        "valor_total": item.get("valor", 0),
        "data_entrada": parsed_date or date.today(),
        "data_saida": data_saida,
        "status": status,
        "observacao": item.get("observacoes", ""),
    }


def push_ordem(db: Session, ordem: OrdemServico, token: str) -> str | None:
    if not ordem.cliente.bling_id:
        return f"OS #{ordem.id}: cliente sem bling_id, sincronize o cliente primeiro"

    data = ordem_to_ordem_servico(ordem)
    if ordem.bling_id:
        result = call_bling(token, "PUT", f"ordens-servico/{ordem.bling_id}", data)
        if result["status"] in (200, 204):
            ordem.bling_pending_sync = False
            db.commit()
            return None
        return f"Erro ao atualizar OS #{ordem.bling_id}: {result['body']}"
    else:
        result = call_bling(token, "POST", "ordens-servico", data)
        if result["status"] in (200, 201):
            bling_id = result["data"].get("id")
            if bling_id:
                ordem.bling_id = bling_id
            ordem.bling_pending_sync = False
            db.commit()
            return None
        return f"Erro ao criar OS: {result['body']}"


def importar_ordens_servico(db: Session, token: str, errors: list) -> tuple:
    imported = 0
    updated = 0

    todos_ids = []
    pagina = 1
    while True:
        result = call_bling(token, "GET", f"ordens-servico?pagina={pagina}&limite=100")
        if result["status"] != 200:
            errors.append(f"Erro ao buscar ordens-servico (página {pagina}): {result['body']}")
            break
        items = result["data"]
        if not items:
            break
        for item in items:
            bling_id = item.get("id")
            if bling_id:
                todos_ids.append(bling_id)
        pagina += 1

    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def fetch_one(cid):
        for tentativa in range(3):
            r = call_bling(token, "GET", f"ordens-servico/{cid}")
            if r["status"] == 200:
                return cid, r["data"], None
            if r["status"] == 429:
                time.sleep(2 ** tentativa)
                continue
            if tentativa == 2:
                return cid, None, f"Erro ao buscar OS {cid}: {r['body']}"
        return cid, None, f"Erro ao buscar OS {cid} após 3 tentativas"

    items_detalhados = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_map = {pool.submit(fetch_one, cid): cid for cid in todos_ids}
        for fut in as_completed(fut_map):
            cid, data, err = fut.result()
            if err:
                errors.append(err)
            elif data:
                items_detalhados.append(data)

    for item in items_detalhados:
        bling_id = item.get("id")
        if not bling_id:
            continue

        mapped = ordem_servico_to_ordem(item)
        contato = item.get("contato") or {}
        contato_bling_id = contato.get("id")

        if contato_bling_id:
            cliente = db.query(Cliente).filter(Cliente.bling_id == contato_bling_id).first()
        else:
            cliente = None

        entity = db.query(OrdemServico).filter(OrdemServico.bling_id == bling_id).first()
        if entity:
            for k, v in mapped.items():
                setattr(entity, k, v)
            if cliente:
                entity.cliente_id = cliente.id
            entity.bling_pending_sync = False
            updated += 1
        else:
            if not cliente:
                errors.append(f"OS {bling_id}: cliente (bling_id={contato_bling_id}) não encontrado localmente")
                continue
            entity = OrdemServico(
                bling_id=bling_id,
                cliente_id=cliente.id,
                tecnico=item.get("vendedor", {}).get("nome") if isinstance(item.get("vendedor"), dict) else "",
                **mapped
            )
            db.add(entity)
            imported += 1

        db.commit()

    return imported, updated


# ─── Contratos sync routes ──────────────────────────────────

@router.post("/importar-contratos")
def importar_contratos_route(request: Request, db: Session = Depends(get_db)):
    token = get_token(db)
    if not token:
        request.session["message"] = {"tipo": "danger", "texto": "Token inválido ou expirado. Reautorize no Bling."}
        return RedirectResponse(url="/bling", status_code=303)
    errors = []
    imp, upd = importar_contratos(db, token, errors)
    msg = f"Contratos: {imp} importado(s), {upd} atualizado(s)"
    if errors:
        msg += f" | Erros: {'; '.join(errors)}"
    request.session["message"] = {"texto": msg, "tipo": "success" if not errors else "warning"}
    return RedirectResponse(url="/bling", status_code=303)


@router.post("/sincronizar-assinaturas")
def sincronizar_assinaturas_pendentes(request: Request, db: Session = Depends(get_db)):
    token = get_token(db)
    if not token:
        request.session["message"] = {"tipo": "danger", "texto": "Token inválido ou expirado. Reautorize no Bling."}
        return RedirectResponse(url="/bling", status_code=303)

    errors = []
    ok = 0
    for a in db.query(Assinatura).filter(Assinatura.bling_pending_sync == True).all():
        err = push_assinatura(db, a, token)
        if err:
            errors.append(err)
        else:
            ok += 1

    msg = f"{ok} assinatura(s) sincronizada(s)"
    if errors:
        msg += f" | {len(errors)} erro(s): {'; '.join(errors)}"
    request.session["message"] = {"texto": msg, "tipo": "success" if not errors else "warning"}
    return RedirectResponse(url="/bling", status_code=303)


# ─── Ordens de Serviço sync routes ──────────────────────────

@router.post("/importar-ordens-servico")
def importar_ordens_servico_route(request: Request, db: Session = Depends(get_db)):
    token = get_token(db)
    if not token:
        request.session["message"] = {"tipo": "danger", "texto": "Token inválido ou expirado. Reautorize no Bling."}
        return RedirectResponse(url="/bling", status_code=303)
    errors = []
    imp, upd = importar_ordens_servico(db, token, errors)
    msg = f"Ordens de Serviço: {imp} importada(s), {upd} atualizada(s)"
    if errors:
        msg += f" | Erros: {'; '.join(errors)}"
    request.session["message"] = {"texto": msg, "tipo": "success" if not errors else "warning"}
    return RedirectResponse(url="/bling", status_code=303)


@router.post("/sincronizar-ordens")
def sincronizar_ordens_pendentes(request: Request, db: Session = Depends(get_db)):
    token = get_token(db)
    if not token:
        request.session["message"] = {"tipo": "danger", "texto": "Token inválido ou expirado. Reautorize no Bling."}
        return RedirectResponse(url="/bling", status_code=303)

    errors = []
    ok = 0
    for o in db.query(OrdemServico).filter(OrdemServico.bling_pending_sync == True).all():
        err = push_ordem(db, o, token)
        if err:
            errors.append(err)
        else:
            ok += 1

msg = f"{ok} OS(s) sincronizada(s)"
     if errors:
         msg += f" | {len(errors)} erro(s): {'; '.join(errors)}"
     request.session["message"] = {"texto": msg, "tipo": "success" if not errors else "warning"}
     return RedirectResponse(url="/bling", status_code=303)


@router.get("/testar-conexao")
def testar_conexao(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return JSONResponse({"success": False, "error": "Não autenticado"})
    emp = db.query(Empresa).first()
    if not emp or not emp.bling_client_id:
        return JSONResponse({"success": False, "error": "Credenciais Bling não configuradas"})
    token = get_token(db)
    if not token:
        return JSONResponse({"success": False, "error": "Falha ao obter token"})
    with httpx.Client() as client:
        try:
            r = client.get("https://api.bling.com.br/Api/v3/empresas/me", headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                return JSONResponse({"success": True})
            return JSONResponse({"success": False, "error": f"HTTP {r.status_code}"})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)})

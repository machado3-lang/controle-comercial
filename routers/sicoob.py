import base64
import httpx
import json
import os
import ssl
from datetime import datetime, timedelta, date
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models import Empresa, ContaReceber, StatusConta

router = APIRouter(prefix="/sicoob", tags=["Sicoob"])

SICOOO_AUTH = "https://auth.sicoob.com.br/auth/realms/cooperado/protocol/openid-connect/token"
SICOOO_API = "https://api.sicoob.com.br/cobranca-bancaria/v3"

CERT_DIR = "certs"


def get_empresa(db: Session) -> Empresa | None:
    return db.query(Empresa).first()


def get_sicoob_token(db: Session) -> str | None:
    emp = get_empresa(db)
    return emp.sicoob_token if emp else None


def refresh_sicoob_token(db: Session, scope: str = "boletos_consulta") -> str | None:
    emp = get_empresa(db)
    if not emp:
        return None
    if not emp.sicoob_client_id:
        return None
    cert_config = get_cert_config(db)
    cert_path = cert_config["cert"] if cert_config else None
    
    with httpx.Client(timeout=30, cert=cert_path if cert_path else None) as client:
        resp = client.post(
            SICOOO_AUTH,
            content=f"grant_type=client_credentials&client_id={emp.sicoob_client_id or ''}&scope={scope}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code == 200:
            data = resp.json()
            emp.sicoob_token = data.get("access_token")
            db.commit()
            return emp.sicoob_token
    return None


def get_token_or_error(db: Session, scope: str = "boletos_consulta") -> tuple[str | None, str | None]:
    emp = get_empresa(db)
    if not emp:
        return None, "Empresa não encontrada - verifique database"
    if not emp.sicoob_client_id:
        return None, "sicoob_client_id não configurado - configure em /sicoob"
    cert_config = get_cert_config(db)
    if not cert_config:
        return None, "Certificado Sicoob não configurado - configure em /sicoob"
    token = refresh_sicoob_token(db, scope)
    if not token:
        return None, "Falha na autenticação Sicoob - verifique credenciais/certificado"
    return token, None


def get_cert_config(db: Session) -> dict | None:
    emp = get_empresa(db)
    if not emp:
        return None
    # Preferir certificados em base64 (persistem no banco)
    if emp.sicoob_cert_base64:
        import base64
        cert_content = base64.b64decode(emp.sicoob_cert_base64)
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pem') as f:
            f.write(cert_content)
            cert_path = f.name
        key_path = None
        if emp.sicoob_cert_key_base64:
            key_content = base64.b64decode(emp.sicoob_cert_key_base64)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pem') as f:
                f.write(key_content)
                key_path = f.name
        return {"cert": (cert_path, key_path) if key_path else cert_path, "password": emp.sicoob_cert_password or None}
    # Fallback para arquivos no disco (não persistem no Railway)
    if not emp.sicoob_cert_path:
        return None
    cert_path = emp.sicoob_cert_path
    if not os.path.isabs(cert_path):
        cert_path = os.path.abspath(cert_path)
    key_path = emp.sicoob_cert_key_path
    if key_path and not os.path.isabs(key_path):
        key_path = os.path.abspath(key_path)
    if not os.path.exists(cert_path):
        return None
    return {"cert": (cert_path, key_path) if key_path else cert_path, "password": emp.sicoob_cert_password or None}


def get_http_client(db: Session):
    cert_config = get_cert_config(db)
    if cert_config:
        return httpx.Client(timeout=30, cert=cert_config["cert"], verify=True)
    return httpx.Client(timeout=30)


def emitir_boleto(db: Session, conta: ContaReceber) -> dict:
    emp = get_empresa(db)
    token = refresh_sicoob_token(db, "boletos_inclusao")
    if not token:
        return {"success": False, "error": "Token Sicoob não configurado"}
    if not emp.sicoob_conta_corrente:
        return {"success": False, "error": "Conta corrente Sicoob não configurada"}
    if not emp.sicoob_client_id:
        return {"success": False, "error": "Client ID Sicoob não configurado"}

    if not conta.cliente:
        return {"success": False, "error": "Cliente não associado à conta"}

    nosso_numero = f"{date.today().strftime('%Y%m%d')}{conta.id:08d}"
    cpf_cnpj = ''.join(filter(str.isdigit, conta.cliente.cpf_cnpj or ''))

    beneficiario = int(emp.sicoob_beneficiario) if emp.sicoob_beneficiario else 91820
    conta_corrente_raw = emp.sicoob_conta_corrente or "110558"
    conta_corrente = int(''.join(filter(str.isdigit, conta_corrente_raw))) if conta_corrente_raw else None
    data_emissao = date.today().strftime("%Y-%m-%d")

    body = {
        "seuNumero": nosso_numero,
        "valor": float(conta.valor),
        "dataVencimento": conta.data_vencimento.strftime("%Y-%m-%d"),
        "dataEmissao": data_emissao,
        "codigoModalidade": 1,
        "codigoEspecieDocumento": "DM",
        "numeroParcela": 1,
        "tipoDesconto": 0,
        "tipoMulta": 0,
        "tipoJurosMora": 0,
        "identificacaoEmissaoBoleto": 1,
        "identificacaoDistribuicaoBoleto": 1,
        "numeroCliente": beneficiario,
        "numeroContaCorrente": conta_corrente,
        "numeroContratoCobranca": 860340,
        "pagador": {
            "numeroCpfCnpj": cpf_cnpj,
            "nome": conta.cliente.nome or "",
            "endereco": conta.cliente.endereco or "",
            "bairro": conta.cliente.bairro or "",
            "cidade": conta.cliente.cidade or "",
            "cep": ''.join(filter(str.isdigit, conta.cliente.cep or "")),
            "uf": conta.cliente.estado or "",
        }
    }

    try:
        cert_config = get_cert_config(db)
        client_args = {"timeout": 30}
        if cert_config and "cert" in cert_config:
            client_args["cert"] = cert_config["cert"]
        
        # Removido debug
        with httpx.Client(**client_args) as client:
            resp = client.post(
                f"{SICOOO_API}/boletos",
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 401:
                token = refresh_sicoob_token(db, "boletos_inclusao")
                if not token:
                    return {"success": False, "error": "Falha ao renovar token Sicoob"}
                resp = client.post(
                    f"{SICOOO_API}/boletos",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
        if resp.status_code in (200, 201):
            data = resp.json()
            resultado = data.get("resultado", data)
            api_nosso_numero = resultado.get("nossoNumero")
            api_seu_numero = resultado.get("seuNumero")
            
            conta.nosso_numero = str(api_seu_numero or nosso_numero)
            if api_nosso_numero:
                conta.api_nosso_numero = str(api_nosso_numero)
            
            # Salvar data de emissão se retornada pela API
            api_data_emissao = resultado.get("dataEmissao")
            if api_data_emissao:
                try:
                    conta.data_emissao = datetime.strptime(api_data_emissao, "%Y-%m-%d").date()
                except: pass
            
            conta.boleto_emitido = True
            conta.boleto_url = resultado.get("codigoBarras") or resultado.get("linhaDigitavel") or "Boleto emitido"
            conta.boleto_txid = resultado.get("txid")
            db.commit()
            return {"success": True, "boleto_url": conta.boleto_url}
        else:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}", "details": resp.headers}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/")
def pagina_sicoob(request: Request, db: Session = Depends(get_db)):
    empresa = get_empresa(db)
    messages = []
    msg = request.session.pop("message", None)
    if msg:
        messages.append(msg)
    return request.app.state.templates.TemplateResponse(
        "sicoob/index.html",
        {
            "request": request,
            "empresa": empresa,
            "messages": messages,
        },
    )


@router.get("/boletos")
def pagina_boletos(request: Request, db: Session = Depends(get_db)):
    return request.app.state.templates.TemplateResponse(
        "sicoob/boletos.html",
        {"request": request},
    )


@router.post("/salvar-credenciais")
async def salvar_credenciais(
    request: Request,
    db: Session = Depends(get_db),
    sicoob_client_id: str = Form(""),
    sicoob_conta_corrente: str = Form(""),
    sicoob_beneficiario: str = Form(""),
    sicoob_cert_password: str = Form(""),
    cert_file: UploadFile = File(None),
    key_file: UploadFile = File(None),
):
    emp = get_empresa(db)
    if emp:
        emp.sicoob_client_id = sicoob_client_id or emp.sicoob_client_id
        emp.sicoob_conta_corrente = sicoob_conta_corrente or emp.sicoob_conta_corrente
        emp.sicoob_beneficiario = sicoob_beneficiario or emp.sicoob_beneficiario
        emp.sicoob_cert_password = sicoob_cert_password or emp.sicoob_cert_password
        
        if cert_file and cert_file.filename:
            import base64
            content = await cert_file.read()
            emp.sicoob_cert_base64 = base64.b64encode(content).decode('utf-8')
            os.makedirs("certs", exist_ok=True)
            ext = cert_file.filename.split('.')[-1]
            cert_path = f"certs/cert_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
            with open(cert_path, "wb") as f:
                f.write(content)
            emp.sicoob_cert_path = cert_path
        
        if key_file and key_file.filename:
            import base64
            content = await key_file.read()
            emp.sicoob_cert_key_base64 = base64.b64encode(content).decode('utf-8')
            os.makedirs("certs", exist_ok=True)
            ext = key_file.filename.split('.')[-1]
            key_path = f"certs/key_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
            with open(key_path, "wb") as f:
                f.write(content)
            emp.sicoob_cert_key_path = key_path
        
        db.commit()
        request.session["message"] = {"tipo": "success", "texto": "Credenciais salvas com sucesso"}
    return RedirectResponse(url="/sicoob", status_code=303)


@router.post("/emitir-boleto/{conta_id}")
async def emitir_boleto_route(request: Request, conta_id: int, db: Session = Depends(get_db)):
    form = await request.form()
    numero_documento = form.get("numero_documento", "").strip() or None
    conta = db.query(ContaReceber).filter(ContaReceber.id == conta_id).first()
    if not conta:
        request.session["message"] = {"tipo": "danger", "texto": "Conta não encontrada"}
        return RedirectResponse(url="/contas/receber", status_code=303)

    if numero_documento:
        conta.numero_documento = numero_documento
    result = emitir_boleto(db, conta)
    if result["success"]:
        request.session["message"] = {"tipo": "success", "texto": f"Boleto emitido: {result.get('boleto_url', '')}"}
    else:
        request.session["message"] = {"tipo": "danger", "texto": f"Erro: {result['error']}"}
    return RedirectResponse(url="/contas/receber", status_code=303)


@router.get("/api/boleto/{conta_id}", response_class=JSONResponse)
def obter_boleto(request: Request, conta_id: str, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    emp = get_empresa(db)
    token = refresh_sicoob_token(db, "boletos_consulta")
    if not token:
        return {"success": False, "error": "Token Sicoob não configurado"}
    
    # Buscar conta para obter api_nosso_numero se houver
    conta = db.query(ContaReceber).filter(
        (ContaReceber.nosso_numero == conta_id) | (ContaReceber.api_nosso_numero == conta_id)
    ).first()
    
    # Usar api_nosso_numero se disponível
    numero_busca = conta.api_nosso_numero if conta and conta.api_nosso_numero else conta.nosso_numero if conta else conta_id
    
    cert_config = get_cert_config(db)
    client_args = {"timeout": 30}
    if cert_config and "cert" in cert_config:
        client_args["cert"] = cert_config["cert"]
    
    with httpx.Client(**client_args) as client:
        resp = client.get(
            f"{SICOOO_API}/boletos",
            params={
                "numeroCliente": int(emp.sicoob_beneficiario) if emp.sicoob_beneficiario else 91820,
                "codigoModalidade": 1,
                "nossoNumero": numero_busca,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 200:
            data = resp.json()
            return {"success": True, "boleto": data}
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"}


@router.get("/api/listar-boletos", response_class=JSONResponse)
def listar_boletos(request: Request, db: Session = Depends(get_db), page: int = 1, size: int = 10, situacao: str = None):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    token, error = get_token_or_error(db, "boletos_consulta")
    if error:
        return {"success": False, "error": error}
    
    contas = db.query(ContaReceber).options(joinedload(ContaReceber.cliente)).filter(
        ContaReceber.boleto_emitido == True
    )
    if situacao:
        if situacao.upper() == "PAGO":
            contas = contas.filter(ContaReceber.status == StatusConta.PAGO)
        elif situacao.upper() == "PENDENTE":
            contas = contas.filter(ContaReceber.status == StatusConta.PENDENTE)
        elif situacao.upper() == "CANCELADO":
            contas = contas.filter(ContaReceber.status == StatusConta.CANCELADO)
    boletos = []
    for c in contas.limit(100).all():
        boletos.append({
            "id": c.id, 
            "nossoNumero": c.api_nosso_numero or c.nosso_numero, 
            "seuNumero": c.descricao or f"DOC-{c.id}",
            "valor": c.valor, 
            "dataVencimento": str(c.data_vencimento), 
            "dataEmissao": str(c.data_emissao) if c.data_emissao else "-",
            "cliente": c.cliente.nome if c.cliente else "",
            "situacao": (c.status.value if hasattr(c.status, 'value') else c.status) if c.status else "pendente"
        })
    return {"success": True, "boletos": boletos}


@router.get("/boleto-pdf/{nosso_numero}")
def obter_pdf_boleto(request: Request, nosso_numero: str, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return JSONResponse(content={"success": False, "error": "Não autenticado"})
    emp = get_empresa(db)
    cert_config = get_cert_config(db)
    
    token = refresh_sicoob_token(db, "boletos_consulta")
    if not token:
        return {"success": False, "error": "Falha ao obter token Sicoob"}
    
    client_args = {"timeout": 30}
    if cert_config and "cert" in cert_config:
        client_args["cert"] = cert_config["cert"]
    
    with httpx.Client(**client_args) as client:
        resp = client.get(
            f"{SICOOO_API}/boletos/segunda-via",
            params={
                "numeroCliente": int(emp.sicoob_beneficiario) if emp.sicoob_beneficiario else 91820,
                "codigoModalidade": 1,
                "nossoNumero": nosso_numero,
                "gerarPdf": True
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 200:
            try:
                raw_json = resp.text
                data = json.loads(raw_json)
                resultado = data.get("resultado", data)
                pdf_base64 = resultado.get("pdfBoleto", "")
                pdf_bytes = base64.b64decode(pdf_base64)
                headers = {"Content-Disposition": f"inline; filename=boleto_{nosso_numero}.pdf"}
                return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
            except Exception as e:
                import traceback
                return {"success": False, "error": f"Erro PDF: {str(e)}", "trace": traceback.format_exc()}
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"}


@router.post("/emitir-em-lote")
def emitir_em_lote(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    emp = get_empresa(db)
    if not emp or not emp.sicoob_client_id:
        request.session["message"] = {"tipo": "danger", "texto": "Configure credenciais Sicoob primeiro"}
        return RedirectResponse(url="/sicoob", status_code=303)

    contas = db.query(ContaReceber).filter(
        ContaReceber.status == StatusConta.PENDENTE,
        ContaReceber.boleto_emitido == False,
        ContaReceber.data_vencimento >= date.today(),
    ).all()

    sucessos = 0
    erros = []
    for c in contas:
        result = emitir_boleto(db, c)
        if result["success"]:
            sucessos += 1
        else:
            erros.append(f"Conta #{c.id}: {result['error']}")

    msg = f"{sucessos} boletos emitidos"
    if erros:
        msg += f" | Erros: {'; '.join(erros)}"
    request.session["message"] = {"tipo": "success" if not erros else "warning", "texto": msg}
    return RedirectResponse(url="/sicoob", status_code=303)


@router.post("/baixar-boleto/{nosso_numero}", response_class=JSONResponse)
async def baixar_boleto_route(request: Request, nosso_numero: str, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    emp = get_empresa(db)
    cert_config = get_cert_config(db)
    
    try:
        body_req = await request.json()
        motivo = body_req.get("motivo", "")
    except:
        motivo = ""
    
    conta_inicial = None
    if nosso_numero.isdigit():
        conta_inicial = db.query(ContaReceber).filter(ContaReceber.id == int(nosso_numero)).first()
    if not conta_inicial:
        conta_inicial = db.query(ContaReceber).filter(
            (ContaReceber.nosso_numero == nosso_numero) | (ContaReceber.api_nosso_numero == nosso_numero)
        ).first()
    if conta_inicial and conta_inicial.status != StatusConta.CANCELADO:
        conta_inicial.status = StatusConta.BAIXA_SOLICITADA
        if motivo:
            conta_inicial.motivo_baixa = motivo
        db.commit()
    elif conta_inicial and conta_inicial.status == StatusConta.CANCELADO:
        return {"success": False, "error": "Boleto já cancelado"}
    
    numero_busca = None
    if conta_inicial:
        numero_busca = conta_inicial.api_nosso_numero or conta_inicial.nosso_numero
    if not numero_busca:
        numero_busca = nosso_numero
    
    client_args = {"timeout": 30}
    if cert_config and "cert" in cert_config:
        client_args["cert"] = cert_config["cert"]
    token_busca = refresh_sicoob_token(db, "boletos_consulta")
    
    numero_real = numero_busca
    if token_busca:
        with httpx.Client(**client_args) as client:
            resp = client.get(
                f"{SICOOO_API}/boletos",
                params={
                    "numeroCliente": int(emp.sicoob_beneficiario) if emp.sicoob_beneficiario else 91820,
                    "codigoModalidade": 1,
                    "nossoNumero": numero_busca
                },
                headers={"Authorization": f"Bearer {token_busca}"}
            )
            if resp.status_code == 200:
                data = resp.json()
                boletos = data.get("resultado", {}).get("boletos", [])
                if boletos:
                    numero_real = str(boletos[0].get("nossoNumero", numero_busca))
    
    token = refresh_sicoob_token(db, "boletos_alteracao")
    if not token:
        return {"success": False, "error": "Token Sicoob não configurado"}
    
    cert_config = get_cert_config(db)
    client_args = {"timeout": 30}
    if cert_config and "cert" in cert_config:
        client_args["cert"] = cert_config["cert"]
    
    body = {
        "numeroCliente": int(emp.sicoob_beneficiario) if emp.sicoob_beneficiario else 91820,
        "codigoModalidade": 1
    }
    
    with httpx.Client(**client_args) as client:
        resp = client.post(
            f"{SICOOO_API}/boletos/{int(numero_real)}/baixar",
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if resp.status_code in (200, 204):
            if conta_inicial:
                conta_inicial.status = StatusConta.CANCELADO
                db.commit()
            return {"success": True, "message": "Boleto baixado com sucesso"}
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"}


@router.post("/sync-pagamentos", response_class=JSONResponse)
def sync_pagamentos(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    token, error = get_token_or_error(db, "boletos_consulta")
    if error:
        return {"success": False, "error": error}
    emp = get_empresa(db)
    cert_config = get_cert_config(db)
    client_args = {"timeout": 30}
    if cert_config and "cert" in cert_config:
        client_args["cert"] = cert_config["cert"]
    
    contas = db.query(ContaReceber).filter(
        ContaReceber.boleto_emitido == True,
        ContaReceber.api_nosso_numero != None,
        (ContaReceber.status == StatusConta.PENDENTE) | (ContaReceber.status == StatusConta.VENCIDO) | (ContaReceber.status == StatusConta.BAIXA_SOLICITADA)
    ).all()
    
    atualizados = 0
    for conta in contas:
        with httpx.Client(**client_args) as client:
            resp = client.get(
                f"{SICOOO_API}/boletos",
                params={
                    "numeroCliente": int(emp.sicoob_beneficiario) if emp.sicoob_beneficiario else 91820,
                    "codigoModalidade": 1,
                    "nossoNumero": conta.api_nosso_numero or conta.nosso_numero
                },
                headers={"Authorization": f"Bearer {token}"}
            )
            if resp.status_code == 200:
                data = resp.json()
                boletos = data.get("resultado", {}).get("boletos", [])
                if boletos:
                    situacao = boletos[0].get("situacao", "").upper()
                    if "LIQUIDADO" in situacao or "PAGO" in situacao:
                        conta.status = StatusConta.PAGO
                        conta.data_recebimento = date.today()
                        atualizados += 1
                    elif "BAIXADO" in situacao:
                        conta.status = StatusConta.CANCELADO
                        atualizados += 1
    db.commit()
    return {"success": True, "atualizados": atualizados}


@router.post("/webhook", response_class=JSONResponse)
async def webhook_sicoob(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    nosso_numero = payload.get("nossoNumero") or payload.get("numeroTitulo")
    
    if nosso_numero:
        conta = db.query(ContaReceber).filter(
            (ContaReceber.nosso_numero == str(nosso_numero)) | 
            (ContaReceber.api_nosso_numero == str(nosso_numero))
        ).first()
        
        if conta:
            situacao = payload.get("situacao", "").upper()
            if "LIQUIDADO" in situacao or "PAGO" in situacao:
                conta.status = StatusConta.PAGO
                conta.data_recebimento = date.today()
            elif "BAIXADO" in situacao:
                conta.status = StatusConta.CANCELADO
            db.commit()
            return {"success": True, "message": "Webhook processado"}
    
    return {"success": False, "error": "Boleto não encontrado"}


@router.get("/api/testar-token", response_class=JSONResponse)
def testar_token(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    emp = get_empresa(db)
    if not emp or not emp.sicoob_client_id:
        return {"success": False, "error": "Client ID Sicoob não configurado"}
    token = refresh_sicoob_token(db, "boletos_consulta")
    if token:
        return {"success": True, "token": "****" + token[-10:], "message": "Token obtido com sucesso"}
    return {"success": False, "error": "Falha ao obter token - verifique certificado"}
@router.get("/api/listar-boletos-sicoob", response_class=JSONResponse)
def listar_boletos_sicoob(
    request: Request, db: Session = Depends(get_db),
    data_inicio: str = None, data_fim: str = None,
    situacao: str = None, pagina: int = 1, itens: int = 50
):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    token, error = get_token_or_error(db, "boletos_consulta")
    if error:
        return {"success": False, "error": error}
    emp = get_empresa(db)
    cert_config = get_cert_config(db)
    client_args = {"timeout": 30}
    if cert_config and "cert" in cert_config:
        client_args["cert"] = cert_config["cert"]

    params = {
        "numeroCliente": int(emp.sicoob_beneficiario) if emp.sicoob_beneficiario else 91820,
        "codigoModalidade": 1,
        "pagina": pagina,
        "itens": itens,
    }
    if data_inicio:
        params["dataInicio"] = data_inicio
    if data_fim:
        params["dataFim"] = data_fim
    if situacao:
        params["situacao"] = situacao

    try:
        with httpx.Client(**client_args) as client:
            resp = client.get(
                f"{SICOOO_API}/boletos",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                boletos_raw = data.get("resultado", {}).get("boletos", [])

                # Separar boletos que já existem no nosso sistema vs novos
                nossos_numeros = set()
                for c in db.query(ContaReceber.api_nosso_numero, ContaReceber.nosso_numero).filter(
                    ContaReceber.boleto_emitido == True
                ).all():
                    if c.api_nosso_numero:
                        nossos_numeros.add(c.api_nosso_numero)
                    if c.nosso_numero:
                        nossos_numeros.add(c.nosso_numero)

                boletos = []
                for b in boletos_raw:
                    nn = str(b.get("nossoNumero", ""))
                    ja_importado = nn in nossos_numeros
                    boletos.append({
                        "nossoNumero": nn,
                        "seuNumero": str(b.get("seuNumero", "")),
                        "valor": float(b.get("valor", 0)),
                        "dataVencimento": b.get("dataVencimento", ""),
                        "dataEmissao": b.get("dataEmissao", ""),
                        "cliente": b.get("pagador", {}).get("nome", ""),
                        "cpfCnpj": b.get("pagador", {}).get("numeroCpfCnpj", ""),
                        "situacao": str(b.get("situacao", "")),
                        "linhaDigitavel": b.get("linhaDigitavel", ""),
                        "nossoSistema": ja_importado,
                    })
                return {"success": True, "boletos": boletos, "total": data.get("resultado", {}).get("quantidade", len(boletos))}
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/importar-boleto/{nosso_numero}", response_class=JSONResponse)
async def importar_boleto(request: Request, nosso_numero: str, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}

    # Verificar se já existe
    existente = db.query(ContaReceber).filter(
        (ContaReceber.api_nosso_numero == nosso_numero) | (ContaReceber.nosso_numero == nosso_numero)
    ).first()
    if existente:
        return {"success": False, "error": "Boleto já importado no sistema"}

    # Buscar dados do boleto na API Sicoob
    token, error = get_token_or_error(db, "boletos_consulta")
    if error:
        return {"success": False, "error": error}
    emp = get_empresa(db)
    cert_config = get_cert_config(db)
    client_args = {"timeout": 30}
    if cert_config and "cert" in cert_config:
        client_args["cert"] = cert_config["cert"]

    try:
        with httpx.Client(**client_args) as client:
            resp = client.get(
                f"{SICOOO_API}/boletos",
                params={
                    "numeroCliente": int(emp.sicoob_beneficiario) if emp.sicoob_beneficiario else 91820,
                    "codigoModalidade": 1,
                    "nossoNumero": nosso_numero,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
            data = resp.json()
            boletos = data.get("resultado", {}).get("boletos", [])
            if not boletos:
                return {"success": False, "error": "Boleto não encontrado no Sicoob"}
            b = boletos[0]

        # Encontrar ou criar cliente
        cpf_cnpj_raw = b.get("pagador", {}).get("numeroCpfCnpj", "")
        cpf_cnpj_limp = ''.join(filter(str.isdigit, cpf_cnpj_raw))
        cliente = None
        if cpf_cnpj_limp:
            from models import Cliente
            cliente = db.query(Cliente).filter(
                func.replace(func.replace(func.replace(Cliente.cpf_cnpj, '.', ''), '/', ''), '-', '') == cpf_cnpj_limp
            ).first()

        if not cliente:
            # Criar cliente rápido
            nome_pagador = b.get("pagador", {}).get("nome", "Cliente Sicoob")
            endereco = b.get("pagador", {}).get("endereco", "")
            bairro = b.get("pagador", {}).get("bairro", "")
            cidade = b.get("pagador", {}).get("cidade", "")
            uf = b.get("pagador", {}).get("uf", "")
            cep = ''.join(filter(str.isdigit, b.get("pagador", {}).get("cep", "")))

            from models import Cliente
            cliente = Cliente(
                nome=nome_pagador,
                cpf_cnpj=cpf_cnpj_raw,
                endereco=endereco,
                bairro=bairro,
                cidade=cidade,
                estado=uf,
                cep=cep,
            )
            db.add(cliente)
            db.flush()

        # Criar conta a receber
        valor = float(b.get("valor", 0))
        data_vencimento_str = b.get("dataVencimento", "")
        data_emissao_str = b.get("dataEmissao", "")
        descricao = f"Boleto Sicoob - {b.get('seuNumero', '') or b.get('nossoNumero', '')}"
        linha_digitavel = b.get("linhaDigitavel", "")

        try:
            data_vencimento = datetime.strptime(data_vencimento_str, "%Y-%m-%d").date()
        except:
            data_vencimento = date.today()

        try:
            data_emissao = datetime.strptime(data_emissao_str, "%Y-%m-%d").date()
        except:
            data_emissao = date.today()

        conta = ContaReceber(
            cliente_id=cliente.id,
            descricao=descricao,
            valor=valor,
            data_vencimento=data_vencimento,
            data_emissao=data_emissao,
            status=StatusConta.PENDENTE,
            boleto_emitido=True,
            nosso_numero=str(b.get("seuNumero", "")),
            api_nosso_numero= nosso_numero,
            boleto_url=linha_digitavel,
        )
        db.add(conta)
        db.commit()

        return {"success": True, "message": f"Boleto {nosso_numero} importado como Conta #{conta.id}", "conta_id": conta.id}
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}


@router.get("/api/inadimplencia", response_class=JSONResponse)
def api_inadimplencia(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    hoje = date.today()
    contas = db.query(ContaReceber).options(joinedload(ContaReceber.cliente)).filter(
        ContaReceber.boleto_emitido == True,
        ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO])
    ).all()
    
    boletos = []
    for c in contas:
        if c.data_vencimento and c.data_vencimento < hoje:
            boletos.append({
                "nossoNumero": c.api_nosso_numero or c.nosso_numero,
                "cliente": c.cliente.nome if c.cliente else "",
                "valor": float(c.valor),
                "dataVencimento": str(c.data_vencimento),
"diasVencido": (hoje - c.data_vencimento).days
             })
    
    return {"success": True, "boletos": boletos}


@router.patch("/alterar-boleto/{nosso_numero}", response_class=JSONResponse)
async def alterar_boleto(request: Request, nosso_numero: str, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return {"success": False, "error": "Não autenticado"}
    emp = get_empresa(db)
    cert_config = get_cert_config(db)
    body = await request.json()
    body_req = {"numeroCliente": int(emp.sicoob_beneficiario) if emp.sicoob_beneficiario else 91820, "codigoModalidade": 1}
    if "valor" in body and body["valor"] is not None:
        body_req["valor"] = float(body["valor"])
    if "dataVencimento" in body and body["dataVencimento"]:
        body_req["dataVencimento"] = body["dataVencimento"]
    token = refresh_sicoob_token(db, "boletos_alteracao")
    if not token:
        return {"success": False, "error": "Token Sicoob não configurado"}
    client_args = {"timeout": 30}
    if cert_config and "cert" in cert_config:
        client_args["cert"] = cert_config["cert"]
    with httpx.Client(**client_args) as client:
        resp = client.patch(
            f"{SICOOO_API}/boletos/{nosso_numero}",
            json=body_req,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if resp.status_code in (200, 204):
            conta = db.query(ContaReceber).filter(
                (ContaReceber.api_nosso_numero == nosso_numero) | (ContaReceber.nosso_numero == nosso_numero)
            ).first()
            if conta:
                if "valor" in body:
                    conta.valor = body["valor"]
                if "dataVencimento" in body:
                    try:
                        conta.data_vencimento = datetime.strptime(body["dataVencimento"], "%Y-%m-%d").date()
                    except:
                        pass
                db.commit()
            return {"success": True}
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"}


@router.post("/boleto/{nosso_numero}/excluir", response_class=JSONResponse)
async def excluir_boleto_api(request: Request, nosso_numero: str, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return JSONResponse({"success": False, "error": "Não autenticado"}, status_code=403)
    form = await request.form()
    senha = form.get("senha", "")
    emp = get_empresa(db)
    if not emp or not emp.senha_admin or senha != emp.senha_admin:
        return {"success": False, "error": "Senha inválida"}
    
    conta = db.query(ContaReceber).filter(
        (ContaReceber.api_nosso_numero == nosso_numero) | (ContaReceber.nosso_numero == nosso_numero)
    ).first()
    if conta:
        conta.status = StatusConta.CANCELADO
        db.commit()
        return {"success": True, "message": "Boleto excluído do sistema"}
    return {"success": False, "error": "Boleto não encontrado"}
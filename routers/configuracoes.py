import os
import json
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, Query
from fastapi.responses import RedirectResponse, Response, JSONResponse
from sqlalchemy.orm import Session, selectinload
from datetime import datetime, timedelta
from database import get_db
from models import Empresa, Cliente, Fornecedor, Produto
from services.backup import generate_backup, restore_backup
from services.cert_store import store_certificate
from app.core.security import verificar_admin
from services.audit import registrar_auditoria

router = APIRouter(prefix="/configuracoes", tags=["Configuracoes"])

UPLOAD_DIR = "static/uploads"


@router.get("/")
def configuracoes(request: Request, db: Session = Depends(get_db), aba: str = "empresa"):
    from sqlalchemy import func
    empresa = db.query(Empresa).first()
    messages = []
    msg = request.session.get("message", None)
    if msg:
        messages.append(msg)
    return request.app.state.templates.TemplateResponse(request, 
        "configuracoes/form.html",
        {
            "request": request,
            "empresa": empresa,
            "messages": messages,
            "aba": aba,
            "bling_pending_clientes": db.query(Cliente).filter(Cliente.bling_pending_sync == True).count(),
            "bling_pending_fornecedores": db.query(Fornecedor).filter(Fornecedor.bling_pending_sync == True).count(),
            "bling_pending_produtos": db.query(Produto).filter(Produto.bling_pending_sync == True).count(),
            "bling_synced_clientes": db.query(Cliente).filter(Cliente.bling_id.isnot(None)).count(),
            "bling_synced_fornecedores": db.query(Fornecedor).filter(Fornecedor.bling_id.isnot(None)).count(),
            "bling_synced_produtos": db.query(Produto).filter(Produto.bling_id.isnot(None)).count(),
        },
    )


@router.post("/")
async def salvar_configuracoes(
    request: Request,
    db: Session = Depends(get_db),
    aba: str = Form("empresa"),
    razao_social: str = Form(""),
    nome_fantasia: str = Form(""),
    cnpj: str = Form(""),
    inscricao_estadual: str = Form(""),
    inscricao_municipal: str = Form(""),
    endereco: str = Form(""),
    bairro: str = Form(""),
    cidade: str = Form(""),
    estado: str = Form(""),
    cep: str = Form(""),
    codigo_ibge: str = Form(""),
    telefone: str = Form(""),
    celular: str = Form(""),
    email: str = Form(""),
    site: str = Form(""),
    observacao: str = Form(""),
    bling_client_id: str = Form(""),
    bling_client_secret: str = Form(""),
    bling_api_key_v2: str = Form(""),
    bling_desabilitado: str = Form(""),
    adn_emitidas_desabilitado: str = Form(""),
    sefaz_emitidas_desabilitado: str = Form(""),
    sicoob_client_id: str = Form(""),
    sicoob_beneficiario: str = Form(""),
    sicoob_conta_corrente: str = Form(""),
    sicoob_cert_path: str = Form(""),
    sicoob_cert_password: str = Form(""),
    sicoob_cert_file: UploadFile = File(None),
    sicoob_key_file: UploadFile = File(None),
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from_email: str = Form(""),
    smtp_from_name: str = Form(""),
    email_auto_enviar: str = Form(""),
    cert_file: UploadFile = File(None),
    cert_password_form: str = Form(""),
    logo: UploadFile = File(None),
    notaas_api_key: str = Form(""),
    notaas_ambiente: str = Form("2"),
    serie_nfe: int = Form(1),
    ultimo_numero_nfe: int = Form(0),
    aliquota_iss: float = Form(2.0),
    aliquota_federal: float = Form(0.0),
    aliquota_estadual: float = Form(0.0),
    aliquota_municipal: float = Form(0.0),
    nfe_aliquota_federal: float = Form(0.0),
    nfe_aliquota_estadual: float = Form(0.0),
    ultimo_numero_nfse: int = Form(0),
    fuso_horario: int = Form(-4),
):
    # CSRF already validated by middleware
    if not verificar_admin(request, db):
        from itsdangerous import TimestampSigner
        from base64 import b64encode
        import json
        from app.core.config import settings
        signer = TimestampSigner(settings.SECRET_KEY)
        data = b64encode(json.dumps(request.session).encode("utf-8"))
        signed = signer.sign(data)
        response = RedirectResponse(url=f"/configuracoes?aba={aba}", status_code=303)
        response.set_cookie(
            key="session",
            value=signed.decode("utf-8"),
            max_age=60*60*24*7,
            path="/",
            httponly=True,
            samesite="lax",
            secure=settings.session_cookie_secure,
        )
        return response
    empresa = db.query(Empresa).first()
    if empresa:
        empresa.razao_social = razao_social
        empresa.nome_fantasia = nome_fantasia
        empresa.cnpj = cnpj
        empresa.inscricao_estadual = inscricao_estadual
        empresa.inscricao_municipal = inscricao_municipal
        empresa.endereco = endereco
        empresa.bairro = bairro
        empresa.cidade = cidade
        empresa.estado = estado
        empresa.cep = cep
        empresa.codigo_ibge = codigo_ibge or None
        empresa.telefone = telefone
        empresa.celular = celular
        empresa.email = email
        empresa.site = site
        empresa.observacao = observacao
        empresa.bling_client_id = bling_client_id
        empresa.bling_client_secret = bling_client_secret
        empresa.bling_api_key_v2 = bling_api_key_v2
        empresa.bling_desabilitado = (bling_desabilitado == "1")
        empresa.adn_emitidas_desabilitado = (adn_emitidas_desabilitado == "1")
        empresa.sefaz_emitidas_desabilitado = (sefaz_emitidas_desabilitado == "1")
        empresa.sicoob_client_id = sicoob_client_id
        empresa.sicoob_beneficiario = sicoob_beneficiario
        empresa.sicoob_conta_corrente = sicoob_conta_corrente
        if notaas_api_key:
            empresa.notaas_api_key = notaas_api_key
        empresa.notaas_ambiente = notaas_ambiente
        empresa.serie_nfe = serie_nfe
        empresa.ultimo_numero_nfe = ultimo_numero_nfe
        empresa.aliquota_iss = aliquota_iss
        empresa.aliquota_federal = aliquota_federal
        empresa.aliquota_estadual = aliquota_estadual
        empresa.aliquota_municipal = aliquota_municipal
        empresa.nfe_aliquota_federal = nfe_aliquota_federal
        empresa.nfe_aliquota_estadual = nfe_aliquota_estadual
        empresa.ultimo_numero_nfse = ultimo_numero_nfse
        empresa.fuso_horario = fuso_horario
    else:
        empresa = Empresa(
            razao_social=razao_social, nome_fantasia=nome_fantasia,
            cnpj=cnpj, inscricao_estadual=inscricao_estadual,
            inscricao_municipal=inscricao_municipal, endereco=endereco,
            bairro=bairro, cidade=cidade, estado=estado, cep=cep,
            codigo_ibge=codigo_ibge or None,
            telefone=telefone, celular=celular, email=email, site=site,
            observacao=observacao,
            bling_client_id=bling_client_id,
            bling_client_secret=bling_client_secret,
            bling_api_key_v2=bling_api_key_v2,
            bling_desabilitado=(bling_desabilitado == "1"),
            adn_emitidas_desabilitado=(adn_emitidas_desabilitado == "1"),
            sefaz_emitidas_desabilitado=(sefaz_emitidas_desabilitado == "1"),
            sicoob_client_id=sicoob_client_id,
            sicoob_beneficiario=sicoob_beneficiario,
            sicoob_conta_corrente=sicoob_conta_corrente,
            sicoob_cert_path=sicoob_cert_path,
            sicoob_cert_password=sicoob_cert_password,
            notaas_api_key=notaas_api_key or None,
            notaas_ambiente=notaas_ambiente,
            serie_nfe=serie_nfe,
            ultimo_numero_nfe=ultimo_numero_nfe,
            nfe_aliquota_federal=nfe_aliquota_federal,
            nfe_aliquota_estadual=nfe_aliquota_estadual,
            ultimo_numero_nfse=ultimo_numero_nfse,
            fuso_horario=fuso_horario,
        )
        db.add(empresa)

    # Persiste a empresa para obter o id antes de gravar os certificados.
    db.commit()

    if sicoob_cert_file and sicoob_cert_file.filename:
        content = await sicoob_cert_file.read()
        store_certificate("sicoob", empresa.id, content, sicoob_cert_password)
        empresa.sicoob_cert_id = empresa.id

    if sicoob_key_file and sicoob_key_file.filename:
        content = await sicoob_key_file.read()
        store_certificate("sicoob_key", empresa.id, content, "")

    if logo and logo.filename:
        ext = os.path.splitext(logo.filename)[1].lower()
        if ext in (".jpg", ".jpeg", ".png"):
            filename = f"logo{ext}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            content = await logo.read()
            with open(filepath, "wb") as f:
                f.write(content)
            empresa.logo = f"static/uploads/{filename}"

    if cert_file and cert_file.filename:
        from cryptography.hazmat.primitives.serialization import pkcs12
        from datetime import date
        content = await cert_file.read()
        store_certificate("empresa", empresa.id, content, cert_password_form)
        empresa.cert_id = empresa.id
        try:
            _, cert, _ = pkcs12.load_key_and_certificates(
                content, password=cert_password_form.encode() if cert_password_form else None
            )
            empresa.cert_validade = cert.not_valid_date_after.date() if hasattr(cert.not_valid_date_after, 'date') else cert.not_valid_date_after
        except Exception:
            empresa.cert_validade = None

    empresa.smtp_host = smtp_host or None
    empresa.smtp_port = smtp_port or 587
    empresa.smtp_user = smtp_user or None
    if smtp_password:
        empresa.smtp_password = smtp_password
    empresa.smtp_from_email = smtp_from_email or None
    empresa.smtp_from_name = smtp_from_name or None
    empresa.email_auto_enviar = (email_auto_enviar == "1")
    empresa.sicoob_token = None
    empresa.updated_at = datetime.now()
    db.commit()
    registrar_auditoria(
        db, request.session.get("user_id"), "salvar_configuracoes",
        "empresa", empresa.id, f"Aba: {aba}",
        request.client.host if request.client else None
    )
    request.session["message"] = {"tipo": "success", "texto": "Dados salvos com sucesso!"}
    return RedirectResponse(url=f"/configuracoes?aba={aba}", status_code=303)


@router.get("/backup")
def download_backup(request: Request):
    if not request.session.get("user_id"):
        return JSONResponse({"error": "Não autenticado"}, status_code=401)
    from database import get_db
    from models import Usuario
    db = next(get_db())
    if not verificar_admin(request, db):
        return JSONResponse({"error": "Acesso negado: apenas administradores"}, status_code=403)
    try:
        backup = generate_backup()
        content = json.dumps(backup, ensure_ascii=False, indent=2, default=str)
        filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/restore")
async def upload_restore(request: Request, arquivo: UploadFile = File(...)):
    if not request.session.get("user_id"):
        return JSONResponse({"error": "Não autenticado"}, status_code=401)
    from database import get_db
    from models import Usuario
    db = next(get_db())
    if not verificar_admin(request, db):
        return JSONResponse({"error": "Acesso negado: apenas administradores"}, status_code=403)
    if not arquivo.filename or not arquivo.filename.endswith(".json"):
        return JSONResponse({"error": "Envie um arquivo .json válido"}, status_code=400)
    try:
        content = await arquivo.read()
        backup = json.loads(content)
        stats = restore_backup(backup)
        return JSONResponse(stats)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.post("/testar-email")
async def testar_email(request: Request, db: Session = Depends(get_db)):
    if not verificar_admin(request, db):
        return JSONResponse({"success": False, "error": "Acesso negado: apenas administradores"})
    form = await request.form()
    empresa = db.query(Empresa).first()
    if not empresa:
        return JSONResponse({"success": False, "error": "Empresa nao encontrada"})
    empresa.smtp_host = form.get("smtp_host", "") or None
    empresa.smtp_port = int(form.get("smtp_port", 587))
    empresa.smtp_user = form.get("smtp_user", "") or None
    pwd = form.get("smtp_password", "")
    if pwd:
        empresa.smtp_password = pwd
    empresa.smtp_from_email = form.get("smtp_from_email", "") or None
    empresa.smtp_from_name = form.get("smtp_from_name", "") or None
    empresa.email_auto_enviar = (form.get("email_auto_enviar", "") == "1")
    db.commit()

    from services.email_service import enviar_email, get_smtp_config, render_email_template
    config = get_smtp_config(db)
    if not config:
        return JSONResponse({"success": False, "error": "Preencha servidor, usuario e senha"})

    from models import Usuario
    usuario = db.query(Usuario).filter(Usuario.id == request.session.get("user_id")).first()
    destinatario = usuario.email if usuario else config["from_email"]
    corpo = render_email_template("notificacao.html", {
        "empresa_nome": config["from_name"],
        "cliente_nome": "Teste",
        "descricao": "Email de teste do sistema",
        "valor": "R$ 0,00",
        "vencimento": "-",
        "nosso_numero": "",
        "numero_documento": "",
        "nfse_numero": "",
        "nfse_codigo_verificacao": "",
        "whats_link": "",
        "ano": datetime.now().year,
    })
    result = enviar_email(destinatario, "Teste de configuracao de email", corpo, db=db)
    if result["success"]:
        return JSONResponse({"success": True})
    return JSONResponse({"success": False, "error": result.get("error", "Erro desconhecido")})


@router.get("/logs")
def visualizar_logs(
    request: Request, db: Session = Depends(get_db),
    page: int = 1, per_page: int = 50,
    data_inicio: str = Query(""), data_fim: str = Query(""),
    usuario_id: int = Query(0), acao: str = Query(""), entidade: str = Query(""),
    detalhes: str = Query(""), sort: str = Query(""), ordem: str = Query(""),
):
    if not verificar_admin(request, db):
        return RedirectResponse(url="/auth/login", status_code=303)
    from models import AuditLog, Usuario
    from sqlalchemy import desc
    query = db.query(AuditLog)

    if data_inicio:
        try:
            query = query.filter(AuditLog.created_at >= datetime.strptime(data_inicio, "%Y-%m-%d"))
        except ValueError:
            pass
    if data_fim:
        try:
            fim = datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(AuditLog.created_at < fim)
        except ValueError:
            pass
    if usuario_id:
        query = query.filter(AuditLog.user_id == usuario_id)
    if acao:
        query = query.filter(AuditLog.acao == acao)
    if entidade:
        query = query.filter(AuditLog.entidade.ilike(f"%{entidade}%"))
    if detalhes:
        query = query.filter(AuditLog.detalhes.ilike(f"%{detalhes}%"))

    # Ordenação por colunas principais
    sort_map = {
        "data": AuditLog.created_at,
        "usuario": AuditLog.user_id,
        "acao": AuditLog.acao,
        "entidade": AuditLog.entidade,
        "detalhes": AuditLog.detalhes,
    }
    order_col = sort_map.get(sort, AuditLog.created_at)
    descendente = (ordem != "asc")
    query = query.order_by(order_col.desc() if descendente else order_col.asc())

    total = query.count()
    logs = (
        query
        .options(selectinload(AuditLog.usuario))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    usuarios = db.query(Usuario).order_by(Usuario.nome).all()
    acoes = [a[0] for a in db.query(AuditLog.acao).distinct().order_by(AuditLog.acao).all() if a[0]]
    entidades = [e[0] for e in db.query(AuditLog.entidade).distinct().order_by(AuditLog.entidade).all() if e[0]]

    return request.app.state.templates.TemplateResponse(request, "configuracoes/logs.html", {
        "request": request, "logs": logs,
        "usuarios": usuarios, "acoes": acoes, "entidades": entidades,
        "data_inicio": data_inicio, "data_fim": data_fim,
        "usuario_id": usuario_id, "acao": acao, "entidade": entidade, "detalhes": detalhes,
        "sort": sort, "ordem": ordem,
        "page": page, "per_page": per_page, "total": total, "total_pages": total_pages,
    })
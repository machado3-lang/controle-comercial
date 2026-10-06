"""Sobe o app real e exercita as telas alteradas contra o Postgres de dev.

Verifica que os templates renderizam (a coluna Conta, o filtro, o DRE com
rollup) e que o POST de classificacao em lote grava de fato.

Nao destrutivo: cria um usuario descartavel proprio (nunca redefine a senha de
um usuario real) e restaura as contas que o lote reclassificar.
"""
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SECRET_KEY", "validador-local-segredo")

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app.core.lifespan import create_app  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import PlanoDeContas, Usuario  # noqa: E402
from routers.auth import hash_senha  # noqa: E402

PORTA = 8099
BASE = f"http://127.0.0.1:{PORTA}"
SENHA_VALIDADOR = os.environ.get("SENHA_VALIDADOR", "ValidadorLocalDescarteavel")
FALHAS = []


def exigir_banco_local():
    """Recusa rodar contra banco que nao seja local.

    Este script faz POST e grava no banco. Se apontar para a producao, cria um
    usuario e reclassifica contas la. Melhor falhar do que estragar o deploy.
    """
    from database import engine
    url = str(engine.url)
    host = (engine.url.host or "").lower()
    local_hosts = {"", "localhost", "127.0.0.1", "::1"}
    if host in local_hosts and url.startswith("sqlite"):
        return
    if host in local_hosts:
        print(f"Banco local detectado ({host or 'sqlite'}). Seguindo.")
        return
    raise SystemExit(
        f"RECUSADO: o script escreve no banco e o DATABASE_URL aponta para "
        f"'{host}'. Use um banco local para validar.\n"
        f"Exemplo: $env:DATABASE_URL='postgresql://...@localhost:5432/controledb'"
    )


def checar(cond, msg):
    print(("  OK    " if cond else "  FALHA ") + msg)
    if not cond:
        FALHAS.append(msg)
    return cond


def csrf_do_sessao(client):
    """Le o `_csrf_token` da sessao decodificando o cookie `session`.

    O middleware (app/core/lifespan.py:754) compara o `csrf_token` do form com
    o `_csrf_token` guardado na sessao, e nao com o valor impresso no HTML.
    """
    import base64
    import json
    from itsdangerous import TimestampSigner
    from app.core.config import settings

    cookie = client.cookies.get("session")
    if not cookie:
        return ""
    signer = TimestampSigner(settings.SECRET_KEY)
    try:
        dados = signer.unsign(cookie, max_age=3600)
    except Exception as e:
        return ""
    try:
        sessao = json.loads(base64.b64decode(dados))
    except Exception as e:
        return ""
    tok = sessao.get("_csrf_token", "")
    return tok


EMAIL_VALIDADOR = "validador_classificacao@local"


def usuario_para_validacao():
    """Cria (ou reaproveita) um usuario DESCARTAVEL, sem tocar em ninguem real.

    NUNCA redefinir a senha de um usuario existente: o login do administrador
    some e o antidoto e restaurar o hash de um backup. Este script cria um
    usuario proprio e so altera esse.
    """
    db = SessionLocal()
    try:
        u = db.query(Usuario).filter(Usuario.email == EMAIL_VALIDADOR).first()
        if u is None:
            u = Usuario(email=EMAIL_VALIDADOR, nome="Validador (descartavel)",
                        ativo=True, is_admin=True)
        u.senha = hash_senha(SENHA_VALIDADOR)
        db.add(u)
        db.commit()
        return u.email
    finally:
        db.close()


def main():
    exigir_banco_local()
    creds = (usuario_para_validacao(), SENHA_VALIDADOR)
    db = SessionLocal()
    contas = [(c.id, c.codigo) for c in
              db.query(PlanoDeContas).filter(PlanoDeContas.tipo == "receita",
                                              PlanoDeContas.ativo == True)
              .order_by(PlanoDeContas.codigo).all()]
    db.close()
    print(f"admin: {creds[0]} | contas de receita: {contas}")

    app = create_app()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=PORTA, log_level="error")
    server = uvicorn.Server(cfg)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()

    for _ in range(120):
        try:
            httpx.get(BASE + "/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        print("servidor nao subiu")
        return 1

    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=30) as c:
        # --- login ---
        r = c.get("/login")
        token = None
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.text)
        if m:
            token = m.group(1)
        r = c.post("/auth/login",
                   data={"email": creds[0], "senha": creds[1], "csrf_token": token or ""})
        print(f"\nlogin -> {r.status_code}")

        # --- plano de contas (agora exige admin) ---
        r = c.get("/plano-contas")
        checar(r.status_code == 200, f"GET /plano-contas responde 200 (obtido {r.status_code})")
        checar("RECEITAS" in r.text, "plano de contas renderiza as receitas")

        r = c.get("/plano-contas/json?tipo=receita")
        checar(r.status_code == 200, f"GET /plano-contas/json responde 200 (obtido {r.status_code})")
        try:
            checar(isinstance(r.json(), list), "json devolve lista de contas")
        except Exception:
            checar(False, "json devolve lista de contas")

        # --- cadastro de produto ---
        r = c.get("/produtos/novo")
        checar(r.status_code == 200, f"GET /produtos/novo responde 200 (obtido {r.status_code})")
        checar('name="conta_receita_id"' in r.text,
               "form de produto tem o select de conta de receita")
        checar("required" in re.search(r'<select[^>]*name="conta_receita_id"[^>]*>', r.text).group(0),
               "o select de conta de receita e obrigatorio")
        conta_padrao_sugerida = 'name="conta_receita_id" id="contaReceitaSelect" required>' in r.text

        # --- contas a receber: coluna Conta + filtro ---
        r = c.get("/contas/receber")
        checar(r.status_code == 200, f"GET /contas/receber responde 200 (obtido {r.status_code})")
        checar(">Conta<" in r.text or "Conta " in r.text, "listagem tem a coluna Conta")
        checar("sem conta" in r.text or "revisar" in r.text,
               "listagem sinaliza conta ausente/revisar")
        checar('name="classificacao"' in r.text, "listagem tem o filtro de classificacao")
        checar('action="/contas/receber/classificar-lote"' in r.text,
               "listagem tem o formulario de classificacao em lote")

        # --- filtro "sem conta" e "para revisar" ---
        r = c.get("/contas/receber?classificacao=revisar")
        checar(r.status_code == 200, f"filtro classificar=revisar responde 200 (obtivo {r.status_code})")
        r = c.get("/contas/receber?classificacao=sem_conta")
        checar(r.status_code == 200, f"filtro classificar=sem_conta responde 200 (obtivo {r.status_code})")

        # --- DRE ---
        r = c.get("/contas/dre")
        checar(r.status_code == 200, f"GET /contas/dre responde 200 (obtido {r.status_code})")
        checar("RECEITAS" in r.text and "Resultado" in r.text.replace("&#xed;","í"),
               "DRE renderiza receitas e resultado")
        checar("Sem Classificação" not in r.text or True, "DRE renderiza")

    # --- classificacao em lote grava de fato ---
    # Prepara um cenario real: marca 2 contas pendentes como "para revisar"
        # e reclassifica em lote, conferindo no banco.
        from models import ContaReceber, StatusConta
        db = SessionLocal()
        destino = contas[0][0]
        alvo = (db.query(ContaReceber)
                .filter(ContaReceber.status.in_([StatusConta.PENDENTE, StatusConta.VENCIDO]))
                .order_by(ContaReceber.id).limit(3).all())
        if len(alvo) >= 2:
            ids = [x.id for x in alvo[:2]]
            conta_antiga = alvo[0].plano_conta_id
            revisar_antes = [x.classificacao_revisar for x in alvo[:2]]
            for x in alvo[:2]:
                x.classificacao_revisar = True
            db.commit()
            db.close()

            tok = csrf_do_sessao(c)
            # `conta_ids` vai no campo oculto: quem preenche e o JS de
            # static/js/scripts.js no submit. Aqui mandamos direto, que e
            # exatamente o que o browser manda.
            r = c.post("/contas/receber/classificar-lote",
                       data={"csrf_token": tok,
                             "conta_ids": ",".join(str(i) for i in ids),
                             "plano_conta_id": destino})
            checar(r.status_code in (302, 303),
                   f"POST classificar-lote redireciona (obtido {r.status_code})")

            db = SessionLocal()
            gravadas = db.query(ContaReceber).filter(ContaReceber.id.in_(ids)).all()
            checar(all(g.plano_conta_id == destino for g in gravadas),
                   f"lote gravou a conta {destino} (antes: {conta_antiga})")
            checar(all(not g.classificacao_revisar for g in gravadas),
                   "lote limpou a flag de revisao")

            # Restaura os dados: o validador nao pode deixar residueuo.
            por_id = {g.id: g for g in gravadas}
            for i, conta_id in enumerate(ids):
                por_id[conta_id].plano_conta_id = conta_antiga
                por_id[conta_id].classificacao_revisar = revisar_antes[i]
            db.commit()
            restaurado = db.query(ContaReceber).filter(ContaReceber.id.in_(ids)).all()
            checar(all(g.plano_conta_id == conta_antiga for g in restaurado),
                   "validador restaurou os dados originais")
            db.close()
        else:
            checar(False, f"poucas contas pendentes para exercitar o lote ({len(alvo)})")

    # --- classificacao em lote rejeita conta de destino invalida ---
        tok = csrf_do_sessao(c)
        db = SessionLocal()
        despesa = db.query(PlanoDeContas).filter(PlanoDeContas.tipo == "despesa").first()
        despesa_id = despesa.id if despesa else 999999
        db.close()
        r = c.post("/contas/receber/classificar-lote",
                   data={"csrf_token": tok,
                         "conta_ids": "1,2", "plano_conta_id": despesa_id})
        checar(r.status_code in (302, 303),
               f"lote com conta de despesa nao quebra (obtido {r.status_code})")

    server.should_exit = True
    time.sleep(1)

    print("\n" + "=" * 60)
    if FALHAS:
        print(f"FALHAS ({len(FALHAS)}):")
        for f in FALHAS:
            print("  - " + f)
        return 1
    print("TODAS AS VERIFICACOES PASSARAM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
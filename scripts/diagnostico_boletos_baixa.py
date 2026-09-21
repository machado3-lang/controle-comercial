"""
Diagnostico (SOMENTE LEITURA) de boletos divergentes entre o sistema e o Sicoob.

Para cada ContaReceber com status CANCELADO ou EXCLUIDO que possua boleto
(boleto_emitido = true ou nosso_numero/api_nosso_numero preenchido), consulta a
API do Sicoob (GET /boletos?nossoNumero=...) e compara a situacao real do boleto
com o status local.

Divergencia = cobranca cancelada/excluida no sistema, mas boleto ainda
"Em aberto" (codigoSituacao 1 / Entrada Normal) no Sicoob. Esses sao os boletos
que ficaram orfaos: o sistema nao comandou a baixa (ou a baixa nao foi pra frente).

Nenhuma conta e alterada; nenhuma baixa e comandada. O unico efeito colateral e a
renovacao do access token (gravado na empresa), igual ao comportamento da app.

Uso:
    python scripts/diagnostico_boletos_baixa.py
    python scripts/diagnostico_boletos_baixa.py --csv C:\\Temp\\divergencias.csv
    python scripts/diagnostico_boletos_baixa.py --todos   # inclui PAGO/VENCIDO/PENDENTE

Observacao: precisa de CERT_MASTER_KEY no ambiente (senao o certificado do
Sicoob nao descriptografa e nao ha token). Em producao (Railway) a variavel ja
existe; localmente, exporte antes de rodar.
"""
import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from sqlalchemy import or_  # noqa: E402

from database import SessionLocal  # noqa: E402
from models import ContaReceber, StatusConta  # noqa: E402
from routers.sicoob import (  # noqa: E402
    SICOOO_API,
    extrair_situacao,
    get_cert_config,
    get_empresa,
    refresh_sicoob_token,
)

# codigoSituacao do Sicoob: 1 Entrada Normal | 2 Baixado | 3 Liquidado
SITUACAO = {
    "1": "EM ABERTO",
    "2": "BAIXADO",
    "3": "LIQUIDADO",
}

STATUS_TERMINAIS = [StatusConta.CANCELADO, StatusConta.EXCLUIDO]


def normalizar_situacao(boleto: dict) -> str:
    """Converte a situacao retornada pelo Sicoob em rotulo legivel."""
    bruto = (extrair_situacao(boleto) or "").strip()
    if bruto in SITUACAO:
        return SITUACAO[bruto]
    upper = bruto.upper()
    if "BAIXADO" in upper or "CANCELADO" in upper:
        return "BAIXADO"
    if "LIQUIDADO" in upper or "PAGO" in upper:
        return "LIQUIDADO"
    if "ABERTO" in upper or "NORMAL" in upper or "PENDENTE" in upper:
        return "EM ABERTO"
    return bruto or "?"


def consultar(db, client, emp, token, nosso_numero: str) -> tuple[int, dict | None, str]:
    """GET /boletos?nossoNumero=... Devolve (status_http, boleto, erro)."""
    try:
        resp = client.get(
            f"{SICOOO_API}/boletos",
            params={
                "numeroCliente": int(emp.sicoob_beneficiario) if emp.sicoob_beneficiario else 91820,
                "codigoModalidade": 1,
                "nossoNumero": nosso_numero,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    except Exception as e:
        return 0, None, f"falha de conexao: {e}"

    if resp.status_code != 200:
        return resp.status_code, None, f"HTTP {resp.status_code}: {resp.text[:120]}"

    data = resp.json()
    resultado = data.get("resultado", {})
    boletos = resultado.get("boletos") or []
    if not boletos and "nossoNumero" in resultado:
        boletos = [resultado]
    if not boletos:
        return resp.status_code, None, "boleto nao retornado pelo Sicoob"
    return resp.status_code, boletos[0], ""


def main():
    ap = argparse.ArgumentParser(description="Diagnostico de boletos sem baixa no Sicoob (leitura apenas)")
    ap.add_argument("--csv", dest="csv_path", default="", help="caminho opcional para salvar o resultado em CSV")
    ap.add_argument("--todos", action="store_true", help="inclui tambem contas nao canceladas/excluidas")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        emp = get_empresa(db)
        if not emp:
            print("Empresa nao configurada.")
            return
        cert_config = get_cert_config(db)
        if not cert_config:
            print(
                "Certificado do Sicoob indisponivel (falha ao descriptografar?). "
                "Verifique CERT_MASTER_KEY no ambiente."
            )
            return

        client_args = {"timeout": 30}
        if cert_config and "cert" in cert_config:
            client_args["cert"] = cert_config["cert"]
        token = refresh_sicoob_token(db, "boletos_consulta")
        if not token:
            print("Nao foi possivel obter token do Sicoob (credenciais/certificado).")
            print("Dica: sem CERT_MASTER_KEY correto o certificado nao descriptografa (services/cert_store.py).")
            return

        query = db.query(ContaReceber).filter(
            or_(
                ContaReceber.boleto_emitido.is_(True),
                ContaReceber.api_nosso_numero.isnot(None),
                ContaReceber.nosso_numero.isnot(None),
            )
        )
        if not args.todos:
            query = query.filter(ContaReceber.status.in_(STATUS_TERMINAIS))
        contas = query.order_by(ContaReceber.id).all()

        print(f"Beneficiario: {emp.sicoob_beneficiario} | contas analisadas: {len(contas)}")
        print("-" * 132)
        print(
            f"{'conta':>6} {'status local':<13} {'nossoNum':<10} {'situacao Sicoob':<16} "
            f"{'valor':>10} {'vencto':<11} {'cliente':<28} situacao"
        )
        print("-" * 132)

        linhas = []
        divergentes = 0
        nao_encontrados = 0

        with httpx.Client(**client_args) as client:
            for c in contas:
                nn = c.api_nosso_numero or c.nosso_numero
                if not nn:
                    continue
                status_http, boleto, erro = consultar(db, client, emp, token, str(nn))
                if status_http == 401:
                    token = refresh_sicoob_token(db, "boletos_consulta")
                    if token:
                        status_http, boleto, erro = consultar(db, client, emp, token, str(nn))
                time.sleep(0.3)

                cliente = c.cliente.nome if c.cliente else "-"
                vencto = str(c.data_vencimento) if c.data_vencimento else "-"
                local = c.status.value if hasattr(c.status, "value") else str(c.status)

                if boleto is None:
                    situacao = "NAO LOCALIZADO"
                    nao_encontrados += 1
                    aviso = f"(ignorar: {erro})" if status_http not in (200,) else ""
                else:
                    situacao = normalizar_situacao(boleto)
                    retornado = str(boleto.get("nossoNumero", ""))
                    aviso = ""
                    if retornado and retornado != str(nn):
                        aviso = f"(ATENCAO: Sicoob devolveu nossoNumero {retornado})"

                terminal = c.status in STATUS_TERMINAIS
                divergente = terminal and situacao == "EM ABERTO"
                if divergente:
                    divergentes += 1

                if divergente:
                    flag = "DIVERGENTE"
                elif situacao == "NAO LOCALIZADO":
                    flag = "VERIFICAR"
                elif terminal:
                    flag = "ok"
                else:
                    flag = "-"
                print(
                    f"{c.id:>6} {local:<13} {str(nn):<10} {situacao:<16} "
                    f"{float(c.valor or 0):>10.2f} {vencto:<11} {cliente[:28]:<28} {flag} {aviso}"
                )
                linhas.append({
                    "conta_id": c.id,
                    "status_local": local,
                    "nosso_numero": str(nn),
                    "api_nosso_numero": c.api_nosso_numero or "",
                    "situacao_sicoob": situacao,
                    "cliente": cliente,
                    "valor": float(c.valor or 0),
                    "vencimento": vencto,
                    "motivo_baixa": c.motivo_baixa or "",
                    "atualizado_em": str(c.updated_at) if c.updated_at else "",
                    "divergente": divergente,
                    "observacao": erro or aviso,
                })

        print("-" * 132)
        print(f"Total analisado: {len(linhas)}")
        print(f"Divergentes (cancelado/excluido no sistema, EM ABERTO no Sicoob): {divergentes}")
        print(f"Nao localizados no Sicoob: {nao_encontrados}")

        if args.csv_path:
            with open(args.csv_path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()) if linhas else ["conta_id"], delimiter=";")
                w.writeheader()
                w.writerows(linhas)
            print(f"CSV salvo em: {args.csv_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

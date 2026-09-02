"""Corrige em massa o CEP e o código IBGE de clientes/fornecedores/empresa.

O que este script faz:
  1. Normaliza o CEP para a máscara "00000-000" (idempotente).
  2. Com --enriquecer-ibge, preenche o código IBGE do município quando estiver
     vazio:
       a) primeiro consulta o ViaCEP pelo CEP (preciso quando o CEP existe na base);
       b) se o ViaCEP não achar (CEP geral/não cadastrado), usa cidade+estado e
          cruza com a base de municípios do IBGE (servicodados.ibge.gov.br).

O IBGE é o que de fato evita a rejeição "CEP do cliente não é válido" na NFSe
do Ambiente Nacional SEFIN, pois o CEP precisa bater com o município informado.

Uso:
  python scripts/corrigir_ceps.py                      # simulação (não grava)
  python scripts/corrigir_ceps.py --apply              # só normaliza CEP
  python scripts/corrigir_ceps.py --apply --enriquecer-ibge   # CEP + IBGE (ViaCEP e IBGE)
"""
import os
import re
import sys
import logging
import unicodedata

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("corrigir")

# Permite passar a URL via --db "postgresql://..." (evita problemas de $env no shell)
_ARGS = sys.argv[1:]
if "--db" in _ARGS:
    _i = _ARGS.index("--db")
    if _i + 1 < len(_ARGS):
        DATABASE_URL = _ARGS[_i + 1]
    else:
        log.error("--db requer a URL do banco em seguida")
        sys.exit(1)
else:
    DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    log.error("DATABASE_URL não definida no ambiente/.env")
    sys.exit(1)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# Tabelas: (nome, col_cep, col_ibge, col_cidade, col_estado)
TABELAS = [
    ("clientes", "cep", "codigo_ibge", "cidade", "estado"),
    ("fornecedores", "cep", "codigo_ibge", "cidade", "estado"),
    ("empresa", "cep", "codigo_ibge", "cidade", "estado"),
    ("transportadoras", "cep", None, None, None),
]


def formatar_cep(cep: str) -> str:
    if not cep:
        return cep
    digitos = re.sub(r"\D", "", cep)
    if len(digitos) == 8:
        return f"{digitos[:5]}-{digitos[5:]}"
    return cep


def normalizar_txt(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.strip().upper())


def buscar_ibge_viacep(cep_limpo: str) -> str:
    try:
        import requests
        r = requests.get(
            f"https://viacep.com.br/ws/{cep_limpo}/json/",
            timeout=6,
            headers={"User-Agent": "ControleComercial/1.0"},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("erro"):
            return ""
        return (data.get("ibge") or "").strip()
    except Exception as e:
        log.warning("  ViaCEP falhou para %s: %s", cep_limpo, e)
        return ""


_MUNICIPIOS_CACHE = None


def _uf_de(mun):
    u = mun.get("uf")
    if isinstance(u, dict) and u.get("sigla"):
        return u["sigla"]

    def g(d, *keys):
        for k in keys:
            if not isinstance(d, dict):
                return {}
            d = d.get(k)
        return d if isinstance(d, dict) else {}

    mm = g(mun, "microrregiao", "mesorregiao", "UF")
    if mm.get("sigla"):
        return mm["sigla"]
    ri = g(mun, "regiao-imediata", "regiao-intermediaria", "UF")
    if ri.get("sigla"):
        return ri["sigla"]
    return ""


def carregar_municipios_ibge():
    global _MUNICIPIOS_CACHE
    if _MUNICIPIOS_CACHE is not None:
        return _MUNICIPIOS_CACHE
    mapa = {}
    try:
        import requests
        r = requests.get(
            "https://servicodados.ibge.gov.br/api/v1/localidades/municipios",
            timeout=120,
            headers={"User-Agent": "ControleComercial/1.0"},
        )
        r.raise_for_status()
        for m in r.json():
            uf = _uf_de(m)
            nome = m.get("nome") or ""
            ibge = str(m.get("id", "")).strip()
            if uf and nome and ibge:
                mapa[(str(uf).upper(), normalizar_txt(nome))] = ibge
        log.info("  Base IBGE carregada: %d municípios", len(mapa))
    except Exception as e:
        log.warning("  Falha ao carregar base IBGE: %s", e)
    _MUNICIPIOS_CACHE = mapa
    return mapa


def buscar_ibge_cidade(cidade: str, estado: str) -> str:
    if not (cidade and estado):
        return ""
    return carregar_municipios_ibge().get((estado.strip().upper(), normalizar_txt(cidade)), "")


def main():
    apply = "--apply" in sys.argv
    enriquecer = "--enriquecer-ibge" in sys.argv
    if not apply:
        log.info("=== MODO SIMULAÇÃO (dry-run). Nada será gravado. ===")
        log.info("=== Use --apply para gravar e --enriquecer-ibge para preencher IBGE. ===\n")

    total_cep = 0
    total_ibge = 0

    with engine.begin() as conn:
        for tabela, col_cep, col_ibge, col_cidade, col_estado in TABELAS:
            cols = f'SELECT id, "{col_cep}"'
            if col_ibge:
                cols += f', "{col_ibge}"'
            if col_cidade:
                cols += f', "{col_cidade}"'
            if col_estado:
                cols += f', "{col_estado}"'
            cols += f" FROM {tabela}"
            rows = conn.execute(text(cols)).fetchall()

            for row in rows:
                rid = row[0]
                cep_atual = row[1]
                idx = 2
                ibge_atual = row[idx] if col_ibge else None
                if col_ibge:
                    idx += 1
                cidade_atual = row[idx] if col_cidade else None
                if col_cidade:
                    idx += 1
                estado_atual = row[idx] if col_estado else None

                novo_cep = formatar_cep(cep_atual)
                cep_mudou = novo_cep != cep_atual and cep_atual not in (None, "")

                novo_ibge = ibge_atual or ""
                ibge_mudou = False
                if enriquecer and col_ibge and not (ibge_atual or "").strip():
                    digitos = re.sub(r"\D", "", cep_atual or "")
                    if len(digitos) == 8:
                        novo_ibge = buscar_ibge_viacep(digitos)
                    if not novo_ibge:
                        novo_ibge = buscar_ibge_cidade(cidade_atual, estado_atual)
                    ibge_mudou = bool(novo_ibge)

                if cep_mudou or ibge_mudou:
                    total_cep += 1 if cep_mudou else 0
                    total_ibge += 1 if ibge_mudou else 0
                    log.info(
                        f"[{tabela}] id={rid}: cep {cep_atual!r}->{novo_cep!r}"
                        + (f"; ibge {ibge_atual!r}->{novo_ibge!r}" if ibge_mudou else "")
                    )
                    if apply:
                        if cep_mudou and ibge_mudou:
                            conn.execute(
                                text(f'UPDATE {tabela} SET "{col_cep}"=:c, "{col_ibge}"=:i WHERE id=:id'),
                                {"c": novo_cep, "i": novo_ibge, "id": rid},
                            )
                        elif cep_mudou:
                            conn.execute(
                                text(f'UPDATE {tabela} SET "{col_cep}"=:c WHERE id=:id'),
                                {"c": novo_cep, "id": rid},
                            )
                        elif ibge_mudou:
                            conn.execute(
                                text(f'UPDATE {tabela} SET "{col_ibge}"=:i WHERE id=:id'),
                                {"i": novo_ibge, "id": rid},
                            )

    log.info("")
    log.info(f"CEPs normalizados: {total_cep}")
    log.info(f"IBGE preenchidos:  {total_ibge}")
    if apply:
        log.info("Alterações gravadas no banco.")
    else:
        log.info("Nenhuma alteração gravada (dry-run).")


if __name__ == "__main__":
    main()

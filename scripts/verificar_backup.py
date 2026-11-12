#!/usr/bin/env python3
"""Verifica se um backup está completo e se restaura 100% em PostgreSQL.

Uso típico (depois de baixar o backup do Railway):

    python scripts/verificar_backup.py --arquivo backups/backup_20261112.json

O script:
  1. Carrega (ou gera) o backup JSON;
  2. Confere cobertura de tabelas/colunas contra o banco configurado em
     DATABASE_URL (não grava nada no banco de origem);
  3. Compara a contagem de linhas do backup com a do banco de origem;
  4. Cria um banco PostgreSQL descartável (verif_backup_<timestamp>), aplica o
     schema do banco de origem (pg_dump --schema-only) e restaura o JSON nele;
  5. Compara linha a linha (checksum por tabela) e confere as sequences.

Nada é alterado no banco de origem. O banco descartável é removido no final,
exceto com --manter-banco.

Exemplos:
    python scripts/verificar_backup.py                       # gera e verifica
    python scripts/verificar_backup.py --arquivo backup.json # verifica arquivo
    python scripts/verificar_backup.py --sem-restaurar       # só valida schema
    python scripts/verificar_backup.py --arquivo backup.json --restaurar-local
                                                             # valida e restaura
                                                             # no banco configurado
    python scripts/verificar_backup.py --pg-bin "C:\\Program Files\\PostgreSQL\\18\\bin"
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from sqlalchemy import create_engine, text, inspect as sa_inspect  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from services.backup import (  # noqa: E402
    generate_backup,
    restore_backup,
    validate_backup,
)


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _canon(val):
    """Normaliza um valor para comparação textual estável."""
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, float):
        return repr(val)
    return val


def _assinatura(linha: dict) -> str:
    return json.dumps(
        {k: _canon(v) for k, v in sorted(linha.items())},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _ler_tabela(engine_, table: str):
    with engine_.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM {table}"))
        cols = list(result.keys())
        return [{c: r._mapping[c] for c in cols} for r in result]


def _encontrar_bin(nome: str, pg_bin=None):
    candidatos = []
    if pg_bin:
        candidatos.append(Path(pg_bin) / f"{nome}.exe")
        candidatos.append(Path(pg_bin) / nome)
    achado = shutil.which(nome)
    if achado:
        candidatos.append(Path(achado))
    if os.name == "nt":
        base = Path(r"C:\Program Files\PostgreSQL")
        if base.exists():
            for versao in sorted(base.iterdir(), reverse=True):
                candidatos.append(versao / "bin" / f"{nome}.exe")
    for c in candidatos:
        if c and Path(c).exists():
            return str(c)
    return None


def _url_admin(url):
    """URL do banco de manutenção (postgres) para CREATE/DROP DATABASE."""
    u = make_url(url)
    return u.set(database="postgres")


def _criar_banco_descartavel(url: str, nome: str, manter: bool):
    admin = create_engine(_url_admin(url), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        # Encerra conexões remanescentes de execuções anteriores.
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": nome},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{nome}"'))
        conn.execute(text(f'CREATE DATABASE "{nome}"'))
    return admin


def _aplicar_schema(url_origem: str, url_destino: str, pg_bin=None) -> str:
    """Cria o schema no banco descartável. Retorna o método usado."""
    pg_dump = _encontrar_bin("pg_dump", pg_bin)
    psql = _encontrar_bin("psql", pg_bin)
    if pg_dump and psql:
        dump = subprocess.run(
            [
                pg_dump,
                "--schema-only",
                "--no-owner",
                "--no-privileges",
                "--no-comments",
                f"--dbname={url_origem}",
            ],
            capture_output=True,
            text=True,
        )
        if dump.returncode != 0:
            raise RuntimeError(f"pg_dump falhou: {dump.stderr.strip()[:500]}")
        aplicar = subprocess.run(
            [psql, f"--dbname={url_destino}", "-v", "ON_ERROR_STOP=1", "-q"],
            input=dump.stdout,
            capture_output=True,
            text=True,
        )
        if aplicar.returncode != 0:
            raise RuntimeError(f"psql falhou: {aplicar.stderr.strip()[:500]}")
        return "pg_dump --schema-only"

    # Alternativa portável quando os binários do PostgreSQL não existem:
    # cria o schema a partir dos models SQLAlchemy (tabelas legadas ficam fora).
    import models  # noqa: F401  (registra os models no Base)
    from database import Base

    engine_destino = create_engine(url_destino)
    Base.metadata.create_all(bind=engine_destino)
    engine_destino.dispose()
    return "models SQLAlchemy (pg_dump não encontrado)"


def _comparar_tabelas(backup: dict, engine_destino) -> list:
    """Compara contagem e conteúdo de cada tabela do backup com o destino."""
    problemas = []
    tabelas = backup.get("tables") or {}
    for table, linhas in tabelas.items():
        try:
            restauradas = _ler_tabela(engine_destino, table)
        except Exception as e:
            problemas.append(f"{table}: não foi possível ler no destino ({e})")
            continue
        if len(restauradas) != len(linhas):
            problemas.append(
                f"{table}: backup tem {len(linhas)} linha(s), destino tem "
                f"{len(restauradas)}"
            )
            continue
        origem = Counter(_assinatura(linha) for linha in linhas)
        destino = Counter(_assinatura(linha) for linha in restauradas)
        faltando = origem - destino
        sobrando = destino - origem
        if faltando or sobrando:
            exemplo = next(iter(faltando or sobrando))
            problemas.append(
                f"{table}: {sum(faltando.values())} linha(s) divergente(s) "
                f"(ex.: {exemplo[:180]})"
            )
    return problemas


def _conferir_sequences(engine_destino) -> list:
    """Garante que a sequence de cada tabela está à frente do maior id."""
    problemas = []
    insp = sa_inspect(engine_destino)
    for table in insp.get_table_names(schema="public"):
        cols = {c["name"] for c in insp.get_columns(table, schema="public")}
        if "id" not in cols:
            continue
        with engine_destino.connect() as conn:
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}
            ).scalar()
            if not seq:
                continue
            maior = conn.execute(text(f"SELECT MAX(id) FROM {table}")).scalar()
            if maior is None:
                continue
            # pg_get_serial_sequence devolve "schema.nome"; validar antes de
            # interpolar (não é possível parametrizar nome de relação).
            if not re.match(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$", seq):
                problemas.append(f"{table}: nome de sequence inesperado: {seq}")
                continue
            if conn.execute(text("SELECT to_regclass(:s)"), {"s": seq}).scalar() is None:
                continue
            ultimo = conn.execute(text(f"SELECT last_value FROM {seq}")).scalar()
            if ultimo is None or int(ultimo) <= int(maior):
                problemas.append(
                    f"{table}: sequence {seq} em {ultimo}, mas MAX(id)={maior} "
                    "(próximo insert pode colidir)"
                )
    return problemas


def _imprimir_relatorio(rel: dict):
    print("\n== Cobertura do backup ==")
    print(f"  versão do arquivo : {rel['version']}")
    print(f"  criado em         : {rel['created_at']}")
    print(f"  schema_version    : backup={rel['schema_version_backup']} "
          f"banco={rel['schema_version_banco']}")
    print(f"  tabelas           : backup={rel['tabelas_no_backup']} "
          f"banco={rel['tabelas_no_banco']}")
    print(f"  linhas no backup  : {rel['linhas_total']}")
    if rel["tabelas_ausentes_no_banco"]:
        print(f"  tabelas do backup ausentes no banco: "
              f"{', '.join(rel['tabelas_ausentes_no_banco'])}")
    if rel["tabelas_no_banco_fora_do_backup"]:
        print(f"  tabelas do banco fora do backup: "
              f"{', '.join(rel['tabelas_no_banco_fora_do_backup'])}")
    for t, cols in rel["colunas_ausentes_no_banco"].items():
        print(f"  {t}: colunas do backup inexistentes no banco: {', '.join(cols)}")
    for aviso in rel["avisos"]:
        print(f"  AVISO: {aviso}")


# --------------------------------------------------------------------------- #
# Principal
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arquivo", help="backup .json a verificar")
    parser.add_argument(
        "--saida",
        help="onde salvar o backup gerado (padrão: backups/verificacao_<ts>.json)",
    )
    parser.add_argument(
        "--sem-restaurar",
        action="store_true",
        help="não cria banco descartável nem restaura (só valida o schema)",
    )
    parser.add_argument(
        "--manter-banco",
        action="store_true",
        help="não apaga o banco descartável ao final",
    )
    parser.add_argument(
        "--restaurar-local",
        action="store_true",
        help="após a verificação, restaura o backup no banco configurado "
        "(modo limpar — APAGA os dados atuais)",
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        help="não pede confirmação para --restaurar-local",
    )
    parser.add_argument("--pg-bin", help="diretório dos binários do PostgreSQL")
    args = parser.parse_args()

    # Importa depois do sys.path para carregar o .env do projeto.
    from database import engine

    if args.arquivo:
        caminho = Path(args.arquivo)
        if not caminho.exists():
            print(f"ERRO: arquivo não encontrado: {caminho}")
            return 1
        print(f"Lendo backup: {caminho}")
        with caminho.open(encoding="utf-8") as fh:
            backup = json.load(fh)
    else:
        print(f"Gerando backup do banco configurado ({engine.dialect.name})...")
        backup = generate_backup()
        destino = (
            Path(args.saida)
            if args.saida
            else RAIZ / "backups" / f"verificacao_{datetime.now():%Y%m%d_%H%M%S}.json"
        )
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(backup, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Backup salvo em: {destino} "
              f"({destino.stat().st_size / 1024 / 1024:.2f} MB)")

    rel = validate_backup(backup)
    _imprimir_relatorio(rel)

    falhas = []
    if not rel["ok"]:
        falhas.append("cobertura do backup não confere com o banco destino")

    if args.sem_restaurar:
        print("\n(--sem-restaurar: banco descartável não foi testado)")
        return 1 if falhas else 0

    url_origem = engine.url.render_as_string(hide_password=False)
    if engine.dialect.name != "postgresql":
        print(
            "\nAVISO: o banco configurado não é PostgreSQL; "
            "o teste de restauração descartável foi ignorado."
        )
        return 1 if falhas else 0

    nome = f"verif_backup_{datetime.now():%Y%m%d_%H%M%S}"
    url_destino = make_url(url_origem).set(database=nome).render_as_string(
        hide_password=False
    )
    admin = None
    try:
        print(f"\n== Restauração em banco descartável ({nome}) ==")
        admin = _criar_banco_descartavel(url_origem, nome, args.manter_banco)
        metodo = _aplicar_schema(url_origem, url_destino, args.pg_bin)
        print(f"  schema aplicado via: {metodo}")

        engine_destino = create_engine(url_destino)
        try:
            stats = restore_backup(backup, modo="limpar", engine_=engine_destino)
            print(f"  restaurado: {stats['imported']} linha(s), "
                  f"{stats['erros']} erro(s)")
            for detalhe in stats["detalhes"][:20]:
                print(f"  - {detalhe}")

            if stats["erros"]:
                falhas.append(f"{stats['erros']} erro(s) de importação no restore")

            print("\n== Comparação linha a linha ==")
            problemas = _comparar_tabelas(backup, engine_destino)
            for p in problemas[:30]:
                print(f"  DIVERGÊNCIA: {p}")
            if problemas:
                falhas.append(f"{len(problemas)} tabela(s) divergente(s) após restore")

            print("\n== Sequences ==")
            seq_problemas = _conferir_sequences(engine_destino)
            for p in seq_problemas[:20]:
                print(f"  DIVERGÊNCIA: {p}")
            if seq_problemas:
                falhas.append("sequences desalinhadas após o restore")
        finally:
            engine_destino.dispose()
    except Exception as e:
        falhas.append(f"teste de restauração falhou: {e}")
    finally:
        if admin is not None:
            if args.manter_banco:
                print(f"\nBanco descartável mantido: {nome}")
            else:
                with admin.connect() as conn:
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{nome}"'))
                print(f"\nBanco descartável removido: {nome}")
            admin.dispose()

    if args.restaurar_local and not falhas:
        print("\n== Restauração no banco configurado (modo limpar) ==")
        if not args.sim:
            resposta = input(
                f"ISSO APAGA OS DADOS ATUAIS do banco '{engine.url.database}'. "
                "Digite SIM para confirmar: "
            )
            if resposta.strip().upper() != "SIM":
                print("Restauração local cancelada.")
                return 1
        stats = restore_backup(backup, modo="limpar")
        print(f"  restaurado: {stats['imported']} linha(s), {stats['erros']} erro(s)")
        for detalhe in stats["detalhes"][:20]:
            print(f"  - {detalhe}")
        if stats["erros"]:
            falhas.append(f"{stats['erros']} erro(s) ao restaurar no banco local")
        problemas = _comparar_tabelas(backup, engine)
        for p in problemas[:30]:
            print(f"  DIVERGÊNCIA: {p}")
        if problemas:
            falhas.append(f"{len(problemas)} tabela(s) divergente(s) no banco local")

    print("\n" + "=" * 60)
    if falhas:
        print("RESULTADO: FALHOU")
        for f in falhas:
            print(f"  - {f}")
        return 1
    print("RESULTADO: OK — backup completo e restauração fiel")
    return 0


if __name__ == "__main__":
    sys.exit(main())

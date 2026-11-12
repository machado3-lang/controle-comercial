import json
import re
import logging
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import text, inspect as sa_inspect, Numeric
from database import engine

logger = logging.getLogger(__name__)


TABLES_IN_ORDER = [
    "marcas_produto",
    "categorias_produto",
    "cfop_natureza",
    "usuarios",
    "clientes",
    "fornecedores",
    "tipos_documento",
    "condicoes_pagamento",
    "plano_contas",
    "empresa",
    "transportadoras",
    "contas_pagar",
    "contas_receber",
    "produtos",
    "produto_variacoes",
    "produto_composicao",
    # Tabelas legadas (nao tem FK e nao estao em models.py, mas guardam dados
    # reais de servicos/insumos em algumas instalacoes).
    "servicos",
    "servicos_insumos",
    "assinaturas",
    "assinaturas_historico",
    "pedidos_venda",
    "pedidos_venda_itens",
    "ordens_servico",
    "pedidos_consolidados",
    "pedidos_consolidados_itens",
    "pedidos_consolidados_itens_origem",
    "nfe",
    "nfe_itens",
    "nfe_cartas_correcao",
    "nfe_distribuidas",
    "nfse",
    "nfse_itens",
    "nfse_recebida",
    "relogios_ponto",
    "movimentacoes_estoque",
    "os_pecas",
    "audit_log",
    "historico_cadastro",
]

ALLOWED_TABLES = set(TABLES_IN_ORDER)

# Tabelas de controle de schema/migracao: nao entram no backup de dados.
# O schema_version e gravado nos metadados do backup para conferencia.
IGNORED_TABLES = {"alembic_version"}

SKIP_TABLES_ON_RESTORE = set()

# Colunas que armazenam enums Python e como o valor é persistido no banco.
# Chave: (tabela, coluna). "forma" indica o que o banco guarda:
#   "name"  -> o NOME do membro (native_enum padrao: "PENDENTE", "CONSOLIDADO")
#   "value" -> o VALOR do membro (values_callable e/ou String livre: "aberta", "avista")
# A fonte da verdade sao os enums definidos em models.py (case-sensitive).
_ENUM_COLUMNS = {
    ("pedidos_venda", "status"): ("StatusPedido", "name"),
    ("ordens_servico", "status"): ("StatusOS", "value"),
    ("contas_pagar", "status"): ("StatusConta", "name"),
    ("contas_receber", "status"): ("StatusConta", "name"),
    ("pedidos_venda", "forma_pagamento"): ("FormaPagamento", "value"),
    ("contas_pagar", "forma_pagamento"): ("FormaPagamento", "value"),
    ("contas_receber", "forma_pagamento"): ("FormaPagamento", "value"),
}


def _build_enum_lookup():
    """Constrói {(tabela, coluna): {variante_lower: valor_canônico}} a partir
    dos enums definidos em models.py."""
    from models import (
        StatusPedido, StatusOS, StatusConta, FormaPagamento,
    )
    _enum_classes = {
        "StatusPedido": StatusPedido,
        "StatusOS": StatusOS,
        "StatusConta": StatusConta,
        "FormaPagamento": FormaPagamento,
    }
    lookup = {}
    for (table, col), (enum_name, form) in _ENUM_COLUMNS.items():
        enum_cls = _enum_classes.get(enum_name)
        if not enum_cls:
            continue
        norm = {}
        for member in enum_cls:
            canon = member.name if form == "name" else member.value
            variantes = {
                member.name.lower(),
                member.value.lower(),
                member.value.lower().replace("_", ""),
                member.value.lower().replace("_", " "),
                canon.lower(),
            }
            for v in variantes:
                norm[v] = canon
        lookup[(table, col)] = norm
    return lookup


_ENUM_LOOKUP = _build_enum_lookup()


def _pg_drop_fk_constraints(conn):
    """No PostgreSQL, `DISABLE TRIGGER ALL` não contorna FKs sem superusuário e
    o schema tem FKs cíclicas e também FKs vindas de tabelas não listadas no
    backup (ex.: audit_log -> usuarios). A solução portável é REMOVER TODAS as
    FKs do banco antes do restore e recriá-las ao final. Retorna (drop_sql, create_sql)."""
    from sqlalchemy import text as _text
    rows = conn.execute(_text("""
        SELECT
            tc.constraint_name,
            tc.table_name,
            kcu.column_name,
            ccu.table_name AS referenced_table_name,
            ccu.column_name AS referenced_column_name,
            rc.delete_rule,
            rc.update_rule,
            kcu.ordinal_position
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
            AND tc.table_name = kcu.table_name
        JOIN information_schema.constraint_column_usage ccu
            ON tc.constraint_name = ccu.constraint_name
            AND tc.table_schema = ccu.table_schema
        JOIN information_schema.referential_constraints rc
            ON tc.constraint_name = rc.constraint_name
            AND tc.table_schema = rc.constraint_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_schema = 'public'
        ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position
    """)).fetchall()

    cons = {}
    for r in rows:
        cname, table, col, ref_table, ref_col, del_rule, upd_rule, pos = r
        key = (table, cname)
        entry = cons.setdefault(key, {
            "ref": ref_table, "del": del_rule, "upd": upd_rule,
            "cols": [], "refcols": [],
        })
        entry["cols"].append((pos, col))
        entry["refcols"].append((pos, ref_col))

    defs = []
    for (table, cname), e in cons.items():
        cols = ", ".join(c for _, c in sorted(e["cols"]))
        refcols = ", ".join(c for _, c in sorted(e["refcols"]))
        extra = ""
        if e["del"] and e["del"] != "NO ACTION":
            extra += " ON DELETE " + e["del"]
        if e["upd"] and e["upd"] != "NO ACTION":
            extra += " ON UPDATE " + e["upd"]
        drop_sql = f"ALTER TABLE {table} DROP CONSTRAINT {cname}"
        create_sql = (
            f"ALTER TABLE {table} ADD CONSTRAINT {cname} "
            f"FOREIGN KEY ({cols}) REFERENCES {e['ref']} ({refcols}){extra}"
        )
        defs.append((drop_sql, create_sql))
    return defs


def _recriar_fks(db_engine, fk_defs):
    """Recria as FKs removidas antes do restore (idempotente por execução)."""
    if not fk_defs:
        return
    try:
        with db_engine.connect() as conn:
            trans = conn.begin()
            for _, create_sql in fk_defs:
                conn.execute(text(create_sql))
            trans.commit()
        logger.info("[RESTORE] %d FKs recriadas", len(fk_defs))
    except Exception as e:
        logger.error("Não foi possível recriar as FKs após o restore: %s", e)


def _normalize_enum_value(table_name: str, col_name: str, value):
    """Normaliza um valor de enum para a forma canônica persistida no banco
    (nome ou valor, conforme definido em _ENUM_COLUMNS)."""
    if not isinstance(value, str):
        return value
    k = (table_name, col_name)
    if k not in _ENUM_LOOKUP:
        return value
    key = value.strip().lower()
    if key in _ENUM_LOOKUP[k]:
        return _ENUM_LOOKUP[k][key]
    return value.strip()


_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_table(table_name: str) -> str:
    """Valida se o nome da tabela está na whitelist para prevenir SQL injection."""
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Tabela não permitida: {table_name}")
    return table_name


def _validate_identifier(name) -> str:
    """Valida um identificador SQL (tabela/coluna) vindo de um backup.

    Diferente de `_validate_table`, aceita tabelas que não estão na lista
    histórica (ex.: criadas depois por uma migração) desde que o nome seja
    sintaticamente válido. A existência real é conferida no banco destino.
    """
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Identificador SQL inválido: {name!r}")
    return name


def _serialize(val, col_type=None):
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        # String preserva a precisão exata (float perderia centavos em somas).
        return str(val)
    if isinstance(col_type, Numeric) and isinstance(val, (int, float)) and not isinstance(val, bool):
        # SQLite devolve float/int mesmo em colunas NUMERIC; converter via repr
        # mantém o valor decimal legível e restaurável sem perda binária.
        return str(Decimal(repr(val)))
    return val


def _resolve_engine(engine_=None):
    """Permite operar em outro banco (ex.: banco descartável de verificação)."""
    return engine_ if engine_ is not None else engine


def _schema_name(engine_=None):
    """Schema padrão do banco (None para SQLite)."""
    return "public" if _resolve_engine(engine_).dialect.name == "postgresql" else None


def list_database_tables(engine_=None) -> list:
    """Lista as tabelas de dados do banco (exclui tabelas de controle)."""
    db_engine = _resolve_engine(engine_)
    with db_engine.connect() as conn:
        insp = sa_inspect(conn)
        names = insp.get_table_names(schema=_schema_name(db_engine))
    return sorted(n for n in names if n not in IGNORED_TABLES)


def _schema_version(conn):
    """Lê a revisão do Alembic aplicada no banco (None se não existir)."""
    try:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        return row[0] if row else None
    except Exception:
        return None


def generate_backup(engine_=None):
    """Gera um backup lógico de TODAS as tabelas de dados do banco.

    A ordem é a histórica (`TABLES_IN_ORDER`) quando a tabela existe; tabelas
    novas/desconhecidas são incluídas automaticamente no final (com aviso) para
    que nenhum dado fique de fora.
    """
    db_engine = _resolve_engine(engine_)
    with db_engine.connect() as conn:
        insp = sa_inspect(conn)
        existing = set(insp.get_table_names(schema=_schema_name(db_engine)))
        ordered = [t for t in TABLES_IN_ORDER if t in existing]
        extras = sorted(
            t for t in existing
            if t not in ALLOWED_TABLES and t not in IGNORED_TABLES
        )
        ausentes = [t for t in TABLES_IN_ORDER if t not in existing]

        warnings = []
        if extras:
            warnings.append(
                "Tabelas fora da lista conhecida incluídas automaticamente: "
                + ", ".join(extras)
            )
        if ausentes:
            warnings.append(
                "Tabelas esperadas que não existem neste banco: "
                + ", ".join(ausentes)
            )

        tables = {}
        for table in ordered + extras:
            _validate_identifier(table)
            tabela_cols = insp.get_columns(table, schema=_schema_name(db_engine))
            tipos = {c["name"]: c["type"] for c in tabela_cols}
            has_id = "id" in tipos
            sql = f"SELECT * FROM {table}" + (" ORDER BY id" if has_id else "")
            result = conn.execute(text(sql))
            columns = list(result.keys())
            tables[table] = [
                {col: _serialize(r._mapping[col], tipos.get(col)) for col in columns}
                for r in result
            ]

        return {
            "version": 2,
            "created_at": datetime.now().isoformat(),
            "schema_version": _schema_version(conn),
            "backend": db_engine.dialect.name,
            "table_order": ordered + extras,
            "warnings": warnings,
            "tables": tables,
        }


def validate_backup(backup_dict: dict, engine_=None) -> dict:
    """Confere a cobertura de um backup contra o banco atual (não grava nada).

    Retorna um relatório com tabelas/colunas ausentes dos dois lados, contagem
    de linhas e se o schema_version bate com o banco destino.
    """
    raw = backup_dict.get("tables")
    if isinstance(raw, dict):
        tables_data = raw
    else:
        # Formato legado: o próprio dict raiz é {tabela: [rows]}
        tables_data = {
            k: v for k, v in backup_dict.items() if isinstance(v, list)
        }

    table_order = backup_dict.get("table_order")
    if not isinstance(table_order, list) or not table_order:
        table_order = [t for t in TABLES_IN_ORDER if t in tables_data]
        table_order += sorted(t for t in tables_data if t not in table_order)
    table_order = [t for t in table_order if t in tables_data]

    db_engine = _resolve_engine(engine_)
    with db_engine.connect() as conn:
        insp = sa_inspect(conn)
        existing = set(insp.get_table_names(schema=_schema_name(db_engine)))
        db_schema_version = _schema_version(conn)
        colunas_ausentes = {}
        colunas_sobrando = {}
        for table in table_order:
            if table not in existing:
                continue
            rows = tables_data.get(table) or []
            cols_no_backup = set()
            for row in rows:
                if isinstance(row, dict):
                    cols_no_backup.update(row.keys())
            if not cols_no_backup:
                continue
            cols_db = {
                c["name"]
                for c in insp.get_columns(table, schema=_schema_name(db_engine))
            }
            faltando = sorted(cols_no_backup - cols_db)
            sobrando = sorted(cols_db - cols_no_backup)
            if faltando:
                colunas_ausentes[table] = faltando
            if sobrando:
                colunas_sobrando[table] = sobrando

    ausentes = [t for t in table_order if t not in existing]
    fora = sorted(
        t for t in existing if t not in tables_data and t not in IGNORED_TABLES
    )
    schema_backup = backup_dict.get("schema_version")
    avisos = list(backup_dict.get("warnings") or [])
    if schema_backup and db_schema_version and schema_backup != db_schema_version:
        avisos.append(
            f"schema_version do backup ({schema_backup}) difere do banco atual "
            f"({db_schema_version}); rode as migrações antes do restore"
        )
    for t in ausentes:
        avisos.append(
            f"Tabela do backup inexistente no banco destino (será ignorada): {t}"
        )
    for t in fora:
        avisos.append(
            f"Tabela do banco fora do backup (ficará sem dados restaurados): {t}"
        )
    for t, cols in colunas_ausentes.items():
        avisos.append(
            f"{t}: coluna(s) do backup inexistente(s) no banco: {', '.join(cols)}"
        )

    schema_difere = bool(
        schema_backup and db_schema_version and schema_backup != db_schema_version
    )
    return {
        "ok": not ausentes and not fora and not colunas_ausentes and not schema_difere,
        "version": backup_dict.get("version"),
        "created_at": backup_dict.get("created_at"),
        "schema_version_backup": schema_backup,
        "schema_version_banco": db_schema_version,
        "tabelas_no_backup": len(tables_data),
        "tabelas_no_banco": len(existing - IGNORED_TABLES),
        "linhas_total": sum(
            len(v) for v in tables_data.values() if isinstance(v, list)
        ),
        "tabelas_ausentes_no_banco": ausentes,
        "tabelas_no_banco_fora_do_backup": fora,
        "colunas_ausentes_no_banco": colunas_ausentes,
        "colunas_no_banco_ausentes_no_backup": colunas_sobrando,
        "avisos": avisos,
    }


def _get_pg_columns(conn, table_name):
    result = conn.execute(text(
        "SELECT column_name, data_type, udt_name "
        "FROM information_schema.columns "
        "WHERE table_name = :t ORDER BY ordinal_position"
    ), {"t": table_name})
    return {row[0]: {"data_type": row[1], "udt_name": row[2]} for row in result}


def restore_backup(backup_dict: dict, modo: str = "sobrepor", engine_=None) -> dict:
    if modo not in ("sobrepor", "limpar"):
        raise ValueError("modo deve ser 'sobrepor' ou 'limpar'")
    logger.info("[RESTORE] Iniciando restore (modo=%s)", modo)
    raw = backup_dict.get("tables")
    if isinstance(raw, dict):
        tables_data = raw
    else:
        # Formato legado: o próprio dict raiz é {tabela: [rows]}
        tables_data = {
            k: v for k, v in backup_dict.items() if isinstance(v, list)
        }

    # Ordem: a gravada no backup (inclui tabelas novas) ou, para backups
    # antigos, a lista histórica seguida das tabelas extras encontradas.
    table_order = backup_dict.get("table_order")
    if not isinstance(table_order, list) or not table_order:
        table_order = [t for t in TABLES_IN_ORDER if t in tables_data]
        table_order += sorted(t for t in tables_data if t not in table_order)
    # Só restaura o que realmente veio no backup; evita limpar tabela vazia
    # por engano caso o metadado table_order esteja inconsistente.
    table_order = [t for t in table_order if t in tables_data]

    db_engine = _resolve_engine(engine_)
    is_pg = db_engine.dialect.name == "postgresql"
    schema_version_backup = backup_dict.get("schema_version")

    total_imported = 0
    total_errors = 0
    detalhes = []
    tabelas = {}

    with db_engine.connect() as conn:
        insp = sa_inspect(conn)
        existing = set(insp.get_table_names(schema=_schema_name(db_engine)))
        db_columns = {
            t: {c["name"] for c in insp.get_columns(t, schema=_schema_name(db_engine))}
            for t in existing
        }
        db_schema_version = _schema_version(conn)

    if schema_version_backup and db_schema_version and schema_version_backup != db_schema_version:
        msg = (
            f"schema_version do backup ({schema_version_backup}) difere do banco "
            f"({db_schema_version})"
        )
        logger.warning("[RESTORE] %s", msg)
        detalhes.append(
            msg + " — rode as migrações antes do restore para fidelidade total."
        )

    for table_name in table_order:
        _validate_identifier(table_name)
        backup_rows = tables_data.get(table_name) or []
        tabelas[table_name] = {
            "backup": len(backup_rows),
            "importado": 0,
            "erros": 0,
            "ignorado": 0,
            "nulos_ignorados": 0,
            "colunas_descartadas": set(),
        }
        if table_name not in existing:
            tabelas[table_name]["ignorado"] = len(backup_rows)
            detalhes.append(
                f"{table_name}: tabela não existe no banco destino — "
                f"{len(backup_rows)} linha(s) ignorada(s)"
            )

    fora_do_backup = sorted(
        t for t in existing if t not in tables_data and t not in IGNORED_TABLES
    )
    if fora_do_backup:
        detalhes.append(
            "Tabelas do banco fora do backup (não serão alteradas): "
            + ", ".join(fora_do_backup)
        )

    # Modo "limpar": remove os registros atuais das tabelas presentes no backup
    # antes de reinserir, garantindo um restore fiel ao ponto do backup.
    # Em PostgreSQL, como `DISABLE TRIGGER ALL` não contorna FKs sem superusuário
    # e o schema tem FKs cíclicas (ex.: pedidos_venda <-> assinaturas), removemos
    # todas as FKs das tabelas antes e as recriamos ao final. Em SQLite o FK não
    # é enforcement por padrão, então nenhuma ação extra é necessária.
    fk_defs = []
    fk_disabled = False
    if is_pg:
        with db_engine.connect() as conn:
            trans = conn.begin()
            fk_defs = _pg_drop_fk_constraints(conn)
            for drop_sql, _ in fk_defs:
                conn.execute(text(drop_sql))
            trans.commit()
        fk_disabled = True
        logger.info(
            "[RESTORE] %d FKs removidas (PostgreSQL, modo=%s)", len(fk_defs), modo
        )

    if modo == "limpar":
        with db_engine.connect() as conn:
            trans = conn.begin()
            try:
                for table_name in table_order:
                    if table_name not in existing:
                        continue
                    _validate_identifier(table_name)
                    conn.execute(text(f"DELETE FROM {table_name}"))
                trans.commit()
            except Exception as e:
                trans.rollback()
                _recriar_fks(db_engine, fk_defs)
                raise RuntimeError(
                    f"Falha ao limpar tabelas antes do restore: {e}"
                ) from e

    for table_name in table_order:
        _validate_identifier(table_name)
        if table_name not in existing:
            continue
        if table_name in SKIP_TABLES_ON_RESTORE:
            tabelas[table_name]["ignorado"] = tabelas[table_name]["backup"]
            continue

        rows = tables_data.get(table_name) or []
        if not rows:
            continue

        with db_engine.connect() as conn:
            trans = conn.begin()
            try:
                columns_info = _get_pg_columns(conn, table_name) if is_pg else {}
                db_cols = db_columns.get(table_name, set())

                for i, row_data in enumerate(rows):
                    try:
                        if is_pg:
                            conn.execute(text(f"SAVEPOINT sp_{i}"))

                        col_names = []
                        col_placeholders = []
                        col_values = {}

                        for k, v in row_data.items():
                            if db_cols and k not in db_cols:
                                tabelas[table_name]["colunas_descartadas"].add(k)
                                continue
                            if not _IDENTIFIER_RE.match(str(k)):
                                continue
                            if v is None:
                                if modo == "limpar" and k != "id":
                                    # Modo limpar: a tabela foi zerada antes, então
                                    # gravar NULL explicitamente para ser fiel ao backup.
                                    col_names.append(k)
                                    col_placeholders.append(f":{k}")
                                    col_values[k] = None
                                else:
                                    # sobrepor: não sobrescreve com NULL (merge) e contabiliza.
                                    tabelas[table_name]["nulos_ignorados"] += 1
                                continue

                            col_type = columns_info.get(k, {})
                            if is_pg and col_type.get("udt_name") in (
                                "statusconta", "statusos", "statuspedido", "formapagamento"
                            ):
                                if isinstance(v, str):
                                    v = _normalize_enum_value(table_name, k, v)
                                col_names.append(k)
                                col_placeholders.append(f"CAST(:{k} AS {col_type['udt_name']})")
                            elif (table_name, k) in _ENUM_LOOKUP and isinstance(v, str):
                                v = _normalize_enum_value(table_name, k, v)
                                col_names.append(k)
                                col_placeholders.append(f":{k}")
                            else:
                                col_names.append(k)
                                col_placeholders.append(f":{k}")
                            col_values[k] = v

                        if not col_names:
                            tabelas[table_name]["ignorado"] += 1
                            continue

                        names = ", ".join(col_names)
                        placeholders = ", ".join(col_placeholders)

                        if is_pg:
                            upsert_cols = [c for c in col_names if c != "id"]
                            if upsert_cols:
                                update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in upsert_cols)
                                stmt = (
                                    f"INSERT INTO {table_name} ({names}) "
                                    f"VALUES ({placeholders}) "
                                    f"ON CONFLICT (id) DO UPDATE SET {update_set}"
                                )
                            else:
                                stmt = f"INSERT INTO {table_name} ({names}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING"
                            conn.execute(text(stmt), col_values)
                        else:
                            stmt = f"INSERT OR REPLACE INTO {table_name} ({names}) VALUES ({placeholders})"
                            conn.execute(text(stmt), col_values)

                        tabelas[table_name]["importado"] += 1

                    except Exception as e:
                        if is_pg:
                            conn.execute(text(f"ROLLBACK TO SAVEPOINT sp_{i}"))
                        is_dup = "UniqueViolation" in type(e).__name__ or "duplicate key" in str(e).lower()
                        if is_dup:
                            tabelas[table_name]["ignorado"] += 1
                            detalhes.append(f"{table_name}:{row_data.get('id', '?')} ignorado (já existente no banco)")
                        else:
                            tabelas[table_name]["erros"] += 1
                            total_errors += 1
                            detalhes.append(f"{table_name}:{row_data.get('id', '?')} erro: {str(e)[:200]}")

                trans.commit()
                total_imported += tabelas[table_name]["importado"]
                logger.info("[RESTORE] tabela %s: %d/%d importados, %d erros",
                             table_name, tabelas[table_name]["importado"],
                             tabelas[table_name]["backup"], tabelas[table_name]["erros"])

                if is_pg and tabelas[table_name]["importado"] > 0:
                    try:
                        with db_engine.connect() as conn_seq:
                            conn_seq.execute(text(
                                f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                                f"COALESCE((SELECT MAX(id)+1 FROM {table_name}), 1), false)"
                            ))
                            conn_seq.commit()
                    except Exception:
                        pass

            except Exception as e:
                trans.rollback()
                tabelas[table_name]["erros"] = tabelas[table_name]["backup"]
                tabelas[table_name]["importado"] = 0
                total_errors += 1
                detalhes.append(f"{table_name}: TABELA NAO IMPORTADA - erro: {str(e)[:300]}")

    if fk_disabled:
        _recriar_fks(db_engine, fk_defs)

    for tn, t in tabelas.items():
        t["nao_processado"] = t["backup"] - t["importado"] - t["erros"] - t["ignorado"]
        descartadas = sorted(t.pop("colunas_descartadas", set()))
        if descartadas:
            detalhes.append(
                f"{tn}: {len(descartadas)} coluna(s) do backup AUSENTE(S) no banco destino "
                f"(descartadas): {', '.join(descartadas)}"
            )
        if t["nulos_ignorados"]:
            detalhes.append(
                f"{tn}: {t['nulos_ignorados']} campo(s) NULL ignorado(s) "
                f"(modo '{modo}': não sobrescreve com NULL)"
            )

    logger.info("[RESTORE] concluído: %d importados, %d erros", total_imported, total_errors)
    return {
        "imported": total_imported,
        "erros": total_errors,
        "detalhes": detalhes,
        "tabelas": tabelas,
        "tabelas_ausentes_no_banco": [
            t for t in table_order if t not in existing
        ],
        "tabelas_no_banco_fora_do_backup": fora_do_backup,
    }

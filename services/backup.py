import json
import re
import logging
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import text, inspect as sa_inspect
from database import engine

logger = logging.getLogger(__name__)


TABLES_IN_ORDER = [
    "marcas_produto",
    "categorias_produto",
    "cfop_natureza",
    "usuarios",
    "clientes",
    "fornecedores",
    "contas_pagar",
    "contas_receber",
    "produtos",
    "produto_variacoes",
    "produto_composicao",
    "assinaturas",
    "assinaturas_historico",
    "pedidos_venda",
    "pedidos_venda_itens",
    "ordens_servico",
    "nfe",
    "nfe_itens",
    "nfse",
    "nfse_itens",
    "empresa",
]

ALLOWED_TABLES = set(TABLES_IN_ORDER)

SKIP_TABLES_ON_RESTORE = set()

# Colunas que armazenam enums Python (VARCHAR no banco) e seus tipos de enum.
# A fonte da verdade são os valores definidos em models.py (case-sensitive).
_ENUM_COLUMNS = {
    "status": ("pedidos_venda", "StatusPedido"),
    "status": ("ordens_servico", "StatusOS"),
    "status": ("contas_pagar", "StatusConta"),
    "status": ("contas_receber", "StatusConta"),
    "forma_pagamento": ("pedidos_venda", "FormaPagamento"),
    "forma_pagamento": ("contas_pagar", "FormaPagamento"),
    "forma_pagamento": ("contas_receber", "FormaPagamento"),
}


def _build_enum_lookup():
    """Constrói um dicionário {coluna: {variante_lower: valor_canônico}} a partir
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
    for col, (_, enum_name) in _ENUM_COLUMNS.items():
        enum_cls = _enum_classes.get(enum_name)
        if not enum_cls:
            continue
        norm = {}
        for member in enum_cls:
            # O SQLAlchemy (native_enum=False) usa o NOME do membro (ex.: CONSOLIDADO)
            # para ler/gravar, não o .value. Normalizamos para o nome canônico.
            canon = member.name
            variantes = {
                member.name.lower(),
                member.value.lower(),
                member.value.lower().replace("_", ""),
                member.value.lower().replace("_", " "),
            }
            for v in variantes:
                norm[v] = canon
        lookup[col] = norm
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


def _normalize_enum_value(col_name: str, value):
    """Normaliza um valor de enum para o NOME canônico do membro em models.py
    (o que o SQLAlchemy espera com native_enum=False)."""
    if not isinstance(value, str) or col_name not in _ENUM_LOOKUP:
        return value
    key = value.strip().lower()
    if key in _ENUM_LOOKUP[col_name]:
        return _ENUM_LOOKUP[col_name][key]
    return value.strip().upper()


def _validate_table(table_name: str) -> str:
    """Valida se o nome da tabela está na whitelist para prevenir SQL injection."""
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Tabela não permitida: {table_name}")
    return table_name


def _serialize(val):
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    return val


def generate_backup():
    with engine.connect() as conn:
        tables = {}
        for table in TABLES_IN_ORDER:
            _validate_table(table)
            rows = conn.execute(text(f"SELECT * FROM {table} ORDER BY id")).fetchall()
            if not rows:
                tables[table] = []
                continue
            columns = rows[0]._fields
            tables[table] = [
                {col: _serialize(getattr(r, col)) for col in columns}
                for r in rows
            ]
        return {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "tables": tables,
        }


def _get_pg_columns(conn, table_name):
    result = conn.execute(text(
        "SELECT column_name, data_type, udt_name "
        "FROM information_schema.columns "
        "WHERE table_name = :t ORDER BY ordinal_position"
    ), {"t": table_name})
    return {row[0]: {"data_type": row[1], "udt_name": row[2]} for row in result}


def restore_backup(backup_dict: dict, modo: str = "sobrepor") -> dict:
    if modo not in ("sobrepor", "limpar"):
        raise ValueError("modo deve ser 'sobrepor' ou 'limpar'")
    logger.info("[RESTORE] Iniciando restore (modo=%s)", modo)
    raw = backup_dict.get("tables")
    if isinstance(raw, dict):
        tables_data = raw
    else:
        # Formato legado: o próprio dict raiz é {tabela: [rows]}
        tables_data = {
            k: v for k, v in backup_dict.items()
            if k in ALLOWED_TABLES and isinstance(v, list)
        }
    total_imported = 0
    total_errors = 0
    detalhes = []
    tabelas = {}

    for table_name in TABLES_IN_ORDER:
        _validate_table(table_name)
        backup_rows = tables_data.get(table_name, [])
        tabelas[table_name] = {"backup": len(backup_rows), "importado": 0, "erros": 0, "ignorado": 0}

    is_pg = "postgresql" in str(engine.url)

    # Modo "limpar": remove os registros atuais das tabelas presentes no backup
    # antes de reinserir, garantindo um restore fiel ao ponto do backup.
    # Em PostgreSQL, como `DISABLE TRIGGER ALL` não contorna FKs sem superusuário
    # e o schema tem FKs cíclicas (ex.: pedidos_venda <-> assinaturas), removemos
    # todas as FKs das tabelas antes e as recriamos ao final. Em SQLite o FK não
    # é enforcement por padrão, então nenhuma ação extra é necessária.
    fk_defs = []
    fk_disabled = False
    if modo == "limpar":
        if is_pg:
            with engine.connect() as conn:
                trans = conn.begin()
                fk_defs = _pg_drop_fk_constraints(conn)
                for drop_sql, _ in fk_defs:
                    conn.execute(text(drop_sql))
                trans.commit()
            fk_disabled = True
            logger.info("[RESTORE] limpar: %d FKs removidas (PostgreSQL)", len(fk_defs))
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                for table_name in tables_data:
                    if table_name in ALLOWED_TABLES:
                        _validate_table(table_name)
                        conn.execute(text(f"DELETE FROM {table_name}"))
                trans.commit()
            except Exception as e:
                trans.rollback()
                raise RuntimeError(f"Falha ao limpar tabelas antes do restore: {e}") from e

    for table_name in TABLES_IN_ORDER:
        _validate_table(table_name)
        if table_name in SKIP_TABLES_ON_RESTORE:
            tabelas[table_name]["ignorado"] = tabelas[table_name]["backup"]
            continue

        rows = tables_data.get(table_name, [])
        if not rows:
            continue

        with engine.connect() as conn:
            trans = conn.begin()
            try:
                columns_info = _get_pg_columns(conn, table_name) if is_pg else {}
                db_columns = set(columns_info.keys()) if is_pg else None

                for i, row_data in enumerate(rows):
                    try:
                        if is_pg:
                            conn.execute(text(f"SAVEPOINT sp_{i}"))

                        col_names = []
                        col_placeholders = []
                        col_values = {}

                        for k, v in row_data.items():
                            if db_columns and k not in db_columns:
                                continue
                            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', str(k)):
                                continue
                            if v is None:
                                continue

                            col_type = columns_info.get(k, {})
                            if is_pg and col_type.get("udt_name") in (
                                "statusconta", "statusos", "statuspedido", "formapagamento"
                            ):
                                if isinstance(v, str):
                                    v = v.upper()
                                col_names.append(k)
                                col_placeholders.append(f"CAST(:{k} AS {col_type['udt_name']})")
                            elif k in _ENUM_LOOKUP and isinstance(v, str):
                                v = _normalize_enum_value(k, v)
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
                        with engine.connect() as conn_seq:
                            conn_seq.execute(text(
                                f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                                f"COALESCE((SELECT MAX(id)+1 FROM {table_name}), 1), false)"
                            ))
                            conn_seq.commit()
                    except:
                        pass

            except Exception as e:
                trans.rollback()
                tabelas[table_name]["erros"] = tabelas[table_name]["backup"]
                tabelas[table_name]["importado"] = 0
                total_errors += 1
                detalhes.append(f"{table_name}: TABELA NAO IMPORTADA - erro: {str(e)[:300]}")

    if fk_disabled:
        try:
            with engine.connect() as conn:
                trans = conn.begin()
                logger.info("[RESTORE] limpar: recriando %d FKs", len(fk_defs))
                for _, create_sql in fk_defs:
                    conn.execute(text(create_sql))
                trans.commit()
        except Exception as e:
            logger.error("Não foi possível recriar as FKs após o restore: %s", e)

    for tn, t in tabelas.items():
        t["nao_processado"] = t["backup"] - t["importado"] - t["erros"] - t["ignorado"]

    logger.info("[RESTORE] concluído: %d importados, %d erros", total_imported, total_errors)
    return {
        "imported": total_imported,
        "erros": total_errors,
        "detalhes": detalhes,
        "tabelas": tabelas,
    }

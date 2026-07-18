import json
import re
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import text, inspect as sa_inspect
from database import engine


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


def restore_backup(backup_dict: dict) -> dict:
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

    for tn, t in tabelas.items():
        t["nao_processado"] = t["backup"] - t["importado"] - t["erros"] - t["ignorado"]

    return {
        "imported": total_imported,
        "erros": total_errors,
        "detalhes": detalhes,
        "tabelas": tabelas,
    }

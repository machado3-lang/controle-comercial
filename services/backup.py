import json
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

SKIP_TABLES_ON_RESTORE = {"empresa"}


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
    tables_data = backup_dict.get("tables", {})
    total_imported = 0
    total_errors = 0
    total_skipped = 0
    detalhes = []
    tabelas = {}

    for table_name in TABLES_IN_ORDER:
        backup_rows = tables_data.get(table_name, [])
        tabelas[table_name] = {"backup": len(backup_rows), "importado": 0, "erros": 0, "ignorado": 0}

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            is_pg = "postgresql" in str(engine.url)

            for table_name in TABLES_IN_ORDER:
                if table_name in SKIP_TABLES_ON_RESTORE:
                    tabelas[table_name]["ignorado"] = tabelas[table_name]["backup"]
                    continue

                rows = tables_data.get(table_name, [])
                if not rows:
                    continue

                columns_info = _get_pg_columns(conn, table_name) if is_pg else {}
                db_columns = set(columns_info.keys()) if is_pg else None

                for row_data in rows:
                    try:
                        col_names = []
                        col_placeholders = []
                        col_values = {}

                        for k, v in row_data.items():
                            if db_columns and k not in db_columns:
                                continue
                            if v is None:
                                continue

                            col_type = columns_info.get(k, {})
                            if is_pg and col_type.get("udt_name") in (
                                "statusconta", "statusos", "statuspedido", "formapagamento"
                            ):
                                col_names.append(k)
                                col_placeholders.append(f"CAST(:{k} AS {col_type['udt_name']})")
                            else:
                                col_names.append(k)
                                col_placeholders.append(f":{k}")
                            col_values[k] = v

                        if not col_names:
                            tabelas[table_name]["ignorado"] += 1
                            continue

                        placeholders = ", ".join(col_placeholders)
                        names = ", ".join(col_names)

                        if is_pg:
                            conflict_cols = ", ".join(c for c in col_names if c == "id")
                            if conflict_cols:
                                stmt = (
                                    f"INSERT INTO {table_name} ({names}) "
                                    f"VALUES ({placeholders}) "
                                    f"ON CONFLICT ({conflict_cols}) DO NOTHING"
                                )
                            else:
                                stmt = f"INSERT INTO {table_name} ({names}) VALUES ({placeholders})"
                            conn.execute(text(stmt), col_values)
                        else:
                            stmt = f"INSERT OR IGNORE INTO {table_name} ({names}) VALUES ({placeholders})"
                            conn.execute(text(stmt), col_values)

                        tabelas[table_name]["importado"] += 1
                        total_imported += 1

                    except Exception as e:
                        tabelas[table_name]["erros"] += 1
                        total_errors += 1
                        detalhes.append(f"{table_name}:{row_data.get('id', '?')} erro: {str(e)[:200]}")

            trans.commit()
        except Exception as e:
            trans.rollback()
            total_errors += 1
            detalhes.append(f"ROLLBACK GERAL: {str(e)[:300]}")

    for tn, t in tabelas.items():
        t["nao_processado"] = t["backup"] - t["importado"] - t["erros"] - t["ignorado"]

    return {
        "imported": total_imported,
        "erros": total_errors,
        "detalhes": detalhes,
        "tabelas": tabelas,
    }

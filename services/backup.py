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
    detalhes = []
    tabelas = {}

    for table_name in TABLES_IN_ORDER:
        backup_rows = tables_data.get(table_name, [])
        tabelas[table_name] = {"backup": len(backup_rows), "importado": 0, "erros": 0, "ignorado": 0}

    is_pg = "postgresql" in str(engine.url)

    for table_name in TABLES_IN_ORDER:
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

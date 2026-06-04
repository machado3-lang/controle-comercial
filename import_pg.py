import json
import os
import psycopg2
from psycopg2.extras import execute_values

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

from models import Base
from database import engine

print("Recriando tabelas...")
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
print("Tabelas recriadas!")

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Desativar FK checks para importar dados inconsistentes
cursor.execute("SET session_replication_role = replica;")
conn.commit()

with open('backup.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

table_order = ['usuarios', 'clientes', 'fornecedores', 'categorias_produto', 'produtos', 
               'assinaturas', 'ordens_servico', 'contas_pagar', 'contas_receber', 
               'pedidos_venda', 'pedidos_venda_itens', 'empresa']

def clean_row(row, cols):
    vals = []
    for c in cols:
        v = row[c]
        if isinstance(v, str):
            if c == 'status' and v:
                v = v.upper()
        if v is None:
            vals.append(None)
        elif isinstance(v, bool):
            vals.append(v)
        elif isinstance(v, (int, float)):
            vals.append(int(v) if v == v else None)
        else:
            vals.append(v)
    return vals

for table in table_order:
    if table not in data or not data[table]:
        print(f"{table}: vazio")
        continue
    
    rows = data[table]
    cols = list(rows[0].keys())
    clean_data = [tuple(clean_row(row, cols)) for row in rows]
    
    try:
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s"
        execute_values(cursor, sql, clean_data, page_size=100)
        print(f"{table}: {len(rows)} importados")
    except Exception as e:
        print(f"{table}: ERRO - {e}")

conn.commit()
# Reativar FK checks
cursor.execute("SET session_replication_role = DEFAULT;")
conn.close()
print("Migração concluída!")
import json
import os
import psycopg2

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

with open('backup.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

table_order = ['usuarios', 'clientes', 'fornecedores', 'categorias_produto', 'produtos', 
               'assinaturas', 'ordens_servico', 'contas_pagar', 'contas_receber', 
               'pedidos_venda', 'pedidos_venda_itens', 'empresa']

def clean_val(v):
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == '':
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return int(v) if v == v else None
    return v

for table in table_order:
    if table not in data or not data[table]:
        print(f"{table}: vazio")
        continue
    
    rows = data[table]
    cols = [c for c in list(rows[0].keys()) if c != 'id']  # Sem ID, auto-increment
    
    try:
        # Truncar tabela primeiro
        cursor.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;")
        
        # Inserir sem ID
        for row in rows:
            vals = [clean_val(row[c]) for c in cols]
            placeholders = ', '.join(['%s'] * len(cols))
            stmt = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
            cursor.execute(stmt, vals)
        
        conn.commit()
        print(f"{table}: {len(rows)} importados")
    except Exception as e:
        print(f"{table}: ERRO - {e}")
        conn.rollback()

conn.close()
print("Migração concluída!")
import json
import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

with open('backup.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

def clean_val(v, col_type=None):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return int(v) if v == v else None
    s = str(v).strip()
    if s in ('', 'None', 'null'):
        return None
    return s

order = ['usuarios', 'clientes', 'fornecedores', 'categorias_produto', 'produtos', 
         'assinaturas', 'ordens_servico', 'contas_pagar', 'contas_receber', 
         'pedidos_venda', 'pedidos_venda_itens', 'empresa']

for table in order:
    if table not in data or not data[table]:
        print(f"{table}: vazio")
        continue
    
    rows = data[table]
    cols = [c for c in rows[0].keys() if c != 'id']
    errors = 0
    success = 0
    
    for row in rows:
        try:
            vals = [clean_val(row[c]) for c in cols]
            placeholders = ', '.join(['%s'] * len(cols))
            stmt = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            cursor.execute(stmt, vals)
            success += 1
        except Exception as e:
            errors += 1
            print(f"  Erro: {e}")
    
    conn.commit()
    print(f"{table}: {success} sucessos, {errors} erros")

cursor.close()
conn.close()
print("Migração concluída!")
import json
import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Importar modelos para recriar tabelas
from models import Base
from database import engine

print("Recriando tabelas...")
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
print("Tabelas recriadas!")

with open('backup.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

table_order = ['usuarios', 'clientes', 'fornecedores', 'categorias_produto', 'produtos', 
               'assinaturas', 'ordens_servico', 'contas_pagar', 'contas_receber', 
               'pedidos_venda', 'pedidos_venda_itens', 'empresa']

for table in table_order:
    if table not in data or not data[table]:
        print(f"{table}: vazio")
        continue
    
    rows = data[table]
    cols = list(rows[0].keys())
    errors = 0
    
    for row in rows:
        try:
            vals = []
            for c in cols:
                v = row[c]
                if v is None:
                    vals.append(None)
                elif isinstance(v, bool):
                    vals.append(v)
                elif isinstance(v, (int, float)):
                    vals.append(int(v) if v == v else None)
                else:
                    vals.append(str(v) if str(v).strip() else None)
            
            placeholders = ', '.join(['%s'] * len(cols))
            stmt = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
            cursor.execute(stmt, vals)
        except Exception as e:
            errors += 1
            pass
    
    conn.commit()
    print(f"{table}: {len(rows)-errors} sucessos, {errors} erros")

cursor.close()
conn.close()
print("Migração concluída!")
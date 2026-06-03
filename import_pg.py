import json
import os
import psycopg2
from urllib.parse import urlparse

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

with open('backup.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

table_order = ['usuarios', 'clientes', 'fornecedores', 'categorias_produto', 'produtos', 
               'assinaturas', 'ordens_servico', 'contas_pagar', 'contas_receber', 
               'pedidos_venda', 'pedidos_venda_itens', 'empresa']

for table in table_order:
    if table in data and data[table]:
        cols = list(data[table][0].keys())
        for row in data[table]:
            vals = []
            for v in row.values():
                if v is None:
                    vals.append('NULL')
                elif isinstance(v, bool):
                    vals.append('TRUE' if v else 'FALSE')
                elif isinstance(v, (str, bytes)):
                    vals.append("'" + str(v).replace("'", "''") + "'")
                else:
                    vals.append(str(v))
            placeholders = ', '.join(['%s'] * len(cols))
            stmt = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            cursor.execute(stmt, [row[c] for c in cols])
        conn.commit()
        print(f"{table}: importado")

cursor.close()
conn.close()
print("Migração concluída!")
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

status_map = {'pendente': 'pendente', 'pago': 'pago', 'vencido': 'vencido', 'cancelado': 'cancelado', 'baixa_solicitada': 'baixa_solicitada'}
status_pedido_map = {'pendente': 'pendente', 'aprovado': 'aprovado', 'faturado': 'faturado', 'pre_venda': 'pre_venda', 'cancelado': 'cancelado'}
status_os_map = {'aberta': 'aberta', 'em_andamento': 'em_andamento', 'finalizada': 'finalizada', 'cancelada': 'cancelada'}

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
    
    for row in rows:
        try:
            vals = []
            for c in cols:
                v = row[c]
                if table in ['contas_pagar', 'contas_receber'] and c == 'status' and v:
                    v = status_map.get(str(v), str(v) if v else v)
                elif table == 'pedidos_venda' and c == 'status' and v:
                    v = status_pedido_map.get(str(v), str(v) if v else v)
                elif table == 'ordens_servico' and c == 'status' and v:
                    v = status_os_map.get(str(v), str(v) if v else v)
                vals.append(str(v) if isinstance(v, str) else v)
            
            placeholders = ', '.join(['%s'] * len(cols))
            stmt = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            cursor.execute(stmt, vals)
        except Exception as e:
            errors += 1
            continue
    
    conn.commit()
    print(f"{table}: importado ({len(rows)-errors} sucessos, {errors} erros)")

cursor.close()
conn.close()
print("Migração concluída!")
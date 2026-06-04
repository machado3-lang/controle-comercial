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

# Mapeamento de enums para maiúsculas (como o SQLAlchemy cria)
enum_maps = {
    'statusconta': {'pendente': 'PENDENTE', 'pago': 'PAGO', 'vencido': 'VENCIDO', 'cancelado': 'CANCELADO', 'baixa_solicitada': 'BAIXA_SOLICITADA'},
    'statuspedido': {'pendente': 'PENDENTE', 'aprovado': 'APROVADO', 'faturado': 'FATURADO', 'pre_venda': 'PRE_VENDA', 'cancelado': 'CANCELADO'},
    'statusos': {'aberta': 'ABERTA', 'em_andamento': 'EM_ANDAMENTO', 'finalizada': 'FINALIZADA', 'cancelada': 'CANCELADA'}
}

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
            vals = []
            for c in cols:
                v = row[c]
                # Converter enums
                if table in ['contas_pagar', 'contas_receber'] and c == 'status' and v:
                    v = enum_maps['statusconta'].get(str(v), str(v).upper())
                elif table == 'pedidos_venda' and c == 'status' and v:
                    v = enum_maps['statuspedido'].get(str(v), str(v).upper())
                elif table == 'ordens_servico' and c == 'status' and v:
                    v = enum_maps['statusos'].get(str(v), str(v).upper())
                
                if v is None:
                    vals.append(None)
                elif isinstance(v, bool):
                    vals.append(v)
                elif isinstance(v, (int, float)):
                    vals.append(int(v) if v == v else None)
                else:
                    vals.append(str(v) if str(v).strip() else None)
            
            placeholders = ', '.join(['%s'] * len(cols))
            stmt = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            cursor.execute(stmt, vals)
            success += 1
        except Exception as e:
            errors += 1
            pass
    
    conn.commit()
    print(f"{table}: {success} sucessos, {errors} erros")

cursor.close()
conn.close()
print("Migração concluída!")
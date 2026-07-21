from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    # Check tables (PostgreSQL)
    result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND (table_name LIKE '%pedido%' OR table_name LIKE '%consolida%')")).fetchall()
    print('Tables:')
    for r in result:
        print(f'  {r[0]}')
    
    # Check columns in pedidos_consolidados
    result = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='pedidos_consolidados' ORDER BY ordinal_position")).fetchall()
    print('\npedidos_consolidados columns:')
    for r in result:
        print(f'  {r[0]}: {r[1]}')
    
    result = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='pedidos_consolidados_itens' ORDER BY ordinal_position")).fetchall()
    print('\npedidos_consolidados_itens columns:')
    for r in result:
        print(f'  {r[0]}: {r[1]}')
    
    result = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='pedidos_consolidados_itens_origem' ORDER BY ordinal_position")).fetchall()
    print('\npedidos_consolidados_itens_origem columns:')
    for r in result:
        print(f'  {r[0]}: {r[1]}')
    
    result = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='pedidos_venda' ORDER BY ordinal_position")).fetchall()
    print('\npedidos_venda columns:')
    for r in result:
        print(f'  {r[0]}: {r[1]}')
    
    result = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='contas_receber' ORDER BY ordinal_position")).fetchall()
    print('\ncontas_receber columns:')
    for r in result:
        print(f'  {r[0]}: {r[1]}')
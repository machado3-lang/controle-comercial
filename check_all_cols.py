from database import engine
from sqlalchemy import inspect
inspector = inspect(engine)
# Check pedidos_venda columns
cols = inspector.get_columns('pedidos_venda')
for c in cols:
    print(f"pedidos_venda.{c['name']}: {c['type']}")
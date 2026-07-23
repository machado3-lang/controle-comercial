from database import engine
from sqlalchemy import inspect
inspector = inspect(engine)
# Check pedidos_venda columns
cols = inspector.get_columns('pedidos_venda')
consolid_cols = [c for c in cols if 'consolid' in c['name'].lower()]
for c in consolid_cols:
    print(f"pedidos_venda.{c['name']}: {c['type']}")
# Check contas_receber
cols = inspector.get_columns('contas_receber')
consolid_cols = [c for c in cols if 'consolid' in c['name'].lower()]
for c in consolid_cols:
    print(f"contas_receber.{c['name']}: {c['type']}")
# Check nfse
cols = inspector.get_columns('nfse')
consolid_cols = [c for c in cols if 'consolid' in c['name'].lower()]
for c in consolid_cols:
    print(f"nfse.{c['name']}: {c['type']}")
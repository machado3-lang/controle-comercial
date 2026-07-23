from database import engine
from sqlalchemy import inspect
inspector = inspect(engine)
tables = [t for t in inspector.get_table_names() if 'consolid' in t.lower()]
for t in sorted(tables):
    print(t)
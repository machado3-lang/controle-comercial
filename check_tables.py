from database import engine
from sqlalchemy import inspect
insp = inspect(engine)
for table in insp.get_table_names():
    if 'consolid' in table.lower():
        print(f'=== {table} ===')
        for col in insp.get_columns(table):
            print(f'  {col["name"]}: {col["type"]}')
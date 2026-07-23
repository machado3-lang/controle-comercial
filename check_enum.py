from database import engine
from sqlalchemy import text
with engine.connect() as conn:
    result = conn.execute(text("SELECT enumlabel FROM pg_enum WHERE enumtypid = (SELECT oid FROM pg_type WHERE typname = 'statuspedido') ORDER BY enumsortorder")).fetchall()
    for r in result:
        print(r[0])
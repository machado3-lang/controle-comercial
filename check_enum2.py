from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    # Get the enum type details
    result = conn.execute(text("""
        SELECT t.typname, e.enumlabel 
        FROM pg_type t 
        JOIN pg_enum e ON t.oid = e.enumtypid 
        WHERE t.typname = 'statuspedido' 
        ORDER BY e.enumsortorder
    """)).fetchall()
    for r in result:
        print(f'{r[0]}: {r[1]}')
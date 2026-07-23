from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    # Add the missing enum value
    try:
        conn.execute(text("ALTER TYPE statuspedido ADD VALUE 'consolidado'"))
        conn.commit()
        print("Added 'consolidado' to statuspedido enum")
    except Exception as e:
        print(f"Error adding enum value: {e}")
        # Try with IF NOT EXISTS equivalent
        pass
    
    # Verify
    result = conn.execute(text("SELECT enumlabel FROM pg_enum WHERE enumtypid = (SELECT oid FROM pg_type WHERE typname = 'statuspedido') ORDER BY enumsortorder")).fetchall()
    for r in result:
        print(r[0])
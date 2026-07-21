from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    # Drop the native enum type and recreate as a check constraint
    # First, change the column to use a varchar with check constraint
    try:
        conn.execute(text("ALTER TABLE pedidos_venda ALTER COLUMN status TYPE varchar(20)"))
        conn.execute(text("DROP TYPE statuspedido"))
        conn.execute(text("ALTER TABLE pedidos_venda ALTER COLUMN status TYPE statuspedido USING status::statuspedido"))
        print("Recreated enum")
    except Exception as e:
        print(f"Error: {e}")
        # Try alternative: just change column type to varchar
        try:
            conn.execute(text("ALTER TABLE pedidos_venda ALTER COLUMN status TYPE varchar(20)"))
            print("Changed to varchar")
        except Exception as e2:
            print(f"Error 2: {e2}")
    
    conn.commit()
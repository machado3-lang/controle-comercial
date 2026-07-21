from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    # Change column type to varchar
    try:
        conn.execute(text("ALTER TABLE pedidos_venda ALTER COLUMN status TYPE varchar(20)"))
        conn.commit()
        print("Changed to varchar")
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
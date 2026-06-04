import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)
engine = create_engine(DATABASE_URL)

MIGRATIONS = [
    # Migration 1: adicionar colunas se não existirem
    """
    ALTER TABLE contas_receber ADD COLUMN IF NOT EXISTS data_emissao DATE;
    """,
    """
    ALTER TABLE clientes ADD COLUMN IF NOT EXISTS telefone_whatsapp VARCHAR(20);
    """,
    """
    ALTER TABLE produtos ADD COLUMN IF NOT EXISTS codigo_barras VARCHAR(50);
    """,
]

def run_migrations():
    for sql in MIGRATIONS:
        if sql.strip():
            with engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()
                print(f"Executado: {sql[:50]}...")

if __name__ == "__main__":
    run_migrations()
    print("Migrations concluídas!")
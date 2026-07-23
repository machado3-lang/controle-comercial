from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    # Add consolidacao_id to pedidos_venda
    try:
        conn.execute(text("ALTER TABLE pedidos_venda ADD COLUMN IF NOT EXISTS consolidacao_id INTEGER"))
        print("Added consolidacao_id to pedidos_venda")
    except Exception as e:
        print(f"pedidos_venda: {e}")
    
    # Add consolidacao_id to contas_receber
    try:
        conn.execute(text("ALTER TABLE contas_receber ADD COLUMN IF NOT EXISTS consolidacao_id INTEGER"))
        print("Added consolidacao_id to contas_receber")
    except Exception as e:
        print(f"contas_receber: {e}")
    
    # Add consolidacao_id to nfse
    try:
        conn.execute(text("ALTER TABLE nfse ADD COLUMN IF NOT EXISTS consolidacao_id INTEGER"))
        print("Added consolidacao_id to nfse")
    except Exception as e:
        print(f"nfse: {e}")
    
    # Add indexes
    try:
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pedidos_venda_consolidacao_id ON pedidos_venda(consolidacao_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_contas_receber_consolidacao_id ON contas_receber(consolidacao_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_nfse_consolidacao_id ON nfse(consolidacao_id)"))
        print("Indexes created")
    except Exception as e:
        print(f"Indexes: {e}")
    
    # Add foreign keys (optional, but good for integrity)
    try:
        conn.execute(text("ALTER TABLE pedidos_venda ADD CONSTRAINT fk_pedidos_venda_consolidacao FOREIGN KEY (consolidacao_id) REFERENCES pedidos_consolidados(id)"))
        print("FK pedidos_venda -> pedidos_consolidados created")
    except Exception as e:
        print(f"FK pedidos_venda: {e}")
    
    try:
        conn.execute(text("ALTER TABLE contas_receber ADD CONSTRAINT fk_contas_receber_consolidacao FOREIGN KEY (consolidacao_id) REFERENCES pedidos_consolidados(id)"))
        print("FK contas_receber -> pedidos_consolidados created")
    except Exception as e:
        print(f"FK contas_receber: {e}")
    
    try:
        conn.execute(text("ALTER TABLE nfse ADD CONSTRAINT fk_nfse_consolidacao FOREIGN KEY (consolidacao_id) REFERENCES pedidos_consolidados(id)"))
        print("FK nfse -> pedidos_consolidados created")
    except Exception as e:
        print(f"FK nfse: {e}")
    
    conn.commit()
    print("Done!")
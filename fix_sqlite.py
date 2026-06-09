import sqlite3
conn = sqlite3.connect('controle.db')
cur = conn.cursor()

# Verificar colunas
cur.execute("PRAGMA table_info(produtos)")
cols = [c[1] for c in cur.fetchall()]
print(f"Colunas produtos: {cols}")

# Adicionar colunas faltantes
if 'marca_id' not in cols:
    cur.execute("ALTER TABLE produtos ADD COLUMN marca_id INTEGER")
    print("Adicionado marca_id")
if 'foto' not in cols:
    cur.execute("ALTER TABLE produtos ADD COLUMN foto VARCHAR(500)")
    print("Adicionado foto")

conn.commit()
conn.close()
print("Done")
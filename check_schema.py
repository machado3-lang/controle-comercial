import sqlite3
conn = sqlite3.connect('controle.db')
cur = conn.cursor()
cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='produtos'")
print(cur.fetchone())
conn.close()
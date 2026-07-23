from database import SessionLocal
from models import Usuario

db = SessionLocal()
usuarios = db.query(Usuario).all()
print(f"Total de usuários: {len(usuarios)}")
for u in usuarios:
    hash_prefix = (u.senha or "None")[:50]
    print(f"ID: {u.id} | Email: {u.email} | Nome: {u.nome} | Ativo: {u.ativo} | Hash: {hash_prefix}...")
db.close()

from database import SessionLocal
from models import Usuario

db = SessionLocal()
u = db.query(Usuario).filter(Usuario.email == 'admin@controle.com').first()
print(f'Senha completa: {u.senha}')
print(f'Len: {len(u.senha)}')
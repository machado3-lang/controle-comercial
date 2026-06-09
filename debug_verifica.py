from routers.auth import verifica_senha
from database import SessionLocal
from models import Usuario

db = SessionLocal()
u = db.query(Usuario).filter(Usuario.email == 'admin@controle.com').first()
result = verifica_senha('admin123', u.senha)
print(f'Verifica senha: {result}')
import hashlib
import secrets
from database import SessionLocal
from models import Usuario

db = SessionLocal()
db.query(Usuario).filter(Usuario.email == 'admin@controle.com').delete()
db.commit()

salt = secrets.token_hex(16)
senha_hash = f'{salt}:{hashlib.sha256((salt + 'admin123').encode()).hexdigest()}'
admin = Usuario(email='admin@controle.com', senha=senha_hash, nome='Administrador', ativo=True)
db.add(admin)
db.commit()
print('Admin recriado')
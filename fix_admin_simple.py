import hashlib
from database import SessionLocal
from models import Usuario

db = SessionLocal()
db.query(Usuario).filter(Usuario.email == 'admin@controle.com').delete()
db.commit()

# Senha simples sem salt para testar
senha_hash = hashlib.sha256('admin123'.encode()).hexdigest()
admin = Usuario(email='admin@controle.com', senha=senha_hash, nome='Administrador', ativo=True)
db.add(admin)
db.commit()
print('Admin recriado com senha simples')
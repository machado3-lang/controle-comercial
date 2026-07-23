import hashlib
import secrets
from database import SessionLocal
from models import Usuario

EMAIL = "admin@controle.com"
NOVA_SENHA = "admin123"
ITERATIONS = 600000

db = SessionLocal()
usuario = db.query(Usuario).filter(Usuario.email == EMAIL).first()
if not usuario:
    print(f"Usuário {EMAIL} não encontrado!")
else:
    salt = secrets.token_hex(16)
    hash_val = hashlib.pbkdf2_hmac("sha256", NOVA_SENHA.encode(), salt.encode(), ITERATIONS).hex()
    usuario.senha = f"2:{ITERATIONS}:{salt}:{hash_val}"
    db.commit()
    print(f"Senha do usuário {EMAIL} atualizada com sucesso!")
    print(f"Novo hash: {usuario.senha}")
db.close()

"""Verifica se algum admin ainda usa a senha padrao do README."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from models import Usuario
from app.core.security import verifica_senha

SENHA_PADRAO = "admin123"

db = SessionLocal()
for u in db.query(Usuario).filter(Usuario.is_admin == True).order_by(Usuario.id).all():
    usa_padrao = verifica_senha(SENHA_PADRAO, u.senha)
    print(f"{u.email:<34} ativo={str(u.ativo):<5} admin123? {usa_padrao}")
db.close()
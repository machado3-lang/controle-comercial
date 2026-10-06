"""Confere a estrutura do hash de senha do usuario e valida a funcao de login.

Nao precisa da senha em texto: valida o formato PBKDF2 do app
(`2:iteracoes:salt:hash`, ver app/core/security.py:hash_senha) e que
`verifica_senha` responde sem exception.

Uso:
    python scripts/conferir_hash_admin.py [usuario_id]
"""
import hashlib
import os
import re
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security import verifica_senha  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import Usuario  # noqa: E402

# Formato do app: 2:<iteracoes>:<salt_hex>:<hash_hex>
PADRAO = re.compile(r"^2:(\d+):([0-9a-f]{32}):([0-9a-f]{64})$")


def main(usuario_id):
    db = SessionLocal()
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if u is None:
        print(f"ERRO: usuario {usuario_id} nao existe")
        return 1
    h = u.senha
    db.close()

    print(f"usuario : {u.id} <{u.email}>  admin={u.is_admin}  ativo={u.ativo}")
    m = PADRAO.match(h or "")
    print(f"formato : {'PBKDF2 valido' if m else 'NAO CONFORME'}")
    if m:
        iters, salt, esperado = int(m.group(1)), m.group(2), m.group(3)
        print(f"iteracoes={iters}  salt={salt}")
        print(f"hash     ={esperado[:32]}...")

        # Prova que o hash e coerente com o proprio salt/iterations:
        # uma senha qualquer tem de produzir outro digest.
        falso = hashlib.pbkdf2_hmac("sha256", b"x", salt.encode(), iters).hex()
        igual = secrets.compare_digest(falso, esperado)
        print(f"digest de senha errada difere do armazenado: {not igual}")
        return 0 if (not igual and verifica_senha("x", h) is False) else 1

    print(f"hash bruto: {h}")
    return 1


if __name__ == "__main__":
    alvo = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    raise SystemExit(main(alvo))
"""Restaura a senha do usuario cuja senha foi sobrescrita por engano.

Contexto: ao rodar `scripts/validar_classificacao_app.py`, o script reescreveu
a senha do PRIMEIRO admin encontrado (`Usuario.is_admin == True`, id=1) com uma
senha de teste. Este script devolve o hash original lido do backup JSON mais
recente em `backups/`, sem tocar nos demais usuarios.

Seguro por padrao: so altera o usuario indicado e so quando o hash atual
diver != original (idempotente).
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from models import Usuario


def hash_original(usuario_id):
    """Hash de senha do usuario no backup JSON mais recente que o contenha."""
    for caminho in reversed(sorted(glob.glob("backups/*.json"))):
        with open(caminho, encoding="utf-8") as f:
            d = json.load(f)
        usuarios = (d.get("tables") or {}).get("usuarios")
        rows = usuarios if isinstance(usuarios, list) else (usuarios or {}).get("rows", [])
        for r in rows:
            if r.get("id") == usuario_id and r.get("senha"):
                return r["senha"], caminho
    return None, None


def main(usuario_id):
    original, origem = hash_original(usuario_id)
    if not original:
        print(f"ERRO: nao achei o hash do usuario {usuario_id} em backups/*.json")
        return 1

    db = SessionLocal()
    try:
        u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
        if u is None:
            print(f"ERRO: usuario {usuario_id} nao existe no banco")
            return 1

        print(f"usuario {u.id} <{u.email}>")
        print(f"  origem do hash : {origem}")
        print(f"  hash no banco  : {u.senha[:34]}")
        print(f"  hash no backup : {original[:34]}")

        if u.senha == original:
            print("  ja esta igual ao backup - nada a fazer")
            return 0

        u.senha = original
        db.commit()
        print("  -> senha restaurada")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    alvo = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    raise SystemExit(main(alvo))
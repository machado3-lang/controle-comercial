"""
Leitura apenas: decodifica o numero embutido na chave de acesso (nNF, posicoes
26-34 da chave) e compara com a coluna numero. Reporta divergencias.

Uso:
    python scripts/checar_numero_nfe.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from models import NFe
from sqlalchemy import func


def main():
    db = SessionLocal()
    try:
        rows = db.query(NFe).filter(NFe.chave_acesso.isnot(None)).all()
        print(f"Total NFe com chave: {len(rows)}\n")

        divergencias = 0
        for n in rows:
            chave = n.chave_acesso or ""
            nfnf = int(chave[25:34]) if len(chave) >= 34 else None
            db_num = int(n.numero) if n.numero is not None else None
            if nfnf is not None and db_num is not None and nfnf != db_num:
                divergencias += 1
                print(
                    f"id={n.id} | DB numero={db_num} | chave numero={nfnf} | "
                    f"status={n.status} | origem={n.origem} | invoice_id={n.invoice_id}"
                )

        print(f"\nDivergencias (coluna numero != numero na chave): {divergencias}")
        print("max(numero) na coluna:", db.query(func.max(NFe.numero)).scalar())
        max_chave = max(
            (int(n.chave_acesso[25:34]) for n in rows if n.chave_acesso and len(n.chave_acesso) >= 34),
            default=0,
        )
        print("max(numero na chave):", max_chave)
    finally:
        db.close()


if __name__ == "__main__":
    main()

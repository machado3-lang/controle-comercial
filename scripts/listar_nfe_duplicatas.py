"""
Lista NF-e duplicadas (mesmo numero + serie) para decisao de limpeza.
SOMENTE LEITURA — nao apaga nem altera nada.

Uso:
    python scripts/listar_nfe_duplicatas.py
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
        grupos = (
            db.query(NFe.numero, NFe.serie, func.count(NFe.id).label("cnt"))
            .group_by(NFe.numero, NFe.serie)
            .having(func.count(NFe.id) > 1)
            .order_by(NFe.numero)
            .all()
        )

        if not grupos:
            print("Nenhuma NF-e duplicada por (numero, serie).")
            return

        print(f"Encontrados {len(grupos)} grupos duplicados:\n")
        for numero, serie, cnt in grupos:
            print(f"=== Numero {numero} (serie {serie}) — {cnt} registros ===")
            rows = (
                db.query(NFe)
                .filter(NFe.numero == numero, NFe.serie == serie)
                .order_by(NFe.id)
                .all()
            )
            for n in rows:
                print(
                    f"  id={n.id} | status={n.status} | origem={n.origem} | "
                    f"invoice_id={n.invoice_id} | chave={n.chave_acesso} | "
                    f"emitido_em={n.data_emissao} | cliente_id={n.cliente_id}"
                )
            print()

        # Resumo do contador atual
        ult = db.query(func.max(NFe.numero)).scalar() or 0
        print(f"Maior numero de NFe no banco: {ult}")
        print(f"empresa.ultimo_numero_nfe (confira no app / configuracoes): deve ser >= {ult}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

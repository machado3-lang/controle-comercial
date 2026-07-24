"""
Repara a coluna `numero` das NFe a partir da chave de acesso.
A chave embute o numero autorizado pela SEFAZ (nNF, posicoes 26-34 da chave).
Muitas linhas ficaram com o numero do rascunho (errado) apos emissao dupla;
corrigir pela chave restabelece a sequencia real.

- Repara numero = nNF(chave) para toda NFe com chave valida.
- Exclui os ids de lixo de teste informados (padrao: 1, 2, 3).
- Ajusta empresa.ultimo_numero_nfe = max(numero) apos o reparo.

MODO SECO por padrao (nao escreve). Use --apply para executar.
Uso:
    python scripts/reparar_numero_nfe.py            # previsualiza
    python scripts/reparar_numero_nfe.py --apply    # executa (producao)
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from models import NFe, Empresa
from models_nfe import NFeItem
from sqlalchemy import func


LIXO_IDS = {1, 2, 3}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--apply", action="store_true",
        help="Executa as alteracoes. Sem isto, somente previsualiza (dry-run).",
    )
    args = ap.parse_args()

    db = SessionLocal()
    try:
        print(f"MODO: {'APPLY (escrita)' if args.apply else 'DRY-RUN (somente leitura)'}\n")

        # 1) Reparar numero a partir da chave
        rows = db.query(NFe).filter(NFe.chave_acesso.isnot(None)).all()
        reparadas = 0
        for n in rows:
            chave = n.chave_acesso or ""
            if len(chave) < 34:
                continue
            try:
                nfnf = int(chave[25:34])
            except Exception:
                continue
            if n.numero is None or int(n.numero) != nfnf:
                if args.apply:
                    n.numero = nfnf
                reparadas += 1
                print(f"  reparar id={n.id}: numero {n.numero} -> {nfnf} (chave)")
        print(f"NF-e com numero reparado da chave: {reparadas}\n")

        # 2) Excluir lixo de teste
        if LIXO_IDS:
            lixo = db.query(NFe).filter(NFe.id.in_(LIXO_IDS)).all()
            for n in lixo:
                if args.apply:
                    db.query(NFeItem).filter(NFeItem.nfe_id == n.id).delete()
                    db.delete(n)
                print(f"  excluir id={n.id} (numero={n.numero}, status={n.status}, origem={n.origem})")
            print(f"NF-e lixo excluidas: {len(lixo)}\n")

        # 3) Ajustar contador de numeracao
        db.flush()  # garante que reparos/exclusoes pendentes reflitam no max()
        max_num = db.query(func.max(NFe.numero)).scalar() or 0
        emp = db.query(Empresa).first()
        if emp:
            print(f"ultimo_numero_nfe atual: {emp.ultimo_numero_nfe} | novo max(numero): {max_num}")
            if args.apply and max_num > (emp.ultimo_numero_nfe or 0):
                emp.ultimo_numero_nfe = max_num

        if args.apply:
            db.commit()
            print("\nCOMMIT realizado.")
        else:
            db.rollback()
            print("\nROLLBACK (dry-run): nenhuma alteracao persistida.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

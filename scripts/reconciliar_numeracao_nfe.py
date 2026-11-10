"""
Reconcilia a numeracao das NF-e com a chave de acesso autorizada pela SEFAZ.

A chave embute o numero real (`nNF`, posicoes 26-34) e a serie (posicoes 23-25).
Quando a coluna `nfe.numero` divergir da chave, este script corrige a nota em
ordem CRESCENTE de numero atual (para nao colidir com o destino de outra nota)
e so SOBE o contador `empresa.ultimo_numero_nfe` quando ele estiver atras --
nunca reduz a sequencia.

E idempotente: rodar de novo nao altera nada quando ja esta tudo alinhado.

Uso:
    python scripts/reconciliar_numeracao_nfe.py              # dry-run (padrao)
    python scripts/reconciliar_numeracao_nfe.py --apply      # grava as correcoes
    python scripts/reconciliar_numeracao_nfe.py --apply --corrigir-contas
        # alem da nota, alinha `numero_documento` das contas SEM boleto emitido
        # (boletos ja emitidos no Sicoob nao sao tocados)

Nao apaga notas nem boletos.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from models import ContaReceber, Empresa, NFe
from services.numeracao import numero_da_chave, serie_da_chave


def _planejar(db):
    """Monta o estado final (numero, serie) de cada NFe e lista o que corrigir."""
    notas = db.query(NFe).order_by(NFe.numero, NFe.id).all()
    final = {n.id: (n.numero, n.serie) for n in notas}
    plano = []
    for nota in notas:
        numero_chave = numero_da_chave(nota.chave_acesso)
        if numero_chave is None:
            continue
        serie_chave = serie_da_chave(nota.chave_acesso)
        serie_alvo = serie_chave if serie_chave is not None else nota.serie
        if nota.numero != numero_chave or nota.serie != serie_alvo:
            plano.append(
                {"nota": nota, "numero": numero_chave, "serie": serie_alvo}
            )
            final[nota.id] = (numero_chave, serie_alvo)

    # Uma nota nao pode assumir um (numero, serie) que outra NFe ja ocupa no
    # estado FINAL. Isso detecta destinos duplicados sem depender da ordem em
    # que as correcoes sao aplicadas (e sem gravar nada no dry-run).
    for item in plano:
        nota = item["nota"]
        alvo = (item["numero"], item["serie"])
        item["conflito"] = next(
            (
                outra_id
                for outra_id, valores in final.items()
                if outra_id != nota.id and valores == alvo
            ),
            None,
        )

    # Ordem crescente do numero ATUAL: libera o destino antes de ocupa-lo
    # (ex.: 4491 -> 4490, depois 4492 -> 4491, depois 4494 -> 4492).
    plano.sort(key=lambda i: (i["nota"].numero or 0, i["nota"].id))
    return plano, final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--apply", action="store_true",
        help="Executa as alteracoes. Sem isto, apenas previsualiza (dry-run).",
    )
    ap.add_argument(
        "--corrigir-contas", action="store_true",
        help="Tambem alinha numero_documento das contas sem boleto emitido.",
    )
    args = ap.parse_args()

    db = SessionLocal()
    try:
        print(f"MODO: {'APPLY (escrita)' if args.apply else 'DRY-RUN (somente leitura)'}\n")
        empresa = db.query(Empresa).first()
        plano, final = _planejar(db)

        if not plano:
            print("Nenhuma NF-e com numero divergente da chave. Nada a fazer.")
            return

        print(f"NF-e divergentes: {len(plano)}\n")
        corrigidas = 0
        ignoradas = 0
        for item in plano:
            nota = item["nota"]
            rotulo = (
                f"id={nota.id} status={nota.status} origem={nota.origem or '-'} "
                f"numero {nota.numero} -> {item['numero']}"
            )
            if item["nota"].serie != item["serie"]:
                rotulo += f" | serie {nota.serie} -> {item['serie']}"

            if item["conflito"] is not None:
                print(
                    f"  IGNORADA {rotulo} "
                    f"(a NFe id={item['conflito']} ja ficaria com esse numero/serie)"
                )
                ignoradas += 1
                continue

            print(f"  {'CORRIGIR' if args.apply else 'corrigiria'} {rotulo}")
            if args.apply:
                nota.numero = item["numero"]
                nota.serie = item["serie"]

                if args.corrigir_contas:
                    contas = (
                        db.query(ContaReceber)
                        .filter(
                            ContaReceber.nfe_id == nota.id,
                            ContaReceber.boleto_emitido.isnot(True),
                        )
                        .all()
                    )
                    for conta in contas:
                        if conta.numero_documento != str(item["numero"]):
                            print(
                                f"      conta {conta.id}: numero_documento "
                                f"{conta.numero_documento} -> {item['numero']}"
                            )
                            conta.numero_documento = str(item["numero"])
            corrigidas += 1

        # Contador: sobe ate o maior numero do estado final, nunca desce.
        max_final = max((valores[0] or 0) for valores in final.values())
        contador_atual = (empresa.ultimo_numero_nfe or 0) if empresa else 0
        print(
            f"\nContador ultimo_numero_nfe: atual {contador_atual} | "
            f"maior numero apos a correcao {max_final}"
        )
        if empresa and max_final > contador_atual:
            print(f"  {'ajustaria' if not args.apply else 'ajustando'} para {max_final}")
            if args.apply:
                empresa.ultimo_numero_nfe = max_final
        else:
            print("  mantido (nao reduz sequencia)")

        if args.apply:
            db.commit()
            print(
                f"\nCOMMIT realizado: {corrigidas} nota(s) corrigida(s), "
                f"{ignoradas} ignorada(s) por conflito."
            )
        else:
            db.rollback()
            print(
                f"\nDRY-RUN: {corrigidas} nota(s) seriam corrigidas, "
                f"{ignoradas} ignorada(s). Nada foi gravado."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()

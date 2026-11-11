"""
Reparo pontual: numeracao das NF-e 4494/4495 + vencimento da consolidacao 31.

Cenario (producao):
- A tentativa de NF-e 4494 foi REJEITADA pela SEFAZ (status "error", sem
  protocolo), mas o NotaAS gravou invoice/chave da tentativa.
- A nota autorizada em seguida ficou com numero LOCAL 4495, embora a chave de
  acesso confirme nNF=4494. Isso travava a reconciliacao e fazia o sistema
  pedir 4495 enquanto a SEFAZ/NotaAS seguia em 4494.
- O contador `empresa.ultimo_numero_nfe` esta em 4494.

O script:
1. Coloca a NF-e rejeitada em quarentena: limpa invoice/chave/protocolo da
   tentativa (ela nao autorizou nada e nao ocupa numero na SEFAZ).
2. Reconcilia pela chave de acesso a NF-e autorizada 4495 -> 4494.
3. Define o 1o vencimento da consolidacao CONS-000031 em 11/12/2026
   (igual ou posterior a nova emissao, que ocorre em 11/11/2026).
4. Liga "Gerar Boleto" na consolidacao, para a cobranca agrupada emitir UM
   boleto ao ser gerada pela tela.

Nao emite nota nem boleto. Depois, pela interface:
- NF-e rejeitada: abra a nota -> "Reenviar" -> "Transmitir". Ela assume o
  numero 4495 (proximo livre), que e o esperado pela SEFAZ/NotaAS.
- Consolidacao CONS-000031: "Gerar cobranca e boleto" (R$ 273,61 =
  NF-e 180,00 + NFSe liquida 93,61, ja descontado o ISS retido).

E idempotente: rodar de novo nao altera o que ja esta correto.

Uso (Railway):
    python scripts/reparo_consolidacao31_nfe4494.py               # dry-run
    python scripts/reparo_consolidacao31_nfe4494.py --aplicar     # grava
    python scripts/reparo_consolidacao31_nfe4494.py --aplicar --sem-boleto
"""
import argparse
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from models import Empresa, PedidoConsolidado
from models_nfe import NFe
from services.numeracao import (
    nota_rejeitada_nfe, numero_da_chave, reconciliar_numero_nfe,
)

NUMERO_REJEITADO = 4494
NUMERO_AUTORIZADO = 4495
NUMERO_CONSOLIDACAO = "CONS-000031"
NOVO_VENCIMENTO = date(2026, 12, 11)


def _fmt(valor):
    return f"{valor.strftime('%d/%m/%Y')}" if valor else "-"


def _buscar_consolidacao(db):
    cons = (
        db.query(PedidoConsolidado)
        .filter(PedidoConsolidado.numero == NUMERO_CONSOLIDACAO)
        .first()
    )
    if cons is None:
        # Fallback pelo id usado nos diagnosticos (producao e local).
        cons = db.query(PedidoConsolidado).filter(PedidoConsolidado.id == 31).first()
    return cons


def _buscar_autorizada(db, numero_alvo):
    """NF-e com protocolo cuja chave confirma `numero_alvo` mas numero local difere."""
    candidatas = (
        db.query(NFe)
        .filter(
            NFe.protocolo.isnot(None),
            NFe.chave_acesso.isnot(None),
            NFe.numero.in_([numero_alvo, NUMERO_AUTORIZADO]),
        )
        .all()
    )
    return [
        n for n in candidatas
        if numero_da_chave(n.chave_acesso) == numero_alvo and n.numero != numero_alvo
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--aplicar", action="store_true",
        help="Grava as alteracoes. Sem isto, apenas previsualiza (dry-run).",
    )
    ap.add_argument(
        "--sem-boleto", action="store_true",
        help="Nao liga 'Gerar Boleto' na consolidacao.",
    )
    args = ap.parse_args()

    db = SessionLocal()
    try:
        print(f"MODO: {'APLICAR (escrita)' if args.aplicar else 'DRY-RUN (somente leitura)'}\n")

        empresa = db.query(Empresa).first()
        if empresa is None:
            print("ERRO: nenhuma Empresa cadastrada.")
            return 1

        cons = _buscar_consolidacao(db)
        if cons is None:
            print(f"ERRO: consolidacao {NUMERO_CONSOLIDACAO} (ou id=31) nao encontrada.")
            return 1

        print(
            f"Consolidacao: {cons.numero or '#' + str(cons.id)} (id={cons.id}, "
            f"status={getattr(cons.status, 'value', cons.status)})"
        )
        print(f"Contador ultimo_numero_nfe: {empresa.ultimo_numero_nfe}\n")

        acoes = []

        # 1) Quarentena da tentativa rejeitada
        rejeitadas = [
            n for n in (cons.nfes or [])
            if nota_rejeitada_nfe(n) and n.numero == NUMERO_REJEITADO
        ]
        if not rejeitadas:
            rejeitadas = [
                n for n in (cons.nfes or [])
                if nota_rejeitada_nfe(n)
            ]
        for nota in rejeitadas:
            if nota.invoice_id or nota.chave_acesso or nota.protocolo or nota.mensagem_retorno:
                acoes.append((
                    "quarentena",
                    f"NF-e id={nota.id} (rejeitada, numero {nota.numero}): "
                    "limpar invoice/chave/protocolo/mensagem da tentativa",
                ))
            else:
                print(f"  OK: NF-e rejeitada id={nota.id} ja esta em quarentena.")

        if not rejeitadas:
            print("  AVISO: nenhuma NF-e rejeitada encontrada nesta consolidacao.")

        # 2) Reconciliacao da autorizada (chave = fonte de verdade)
        autorizadas = []
        for nota in _buscar_autorizada(db, NUMERO_REJEITADO):
            autorizadas.append(nota)
            acoes.append((
                "reconciliar",
                f"NF-e id={nota.id}: numero local {nota.numero} -> "
                f"{numero_da_chave(nota.chave_acesso)} (chave de acesso)",
            ))
        if not autorizadas:
            print(
                f"  OK: nenhuma NF-e autorizada com chave nNF={NUMERO_REJEITADO} "
                "precisando de reconciliacao."
            )

        # 3) Vencimento da consolidacao
        if cons.primeiro_vencimento != NOVO_VENCIMENTO:
            acoes.append((
                "vencimento",
                f"Consolidacao: 1o vencimento {_fmt(cons.primeiro_vencimento)} -> "
                f"{_fmt(NOVO_VENCIMENTO)}",
            ))
        else:
            print(f"  OK: 1o vencimento ja e {_fmt(NOVO_VENCIMENTO)}.")

        # 4) Boleto automatico
        if not args.sem_boleto:
            if not cons.gerar_boleto:
                acoes.append((
                    "boleto",
                    "Consolidacao: ligar 'Gerar Boleto' (emite 1 boleto na cobranca)",
                ))
            else:
                print("  OK: 'Gerar Boleto' ja esta ligado.")

        if not acoes:
            print("\nNada a corrigir. O reparo ja foi aplicado.")
            db.rollback()
            return 0

        print("\nAcoes planejadas:")
        for _, descricao in acoes:
            print(f"  - {descricao}")

        if args.aplicar:
            for nota in rejeitadas:
                nota.invoice_id = None
                nota.chave_acesso = None
                nota.protocolo = None
                nota.mensagem_retorno = None

            for nota in autorizadas:
                reconciliar_numero_nfe(
                    db, empresa, nota, numero=numero_da_chave(nota.chave_acesso)
                )

            cons.primeiro_vencimento = NOVO_VENCIMENTO
            if not args.sem_boleto:
                cons.gerar_boleto = True

            db.commit()
            print("\nCOMMIT realizado.")
        else:
            db.rollback()
            print("\nDRY-RUN: nada foi gravado (rollback).")

        # Resumo do estado final (apos commit, ou calculado no dry-run)
        nota_nfe = next((n for n in (cons.nfes or []) if n.valor_total), None)
        total = Decimal("0")
        if nota_nfe is not None:
            total += Decimal(str(nota_nfe.valor_total or 0))
        if cons.nfse is not None:
            total += Decimal(str(cons.nfse.valor_liquido or 0))

        print("\nDepois do reparo:")
        print(f"  - Proxima NF-e deve ser {NUMERO_AUTORIZADO}.")
        print(f"  - Total agrupado previsto: R$ {total:.2f}")
        print("  - Reenvie a NF-e rejeitada pela tela e depois gere a cobranca")
        print("    agrupada na consolidacao (com boleto, se habilitado).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

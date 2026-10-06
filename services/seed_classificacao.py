"""Seed e backfill da classificacao contabil automatica.

Plano de contas: garante as contas raiz minimas (3 RECEITAS / 4 DESPESAS)
quando a tabela esta vazia (instalacao nova). Em bases ja migradas, o plano
existente do usuario e preservado: nada e criado, alterado ou removido.

Conta padrao da empresa: se `empresa.conta_receita_padrao_id` estiver vazio e
existir alguma conta de receita ativa, aponta para `3.1 Venda de Produtos`
(maior volume no ramo de servicos) para que nenhuma conta a receber nasca sem
classificacao.

Backfill: classifica retroativamente as `contas_receber` com
`plano_conta_id IS NULL`, resolvendo pelos itens do documento de origem. Contas
sem origem rastreavel (importacao de boleto, assinatura) vao para a conta
padrao e ficam marcadas em `classificacao_revisar` para conference.

Idempotente: rodar de novo nao altera nada.
"""
import logging

from sqlalchemy import select, text

from database import SessionLocal
from models import ContaReceber, Empresa, PlanoDeContas
from services.classificacao_contabil import classificar_conta_existente

logger = logging.getLogger(__name__)




def _semear_plano_minimo(db):
    """Cria as contas raiz 3/RECEITAS e 4/DESPESAS, com os grupos 3.1/3.2 e
    4.1/4.2, somente quando o plano esta vazio (instalacao nova).

    Preserva integralmente qualquer plano ja cadastrado pelo usuario.
    """
    total = db.query(PlanoDeContas).count()
    if total > 0:
        return 0

    # (codigo, nome, tipo, pai)
    estrutura = (
        ("3", "RECEITAS", "receita", None),
        ("3.1", "Venda de Produtos", "receita", "3"),
        ("3.2", "Presta\u00e7\u00e3o de Servi\u00e7os", "receita", "3"),
        ("4", "DESPESAS", "despesa", None),
        ("4.1", "Custos Operacionais", "despesa", "4"),
        ("4.2", "Despesas Administrativas", "despesa", "4"),
    )

    criadas = 0
    por_codigo = {}
    for codigo, nome, tipo, pai_codigo in estrutura:
        parent_id = por_codigo.get(pai_codigo) if pai_codigo else None
        nivel = 1 if pai_codigo is None else 2
        conta = PlanoDeContas(codigo=codigo, nome=nome, tipo=tipo,
                              parent_id=parent_id, nivel=nivel, ativo=True)
        db.add(conta)
        db.flush()  # garante o id para os filhos
        por_codigo[codigo] = conta.id
        criadas += 1
    db.commit()
    logger.info("[MIGRATION] plano_contas vazio: criadas %d contas base", criadas)
    return criadas

    criadas = 0
    for codigo, nome, tipo in CONTAS_RAIZ:
        db.add(PlanoDeContas(codigo=codigo, nome=nome, tipo=tipo,
                             parent_id=None, nivel=1, ativo=True))
        criadas += 1
    db.commit()
    logger.info("[MIGRATION] plano_contas vazio: criadas %d contas raiz", criadas)
    return criadas


def _configurar_conta_padrao(db):
    """Aponta empresa.conta_receita_padrao_id para uma conta de receita ativa,
    se ainda nao configurada. Nunca sobrescreve escolha do usuario."""
    empresa = db.query(Empresa).order_by(Empresa.id).first()
    if empresa is None or empresa.conta_receita_padrao_id:
        return None

    conta = (
        db.query(PlanoDeContas)
        .filter(PlanoDeContas.tipo == "receita",
                PlanoDeContas.ativo == True,
                PlanoDeContas.nivel >= 2)
        .order_by(PlanoDeContas.codigo)
        .first()
    )
    if conta is None:
        conta = (
            db.query(PlanoDeContas)
            .filter(PlanoDeContas.tipo == "receita", PlanoDeContas.ativo == True)
            .order_by(PlanoDeContas.codigo)
            .first()
        )
    if conta is None:
        return None

    empresa.conta_receita_padrao_id = conta.id
    db.commit()
    logger.info("[MIGRATION] conta_receita_padrao_id = %s (%s)",
                conta.codigo, conta.nome)
    return conta.id


def _backfill_classificacao(db, lote=500):
    """Classifica as contas a receber sem conta de receita.

    Resolve pelos itens do documento de origem; quando nao ha origem (boleto
    importado, assinatura) ou a origem e ambigua (nota mista), usa a conta
    padrao e marca `classificacao_revisar` para conference manual.
    """
    total = (
        db.query(ContaReceber)
        .filter(ContaReceber.plano_conta_id.is_(None))
        .count()
    )
    if total == 0:
        return 0

    classificadas = 0
    marcadas = 0
    while True:
        lote_atual = (
            db.query(ContaReceber)
            .filter(ContaReceber.plano_conta_id.is_(None))
            .order_by(ContaReceber.id)
            .limit(lote)
            .all()
        )
        if not lote_atual:
            break

        for conta in lote_atual:
            if classificar_conta_existente(db, conta):
                classificadas += 1
                if conta.classificacao_revisar:
                    marcadas += 1

        db.commit()
        if len(lote_atual) < lote:
            break

    nao_classificadas = total - classificadas
    logger.info(
        "[MIGRATION] backfill de classificacao: %d/%d contas classificadas "
        "(%d marcadas para revisao, %d sem conta resolvivel)",
        classificadas, total, marcadas, nao_classificadas,
    )
    return classificadas


def seed_classificacao_contabil():
    """Ponto de entrada idempotente, chamada no startup do app."""
    db = SessionLocal()
    try:
        _semear_plano_minimo(db)
        _configurar_conta_padrao(db)
        _backfill_classificacao(db)
    except Exception as e:
        db.rollback()
        logger.warning("[MIGRATION] Nao foi possivel preparar a classificacao contabil: %s", e)
    finally:
        db.close()
"""Classificacao contabil automatica de contas a receber.

Antes deste modulo, `ContaReceber.plano_conta_id` era preenchido apenas pelas
duas telas manuais de `/contas/receber`. Todas as 14 geracoes automaticas
(NF-e, NFS-e, OS, pedido, consolidacao, assinatura, importacao de boleto)
nasciam com o campo NULL, e o DRE concentrava tudo na linha sintetica
"Sem Classificação" (routers/contas.py:984).

A correcao nao e editar os 14 call sites: e resolver a conta uma unica vez,
aqui, usando as FKs de origem que a propria `ContaReceber` ja carrega
(`nfe_id`, `nfse_id`, `pedido_id`, `os_id`).

Cascata (primeiro que resolver vence):

  1. explicito   -- parametro `plano_conta_id` (telas manuais mandam)
  2. documento   -- conta dos itens do documento (NF-e/NFS-e/pedido/OS),
                    quando todos os itens apontam para a mesma conta
  3. fallback    -- `empresa.conta_receita_padrao_id`

Por que o passo 2 exige unanimidade: uma nota pode misturar produtos e servicos
com contas de receita diferentes, e a `ContaReceber` atual e 1:N por valor total
(nao ha rateio por linha). Escolher a conta majoritaria distorceria o DRE, entao
nota mista cai no fallback e e marcada para revisao.

A conta de receita mora no cadastro do produto/servico (`produtos.conta_receita_id`),
nao no titulo financeiro, porque o mesmo produto/servico e vendido por varios
tipos de documento e e o produto que define para onde a receita vai.
"""
import logging

import sqlalchemy as sa

from models import Empresa, PlanoDeContas, Produto

logger = logging.getLogger(__name__)


def _conta_padrao_empresa(db):
    """Conta de receita padrao configurada em `empresa` (fallback da cascata)."""
    try:
        empresa = db.query(Empresa).order_by(Empresa.id).first()
    except Exception as e:  # tabela pode nao existir em base Antiga
        logger.debug("[classificacao] empresa indisponivel: %s", e)
        return None
    if empresa is None:
        return None
    return _conta_valida(db, empresa.conta_receita_padrao_id)


def _conta_valida(db, conta_id):
    """Retorna o id somente se a conta existe e esta ativa.

    Conta inativa nao pode ser usada para classificacao nova: o DRE filtra
    `ativo == True` (routers/contas.py:955), entao classificar em conta
    desativada esconderia o valor do relatorio.
    """
    if not conta_id:
        return None
    return db.query(PlanoDeContas.id).filter(
        PlanoDeContas.id == conta_id,
        PlanoDeContas.ativo == True,
    ).scalar()


def _coletar_produtos(db, tabela, coluna_doc, doc_id):
    """Acumula os produto_id dos itens de um documento.

    Usa SQLAlchemy Core (tabela/coluna), nao as classes ORM: os mappers de
    `models_nfe` declaram relationships para `PedidoVenda`/`PedidoConsolidado`,
    que vivem em `models.py`. Se `models_nfe` for importado isoladamente, o
    ORM falha ao configurar os mappers; o Core nao depende de mapper e funciona.
    """
    if not doc_id:
        return set()
    produto_ids = set()
    try:
        t = sa.table(tabela, sa.column("produto_id"), sa.column(coluna_doc))
        q = (
            sa.select(t.c.produto_id)
            .where(t.c.produto_id.isnot(None), t.c[coluna_doc] == doc_id)
            .limit(5000)
        )
        for (pid,) in db.execute(q).all():
            produto_ids.add(pid)
    except Exception as e:
        logger.debug("[classificacao] itens %s.%s inacessiveis: %s",
                     tabela, coluna_doc, e)
    return produto_ids


def _coletar_itens(db, *, nfe_id=None, nfse_id=None, pedido_id=None, os_id=None):
    """Produto_ids de todos os itens do documento de origem.

    OS nao tem tabela de itens: as pecas ficam em `os_pecas` e os servicos sao
    gravados em campos de valor agregado na propria OS (`valor_servico` /
    `valor_pecas`), sem referencia a produto. Para OS ficamos com as pecas.
    """
    produto_ids = set()
    produto_ids |= _coletar_produtos(db, "nfe_itens", "nfe_id", nfe_id)
    produto_ids |= _coletar_produtos(db, "nfse_itens", "nfse_id", nfse_id)
    produto_ids |= _coletar_produtos(db, "pedidos_venda_itens", "pedido_id", pedido_id)
    produto_ids |= _coletar_produtos(db, "os_pecas", "os_id", os_id)
    return produto_ids


def _contas_dos_itens(db, *, nfe_id=None, nfse_id=None, pedido_id=None, os_id=None):
    """Contas de receita distintas dos produtos dos itens do documento."""
    produto_ids = _coletar_itens(
        db, nfe_id=nfe_id, nfse_id=nfse_id, pedido_id=pedido_id, os_id=os_id
    )
    if not produto_ids:
        return set()

    t = sa.table("produtos", sa.column("id"), sa.column("conta_receita_id"))
    try:
        rows = db.execute(
            sa.select(sa.distinct(t.c.conta_receita_id)).where(
                t.c.id.in_(produto_ids),
                t.c.conta_receita_id.isnot(None),
            )
        ).all()
        return {r[0] for r in rows if r[0]}
    except Exception as e:
        logger.debug("[classificacao] produtos inacessiveis: %s", e)
        return set()


def resolver_conta_receita(
    db,
    *,
    plano_conta_id=None,
    nfe_id=None,
    nfse_id=None,
    pedido_id=None,
    os_id=None,
    cliente_id=None,
):
    """Resolve a conta de receita de uma conta a receber.

    Retorna `(conta_id, revisar)` onde `revisar` indica que o resultado veio do
    fallback e merece conference manual na tela de contas a receber.

    Nunca levanta excecao: a classificacao contabil nao pode derrubar o
    faturamento. Se tudo falhar, devolve `(None, True)`.
    """
    try:
        explicita = _conta_valida(db, plano_conta_id)
        if explicita:
            return explicita, False

        # Documento com itens apontando todos para a mesma conta: confia na
        # origem. Nota mista (mais de uma conta) cai no fallback.
        if nfe_id or nfse_id or pedido_id or os_id:
            contas = _contas_dos_itens(
                db, nfe_id=nfe_id, nfse_id=nfse_id, pedido_id=pedido_id, os_id=os_id
            )
            if len(contas) == 1:
                resolvida = _conta_valida(db, next(iter(contas)))
                if resolvida:
                    return resolvida, False

        padrao = _conta_padrao_empresa(db)
        if padrao:
            return padrao, True

        logger.warning(
            "[classificacao] sem conta de receita para nfe=%s nfse=%s pedido=%s os=%s: "
            "cadastre a conta em produtos.conta_receita_id ou empresa.conta_receita_padrao_id",
            nfe_id, nfse_id, pedido_id, os_id,
        )
        return None, True
    except Exception as e:
        logger.exception("[classificacao] falha ao resolver conta de receita: %s", e)
        return None, True


def _conta_receita_padrao_servicos(db):
    """Conta de receita do ramo de servicos (`3.2`), usada por mensalidades."""
    conta = db.query(PlanoDeContas).filter(
        PlanoDeContas.codigo == "3.2", PlanoDeContas.ativo == True
    ).first()
    return conta.id if conta else None


def _conta_do_produto(db, produto_id):
    """Conta de receita de um produto, validada (None se ausente/inativa)."""
    if not produto_id:
        return None
    t = sa.table("produtos", sa.column("id"), sa.column("conta_receita_id"))
    try:
        conta_id = db.execute(
            sa.select(t.c.conta_receita_id).where(t.c.id == produto_id)
        ).scalar()
    except Exception:
        return None
    return _conta_valida(db, conta_id)


def resolver_conta_assinatura(db, assinatura_id=None):
    """Conta de receita da cobranca recorrente de assinatura.

    A assinatura tem `produto_id` (models.py:294) e esse produto ja carrega a
    conta de receita do servico contratado. Sem vinculo, cai em `3.2 Prestacao
    de Servicos`, ramo natural das mensalidades.
    """
    try:
        if assinatura_id:
            t = sa.table("assinaturas", sa.column("id"), sa.column("produto_id"))
            produto_id = db.execute(
                sa.select(t.c.produto_id).where(t.c.id == assinatura_id)
            ).scalar()
            conta = _conta_do_produto(db, produto_id)
            if conta:
                return conta, False
        padrao = _conta_receita_padrao_servicos(db) or _conta_padrao_empresa(db)
        return padrao, padrao is not None
    except Exception as e:
        logger.debug("[classificacao] assinatura %s inacessivel: %s", assinatura_id, e)
        return None, True


def resolver_conta_boleto(db):
    """Conta de receita de boleto importado do Sicoob.

    O boleto chega sem documento de origem (`nfe_id`/`nfse_id`/`pedido_id`/
    `os_id` nulos), entao usa a conta padrao da empresa.
    """
    return _conta_padrao_empresa(db), True


def classificar_conta_existente(db, conta):
    """Aplica a resolucao em uma `ContaReceber` ja persistida (backfill).

    Nao faz commit. Retorna True se classificou.
    """
    if conta.plano_conta_id:
        return False

    tem_origem = any((conta.nfe_id, conta.nfse_id, conta.pedido_id, conta.os_id))
    if tem_origem:
        conta_id, revisar = resolver_conta_receita(
            db,
            nfe_id=conta.nfe_id,
            nfse_id=conta.nfse_id,
            pedido_id=conta.pedido_id,
            os_id=conta.os_id,
        )
    elif _eh_cobranca_de_assinatura(conta):
        conta_id, revisar = resolver_conta_assinatura(db)
    else:
        conta_id, revisar = resolver_conta_boleto(db)

    if not conta_id:
        return False
    conta.plano_conta_id = conta_id
    if revisar:
        conta.classificacao_revisar = True
    return True


# Marcador gravado por routers/assinaturas.py ao criar a cobranca recorrente.
_MARCADOR_ASSINATURA = "cobranca automatica - assinatura"


def _eh_cobranca_de_assinatura(conta):
    """Distingue a mensalidade de assinatura da importacao de boleto Sicoob.

    Ambas nascem sem documento de origem, mas a assinatura carrega um marcador
    em `observacao`. Nao se pode inferir pela descricao: o texto e livre e tanto
    "Mensalidade de sistema" quanto "Boleto Sicoob - 123" aparecem ali.
    """
    return _MARCADOR_ASSINATURA in (conta.observacao or "").lower()
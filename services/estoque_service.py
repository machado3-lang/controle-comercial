"""Servico de movimentacao de estoque.

Regra central: o saldo do produto eh SEMPRE atualizado por uma
MovimentacaoEstoque (nunca editado direto). Todo lancamento eh idempotente
por (doc_tipo, doc_id, produto_id, tipo) para nao duplicar ao reemitir notas.
"""
import logging
from decimal import Decimal
from database import SessionLocal
from models_estoque import MovimentacaoEstoque

logger = logging.getLogger(__name__)


def _ja_lancado(db, doc_tipo, doc_id, produto_id, tipo):
    return db.query(MovimentacaoEstoque).filter(
        MovimentacaoEstoque.doc_tipo == doc_tipo,
        MovimentacaoEstoque.doc_id == doc_id,
        MovimentacaoEstoque.produto_id == produto_id,
        MovimentacaoEstoque.tipo == tipo,
    ).first() is not None


def lancar_movimentacao(
    db,
    produto_id: int,
    tipo: str,
    quantidade: float,
    doc_tipo: str = None,
    doc_id: int = None,
    usuario_id: int = None,
    motivo: str = None,
    deposito_id: int = None,
) -> bool:
    """Lanca uma movimentacao e atualiza o saldo do produto.

    Retorna False se ja existir lancamento identico (idempotencia).
    """
    from models import Produto

    if quantidade == 0:
        return False

    if _ja_lancado(db, doc_tipo, doc_id, produto_id, tipo):
        return False

    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if not produto:
        return False

    saldo_antes = float(produto.estoque or 0)
    saldo_apos = saldo_antes + float(quantidade)

    mov = MovimentacaoEstoque(
        produto_id=produto_id,
        tipo=tipo,
        quantidade=float(quantidade),
        doc_tipo=doc_tipo,
        doc_id=doc_id,
        usuario_id=usuario_id,
        motivo=motivo,
        deposito_id=deposito_id,
        saldo_apos=saldo_apos,
    )
    db.add(mov)
    produto.estoque = saldo_apos
    db.commit()
    return True


def baixar_por_itens(db, itens, tipo_saida, doc_tipo, doc_id, usuario_id=None):
    """Baixa estoque para uma lista de itens {produto_id, quantidade}.

    `tipo_saida` eh SAIDA_VENDA ou SAIDA_INSUMO. Ignora itens sem produto_id.
    """
    for item in itens:
        pid = item.get("produto_id") if isinstance(item, dict) else getattr(item, "produto_id", None)
        qtd = item.get("quantidade") if isinstance(item, dict) else getattr(item, "quantidade", None)
        if not pid or not qtd:
            continue
        lancar_movimentacao(
            db, produto_id=pid, tipo=tipo_saida,
            quantidade=-float(qtd), doc_tipo=doc_tipo, doc_id=doc_id,
            usuario_id=usuario_id,
        )


def ajuste_inventario(db, produto_id, quantidade_fisica, usuario_id=None, motivo="Ajuste de inventario"):
    """Ajusta o saldo do produto para a quantidade fisica contada."""
    from models import Produto
    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if not produto:
        return False
    saldo_atual = float(produto.estoque or 0)
    diferenca = float(quantidade_fisica) - saldo_atual
    if diferenca == 0:
        return False
    tipo = "AJUSTE_POS" if diferenca > 0 else "AJUSTE_NEG"
    return lancar_movimentacao(
        db, produto_id=produto_id, tipo=tipo,
        quantidade=diferenca, doc_tipo="ajuste", doc_id=produto_id,
        usuario_id=usuario_id, motivo=motivo,
    )


def baixar_nfse(db, nfse, usuario_id=None):
    """NFSe autorizada: baixa como SAIDA_INSUMO os insumos consumidos.

    - Itens marcados como insumo (produto.eh_insumo=True): baixa direta (compatibilidade).
    - Servicos (e kits) com composicao (ProdutoComposicao): explode os insumos
      vinculados e baixa cada um em SAIDA_INSUMO, na proporcao da quantidade do servico.
    Mercadorias vendidas a parte (tipo='produto') nao entram na NFSe e sao baixadas
    como SAIDA_VENDA pela baixar_nfe.
    Se a NFSe veio de uma OS, tambem baixa as pecas vinculadas (os_pecas)."""
    from models import Produto
    for item in nfse.itens:
        if not item.produto_id:
            continue
        produto = db.query(Produto).filter(Produto.id == item.produto_id).first()
        if not produto:
            continue
        qtd_item = float(item.quantidade or 1)
        # Insumo direto (flag eh_insumo) — mantido para compatibilidade
        if getattr(produto, "eh_insumo", False):
            lancar_movimentacao(
                db, produto_id=produto.id, tipo="SAIDA_INSUMO",
                quantidade=-qtd_item,
                doc_tipo="nfse", doc_id=nfse.id, usuario_id=usuario_id,
                motivo=f"Consumo NFSe #{nfse.numero or nfse.id}",
            )
        # Insumos vinculados ao servico/kit (ProdutoComposicao)
        for comp in produto.composicoes:
            insumo = comp.insumo
            if not insumo:
                continue
            qtd = (comp.quantidade_padrao or 1) * qtd_item
            lancar_movimentacao(
                db, produto_id=insumo.id, tipo="SAIDA_INSUMO",
                quantidade=-float(qtd),
                doc_tipo="nfse", doc_id=nfse.id, usuario_id=usuario_id,
                motivo=f"Consumo NFSe #{nfse.numero or nfse.id} (servico: {produto.nome})",
            )
    if getattr(nfse, "os_id", None):
        try:
            baixar_os_pecas(db, nfse.os_id, usuario_id=usuario_id)
        except Exception as e:
            logger.warning(f"Erro ao baixar pecas da OS via NFSe: {e}")


def baixar_nfe(db, nfe, usuario_id=None):
    """NFe emitida: baixa como SAIDA_VENDA os itens com produto_id."""
    from models_nfe import NFeItem
    itens = db.query(NFeItem).filter(NFeItem.nfe_id == nfe.id).all()
    for item in itens:
        if not item.produto_id:
            continue
        lancar_movimentacao(
            db, produto_id=item.produto_id, tipo="SAIDA_VENDA",
            quantidade=-float(item.quantidade or 0),
            doc_tipo="nfe", doc_id=nfe.id, usuario_id=usuario_id,
            motivo=f"Venda NFe #{nfe.numero}",
        )


def baixar_pedido(db, pedido, usuario_id=None):
    """Pedido efetivado (sem nota): baixa como SAIDA_VENDA os itens com produto_id."""
    from models import PedidoVendaItem
    itens = db.query(PedidoVendaItem).filter(PedidoVendaItem.pedido_id == pedido.id).all()
    for item in itens:
        if not item.produto_id:
            continue
        lancar_movimentacao(
            db, produto_id=item.produto_id, tipo="SAIDA_VENDA",
            quantidade=-float(item.quantidade or 0),
            doc_tipo="pedido", doc_id=pedido.id, usuario_id=usuario_id,
            motivo=f"Venda Pedido #{pedido.numero or pedido.id}",
        )


def baixar_os_pecas(db, os_id, usuario_id=None):
    """OS: baixa as pecas vinculadas (os_pecas). Insumo -> SAIDA_INSUMO;
    mercadoria -> SAIDA_VENDA."""
    from models_estoque import OSPeca
    from models import Produto
    pecas = db.query(OSPeca).filter(OSPeca.os_id == os_id).all()
    for p in pecas:
        produto = db.query(Produto).filter(Produto.id == p.produto_id).first()
        if not produto:
            continue
        tipo = "SAIDA_INSUMO" if getattr(produto, "eh_insumo", False) else "SAIDA_VENDA"
        lancar_movimentacao(
            db, produto_id=produto.id, tipo=tipo,
            quantidade=-float(p.quantidade or 0),
            doc_tipo="os", doc_id=os_id, usuario_id=usuario_id,
            motivo=f"Peca OS #{os_id}",
        )


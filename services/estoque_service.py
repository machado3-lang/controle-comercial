"""Servico de movimentacao de estoque.

Regra central: o saldo do produto eh SEMPRE atualizado por uma
MovimentacaoEstoque (nunca editado direto). Todo lancamento eh idempotente
por (doc_tipo, doc_id, produto_id, tipo) para nao duplicar ao reemitir notas.
"""
import json
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


def _pedido_ja_baixado(db, pedido_id):
    """Verifica se o pedido já teve baixa de estoque — seja pela finalização
    (doc_tipo='pedido'), seja por NF emitida (doc_tipo='nfe'/'nfse' vinculada
    ao pedido). Evita a dupla baixa quando o usuário finaliza o pedido e depois
    emite a nota (ou vice-versa)."""
    if not pedido_id:
        return False
    from models_nfe import NFe, NFSe
    nfe_ids = [n.id for n in db.query(NFe.id).filter(NFe.pedido_id == pedido_id).all()]
    nfse_ids = [n.id for n in db.query(NFSe.id).filter(NFSe.pedido_id == pedido_id).all()]
    from sqlalchemy import or_
    return db.query(MovimentacaoEstoque.id).filter(
        MovimentacaoEstoque.tipo.in_(["SAIDA_VENDA", "SAIDA_INSUMO"]),
        or_(
            (MovimentacaoEstoque.doc_tipo == "pedido") & (MovimentacaoEstoque.doc_id == pedido_id),
            (MovimentacaoEstoque.doc_tipo == "nfe") & (MovimentacaoEstoque.doc_id.in_(nfe_ids or [-1])),
            (MovimentacaoEstoque.doc_tipo == "nfse") & (MovimentacaoEstoque.doc_id.in_(nfse_ids or [-1])),
        ),
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
    variacao_id: int = None,
    idempotente: bool = True,
) -> bool:
    """Lanca uma movimentacao e atualiza o saldo do produto.

    Retorna False se quantidade 0 ou (quando idempotente) se ja existir
    lancamento identico.

    Se variacao_id for informado, a incidencia recai na variacao e o saldo do
    pai (Produto.estoque) passa a ser a soma das variacoes.
    """
    from models import Produto, ProdutoVariacao
    from sqlalchemy import func

    if quantidade == 0:
        return False

    if idempotente and _ja_lancado(db, doc_tipo, doc_id, produto_id, tipo):
        return False

    produto = db.query(Produto).filter(Produto.id == produto_id).first()
    if not produto:
        return False

    if variacao_id:
        variacao = db.query(ProdutoVariacao).filter(
            ProdutoVariacao.id == variacao_id,
            ProdutoVariacao.produto_id == produto_id,
        ).first()
        if variacao:
            variacao.estoque_atual = float(variacao.estoque_atual or 0) + float(quantidade)
        produto.estoque = db.query(func.sum(ProdutoVariacao.estoque_atual)).filter(
            ProdutoVariacao.produto_id == produto_id
        ).scalar() or 0
    else:
        saldo_antes = float(produto.estoque or 0)
        produto.estoque = saldo_antes + float(quantidade)

    mov = MovimentacaoEstoque(
        produto_id=produto_id,
        tipo=tipo,
        quantidade=float(quantidade),
        doc_tipo=doc_tipo,
        doc_id=doc_id,
        usuario_id=usuario_id,
        motivo=motivo,
        deposito_id=deposito_id,
        saldo_apos=produto.estoque,
    )
    db.add(mov)
    db.commit()
    return True


def baixar_por_itens(db, itens, tipo_saida, doc_tipo, doc_id, usuario_id=None):
    """Baixa estoque para uma lista de itens {produto_id, quantidade, variacao_id?}.

    `tipo_saida` eh SAIDA_VENDA ou SAIDA_INSUMO. Ignora itens sem produto_id.
    """
    for item in itens:
        pid = item.get("produto_id") if isinstance(item, dict) else getattr(item, "produto_id", None)
        qtd = item.get("quantidade") if isinstance(item, dict) else getattr(item, "quantidade", None)
        vid = item.get("variacao_id") if isinstance(item, dict) else getattr(item, "variacao_id", None)
        if not pid or not qtd:
            continue
        lancar_movimentacao(
            db, produto_id=pid, tipo=tipo_saida,
            quantidade=-float(qtd), doc_tipo=doc_tipo, doc_id=doc_id,
            usuario_id=usuario_id, variacao_id=vid,
        )


def ajuste_inventario(db, produto_id, quantidade_fisica, usuario_id=None, motivo="Ajuste de inventario"):
    """Ajusta o saldo do produto (sem variacoes) para a quantidade fisica contada.

    Ajustes nao sao idempotentes: cada contagem fisica eh um evento distinto.
    """
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
        usuario_id=usuario_id, motivo=motivo, idempotente=False,
    )


def ajustar_variacao(db, produto_id, variacao_id, quantidade_fisica, usuario_id=None, motivo="Ajuste de inventario"):
    """Ajusta o estoque de uma variacao especifica para a quantidade fisica contada.

    O saldo do pai (Produto.estoque) passa a ser a soma das variacoes.
    """
    from models import Produto, ProdutoVariacao
    variacao = db.query(ProdutoVariacao).filter(
        ProdutoVariacao.id == variacao_id,
        ProdutoVariacao.produto_id == produto_id,
    ).first()
    if not variacao:
        return False
    saldo_atual = float(variacao.estoque_atual or 0)
    diferenca = float(quantidade_fisica) - saldo_atual
    if diferenca == 0:
        return False
    tipo = "AJUSTE_POS" if diferenca > 0 else "AJUSTE_NEG"
    return lancar_movimentacao(
        db, produto_id=produto_id, tipo=tipo,
        quantidade=diferenca, doc_tipo="ajuste", doc_id=produto_id,
        usuario_id=usuario_id, motivo=motivo, variacao_id=variacao_id, idempotente=False,
    )


def baixar_nfse(db, nfse, usuario_id=None):
    """NFSe autorizada: baixa como SAIDA_INSUMO os insumos consumidos.

    - Itens marcados como insumo (produto.eh_insumo=True): baixa direta (compatibilidade).
    - Servicos (e kits) com composicao (ProdutoComposicao): explode os insumos
      vinculados e baixa cada um em SAIDA_INSUMO, na proporcao da quantidade do servico.
    Mercadorias vendidas a parte (tipo='produto') nao entram na NFSe e sao baixadas
    como SAIDA_VENDA pela baixar_nfe.
    Se a NFSe veio de uma OS, tambem baixa as pecas vinculadas (os_pecas)."""
    # Idempotente: se o pedido já foi baixado (pela finalização ou por outra
    # NF), não baixa de novo — evita dupla baixa de estoque.
    if getattr(nfse, "pedido_id", None) and _pedido_ja_baixado(db, nfse.pedido_id):
        logger.info(
            "Pedido %s já teve baixa de estoque; ignorando re-baixa da NFSe %s",
            nfse.pedido_id, nfse.id,
        )
        return
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
                variacao_id=item.variacao_id,
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
    # Idempotente: se o pedido já foi baixado (pela finalização ou por outra
    # NF), não baixa de novo — evita dupla baixa de estoque.
    if getattr(nfe, "pedido_id", None) and _pedido_ja_baixado(db, nfe.pedido_id):
        logger.info(
            "Pedido %s já teve baixa de estoque; ignorando re-baixa da NFe %s",
            nfe.pedido_id, nfe.id,
        )
        return
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
            variacao_id=item.variacao_id,
        )


def baixar_pedido(db, pedido, usuario_id=None):
    """Pedido efetivado (sem nota): baixa como SAIDA_VENDA os itens com produto_id."""
    # Idempotente: não baixa se o pedido já teve saída (pela finalização ou
    # por NF emitida depois) — evita dupla baixa de estoque.
    if _pedido_ja_baixado(db, pedido.id):
        logger.info("Pedido %s já teve baixa de estoque; ignorando re-baixa", pedido.id)
        return
    from models import PedidoVendaItem
    itens = db.query(PedidoVendaItem).filter(PedidoVendaItem.pedido_id == pedido.id).all()
    for item in itens:
        if not item.produto_id:
            continue
        produto = item.produto
        # Kit e agregacao: a baixa e feita pelos insumos (filhos), nao pelo pai
        if produto and produto.tipo == "kit":
            continue
        lancar_movimentacao(
            db, produto_id=item.produto_id, tipo="SAIDA_VENDA",
            quantidade=-float(item.quantidade or 0),
            doc_tipo="pedido", doc_id=pedido.id, usuario_id=usuario_id,
            motivo=f"Venda Pedido #{pedido.numero or pedido.id}",
            variacao_id=item.variacao_id,
        )


def baixar_os_pecas(db, os_id, usuario_id=None):
    """OS: baixa as pecas vinculadas. Prioriza os registros estruturados
    (OSPeca); se nao houver nenhum, usa o JSON pecas_utilizadas da OS.
    Insumo -> SAIDA_INSUMO; mercadoria -> SAIDA_VENDA."""
    from models_estoque import OSPeca
    from models import Produto, OrdemServico
    pecas = db.query(OSPeca).filter(OSPeca.os_id == os_id).all()
    if not pecas:
        os = db.query(OrdemServico).filter(OrdemServico.id == os_id).first()
        if os and os.pecas_utilizadas:
            try:
                dados = json.loads(os.pecas_utilizadas)
                if isinstance(dados, list):
                    pecas = [
                        type("P", (), {"produto_id": p.get("id"), "quantidade": p.get("qtd", 1)})()
                        for p in dados if p.get("id")
                    ]
            except (json.JSONDecodeError, TypeError):
                pecas = []
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


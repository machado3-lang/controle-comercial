from database import get_db
from models import PedidoVenda, PedidoConsolidado, Cliente, Produto, StatusPedido, StatusConsolidacao, FormaPagamento
from datetime import date
db = next(get_db())
try:
    # Check existing pedidos
    pedidos = db.query(PedidoVenda).filter(
        PedidoVenda.status.in_([StatusPedido.PRE_VENDA, StatusPedido.PENDENTE]),
        PedidoVenda.consolidacao_id.is_(None)
    ).all()
    print(f'Pedidos disponíveis para consolidação: {len(pedidos)}')
    for p in pedidos:
        cliente_nome = p.cliente.nome if p.cliente else 'N/A'
        print(f'  #{p.id} {p.numero} - Cliente: {cliente_nome} - Status: {p.status} - Total: {p.total} - Itens: {len(p.itens)}')
        for item in p.itens:
            prod = item.produto
            tipo = prod.tipo if prod else 'N/A'
            print(f'    Item: {prod.nome if prod else "N/A"} - Tipo: {tipo} - Qtd: {item.quantidade} - Preço: {item.preco_unitario} - Total: {item.total}')
finally:
    db.close()
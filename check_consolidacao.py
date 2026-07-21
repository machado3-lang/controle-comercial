from database import get_db
from models import PedidoConsolidado, StatusConsolidacao
db = next(get_db())
try:
    cons = db.query(PedidoConsolidado).filter(PedidoConsolidado.id == 7).first()
    if cons:
        print('Consolidacao: ' + str(cons.numero))
        print('Status: ' + str(cons.status))
        cliente_nome = cons.cliente.nome if cons.cliente else 'N/A'
        print('Cliente: ' + cliente_nome)
        print('Total: ' + str(cons.total))
        print('Itens: ' + str(len(cons.itens)))
        for item in cons.itens:
            prod = item.produto
            tipo = prod.tipo if prod else 'N/A'
            lc116 = prod.codigo_lc116 if prod else 'N/A'
            print('  - ' + str(item.descricao) + ' | Qtd: ' + str(item.quantidade) + ' | Preco: ' + str(item.preco_unitario) + ' | Total: ' + str(item.total) + ' | Tipo: ' + str(tipo) + ' | LC116: ' + str(lc116))
finally:
    db.close()
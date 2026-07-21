from database import get_db
from models import PedidoVenda, PedidoVendaItem, Cliente, Produto, Empresa, StatusPedido, StatusConsolidacao, FormaPagamento
from sqlalchemy import func
from datetime import date

db = next(get_db())
try:
    # Get a client and a service product
    cliente = db.query(Cliente).first()
    servico = db.query(Produto).filter(Produto.tipo == 'servico').first()
    
    print('Cliente: ' + (cliente.nome if cliente else 'None'))
    print('Serviço: ' + (servico.nome if servico else 'None') + ' - LC116: ' + (servico.codigo_lc116 if servico else 'N/A'))
    
    # Create a test pedido with service items
    empresa = db.query(Empresa).first()
    if empresa:
        empresa.ultimo_numero_pedido = (empresa.ultimo_numero_pedido or 0) + 1
        numero = str(empresa.ultimo_numero_pedido)
    else:
        ultimo_numero = db.query(func.max(PedidoVenda.numero)).scalar()
        ultimo_val = int(ultimo_numero) if ultimo_numero else 0
        numero = str(ultimo_val + 1)
    
    pedido = PedidoVenda(
        cliente_id=cliente.id,
        numero=numero,
        data=date.today(),
        status=StatusPedido.PENDENTE,
        tipo_pedido='venda',
        forma_pagamento=FormaPagamento.AVISTA,
    )
    db.add(pedido)
    db.flush()
    
    item = PedidoVendaItem(
        pedido_id=pedido.id,
        produto_id=servico.id,
        descricao=servico.nome,
        quantidade=1,
        preco_unitario=servico.preco,
        total=servico.preco,
        fornecedor_id=servico.fornecedor_id
    )
    db.add(item)
    pedido.total = servico.preco
    db.commit()
    
    print('Criado pedido #' + str(pedido.id) + ' - Numero: ' + numero + ' - Total: ' + str(pedido.total))
finally:
    db.close()
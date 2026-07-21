from database import get_db
from models import PedidoVenda, PedidoConsolidado, PedidoConsolidadoItem, PedidoConsolidadoItemOrigem, Cliente, Produto, Empresa, StatusPedido, StatusConsolidacao, FormaPagamento
from sqlalchemy import func
from datetime import date
from decimal import Decimal

db = next(get_db())
try:
    # Get the pending pedidos for the same client
    pedidos = db.query(PedidoVenda).filter(
        PedidoVenda.status == StatusPedido.PENDENTE,
        PedidoVenda.consolidacao_id.is_(None)
    ).all()
    
    print('Pedidos disponíveis: ' + str(len(pedidos)))
    for p in pedidos:
        print('  #' + str(p.id) + ' - ' + (p.numero or 'N/A') + ' - Cliente: ' + (p.cliente.nome if p.cliente else 'N/A') + ' - Total: ' + str(p.total))
    
    if not pedidos:
        print('Nenhum pedido disponível para consolidar')
    else:
        # Create a consolidation
        cliente_id = pedidos[0].cliente_id
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        
        # Generate number
        empresa = db.query(Empresa).first()
        if empresa:
            empresa.ultimo_numero_pedido = (empresa.ultimo_numero_pedido or 0) + 1
            numero = 'CONS-' + str(empresa.ultimo_numero_pedido).zfill(6)
        else:
            ultimo = db.query(func.max(PedidoConsolidado.numero)).scalar()
            num = int(ultimo.split('-')[-1]) + 1 if ultimo else 1
            numero = 'CONS-' + str(num).zfill(6)
        
        consolidacao = PedidoConsolidado(
            numero=numero,
            data=date.today(),
            data_fechamento=date.today(),
            cliente_id=cliente_id,
            status=StatusConsolidacao.CONCLUIDO,  # Already finalized
            forma_pagamento=FormaPagamento.AVISTA,
            gerar_boleto=False,
            observacao='Consolidação de teste para NFSe',
            periodo_inicio=date.today(),
            periodo_fim=date.today(),
            finalizado_at=date.today()
        )
        db.add(consolidacao)
        db.flush()
        
        # Aggregate items
        itens_agregados = {}
        total_consolidado = Decimal('0')
        
        for pedido in pedidos:
            for item in pedido.itens:
                key = (item.produto_id, item.variacao_id, item.descricao, item.preco_unitario)
                if key not in itens_agregados:
                    itens_agregados[key] = {
                        'produto_id': item.produto_id,
                        'variacao_id': item.variacao_id,
                        'descricao': item.descricao,
                        'quantidade': Decimal('0'),
                        'preco_unitario': item.preco_unitario,
                        'total': Decimal('0'),
                        'unidade': 'UN',
                        'ncm': None,
                        'cfop': None,
                        'origens': [],
                    }
                agg = itens_agregados[key]
                agg['quantidade'] += Decimal(str(item.quantidade))
                agg['total'] += item.total or Decimal('0')
                agg['origens'].append({
                    'pedido_id': pedido.id,
                    'item_id': item.id,
                    'quantidade': Decimal(str(item.quantidade)),
                    'preco_unitario': item.preco_unitario,
                    'total': item.total or Decimal('0'),
                })
            # Mark pedido as consolidated
            pedido.consolidacao_id = consolidacao.id
            pedido.status = StatusPedido.CONSOLIDADO
        
        # Create consolidated items
        for agg in itens_agregados.values():
            item_cons = PedidoConsolidadoItem(
                consolidacao_id=consolidacao.id,
                produto_id=agg['produto_id'],
                variacao_id=agg['variacao_id'],
                descricao=agg['descricao'],
                quantidade=agg['quantidade'],
                preco_unitario=agg['preco_unitario'],
                total=agg['total'],
                unidade=agg['unidade'],
                ncm=agg['ncm'],
                cfop=agg['cfop'],
            )
            db.add(item_cons)
            db.flush()
            
            for orig in agg['origens']:
                item_origem = PedidoConsolidadoItemOrigem(
                    item_consolidado_id=item_cons.id,
                    pedido_origem_id=orig['pedido_id'],
                    item_origem_id=orig['item_id'],
                    quantidade=orig['quantidade'],
                    preco_unitario=orig['preco_unitario'],
                    total=orig['total'],
                )
                db.add(item_origem)
            
            total_consolidado += agg['total']
        
        consolidacao.total = total_consolidado
        db.commit()
        
        print('Consolidação criada: ' + consolidacao.numero + ' (ID: ' + str(consolidacao.id) + ') - Total: ' + str(total_consolidado))
        print('Status: ' + str(consolidacao.status))
        
finally:
    db.close()
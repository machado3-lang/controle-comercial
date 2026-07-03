from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session, selectinload
from database import get_db
from models import PedidoVenda, Produto
from models_nfe import NFSe, NFSeItem
from services.nfse_betha import emitir_completa, NFSeBethaError

router = APIRouter(prefix="/nfse", tags=["NFSe"])

@router.get("/")
def listar_nfse(request: Request, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).order_by(NFSe.created_at.desc()).all()
    return request.app.state.templates.TemplateResponse(
        "nfse/lista.html",
        {"request": request, "nfse": nfse}
    )

@router.get("/pedidos-servico")
def listar_pedidos_servico(request: Request, db: Session = Depends(get_db)):
    pedidos = db.query(PedidoVenda).filter(
        PedidoVenda.nfse == None
    ).all()
    return {"pedidos": pedidos}

@router.get("/emitir/{pedido_id}")
def pagina_emitir(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoVenda).options(
        selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.produto)
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return request.app.state.templates.TemplateResponse(
        "nfse/emissao.html",
        {"request": request, "pedido": pedido}
    )

@router.post("/emitir/{pedido_id}")
def emitir_nfse(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    from models import PedidoVendaItem
    pedido = db.query(PedidoVenda).options(
        selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.produto)
    ).filter(PedidoVenda.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    try:
        # tpAmb=1 = Produção (homologação tpAmb=2 está suspensa)
        resultado = emitir_completa(pedido, db, tpAmb=1)
        
        nfse = NFSe(
            pedido_id=pedido_id,
            numero=resultado.get('numero'),
            codigo_verificacao=resultado.get('codigo_verificacao'),
            status="autorizada" if resultado.get('protocolo') else "pendente",
            xml_path=f"/static/uploads/nfs/emitida_pedido_{pedido_id}.xml",
            valor_total=pedido.total,
            data_emissao=resultado.get('data_emissao')
        )
        db.add(nfse)
        db.commit()
        
        return JSONResponse({"success": True, "protocolo": resultado.get('protocolo'), "erros": resultado.get('erros', [])})
    except NFSeBethaError as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/detalhe/{nfse_id}")
def detalhe_nfse(request: Request, nfse_id: int, db: Session = Depends(get_db)):
    nfse = db.query(NFSe).options(
        selectinload(NFSe.pedido).selectinload(PedidoVenda.cliente)
    ).filter(NFSe.id == nfse_id).first()
    if not nfse:
        raise HTTPException(status_code=404, detail="NFSe não encontrada")
    return request.app.state.templates.TemplateResponse(
        "nfse/detalhe.html",
        {"request": request, "nfse": nfse}
    )
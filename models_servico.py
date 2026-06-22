from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Float
from sqlalchemy.orm import relationship
from database import Base

class Servico(Base):
    __tablename__ = "servicos"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    descricao = Column(Text, nullable=True)
    preco_padrao = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.now)
    
    insumos = relationship("ServicoInsumo", back_populates="servico")


class ServicoInsumo(Base):
    __tablename__ = "servicos_insumos"
    
    id = Column(Integer, primary_key=True, index=True)
    servico_id = Column(Integer, ForeignKey("servicos.id"), nullable=False)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=False)
    quantidade = Column(Integer, default=1)
    
    servico = relationship("Servico", back_populates="insumos")
    produto = relationship("Produto")
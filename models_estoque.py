"""Modelos de controle de estoque.

MovimentacaoEstoque: historico de toda entrada/saida. O saldo do produto
eh derivado/atualizado por estas movimentacoes, nunca editado manualmente.

OSPeca: pecas vinculadas a uma Ordem de Servico, como produtos cadastrados
(categoria Pecas / insumo), para permitir baixa de estoque no reparo.
"""
from sqlalchemy import Column, Integer, String, Float, Numeric, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class MovimentacaoEstoque(Base):
    __tablename__ = "movimentacoes_estoque"

    id = Column(Integer, primary_key=True)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=False, index=True)
    tipo = Column(String(20), nullable=False)  # ENTRADA_COMPRA, SAIDA_VENDA, SAIDA_INSUMO, AJUSTE_POS, AJUSTE_NEG, TRANSFERENCIA
    quantidade = Column(Float, nullable=False)  # com sinal: + entrada, - saida
    data = Column(DateTime, default=datetime.now)
    doc_tipo = Column(String(20), nullable=True)  # nfe, nfse, pedido, os, ajuste
    doc_id = Column(Integer, nullable=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    motivo = Column(String(200), nullable=True)
    deposito_id = Column(Integer, nullable=True)  # Fase 1: 1 deposito (null = principal)
    saldo_apos = Column(Float, nullable=True)

    produto = relationship("Produto")


class OSPeca(Base):
    __tablename__ = "os_pecas"

    id = Column(Integer, primary_key=True)
    os_id = Column(Integer, ForeignKey("ordens_servico.id"), nullable=False, index=True)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=False)
    quantidade = Column(Float, nullable=False, default=1)
    valor_unitario = Column(Numeric(12, 2), nullable=True)

    produto = relationship("Produto")
    os = relationship("OrdemServico", back_populates="os_pecas")

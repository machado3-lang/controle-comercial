from sqlalchemy import Column, Integer, String, Float, Numeric, DateTime, ForeignKey, Text, Date, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class NFe(Base):
    __tablename__ = "nfe"
    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey("pedidos_venda.id"), nullable=True)
    os_id = Column(Integer, ForeignKey("ordens_servico.id"), nullable=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True, index=True)
    origem = Column(String(10), default="avulsa", nullable=False)  # "pedido" ou "avulsa"
    numero = Column(Integer, nullable=False)
    serie = Column(Integer, nullable=False, default=1)
    chave_acesso = Column(String(50), nullable=True)
    protocolo = Column(String(50), nullable=True)
    invoice_id = Column(String(50), nullable=True, unique=True)
    status = Column(String(20), default="pendente", index=True)
    modelo = Column(Integer, default=55)
    natureza_operacao = Column(String(100), default="Venda de mercadoria")
    cfop = Column(String(4), nullable=True)
    data_emissao = Column(DateTime, nullable=True)
    data_saida = Column(DateTime, nullable=True)
    finalidade = Column(String(30), default="normal")
    indicador_presenca = Column(Integer, default=1)
    valor_total = Column(Numeric(12, 2), default=0)
    base_calculo = Column(Numeric(12, 2), default=0)
    valor_icms = Column(Numeric(12, 2), default=0)
    xml_path = Column(String(500), nullable=True)
    xml_text = Column(Text, nullable=True)
    pdf_path = Column(String(500), nullable=True)
    mensagem_retorno = Column(Text, nullable=True)
    aliquota_federal = Column(Numeric(7, 4), default=0.0)
    aliquota_estadual = Column(Numeric(7, 4), default=0.0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    pedido = relationship("PedidoVenda", back_populates="nfes")
    os = relationship("OrdemServico")
    cliente = relationship("Cliente")
    itens = relationship("NFeItem", back_populates="nfe", cascade="all, delete-orphan")


class NFeItem(Base):
    __tablename__ = "nfe_itens"
    id = Column(Integer, primary_key=True)
    nfe_id = Column(Integer, ForeignKey("nfe.id"), nullable=False)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=True)
    item_pai_id = Column(Integer, ForeignKey("nfe_itens.id"), nullable=True)
    descricao = Column(String(300), nullable=False)
    ncm = Column(String(10), nullable=True)
    cfop = Column(String(4), nullable=True)
    unidade = Column(String(6), default="UN")
    quantidade = Column(Numeric(12, 2), nullable=False)
    preco_unitario = Column(Numeric(12, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    
    nfe = relationship("NFe", back_populates="itens")
    produto = relationship("Produto")


class NFSe(Base):
    __tablename__ = "nfse"
    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey("pedidos_venda.id"), nullable=True)
    consolidacao_id = Column(Integer, ForeignKey("pedidos_consolidados.id"), nullable=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True, index=True)
    numero = Column(String(50))
    codigo_verificacao = Column(String(100))
    chave_acesso = Column(String(100), index=True)
    data_emissao = Column(DateTime)
    status = Column(String(20), default="pendente", index=True)
    xml_path = Column(String(500))
    xml_text = Column(Text, nullable=True)
    pdf_path = Column(String(500))
    mensagem_retorno = Column(Text)
    valor_total = Column(Numeric(12, 2))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    natureza_operacao = Column(String(100))
    regime_especial = Column(String(100))
    municipio_codigo = Column(String(10))
    municipio_nome = Column(String(100))
    protocolo = Column(String(50), nullable=True)
    iss_retido = Column(Boolean, default=False)
    aliquota_iss = Column(Numeric(7, 4), default=2.0)
    aliquota_federal = Column(Numeric(7, 4), default=0.0)
    aliquota_estadual = Column(Numeric(7, 4), default=0.0)
    aliquota_municipal = Column(Numeric(7, 4), default=0.0)
    observacoes = Column(Text, default="")
    origem = Column(String(20), default="avulsa", nullable=False)
    
    pedido = relationship("PedidoVenda", back_populates="nfse")
    consolidacao = relationship("PedidoConsolidado", back_populates="nfse")
    cliente = relationship("Cliente")
    itens = relationship("NFSeItem", back_populates="nfse", cascade="all, delete-orphan")


class NFSeItem(Base):
    __tablename__ = "nfse_itens"
    id = Column(Integer, primary_key=True)
    nfse_id = Column(Integer, ForeignKey("nfse.id"))
    produto_id = Column(Integer, ForeignKey("produtos.id"))
    descricao = Column(String(300))
    quantidade = Column(Numeric(12, 2))
    valor_unitario = Column(Numeric(12, 2))
    valor_total = Column(Numeric(12, 2))
    codigo_servico = Column(String(20))
    tributacao_municipal = Column(String(20))
    
    nfse = relationship("NFSe", back_populates="itens")
    produto = relationship("Produto")


class NFSeRecebida(Base):
    """NFSe recebida (somos o tomador/prestador de serviço contratado).
    Mantida em tabela própria, separada das emitidas, para controle de
    despesas/fornecedores e escrituração (SPED)."""
    __tablename__ = "nfse_recebida"
    id = Column(Integer, primary_key=True)
    chave_acesso = Column(String(100), index=True)
    numero = Column(String(50))
    codigo_verificacao = Column(String(100))
    data_emissao = Column(DateTime)
    valor_total = Column(Numeric(12, 2))
    status = Column(String(20), default="autorizada", index=True)
    xml_text = Column(Text, nullable=True)
    emitente_nome = Column(String(200))
    emitente_cnpj = Column(String(20))
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True, index=True)
    origem = Column(String(20), default="adn")
    cancelada = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    fornecedor = relationship("Fornecedor")
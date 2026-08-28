"""
Modelo de Relógios de Ponto (REPs) vendidos.

Tabela standalone para cadastro e consulta de relógios de ponto, catracas e
controladores de acesso vendidos. Vincula-se a cadastros já existentes
(Cliente, Produto, Fornecedor) para enriquecer as consultas, mas mantém
colunas de "cache" (nome, CPF/CNPJ, marca, etc.) preenchidas a partir do
cadastro no momento do salvamento, de modo que a exibição não depende de
joins e o histórico é preservado mesmo que o vínculo seja removido.

Os ForeignKeys usam ondelete="SET NULL": Clientes/Fornecedores não são
excluídos (apenas inativados), mas caso um vínculo desapareça, o registro
de venda permanece íntegro.
"""
from sqlalchemy import (
    Column, Integer, String, Date, Numeric, Boolean, Text,
    ForeignKey, DateTime,
)
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class RelogioPonto(Base):
    __tablename__ = "relogios_ponto"

    id = Column(Integer, primary_key=True, index=True)

    # --- Vínculos (opcionais) ---
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="SET NULL"), nullable=True, index=True)
    produto_id = Column(Integer, ForeignKey("produtos.id", ondelete="SET NULL"), nullable=True, index=True)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id", ondelete="SET NULL"), nullable=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True, index=True)

    # --- Cache preenchido a partir dos cadastros no salvamento ---
    cliente_nome_cache = Column(String(200), nullable=True)
    cpf_cnpj_cache = Column(String(20), nullable=True)
    contato_cache = Column(String(200), nullable=True)
    modelo_cache = Column(String(200), nullable=True)
    marca_cache = Column(String(100), nullable=True)
    fornecedor_nome_cache = Column(String(200), nullable=True)

    # --- Dados diretos da venda ---
    data_venda = Column(Date, nullable=True, index=True)
    numero_serial = Column(String(100), nullable=True, unique=True, index=True)
    valor = Column(Numeric(12, 2), nullable=True)
    atestado_tecnico = Column(Boolean, default=False, nullable=False)
    observacao = Column(Text, nullable=True)
    observacao2 = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    cliente = relationship("Cliente")
    produto = relationship("Produto")
    fornecedor = relationship("Fornecedor")
    usuario = relationship("Usuario")

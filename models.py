from sqlalchemy import Column, Integer, String, Float, Date, Text, ForeignKey, DateTime, Enum, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, date
import enum

from database import Base


class StatusConta(str, enum.Enum):
    PENDENTE = "pendente"
    PAGO = "pago"
    VENCIDO = "vencido"
    CANCELADO = "cancelado"
    BAIXA_SOLICITADA = "baixa_solicitada"


class StatusOS(str, enum.Enum):
    ABERTA = "aberta"
    EM_ANDAMENTO = "em_andamento"
    FINALIZADA = "finalizada"
    CANCELADA = "cancelada"


class Cliente(Base):
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(20), nullable=True, unique=True)
    bling_id = Column(Integer, nullable=True, unique=True)
    bling_updated_at = Column(DateTime, nullable=True)
    bling_pending_sync = Column(Boolean, default=False)
    nome = Column(String(200), nullable=False)
    cpf_cnpj = Column(String(20), nullable=True)
    tipo_pessoa = Column(String(10), nullable=True)
    email = Column(String(200), nullable=True)
    telefone = Column(String(20), nullable=True)
    celular = Column(String(20), nullable=True)
    endereco = Column(String(300), nullable=True)
    bairro = Column(String(100), nullable=True)
    cidade = Column(String(100), nullable=True)
    estado = Column(String(2), nullable=True)
    cep = Column(String(10), nullable=True)
    contato = Column(String(200), nullable=True)
    fantasia = Column(String(200), nullable=True)
    inscricao_estadual = Column(String(20), nullable=True)
    inscricao_municipal = Column(String(20), nullable=True)
    situacao = Column(String(1), default="A")
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    contas_receber = relationship("ContaReceber", back_populates="cliente")
    assinaturas = relationship("Assinatura", back_populates="cliente")
    ordens_servico = relationship("OrdemServico", back_populates="cliente")


class Fornecedor(Base):
    __tablename__ = "fornecedores"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(20), nullable=True, unique=True)
    bling_id = Column(Integer, nullable=True, unique=True)
    bling_updated_at = Column(DateTime, nullable=True)
    bling_pending_sync = Column(Boolean, default=False)
    nome = Column(String(200), nullable=False)
    cpf_cnpj = Column(String(20), nullable=True)
    tipo_pessoa = Column(String(10), nullable=True)
    email = Column(String(200), nullable=True)
    telefone = Column(String(20), nullable=True)
    celular = Column(String(20), nullable=True)
    endereco = Column(String(300), nullable=True)
    bairro = Column(String(100), nullable=True)
    cidade = Column(String(100), nullable=True)
    estado = Column(String(2), nullable=True)
    cep = Column(String(10), nullable=True)
    contato = Column(String(200), nullable=True)
    fantasia = Column(String(200), nullable=True)
    inscricao_estadual = Column(String(20), nullable=True)
    inscricao_municipal = Column(String(20), nullable=True)
    situacao = Column(String(1), default="A")
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    contas_pagar = relationship("ContaPagar", back_populates="fornecedor")
    assinaturas = relationship("Assinatura", back_populates="fornecedor")


class ContaPagar(Base):
    __tablename__ = "contas_pagar"

    id = Column(Integer, primary_key=True, index=True)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True)
    descricao = Column(String(300), nullable=False)
    valor = Column(Float, nullable=False)
    data_vencimento = Column(Date, nullable=False)
    data_pagamento = Column(Date, nullable=True)
    status = Column(Enum(StatusConta), default=StatusConta.PENDENTE)
    forma_pagamento = Column(String(100), nullable=True)
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    fornecedor = relationship("Fornecedor", back_populates="contas_pagar")


class ContaReceber(Base):
    __tablename__ = "contas_receber"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True)
    descricao = Column(String(300), nullable=False)
    valor = Column(Float, nullable=False)
    data_vencimento = Column(Date, nullable=False)
    data_recebimento = Column(Date, nullable=True)
    status = Column(Enum(StatusConta), default=StatusConta.PENDENTE)
    forma_pagamento = Column(String(100), nullable=True)
    observacao = Column(Text, nullable=True)
    nosso_numero = Column(String(30), nullable=True, unique=True)
    boleto_emitido = Column(Boolean, default=False)
    boleto_url = Column(String(500), nullable=True)
    boleto_txid = Column(String(50), nullable=True)
    numero_documento = Column(String(30), nullable=True)
    api_nosso_numero = Column(String(30), nullable=True)
    data_emissao = Column(Date, nullable=True)
    motivo_baixa = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    cliente = relationship("Cliente", back_populates="contas_receber")


class Assinatura(Base):
    __tablename__ = "assinaturas"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    bling_id = Column(Integer, nullable=True, unique=True)
    bling_updated_at = Column(DateTime, nullable=True)
    bling_pending_sync = Column(Boolean, default=False)
    periodicidade = Column(Integer, default=1)
    descricao = Column(String(300), nullable=False)
    valor = Column(Float, nullable=False)
    quantidade = Column(Integer, nullable=True)
    data_inicio = Column(Date, nullable=False)
    data_fim = Column(Date, nullable=True)
    dia_vencimento = Column(Integer, nullable=False)
    situacao = Column(Integer, default=1)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True)
    valor_revenda = Column(Float, nullable=True)
    numero_contrato = Column(String(50), nullable=True)
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    cliente = relationship("Cliente", back_populates="assinaturas")
    fornecedor = relationship("Fornecedor", back_populates="assinaturas")
    historico = relationship("AssinaturaHistorico", back_populates="assinatura", cascade="all, delete-orphan",
                             order_by="AssinaturaHistorico.data_alteracao.desc()")


class AssinaturaHistorico(Base):
    __tablename__ = "assinaturas_historico"

    id = Column(Integer, primary_key=True, index=True)
    assinatura_id = Column(Integer, ForeignKey("assinaturas.id"), nullable=False)
    valor_anterior = Column(Float, nullable=True)
    valor_revenda_anterior = Column(Float, nullable=True)
    quantidade_anterior = Column(Integer, nullable=True)
    valor_novo = Column(Float, nullable=True)
    valor_revenda_novo = Column(Float, nullable=True)
    quantidade_novo = Column(Integer, nullable=True)
    data_alteracao = Column(DateTime, default=datetime.now)

    assinatura = relationship("Assinatura", back_populates="historico")


class OrdemServico(Base):
    __tablename__ = "ordens_servico"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    bling_id = Column(Integer, nullable=True, unique=True)
    bling_updated_at = Column(DateTime, nullable=True)
    bling_pending_sync = Column(Boolean, default=False)
    equipamento = Column(String(200), nullable=False)
    marca = Column(String(100), nullable=True)
    modelo = Column(String(100), nullable=True)
    numero_serie = Column(String(100), nullable=True)
    defeito_relatado = Column(Text, nullable=True)
    servicos_executados = Column(Text, nullable=True)
    pecas_utilizadas = Column(Text, nullable=True)
    valor_servico = Column(Float, default=0)
    valor_pecas = Column(Float, default=0)
    valor_total = Column(Float, default=0)
    data_entrada = Column(Date, nullable=False, default=date.today)
    data_saida = Column(Date, nullable=True)
    status = Column(Enum(StatusOS), default=StatusOS.ABERTA)
    tecnico = Column(String(200), nullable=True)
    autorizado_por = Column(String(200), nullable=True)
    numero_requisicao = Column(String(100), nullable=True)
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    cliente = relationship("Cliente", back_populates="ordens_servico")


class Empresa(Base):
    __tablename__ = "empresa"

    id = Column(Integer, primary_key=True, index=True)
    razao_social = Column(String(200), nullable=True)
    nome_fantasia = Column(String(200), nullable=True)
    cnpj = Column(String(20), nullable=True)
    inscricao_estadual = Column(String(20), nullable=True)
    inscricao_municipal = Column(String(20), nullable=True)
    endereco = Column(String(300), nullable=True)
    bairro = Column(String(100), nullable=True)
    cidade = Column(String(100), nullable=True)
    estado = Column(String(2), nullable=True)
    cep = Column(String(10), nullable=True)
    telefone = Column(String(20), nullable=True)
    celular = Column(String(20), nullable=True)
    email = Column(String(200), nullable=True)
    site = Column(String(200), nullable=True)
    logo = Column(String(300), nullable=True)
    senha_admin = Column(String(100), nullable=True)
    bling_token = Column(String(200), nullable=True)
    bling_client_id = Column(String(200), nullable=True)
    bling_client_secret = Column(String(200), nullable=True)
    bling_refresh_token = Column(String(200), nullable=True)
    bling_token_expires_at = Column(DateTime, nullable=True)
    bling_webhook_secret = Column(String(100), nullable=True)
    bling_api_key_v2 = Column(String(100), nullable=True)
    sicoob_client_id = Column(String(200), nullable=True)
    sicoob_token = Column(String(300), nullable=True)
    sicoob_conta_corrente = Column(String(30), nullable=True)
    sicoob_beneficiario = Column(String(20), nullable=True)
    sicoob_cert_path = Column(String(500), nullable=True)
    sicoob_cert_key_path = Column(String(500), nullable=True)
    sicoob_cert_password = Column(String(100), nullable=True)
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

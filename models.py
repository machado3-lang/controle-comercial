from sqlalchemy import Column, Integer, BigInteger, String, Float, Date, Text, ForeignKey, DateTime, Enum, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, date
import enum

from database import Base, engine

# Import NFSe from models_nfe for PedidoVenda.nfse relationship
from models_nfe import NFSe, NFe  # noqa: F401


def get_safe_day(date_obj, target_day: int) -> date:
    year = date_obj.year
    month = date_obj.month
    try:
        return date(year, month, target_day)
    except ValueError:
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1
        return date(next_year, next_month, 1)


def recreate_tables():
    """Drop and recreate all tables - use for migration"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


class StatusConta(str, enum.Enum):
    PENDENTE = "pendente"
    PAGO = "pago"
    VENCIDO = "vencido"
    CANCELADO = "cancelado"
    BAIXA_SOLICITADA = "baixa_solicitada"
    EXCLUIDO = "excluido"


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(200), nullable=False, unique=True)
    senha = Column(String(200), nullable=False)
    nome = Column(String(200), nullable=False)
    ativo = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    permissoes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class StatusOS(str, enum.Enum):
    ABERTA = "aberta"
    EM_ANDAMENTO = "em_andamento"
    FINALIZADA = "finalizada"
    CANCELADA = "cancelada"


class Cliente(Base):
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(50), nullable=True)
    bling_id = Column(BigInteger, nullable=True, unique=True)
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
    isento_ie = Column(Boolean, default=False)
    indicador_ie = Column(String(20), default="contribuidor")  # contribuidor, isento, nao_contribuinte
    codigo_ibge = Column(String(7), nullable=True)
    situacao = Column(String(1), default="A")
    iss_retido = Column(Boolean, default=False)
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    contas_receber = relationship("ContaReceber", back_populates="cliente")
    assinaturas = relationship("Assinatura", back_populates="cliente")
    ordens_servico = relationship("OrdemServico", back_populates="cliente")


class Fornecedor(Base):
    __tablename__ = "fornecedores"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(50), nullable=True)
    bling_id = Column(BigInteger, nullable=True, unique=True)
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
    isento_ie = Column(Boolean, default=False)
    indicador_ie = Column(String(20), default="contribuidor")
    codigo_ibge = Column(String(7), nullable=True)
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
    bling_id = Column(BigInteger, nullable=True, unique=True)
    bling_updated_at = Column(DateTime, nullable=True)
    bling_pending_sync = Column(Boolean, default=False)
    periodicidade = Column(Integer, default=1)
    descricao = Column(String(300), nullable=False)
    valor = Column(Float, nullable=False)
    quantidade = Column(Integer, nullable=True)
    data_inicio = Column(Date, nullable=False)
    data_fim = Column(Date, nullable=True)
    dia_vencimento = Column(Integer, nullable=False)
    mes_vencimento = Column(Integer, default=0)
    situacao = Column(Integer, default=1)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True)
    valor_revenda = Column(Float, nullable=True)
    numero_contrato = Column(String(50), nullable=True)
    observacao = Column(Text, nullable=True)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    cliente = relationship("Cliente", back_populates="assinaturas")
    fornecedor = relationship("Fornecedor", back_populates="assinaturas")
    produto = relationship("Produto")
    historico = relationship("AssinaturaHistorico", back_populates="assinatura", cascade="all, delete-orphan",
                             order_by="AssinaturaHistorico.data_alteracao.desc()")


class AssinaturaHistorico(Base):
    __tablename__ = "assinaturas_historico"

    id = Column(Integer, primary_key=True, index=True)
    assinatura_id = Column(Integer, ForeignKey("assinaturas.id"), nullable=False)
    valor_anterior = Column(Float, nullable=True)
    valor_revenda_anterior = Column(Float, nullable=True)
    quantidade_anterior = Column(Integer, nullable=True)
    dia_vencimento_anterior = Column(Integer, nullable=True)
    valor_novo = Column(Float, nullable=True)
    valor_revenda_novo = Column(Float, nullable=True)
    quantidade_novo = Column(Integer, nullable=True)
    dia_vencimento_novo = Column(Integer, nullable=True)
    data_alteracao = Column(DateTime, default=datetime.now)

    assinatura = relationship("Assinatura", back_populates="historico")


class OrdemServico(Base):
    __tablename__ = "ordens_servico"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    bling_id = Column(BigInteger, nullable=True, unique=True)
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


class MarcaProduto(Base):
    __tablename__ = "marcas_produto"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.now)


class CategoriaProduto(Base):
    __tablename__ = "categorias_produto"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    produtos = relationship("Produto", back_populates="categoria")


class Produto(Base):
    __tablename__ = "produtos"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(50), nullable=True, unique=True)
    nome = Column(String(200), nullable=False)
    descricao = Column(Text, nullable=True)
    preco = Column(Float, nullable=False, default=0)
    preco_custo = Column(Float, nullable=True)
    ncm = Column(String(10), nullable=True)
    origem = Column(Integer, default=0)  # 0=nacional, 1=importado
    unidade = Column(String(10), nullable=True, default="UN")
    categoria_id = Column(Integer, ForeignKey("categorias_produto.id"), nullable=True)
    foto = Column(String(500), nullable=True)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True)
    marca_id = Column(Integer, ForeignKey("marcas_produto.id"), nullable=True)
    marca = Column(String(100), nullable=True)
    peso_liq = Column(Float, nullable=True)
    peso_bruto = Column(Float, nullable=True)
    altura = Column(Float, nullable=True)
    largura = Column(Float, nullable=True)
    profundidade = Column(Float, nullable=True)
    unidade_medida = Column(String(20), nullable=True, default="cm")
    estoque = Column(Float, nullable=False, default=0)
    estoque_minimo = Column(Float, nullable=False, default=0)
    situacao = Column(String(1), nullable=False, default="A")
    bling_id = Column(BigInteger, nullable=True, unique=True)
    bling_updated_at = Column(DateTime, nullable=True)
    bling_pending_sync = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    categoria = relationship("CategoriaProduto", back_populates="produtos")
    fornecedor = relationship("Fornecedor")
    marca_rel = relationship("MarcaProduto")
    variacoes = relationship("ProdutoVariacao", back_populates="produto", cascade="all, delete-orphan")
    composicoes = relationship("ProdutoComposicao", foreign_keys="[ProdutoComposicao.produto_pai_id]", back_populates="produto_pai", cascade="all, delete-orphan")
    tipo = Column(String(20), default="produto")  # "produto", "servico" ou "kit"
    codigo_lc116 = Column(String(10), nullable=True)
    codigo_tributacao_municipal = Column(String(10), nullable=True)

    @property
    def preco_padrao(self):
        return self.preco

    @preco_padrao.setter
    def preco_padrao(self, value):
        self.preco = value


class ProdutoVariacao(Base):
    __tablename__ = "produto_variacoes"

    id = Column(Integer, primary_key=True, index=True)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=False)
    nome_variacao = Column(String(100), nullable=True)
    sku = Column(String(50), nullable=False, unique=True)
    preco_adicional = Column(Float, default=0)
    estoque_atual = Column(Float, default=0)
    estoque_minimo = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    produto = relationship("Produto", back_populates="variacoes")
    itens_pedido = relationship("PedidoVendaItem", foreign_keys="[PedidoVendaItem.variacao_id]", back_populates="variacao")


class ProdutoComposicao(Base):
    __tablename__ = "produto_composicao"

    id = Column(Integer, primary_key=True, index=True)
    produto_pai_id = Column(Integer, ForeignKey("produtos.id"), nullable=False)
    insumo_id = Column(Integer, ForeignKey("produtos.id"), nullable=False)
    quantidade_padrao = Column(Float, default=1)
    created_at = Column(DateTime, default=datetime.now)

    produto_pai = relationship("Produto", foreign_keys=[produto_pai_id], back_populates="composicoes")
    insumo = relationship("Produto", foreign_keys=[insumo_id])


class StatusPedido(str, enum.Enum):
    PENDENTE = "pendente"
    APROVADO = "aprovado"
    FATURADO = "faturado"
    PRE_VENDA = "pre_venda"
    CANCELADO = "cancelado"


class FormaPagamento(str, enum.Enum):
    AVISTA = "avista"
    APRAZO = "aprazo"
    CARTAO_CREDITO = "cartao_credito"
    CARTAO_DEBITO = "cartao_debito"
    BOLETO = "boleto"


class PedidoVenda(Base):
    __tablename__ = "pedidos_venda"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    numero = Column(String(50), nullable=True)
    data = Column(Date, nullable=False, default=date.today)
    status = Column(Enum(StatusPedido), default=StatusPedido.PENDENTE)
    total = Column(Float, nullable=False, default=0)
    observacao = Column(Text, nullable=True)
    tipo_pedido = Column(String(20), default="venda")  # venda ou pre_venda
    forma_pagamento = Column(String(20), nullable=True)
    gerar_boleto = Column(Boolean, default=False)
    terminos_boleto = Column(Text, nullable=True)
    pedido_agrupado_id = Column(Integer, ForeignKey("pedidos_venda.id"), nullable=True)  # Referência ao pedido criado pelo agrupamento
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    cliente = relationship("Cliente")
    itens = relationship("PedidoVendaItem", back_populates="pedido", cascade="all, delete-orphan")
    nfse = relationship("NFSe", back_populates="pedido", uselist=False)
    nfes = relationship("NFe", back_populates="pedido")
    pedido_agrupado = relationship("PedidoVenda", remote_side=[id])


class PedidoVendaItem(Base):
    __tablename__ = "pedidos_venda_itens"

    id = Column(Integer, primary_key=True, index=True)
    pedido_id = Column(Integer, ForeignKey("pedidos_venda.id"), nullable=False)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=True)
    variacao_id = Column(Integer, ForeignKey("produto_variacoes.id"), nullable=True)
    item_pai_id = Column(Integer, ForeignKey("pedidos_venda_itens.id"), nullable=True)

    descricao = Column(String(300), nullable=False)
    quantidade = Column(Float, nullable=False, default=1)
    preco_unitario = Column(Float, nullable=False, default=0)
    total = Column(Float, nullable=False, default=0)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True)

    pedido = relationship("PedidoVenda", back_populates="itens")
    produto = relationship("Produto")
    variacao = relationship("ProdutoVariacao", foreign_keys=[variacao_id], back_populates="itens_pedido")
    fornecedor = relationship("Fornecedor")
    pai = relationship("PedidoVendaItem", remote_side=[id], back_populates="filhos")
    filhos = relationship("PedidoVendaItem", back_populates="pai")


class Empresa(Base):
    __tablename__ = "empresa"

    id = Column(Integer, primary_key=True, index=True)
    razao_social = Column(String(500), nullable=True)
    nome_fantasia = Column(String(500), nullable=True)
    cnpj = Column(String(20), nullable=True)
    inscricao_estadual = Column(String(20), nullable=True)
    inscricao_municipal = Column(String(20), nullable=True)
    endereco = Column(String(300), nullable=True)
    bairro = Column(String(100), nullable=True)
    cidade = Column(String(100), nullable=True)
    estado = Column(String(2), nullable=True)
    cep = Column(String(10), nullable=True)
    codigo_ibge = Column(String(7), nullable=True)
    telefone = Column(String(20), nullable=True)
    celular = Column(String(20), nullable=True)
    email = Column(String(200), nullable=True)
    site = Column(String(200), nullable=True)
    logo = Column(String(300), nullable=True)
    senha_admin = Column(String(100), nullable=True)
    senha_lembrete = Column(String(200), nullable=True)
    bling_token = Column(String(200), nullable=True)
    bling_client_id = Column(String(200), nullable=True)
    bling_client_secret = Column(String(200), nullable=True)
    bling_refresh_token = Column(String(200), nullable=True)
    bling_token_expires_at = Column(DateTime, nullable=True)
    bling_webhook_secret = Column(String(100), nullable=True)
    bling_api_key_v2 = Column(String(100), nullable=True)
    bling_desabilitado = Column(Boolean, default=False)
    sicoob_client_id = Column(String(200), nullable=True)
    sicoob_token = Column(String(3000), nullable=True)
    sicoob_conta_corrente = Column(String(30), nullable=True)
    sicoob_beneficiario = Column(String(20), nullable=True)
    sicoob_cert_path = Column(String(500), nullable=True)
    sicoob_cert_key_path = Column(String(500), nullable=True)
    sicoob_cert_password = Column(String(100), nullable=True)
    sicoob_cert_base64 = Column(Text, nullable=True)  # Certificado armazenado como base64
    sicoob_cert_key_base64 = Column(Text, nullable=True)  # Chave armazenada como base64
    observacao = Column(Text, nullable=True)
    categoria_servico_padrao_id = Column(Integer, ForeignKey("categorias_produto.id"), nullable=True)
    notaas_api_key = Column(Text, nullable=True)
    aliquota_iss = Column(Float, nullable=False, default=2.0)
    notaas_ambiente = Column(String(1), nullable=False, default="2")
    serie_nfe = Column(Integer, nullable=False, default=1)
    ultimo_numero_nfe = Column(Integer, nullable=False, default=0)
    ultimo_numero_nfse = Column(Integer, nullable=False, default=0)
    cfop_padrao = Column(String(4), nullable=False, default="5102")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    categoria_servico_padrao = relationship("CategoriaProduto")


class CfopNatureza(Base):
    __tablename__ = "cfop_natureza"
    id = Column(Integer, primary_key=True)
    cfop = Column(String(4), nullable=False, index=True)
    natureza = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=datetime.now)

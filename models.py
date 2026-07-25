from sqlalchemy import Column, Integer, BigInteger, String, Float, Numeric, Date, Text, ForeignKey, DateTime, Enum, Boolean
from sqlalchemy.orm import relationship, deferred
from datetime import datetime, date
import enum

from database import Base, engine

# Import NFSe from models_nfe for PedidoVenda.nfse relationship
from models_nfe import NFSe, NFe  # noqa: F401
# Import estoque models for OrdemServico.os_pecas relationship
from models_estoque import OSPeca  # noqa: F401


class NFeDistribuida(Base):
    """NFe obtidas via Distribuição DF-e (SEFAZ)"""
    __tablename__ = "nfe_distribuidas"

    id = Column(Integer, primary_key=True, index=True)
    chave_acesso = Column(String(44), unique=True, nullable=False, index=True)
    numero = Column(String(20), nullable=True)
    dh_emi = Column(String(30), nullable=True)
    valor = Column(Numeric(12, 2), nullable=True)
    emitente_nome = Column(String(300), nullable=True)
    emitente_cnpj = Column(String(20), nullable=True)
    destinatario_nome = Column(String(300), nullable=True)
    destinatario_cnpj = Column(String(20), nullable=True)
    nsu = Column(String(20), nullable=True)
    schema_nfe = Column(String(50), nullable=True)
    xml = Column(Text, nullable=True)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now)

    fornecedor = relationship("Fornecedor")


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
    PENDENTE = "PENDENTE"
    PAGO = "PAGO"
    VENCIDO = "VENCIDO"
    CANCELADO = "CANCELADO"
    BAIXA_SOLICITADA = "BAIXA_SOLICITADA"
    EXCLUIDO = "EXCLUIDO"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            upper = value.upper()
            for m in cls:
                if m.value == upper:
                    return m
        return None


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

    consolidacoes_finalizadas = relationship("PedidoConsolidado", foreign_keys="[PedidoConsolidado.finalizado_por]", back_populates="finalizador")
    audit_logs = relationship("AuditLog", back_populates="usuario")
    historico_cadastros = relationship("HistoricoCadastro", back_populates="usuario")


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
    cpf_cnpj = Column(String(20), nullable=True, unique=True)
    tipo_pessoa = Column(String(10), nullable=True)
    email = Column(String(200), nullable=True)
    telefone = Column(String(20), nullable=True)
    celular = Column(String(20), nullable=True)
    endereco = Column(String(300), nullable=True)
    numero = Column(String(20), nullable=True)
    complemento = Column(String(200), nullable=True)
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
    tambem_fornecedor = Column(Boolean, default=False)
    observacao = Column(Text, nullable=True)
    data_sincronizacao = Column(DateTime, nullable=True)  # ultima consulta cnpj.ws
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    contas_receber = relationship("ContaReceber", back_populates="cliente")
    assinaturas = relationship("Assinatura", back_populates="cliente")
    ordens_servico = relationship("OrdemServico", back_populates="cliente")
    pedidos_venda = relationship("PedidoVenda", back_populates="cliente")
    consolidacoes = relationship("PedidoConsolidado", back_populates="cliente")
    nfes = relationship("NFe", back_populates="cliente")
    nfses = relationship("NFSe", back_populates="cliente")


class Fornecedor(Base):
    __tablename__ = "fornecedores"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(50), nullable=True)
    bling_id = Column(BigInteger, nullable=True, unique=True)
    bling_updated_at = Column(DateTime, nullable=True)
    bling_pending_sync = Column(Boolean, default=False)
    nome = Column(String(200), nullable=False)
    cpf_cnpj = Column(String(20), nullable=True, unique=True)
    tipo_pessoa = Column(String(10), nullable=True)
    email = Column(String(200), nullable=True)
    telefone = Column(String(20), nullable=True)
    celular = Column(String(20), nullable=True)
    endereco = Column(String(300), nullable=True)
    numero = Column(String(20), nullable=True)
    complemento = Column(String(200), nullable=True)
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
    tambem_cliente = Column(Boolean, default=False)
    observacao = Column(Text, nullable=True)
    data_sincronizacao = Column(DateTime, nullable=True)  # ultima consulta cnpj.ws
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    contas_pagar = relationship("ContaPagar", back_populates="fornecedor")
    assinaturas = relationship("Assinatura", back_populates="fornecedor")
    produtos = relationship("Produto", back_populates="fornecedor")
    itens_venda = relationship("PedidoVendaItem", back_populates="fornecedor")
    nfse_recebidas = relationship("NFSeRecebida", back_populates="fornecedor")


class ContaPagar(Base):
    __tablename__ = "contas_pagar"

    id = Column(Integer, primary_key=True, index=True)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True, index=True)
    descricao = Column(Text, nullable=False)
    valor = Column(Numeric(12, 2), nullable=False)
    valor_juros = Column(Numeric(12, 2), default=0)
    valor_desconto = Column(Numeric(12, 2), default=0)
    valor_total = Column(Numeric(12, 2), nullable=True)
    data_vencimento = Column(Date, nullable=False, index=True)
    data_pagamento = Column(Date, nullable=True, index=True)
    status = Column(Enum(StatusConta, name='statusconta', native_enum=True), default=StatusConta.PENDENTE, index=True)
    forma_pagamento = Column(String(100), nullable=True)




    observacao = Column(Text, nullable=True)
    numero_documento = Column(String(30), nullable=True)
    tipo_documento_id = Column(Integer, ForeignKey("tipos_documento.id"), nullable=True, index=True)
    plano_conta_id = Column(Integer, ForeignKey("plano_contas.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    # Parcelamento: numero da parcela (1..N), total de parcelas e UUID que
    # agrupa todas as parcelas geradas juntas (espelha ContaReceber).
    numero_parcela = Column(Integer, nullable=True, default=1)
    total_parcelas = Column(Integer, nullable=True, default=1)
    parcelamento_grupo = Column(String(36), nullable=True, index=True)

    fornecedor = relationship("Fornecedor", back_populates="contas_pagar")
    tipo_documento = relationship("TipoDocumento", back_populates="contas_pagar")
    plano_conta = relationship("PlanoDeContas", back_populates="contas_pagar")


class ContaReceber(Base):
    __tablename__ = "contas_receber"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True, index=True)
    descricao = Column(Text, nullable=False)
    valor = Column(Numeric(12, 2), nullable=False)
    valor_juros = Column(Numeric(12, 2), default=0)
    valor_desconto = Column(Numeric(12, 2), default=0)
    valor_total = Column(Numeric(12, 2), nullable=True)
    data_vencimento = Column(Date, nullable=False, index=True)
    data_recebimento = Column(Date, nullable=True, index=True)
    status = Column(Enum(StatusConta, name='statusconta', native_enum=True), default=StatusConta.PENDENTE, index=True)
    forma_pagamento = Column(String(100), nullable=True)




    observacao = Column(Text, nullable=True)
    nosso_numero = Column(String(30), nullable=True, unique=True)
    boleto_emitido = Column(Boolean, default=False, index=True)
    boleto_url = Column(String(500), nullable=True)
    boleto_txid = Column(String(50), nullable=True)
    numero_documento = Column(String(30), nullable=True)
    api_nosso_numero = Column(String(30), nullable=True)
    data_emissao = Column(Date, nullable=True)
    motivo_baixa = Column(String(100), nullable=True)
    tipo_documento_id = Column(Integer, ForeignKey("tipos_documento.id"), nullable=True, index=True)
    plano_conta_id = Column(Integer, ForeignKey("plano_contas.id"), nullable=True, index=True)
    nfse_id = Column(Integer, ForeignKey("nfse.id"), nullable=True, index=True)
    email_enviado = Column(Boolean, default=False)
    data_envio_email = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    consolidacao_id = Column(Integer, ForeignKey("pedidos_consolidados.id"), nullable=True, index=True)
    # Parcelamento: numero da parcela (1..N), total de parcelas do faturamento
    # e um identificador (UUID) que agrupa todas as parcelas geradas juntas.
    numero_parcela = Column(Integer, nullable=True, default=1)
    total_parcelas = Column(Integer, nullable=True, default=1)
    parcelamento_grupo = Column(String(36), nullable=True, index=True)
    # Vinculos com o documento de origem do faturamento
    pedido_id = Column(Integer, ForeignKey("pedidos_venda.id"), nullable=True, index=True)
    nfe_id = Column(Integer, ForeignKey("nfe.id"), nullable=True, index=True)

    cliente = relationship("Cliente", back_populates="contas_receber")
    tipo_documento = relationship("TipoDocumento", back_populates="contas_receber")
    plano_conta = relationship("PlanoDeContas", back_populates="contas_receber")
    nfse = relationship("NFSe", back_populates="contas_receber")
    consolidacao = relationship("PedidoConsolidado", back_populates="contas_receber")
    pedido = relationship("PedidoVenda", foreign_keys=[pedido_id])
    nfe = relationship("NFe", foreign_keys=[nfe_id])


class Assinatura(Base):
    __tablename__ = "assinaturas"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False, index=True)
    bling_id = Column(BigInteger, nullable=True, unique=True)
    bling_updated_at = Column(DateTime, nullable=True)
    bling_pending_sync = Column(Boolean, default=False)
    periodicidade = Column(Integer, default=1)
    descricao = Column(Text, nullable=False)
    valor = Column(Numeric(12, 2), nullable=False)
    quantidade = Column(Integer, nullable=True)
    data_inicio = Column(Date, nullable=False)
    data_fim = Column(Date, nullable=True, index=True)
    dia_vencimento = Column(Integer, nullable=False)
    mes_vencimento = Column(Integer, default=0)
    situacao = Column(Integer, default=1)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True, index=True)
    valor_revenda = Column(Numeric(12, 2), nullable=True)
    numero_contrato = Column(String(50), nullable=True)
    observacao = Column(Text, nullable=True)
    travar_cobranca = Column(Boolean, default=False, nullable=True)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=True, index=True)
    nfse_id = Column(Integer, ForeignKey("nfse.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    cliente = relationship("Cliente", back_populates="assinaturas")
    fornecedor = relationship("Fornecedor", back_populates="assinaturas")
    produto = relationship("Produto", back_populates="assinaturas")
    nfse = relationship("NFSe", back_populates="assinaturas")
    historico = relationship("AssinaturaHistorico", back_populates="assinatura", cascade="all, delete-orphan",
                             order_by="AssinaturaHistorico.data_alteracao.desc()")


class AssinaturaHistorico(Base):
    __tablename__ = "assinaturas_historico"

    id = Column(Integer, primary_key=True, index=True)
    assinatura_id = Column(Integer, ForeignKey("assinaturas.id"), nullable=False, index=True)
    valor_anterior = Column(Numeric(12, 2), nullable=True)
    valor_revenda_anterior = Column(Numeric(12, 2), nullable=True)
    quantidade_anterior = Column(Integer, nullable=True)
    dia_vencimento_anterior = Column(Integer, nullable=True)
    valor_novo = Column(Numeric(12, 2), nullable=True)
    valor_revenda_novo = Column(Numeric(12, 2), nullable=True)
    quantidade_novo = Column(Integer, nullable=True)
    dia_vencimento_novo = Column(Integer, nullable=True)
    data_alteracao = Column(DateTime, default=datetime.now)

    assinatura = relationship("Assinatura", back_populates="historico")


class OrdemServico(Base):
    __tablename__ = "ordens_servico"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False, index=True)
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
    os_pecas = relationship("OSPeca", back_populates="os", cascade="all, delete-orphan", order_by="OSPeca.id")
    valor_servico = Column(Numeric(12, 2), default=0)
    valor_pecas = Column(Numeric(12, 2), default=0)
    valor_total = Column(Numeric(12, 2), default=0)
    data_entrada = Column(Date, nullable=False, default=date.today)
    data_saida = Column(Date, nullable=True)
    status = Column(Enum(StatusOS, native_enum=False), default=StatusOS.ABERTA, index=True)
    tecnico = Column(String(200), nullable=True)
    autorizado_por = Column(String(200), nullable=True)
    numero_requisicao = Column(String(100), nullable=True)
    observacao = Column(Text, nullable=True)
    data_sincronizacao = Column(DateTime, nullable=True)  # ultima consulta cnpj.ws
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    cliente = relationship("Cliente", back_populates="ordens_servico")
    nfes = relationship("NFe", back_populates="os")


class MarcaProduto(Base):
    __tablename__ = "marcas_produto"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.now)

    produtos = relationship("Produto", back_populates="marca_rel")


class CategoriaProduto(Base):
    __tablename__ = "categorias_produto"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    produtos = relationship("Produto", back_populates="categoria")
    empresas = relationship("Empresa", back_populates="categoria_servico_padrao")


class Produto(Base):
    __tablename__ = "produtos"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(50), nullable=True, unique=True)
    nome = Column(String(200), nullable=False)
    descricao = Column(Text, nullable=True)
    preco = Column(Numeric(12, 2), nullable=False, default=0)
    preco_custo = Column(Numeric(12, 2), nullable=True)
    ncm = Column(String(10), nullable=True)
    origem = Column(Integer, default=0)  # 0=nacional, 1=importado
    unidade = Column(String(10), nullable=True, default="UN")
    categoria_id = Column(Integer, ForeignKey("categorias_produto.id"), nullable=True, index=True)
    foto = Column(String(500), nullable=True)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True, index=True)
    marca_id = Column(Integer, ForeignKey("marcas_produto.id"), nullable=True, index=True)
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
    fornecedor = relationship("Fornecedor", back_populates="produtos")
    marca_rel = relationship("MarcaProduto", back_populates="produtos")
    variacoes = relationship("ProdutoVariacao", back_populates="produto", cascade="all, delete-orphan")
    composicoes = relationship("ProdutoComposicao", foreign_keys="[ProdutoComposicao.produto_pai_id]", back_populates="produto_pai", cascade="all, delete-orphan")
    composicoes_insumo = relationship("ProdutoComposicao", foreign_keys="[ProdutoComposicao.insumo_id]", back_populates="insumo")
    assinaturas = relationship("Assinatura", back_populates="produto")
    itens_venda = relationship("PedidoVendaItem", back_populates="produto")
    itens_consolidado = relationship("PedidoConsolidadoItem", back_populates="produto")
    itens_nfe = relationship("NFeItem", back_populates="produto")
    itens_nfse = relationship("NFSeItem", back_populates="produto")
    tipo = Column(String(20), default="produto")  # "produto", "servico" ou "kit"
    eh_insumo = Column(Boolean, default=False)  # True: consumido em servicos (baixa como SAIDA_INSUMO na NFSe)
    codigo_lc116 = Column(String(10), nullable=True)
    codigo_tributacao_municipal = Column(String(20), nullable=True)

    @property
    def preco_padrao(self):
        return self.preco

    @preco_padrao.setter
    def preco_padrao(self, value):
        self.preco = value


class ProdutoVariacao(Base):
    __tablename__ = "produto_variacoes"

    id = Column(Integer, primary_key=True, index=True)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=False, index=True)
    nome_variacao = Column(String(100), nullable=True)
    sku = Column(String(50), nullable=False, unique=True)
    preco_adicional = Column(Numeric(12, 2), default=0)
    estoque_atual = Column(Float, default=0)
    estoque_minimo = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    produto = relationship("Produto", back_populates="variacoes")
    itens_pedido = relationship("PedidoVendaItem", foreign_keys="[PedidoVendaItem.variacao_id]", back_populates="variacao")
    itens_consolidado = relationship("PedidoConsolidadoItem", back_populates="variacao")


class ProdutoComposicao(Base):
    __tablename__ = "produto_composicao"

    id = Column(Integer, primary_key=True, index=True)
    produto_pai_id = Column(Integer, ForeignKey("produtos.id"), nullable=False, index=True)
    insumo_id = Column(Integer, ForeignKey("produtos.id"), nullable=False, index=True)
    quantidade_padrao = Column(Float, default=1)
    created_at = Column(DateTime, default=datetime.now)

    produto_pai = relationship("Produto", foreign_keys=[produto_pai_id], back_populates="composicoes")
    insumo = relationship("Produto", foreign_keys=[insumo_id], back_populates="composicoes_insumo")


class StatusPedido(str, enum.Enum):
    PENDENTE = "pendente"
    APROVADO = "aprovado"
    FATURADO = "faturado"
    PRE_VENDA = "pre_venda"
    CONSOLIDADO = "consolidado"
    CANCELADO = "cancelado"


class StatusConsolidacao(str, enum.Enum):
    ABERTO = "aberto"
    PROCESSANDO = "processando"
    CONCLUIDO = "concluido"
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
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False, index=True)
    numero = Column(String(50), nullable=True, unique=True, index=True)
    data = Column(Date, nullable=False, default=date.today)
    status = Column(Enum(StatusPedido, native_enum=False), default=StatusPedido.PENDENTE, index=True)
    total = Column(Numeric(12, 2), nullable=False, default=0)
    observacao = Column(Text, nullable=True)
    tipo_pedido = Column(String(20), default="venda")  # venda ou pre_venda
    forma_pagamento = Column(String(20), nullable=True)
    gerar_boleto = Column(Boolean, default=False)
    terminos_boleto = Column(Text, nullable=True)
    consolidacao_id = Column(Integer, ForeignKey("pedidos_consolidados.id"), nullable=True, index=True)
    pedido_agrupado_id = Column(Integer, ForeignKey("pedidos_venda.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    cliente = relationship("Cliente", back_populates="pedidos_venda")
    itens = relationship("PedidoVendaItem", back_populates="pedido", cascade="all, delete-orphan")
    nfse = relationship("NFSe", back_populates="pedido", uselist=False)
    nfes = relationship("NFe", back_populates="pedido")
    consolidacao = relationship("PedidoConsolidado", back_populates="pedidos_origem")
    pedido_agrupado = relationship("PedidoVenda", remote_side=[id], back_populates="pedidos_origem_agrupamento")
    pedidos_origem_agrupamento = relationship("PedidoVenda", back_populates="pedido_agrupado")
    itens_origem_consolidado = relationship("PedidoConsolidadoItemOrigem", foreign_keys="[PedidoConsolidadoItemOrigem.pedido_origem_id]", back_populates="pedido_origem")


class PedidoVendaItem(Base):
    __tablename__ = "pedidos_venda_itens"

    id = Column(Integer, primary_key=True, index=True)
    pedido_id = Column(Integer, ForeignKey("pedidos_venda.id"), nullable=False, index=True)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=True, index=True)
    variacao_id = Column(Integer, ForeignKey("produto_variacoes.id"), nullable=True, index=True)
    item_pai_id = Column(Integer, ForeignKey("pedidos_venda_itens.id"), nullable=True, index=True)

    descricao = Column(Text, nullable=False)
    quantidade = Column(Numeric(12, 3), nullable=False, default=1)
    preco_unitario = Column(Numeric(12, 2), nullable=False, default=0)
    total = Column(Numeric(12, 2), nullable=False, default=0)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True, index=True)

    pedido = relationship("PedidoVenda", back_populates="itens")
    produto = relationship("Produto", back_populates="itens_venda")
    variacao = relationship("ProdutoVariacao", foreign_keys=[variacao_id], back_populates="itens_pedido")
    fornecedor = relationship("Fornecedor", back_populates="itens_venda")
    pai = relationship("PedidoVendaItem", remote_side=[id], back_populates="filhos")
    filhos = relationship("PedidoVendaItem", back_populates="pai")
    origens_consolidado = relationship("PedidoConsolidadoItemOrigem", foreign_keys="[PedidoConsolidadoItemOrigem.item_origem_id]", back_populates="item_origem")


class PedidoConsolidado(Base):
    """Pedido consolidado/agrupado - representa o pedido final de faturamento"""
    __tablename__ = "pedidos_consolidados"

    id = Column(Integer, primary_key=True, index=True)
    numero = Column(String(50), nullable=True, unique=True, index=True)
    data = Column(Date, nullable=False, default=date.today)
    data_fechamento = Column(Date, nullable=True)  # Data do fechamento mensal
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False, index=True)
    status = Column(Enum(StatusConsolidacao, native_enum=False), default=StatusConsolidacao.ABERTO, index=True)
    total = Column(Numeric(12, 2), nullable=False, default=0)
    observacao = Column(Text, nullable=True)
    forma_pagamento = Column(String(20), nullable=True)
    gerar_boleto = Column(Boolean, default=False)
    terminos_boleto = Column(Text, nullable=True)
    periodo_inicio = Column(Date, nullable=True)  # Início do período de consolidação
    periodo_fim = Column(Date, nullable=True)     # Fim do período de consolidação
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    finalizado_at = Column(DateTime, nullable=True)
    finalizado_por = Column(Integer, ForeignKey("usuarios.id"), nullable=True, index=True)

    cliente = relationship("Cliente", back_populates="consolidacoes")
    pedidos_origem = relationship("PedidoVenda", back_populates="consolidacao")
    itens = relationship("PedidoConsolidadoItem", back_populates="consolidacao", cascade="all, delete-orphan")
    contas_receber = relationship("ContaReceber", back_populates="consolidacao")
    nfse = relationship("NFSe", back_populates="consolidacao", uselist=False)
    finalizador = relationship("Usuario", foreign_keys=[finalizado_por], back_populates="consolidacoes_finalizadas")

    @property
    def qtd_pedidos_origem(self):
        return len(self.pedidos_origem) if self.pedidos_origem else 0

    @property
    def numeros_pedidos_origem(self):
        return ", ".join([p.numero or f"#{p.id}" for p in self.pedidos_origem]) if self.pedidos_origem else ""


class PedidoConsolidadoItem(Base):
    """Itens do pedido consolidado (podem ser agregados de múltiplos pedidos)"""
    __tablename__ = "pedidos_consolidados_itens"

    id = Column(Integer, primary_key=True, index=True)
    consolidacao_id = Column(Integer, ForeignKey("pedidos_consolidados.id"), nullable=False, index=True)
    produto_id = Column(Integer, ForeignKey("produtos.id"), nullable=True, index=True)
    variacao_id = Column(Integer, ForeignKey("produto_variacoes.id"), nullable=True, index=True)
    descricao = Column(Text, nullable=False)
    quantidade = Column(Numeric(12, 3), nullable=False, default=0)
    preco_unitario = Column(Numeric(12, 2), nullable=False, default=0)
    total = Column(Numeric(12, 2), nullable=False, default=0)
    unidade = Column(String(10), nullable=True, default="UN")
    ncm = Column(String(10), nullable=True)
    cfop = Column(String(4), nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    consolidacao = relationship("PedidoConsolidado", back_populates="itens")
    produto = relationship("Produto", back_populates="itens_consolidado")
    variacao = relationship("ProdutoVariacao", back_populates="itens_consolidado")
    itens_origem = relationship("PedidoConsolidadoItemOrigem", back_populates="item_consolidado", cascade="all, delete-orphan")


class PedidoConsolidadoItemOrigem(Base):
    """Rastreabilidade: vincula cada item consolidado aos itens originais dos pré-pedidos"""
    __tablename__ = "pedidos_consolidados_itens_origem"

    id = Column(Integer, primary_key=True, index=True)
    item_consolidado_id = Column(Integer, ForeignKey("pedidos_consolidados_itens.id"), nullable=False, index=True)
    pedido_origem_id = Column(Integer, ForeignKey("pedidos_venda.id"), nullable=False, index=True)
    item_origem_id = Column(Integer, ForeignKey("pedidos_venda_itens.id"), nullable=False, index=True)
    quantidade = Column(Numeric(12, 3), nullable=False, default=0)
    preco_unitario = Column(Numeric(12, 2), nullable=False, default=0)
    total = Column(Numeric(12, 2), nullable=False, default=0)

    item_consolidado = relationship("PedidoConsolidadoItem", back_populates="itens_origem")
    pedido_origem = relationship("PedidoVenda", foreign_keys=[pedido_origem_id], back_populates="itens_origem_consolidado")
    item_origem = relationship("PedidoVendaItem", foreign_keys=[item_origem_id], back_populates="origens_consolidado")


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
    sicoob_cert_password = Column(String(100), nullable=True)  # DEPRECATED: use cert_store.py instead
    sicoob_cert_base64 = deferred(Column(Text, nullable=True))  # DEPRECATED: use cert_store.py instead
    sicoob_cert_key_base64 = deferred(Column(Text, nullable=True))  # DEPRECATED: use cert_store.py instead
    sicoob_cert_id = Column(Integer, nullable=True)   # ID do certificado no armazenamento seguro
    smtp_host = Column(String(200), nullable=True)
    smtp_port = Column(Integer, default=587)
    smtp_user = Column(String(200), nullable=True)
    smtp_password = Column(String(200), nullable=True)
    smtp_from_email = Column(String(200), nullable=True)
    smtp_from_name = Column(String(200), nullable=True)
    email_auto_enviar = Column(Boolean, default=True)
    observacao = Column(Text, nullable=True)
    categoria_servico_padrao_id = Column(Integer, ForeignKey("categorias_produto.id"), nullable=True, index=True)
    notaas_api_key = Column(Text, nullable=True)
    aliquota_iss = Column(Numeric(7, 4), nullable=False, default=2.0)
    aliquota_federal = Column(Numeric(7, 4), nullable=False, default=0.0)
    aliquota_estadual = Column(Numeric(7, 4), nullable=False, default=0.0)
    aliquota_municipal = Column(Numeric(7, 4), nullable=False, default=0.0)
    nfe_aliquota_federal = Column(Numeric(7, 4), nullable=False, default=0.0)
    nfe_aliquota_estadual = Column(Numeric(7, 4), nullable=False, default=0.0)
    notaas_ambiente = Column(String(1), nullable=False, default="2")
    serie_nfe = Column(Integer, nullable=False, default=1)
    ultimo_numero_nfe = Column(Integer, nullable=False, default=0)
    ultimo_numero_nfse = Column(Integer, nullable=False, default=0)
    adn_emitidas_desabilitado = Column(Boolean, default=True)
    sefaz_emitidas_desabilitado = Column(Boolean, default=True)
    ultimo_numero_pedido = Column(Integer, default=0)
    ultimo_codigo_cliente = Column(Integer, default=0)
    ultimo_codigo_fornecedor = Column(Integer, default=0)
    ultimo_codigo_produto = Column(Integer, default=0)
    cert_path = Column(String(500), nullable=True)
    cert_password = Column(String(100), nullable=True)  # DEPRECATED: use cert_store.py instead
    cert_base64 = deferred(Column(Text, nullable=True))  # DEPRECATED: use cert_store.py instead
    cert_id = Column(Integer, nullable=True)   # ID do certificado no armazenamento seguro
    cert_validade = Column(Date, nullable=True)  # Data de validade do certificado
    nfe_ultnsu = Column(String(20), nullable=True)  # Último NSU consultado na SEFAZ
    cfop_padrao = Column(String(4), nullable=False, default="5102")
    fuso_horario = Column(Integer, nullable=False, default=-4)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    categoria_servico_padrao = relationship("CategoriaProduto", back_populates="empresas")


class TipoDocumento(Base):
    __tablename__ = "tipos_documento"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.now)

    contas_pagar = relationship("ContaPagar", back_populates="tipo_documento")
    contas_receber = relationship("ContaReceber", back_populates="tipo_documento")


class CondicaoPagamento(Base):
    __tablename__ = "condicoes_pagamento"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False, unique=True)
    num_parcelas = Column(Integer, nullable=False, default=1)
    intervalo_dias = Column(Integer, nullable=False, default=30)
    primeiro_vencimento = Column(Integer, nullable=False, default=0)
    ativo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class PlanoDeContas(Base):
    __tablename__ = "plano_contas"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(20), nullable=False)
    nome = Column(String(200), nullable=False)
    tipo = Column(String(10), nullable=False)  # "receita" ou "despesa"
    parent_id = Column(Integer, ForeignKey("plano_contas.id"), nullable=True, index=True)
    nivel = Column(Integer, default=1)
    ativo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

    parent = relationship("PlanoDeContas", back_populates="children", remote_side=[parent_id])
    children = relationship("PlanoDeContas", back_populates="parent", remote_side=[id])
    contas_pagar = relationship("ContaPagar", back_populates="plano_conta")
    contas_receber = relationship("ContaReceber", back_populates="plano_conta")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True, index=True)
    acao = Column(String(100), nullable=False, index=True)
    entidade = Column(String(100), nullable=True)
    entidade_id = Column(Integer, nullable=True)
    detalhes = Column(Text, nullable=True)
    ip = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)

    usuario = relationship("Usuario", back_populates="audit_logs")


class CfopNatureza(Base):
    __tablename__ = "cfop_natureza"
    id = Column(Integer, primary_key=True)
    cfop = Column(String(4), nullable=False, index=True)
    natureza = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class HistoricoCadastro(Base):
    __tablename__ = "historico_cadastro"

    id = Column(Integer, primary_key=True)
    entidade_tipo = Column(String(20), nullable=False, index=True)  # cliente, fornecedor
    entidade_id = Column(Integer, nullable=False, index=True)
    campo = Column(String(50), nullable=False)
    rotulo = Column(String(80), nullable=True)
    valor_antigo = Column(Text, nullable=True)
    valor_novo = Column(Text, nullable=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    data = Column(DateTime, default=datetime.now, index=True)

    usuario = relationship("Usuario", primaryjoin="HistoricoCadastro.usuario_id == Usuario.id", foreign_keys=[usuario_id], back_populates="historico_cadastros")

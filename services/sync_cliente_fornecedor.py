from sqlalchemy.orm import Session
from models import Cliente, Fornecedor


def upsert_fornecedor_de_cliente(db: Session, cliente: Cliente):
    """Cria ou atualiza um Fornecedor a partir dos dados de um Cliente."""
    doc = (cliente.cpf_cnpj or "").strip()
    if doc:
        fornecedor = db.query(Fornecedor).filter(
            Fornecedor.cpf_cnpj == doc
        ).first()
    else:
        fornecedor = None

    if fornecedor:
        fornecedor.nome = cliente.nome
        fornecedor.tipo_pessoa = cliente.tipo_pessoa
        fornecedor.email = cliente.email
        fornecedor.telefone = cliente.telefone
        fornecedor.celular = cliente.celular
        fornecedor.endereco = cliente.endereco
        fornecedor.numero = cliente.numero
        fornecedor.complemento = cliente.complemento
        fornecedor.bairro = cliente.bairro
        fornecedor.cidade = cliente.cidade
        fornecedor.estado = cliente.estado
        fornecedor.cep = cliente.cep
        fornecedor.contato = cliente.contato
        fornecedor.fantasia = cliente.fantasia
        fornecedor.inscricao_estadual = cliente.inscricao_estadual
        fornecedor.inscricao_municipal = cliente.inscricao_municipal
        fornecedor.codigo_ibge = cliente.codigo_ibge
        fornecedor.isento_ie = cliente.isento_ie
        fornecedor.indicador_ie = cliente.indicador_ie
        fornecedor.situacao = cliente.situacao
        fornecedor.observacao = cliente.observacao
    else:
        fornecedor = Fornecedor(
            nome=cliente.nome,
            cpf_cnpj=cliente.cpf_cnpj,
            tipo_pessoa=cliente.tipo_pessoa,
            email=cliente.email,
            telefone=cliente.telefone,
            celular=cliente.celular,
            endereco=cliente.endereco,
            numero=cliente.numero,
            complemento=cliente.complemento,
            bairro=cliente.bairro,
            cidade=cliente.cidade,
            estado=cliente.estado,
            cep=cliente.cep,
            contato=cliente.contato,
            fantasia=cliente.fantasia,
            inscricao_estadual=cliente.inscricao_estadual,
            inscricao_municipal=cliente.inscricao_municipal,
            codigo_ibge=cliente.codigo_ibge,
            isento_ie=cliente.isento_ie,
            indicador_ie=cliente.indicador_ie,
            situacao=cliente.situacao,
            observacao=cliente.observacao,
            bling_pending_sync=True,
        )
        db.add(fornecedor)

    db.commit()


def upsert_cliente_de_fornecedor(db: Session, fornecedor: Fornecedor):
    """Cria ou atualiza um Cliente a partir dos dados de um Fornecedor."""
    doc = (fornecedor.cpf_cnpj or "").strip()
    if doc:
        cliente = db.query(Cliente).filter(
            Cliente.cpf_cnpj == doc
        ).first()
    else:
        cliente = None

    if cliente:
        cliente.nome = fornecedor.nome
        cliente.tipo_pessoa = fornecedor.tipo_pessoa
        cliente.email = fornecedor.email
        cliente.telefone = fornecedor.telefone
        cliente.celular = fornecedor.celular
        cliente.endereco = fornecedor.endereco
        cliente.numero = fornecedor.numero
        cliente.complemento = fornecedor.complemento
        cliente.bairro = fornecedor.bairro
        cliente.cidade = fornecedor.cidade
        cliente.estado = fornecedor.estado
        cliente.cep = fornecedor.cep
        cliente.contato = fornecedor.contato
        cliente.fantasia = fornecedor.fantasia
        cliente.inscricao_estadual = fornecedor.inscricao_estadual
        cliente.inscricao_municipal = fornecedor.inscricao_municipal
        cliente.codigo_ibge = fornecedor.codigo_ibge
        cliente.isento_ie = fornecedor.isento_ie
        cliente.indicador_ie = fornecedor.indicador_ie
        cliente.situacao = fornecedor.situacao
        cliente.observacao = fornecedor.observacao
    else:
        cliente = Cliente(
            nome=fornecedor.nome,
            cpf_cnpj=fornecedor.cpf_cnpj,
            tipo_pessoa=fornecedor.tipo_pessoa,
            email=fornecedor.email,
            telefone=fornecedor.telefone,
            celular=fornecedor.celular,
            endereco=fornecedor.endereco,
            numero=fornecedor.numero,
            complemento=fornecedor.complemento,
            bairro=fornecedor.bairro,
            cidade=fornecedor.cidade,
            estado=fornecedor.estado,
            cep=fornecedor.cep,
            contato=fornecedor.contato,
            fantasia=fornecedor.fantasia,
            inscricao_estadual=fornecedor.inscricao_estadual,
            inscricao_municipal=fornecedor.inscricao_municipal,
            codigo_ibge=fornecedor.codigo_ibge,
            isento_ie=fornecedor.isento_ie,
            indicador_ie=fornecedor.indicador_ie,
            situacao=fornecedor.situacao,
            observacao=fornecedor.observacao,
            bling_pending_sync=True,
        )
        db.add(cliente)

    db.commit()

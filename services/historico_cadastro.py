"""Registro de histórico de alterações em cadastros (cliente/fornecedor)."""
from sqlalchemy.orm import Session
from models import HistoricoCadastro, Usuario

# Rótulos amigáveis dos campos
ROTULOS = {
    "nome": "Nome/Razão Social",
    "fantasia": "Nome Fantasia",
    "cpf_cnpj": "CPF/CNPJ",
    "tipo_pessoa": "Tipo de Pessoa",
    "email": "Email",
    "telefone": "Telefone",
    "celular": "Celular",
    "contato": "Contato",
    "endereco": "Endereço",
    "numero": "Número",
    "complemento": "Complemento",
    "bairro": "Bairro",
    "cidade": "Cidade",
    "estado": "Estado",
    "cep": "CEP",
    "codigo_ibge": "Cód. IBGE",
    "inscricao_estadual": "Inscrição Estadual",
    "inscricao_municipal": "Inscrição Municipal",
    "isento_ie": "Isento IE",
    "indicador_ie": "Indicador IE",
    "iss_retido": "ISS Retido",
    "situacao": "Situação",
    "observacao": "Observação",
}


def _normalizar(valor):
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "Sim" if valor else "Não"
    return str(valor).strip()


def registrar_historico(db: Session, entidade_tipo: str, entidade_id: int,
                         campos: dict, usuario_id: int = None):
    """Compara valores antigos e novos e registra as diferenças.

    `campos` = {nome_campo: (valor_antigo, valor_novo)}
    """
    registros = []
    for campo, (antigo, novo) in campos.items():
        a = _normalizar(antigo)
        n = _normalizar(novo)
        if a != n:
            h = HistoricoCadastro(
                entidade_tipo=entidade_tipo,
                entidade_id=entidade_id,
                campo=campo,
                rotulo=ROTULOS.get(campo, campo),
                valor_antigo=a,
                valor_novo=n,
                usuario_id=usuario_id,
            )
            db.add(h)
            registros.append(h)
    if registros:
        db.flush()
    return registros

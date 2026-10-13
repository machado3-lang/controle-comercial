"""
Devolucao da numeracao ao excluir um rascunho.

O numero da nota e consumido no momento da criacao do rascunho (`_proximo_numero`
em routers/nfe.py e routers/nfse.py) e nunca era devolvido na exclusao: todo
rascunho apagado queimava um numero da sequencia, deixando um buraco entre a
ultima nota autorizada na SEFAZ/prefeitura e o contador em Configuracoes.

As funcoes abaixo devolvem o numero ao contador, mas so quando e seguro:

- a nota nunca foi autorizada (sem invoice_id/chave/protocolo/DPS), ou seja, o
  numero nao chegou a ser registrado na SEFAZ/prefeitura e pode ser reusado;
- `empresa.ultimo_numero_*` ainda aponta exatamente para o numero excluido, o que
  significa que nenhum rascunho posterior consumiu a sequencia. Se o contador
  ja avancou, mexer nele faria o proximo rascunho colidir com uma nota existente;
- nenhuma outra nota usa aquele numero (evita criar duplicata).

O contador da DPS (`empresa.ultimo_numero_dps`) nunca e devolvido: e uma
sequencia propria, alocada na transmissao, e a SEFIN rejeita reuso de DPS ja
finalizada (E050).
"""
from sqlalchemy.orm import Session

from models import Empresa
from models_nfe import NFe, NFSe


def _para_int(valor):
    texto = str(valor if valor is not None else "").strip()
    return int(texto) if texto.isdigit() else None


def _reverter(db: Session, empresa: Empresa, campo: str, modelo, nota_id, numero, valor):
    """
    Aponta `empresa.<campo>` para `numero - 1` e devolve o novo valor, ou None
    se a devolucao nao for segura. `valor` e o numero como gravado na coluna
    `numero` da nota (int na NFe, texto na NFSe), usado na checagem de duplicata.
    """
    if empresa is None or numero is None:
        return None
    if (getattr(empresa, campo, 0) or 0) != numero:
        return None
    outra = (
        db.query(modelo.id)
        .filter(modelo.id != nota_id, modelo.numero == valor)
        .first()
    )
    if outra is not None:
        return None
    setattr(empresa, campo, numero - 1)
    return numero - 1


def reverter_numero_nfe(db: Session, empresa: Empresa, nfe: NFe):
    """Devolve o numero de um rascunho de NFe ao contador. Chamar ANTES de `db.delete(nfe)`."""
    if nfe.invoice_id or nfe.chave_acesso or nfe.protocolo:
        return None
    return _reverter(
        db, empresa, "ultimo_numero_nfe", NFe, nfe.id, _para_int(nfe.numero), nfe.numero
    )


def reverter_numero_nfse(db: Session, empresa: Empresa, nfse: NFSe):
    """
    Devolve o numero de um rascunho de NFSe ao contador. Chamar ANTES de `db.delete(nfse)`.
    `nfse.numero` e texto e pode assumir valores nao numericos (ex.: "Pendente"),
    que simplesmente nao devolvem numero ao contador.
    """
    if nfse.chave_acesso or nfse.codigo_verificacao or nfse.protocolo or nfse.numero_dps:
        return None
    return _reverter(
        db, empresa, "ultimo_numero_nfse", NFSe, nfse.id, _para_int(nfse.numero), nfse.numero
    )
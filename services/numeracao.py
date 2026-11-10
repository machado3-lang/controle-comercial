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
import logging
import re

from sqlalchemy.orm import Session

from models import Empresa
from models_nfe import NFe, NFSe

logger = logging.getLogger(__name__)


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


# --------------------------------------------------------------------------
# Numeracao NFe a partir da chave de acesso (fonte de verdade da SEFAZ)
#
# Layout da chave (44 digitos):
#   cUF(2) AAMM(4) CNPJ(14) mod(2) serie(3) nNF(9) tpEmis(1) cNF(8) DV(1)
# --------------------------------------------------------------------------

def _digitos_chave(chave) -> str:
    return re.sub(r"\D", "", str(chave or ""))


def numero_da_chave(chave):
    """Retorna o nNF gravado na chave de acesso, ou None se a chave for invalida."""
    digitos = _digitos_chave(chave)
    if len(digitos) != 44:
        return None
    try:
        return int(digitos[25:34])
    except ValueError:
        return None


def serie_da_chave(chave):
    """Retorna a serie gravada na chave de acesso, ou None se a chave for invalida."""
    digitos = _digitos_chave(chave)
    if len(digitos) != 44:
        return None
    try:
        return int(digitos[22:25])
    except ValueError:
        return None


def reconciliar_numero_nfe(
    db: Session, empresa: Empresa, nfe: NFe, numero=None, serie=None
):
    """Alinha `nfe.numero`/`nfe.serie` com a chave de acesso autorizada.

    A chave e a fonte de verdade do que a SEFAZ autorizou. Se o numero local
    divergir (ex.: ajuste manual, carga externa, emissao dupla antiga), a coluna
    e corrigida e o contador so SOBE (nunca desce) para nao reabrir a sequencia.

    Nao sobrescreve quando o numero ja pertence a outra nota (evita duplicata) e
    nao faz commit: o chamador decide quando persistir.

    Retorna True quando alterou numero ou serie.
    """
    if numero is None:
        numero = numero_da_chave(nfe.chave_acesso)
    if serie is None:
        serie = serie_da_chave(nfe.chave_acesso)

    alterou = False
    serie_alvo = serie or nfe.serie
    if numero is not None and numero != nfe.numero:
        conflito = (
            db.query(NFe.id)
            .filter(
                NFe.id != nfe.id,
                NFe.numero == numero,
                NFe.serie == serie_alvo,
            )
            .first()
        )
        if conflito is not None:
            logger.warning(
                "NFe #%s: numero local %s diverge da chave (%s), mas a NFe #%s "
                "ja usa esse numero na serie %s; reconciliacao ignorada",
                nfe.id, nfe.numero, numero, conflito[0], serie_alvo,
            )
        else:
            logger.warning(
                "NFe #%s: numero local %s divergia da chave (%s); corrigido",
                nfe.id, nfe.numero, numero,
            )
            nfe.numero = numero
            alterou = True

    if serie is not None and nfe.serie != serie:
        nfe.serie = serie
        alterou = True

    if empresa is not None and numero is not None:
        if (empresa.ultimo_numero_nfe or 0) < numero:
            empresa.ultimo_numero_nfe = numero

    return alterou


def proximo_numero_nfe(db: Session, empresa: Empresa, commit: bool = True) -> int:
    """Aloca o proximo numero de NFe, sem reutilizar numero ja existente.

    O `with_for_update()` serializa criacoes concorrentes. Pular numeros ja
    usados evita duplicata quando o contador foi reduzido manualmente ou veio
    de um backup antigo. `commit=False` mantem a transacao do chamador (usado na
    emissao de consolidacao, que tem alteracoes pendentes na mesma sessao).
    """
    empresa_locked = (
        db.query(Empresa).filter(Empresa.id == empresa.id).with_for_update().first()
    )
    if empresa_locked is None:
        empresa_locked = empresa

    serie = empresa_locked.serie_nfe or 1
    candidato = (empresa_locked.ultimo_numero_nfe or 0) + 1
    while (
        db.query(NFe.id)
        .filter(NFe.numero == candidato, NFe.serie == serie)
        .first()
        is not None
    ):
        candidato += 1

    empresa_locked.ultimo_numero_nfe = candidato
    if commit:
        db.commit()
    else:
        db.flush()
    return candidato

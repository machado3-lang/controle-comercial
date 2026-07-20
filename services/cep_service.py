"""Consulta de CEP via ViaCEP (https://viacep.com.br/). Sem autenticacao."""
import logging
import re
import requests

logger = logging.getLogger(__name__)

VIACEP_URL = "https://viacep.com.br/ws/{cep}/json/"
_TIMEOUT = 6


def _somente_digitos(cep):
    return re.sub(r"\D", "", cep or "")


def buscar_cep(cep):
    """Retorna dict com endereco ou None em caso de erro/nao encontrado.

    Campos: cep, logradouro, complemento, bairro, localidade, uf, ibge.
    """
    cep_limpo = _somente_digitos(cep)
    if len(cep_limpo) != 8:
        return None
    try:
        r = requests.get(VIACEP_URL.format(cep=cep_limpo), timeout=_TIMEOUT,
                         headers={"User-Agent": "ControleComercial/1.0"})
        r.raise_for_status()
        data = r.json()
        if data.get("erro"):
            return None
        return {
            "cep": data.get("cep", ""),
            "logradouro": data.get("logradouro", ""),
            "complemento": data.get("complemento", ""),
            "bairro": data.get("bairro", ""),
            "localidade": data.get("localidade", ""),
            "uf": data.get("uf", ""),
            "ibge": data.get("ibge", ""),
        }
    except Exception as e:
        logger.warning(f"ViaCEP erro para {cep}: {e}")
        return None

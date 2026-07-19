"""Consulta de CNPJ via publica.cnpj.ws (https://publica.cnpj.ws/).

Sem chave para a API publica. Implementa:
- Validacao local do digito verificador (base 10 para somente digitos,
  base 36 para CNPJ alfanumerico - Lei 14.823/24).
- Retry com backoff para erro 429 (limite de requisicoes).
- Mapeamento dos campos para o cadastro de cliente/fornecedor.
"""
import logging
import re
import time
import requests

logger = logging.getLogger(__name__)

CNPJ_WS_URL = "https://publica.cnpj.ws/cnpj/{cnpj}/"
_TIMEOUT = 10
_SITUACOES_IRREGULARES = {"BAIXADA", "SUSPENSA", "INAPTA", "NULA"}


def _normalizar(cnpj):
    """Remove mascara e deixa maiusculo. Mantem letras (alfanumerico)."""
    return re.sub(r"[^A-Za-z0-9]", "", cnpj or "").upper()


def validar_dv_cnpj(cnpj):
    """Valida o digito verificador. Suporta CNPJ alfanumerico (base 36).

    Retorna True se valido, False se invalido, None se vazio/curto.
    """
    c = _normalizar(cnpj)
    if len(c) != 14:
        return None
    if len(set(c)) == 1:
        return False  # todos iguais

    # Pesos primeiros 12 e segundo DV
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

    def char_val(ch):
        # base 36: 0-9 = 0-9, A-Z = 10-35
        return int(ch, 36)

    def calc_dv(corpo, pesos):
        soma = sum(char_val(ch) * p for ch, p in zip(corpo, pesos))
        resto = soma % 11
        return 0 if resto < 2 else 11 - resto

    corpo = c[:12]
    dv1 = calc_dv(corpo, pesos1)
    dv2 = calc_dv(c[:13], pesos2)
    return dv1 == int(c[12], 36) and dv2 == int(c[13], 36)


def buscar_cnpj(cnpj, max_tentativas=3):
    """Consulta a API publica. Retorna dict mapeado ou dict com 'erro'.

    Em caso de 429 faz retry com backoff exponencial.
    """
    c = _normalizar(cnpj)
    if not c:
        return {"erro": "CNPJ vazio"}
    if validar_dv_cnpj(c) is False:
        return {"erro": "CNPJ inválido (dígito verificador)"}

    tentativa = 0
    while tentativa < max_tentativas:
        tentativa += 1
        try:
            r = requests.get(CNPJ_WS_URL.format(cnpj=c), timeout=_TIMEOUT)
            if r.status_code == 429:
                espera = 2 ** tentativa
                logger.warning(f"cnpj.ws 429 - aguardando {espera}s (tentativa {tentativa})")
                time.sleep(espera)
                continue
            r.raise_for_status()
            data = r.json()
            return _mapear(data)
        except requests.HTTPError as e:
            if r.status_code == 404:
                return {"erro": "CNPJ não encontrado na Receita Federal"}
            logger.warning(f"cnpj.ws HTTPError: {e}")
            return {"erro": f"Erro na consulta ({r.status_code})"}
        except Exception as e:
            logger.warning(f"cnpj.ws erro: {e}")
            return {"erro": "Falha ao consultar CNPJ"}
    return {"erro": "Limite de 3 consultas por minuto da API atingido. Aguarde cerca de 1 minuto e tente novamente."}


def _mapear(data):
    est = data.get("estabelecimento", {}) or {}
    endereco = " ".join(filter(None, [
        est.get("tipo_logradouro", ""),
        est.get("logradouro", ""),
    ])).strip()
    inscricoes = est.get("inscricoes_estaduais") or []
    # pega a primeira inscricao estadual valida (sem a tag de ISENTO se possivel)
    inscricao_estadual = ""
    for insc in inscricoes:
        val = (insc.get("inscricao_estadual") or "").strip()
        if val and val.upper() != "ISENTO":
            inscricao_estadual = val
            break
    if not inscricao_estadual and inscricoes:
        inscricao_estadual = (inscricoes[0].get("inscricao_estadual") or "").strip()

    situacao = (est.get("situacao_cadastral") or "").upper()
    irregular = situacao in _SITUACOES_IRREGULARES

    return {
        "cnpj": est.get("cnpj") or data.get("cnpj_raiz", ""),
        "razao_social": data.get("razao_social", ""),
        "nome_fantasia": est.get("nome_fantasia") or "",
        "situacao_cadastral": est.get("situacao_cadastral") or "",
        "irregular": irregular,
        "inscricao_estadual": inscricao_estadual,
        "inscricao_municipal": "",
        "endereco": endereco,
        "numero": est.get("numero") or "",
        "complemento": est.get("complemento") or "",
        "bairro": est.get("bairro") or "",
        "cidade": (est.get("cidade") or {}).get("nome") or "",
        "estado": (est.get("estado") or {}).get("sigla") or "",
        "cep": est.get("cep") or "",
        "email": est.get("email") or "",
        "telefone": "",
        "atualizado_em": data.get("atualizado_em") or est.get("atualizado_em") or "",
    }

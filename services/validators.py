"""
Validadores centralizados para CPF, CNPJ, IE, CEP, telefone, email.
Substitui validações duplicadas em routers/bling.py, routers/nfe.py, routers/nfse.py, routers/clientes.py, etc.
"""
import re
from typing import Optional, Tuple, List
from pydantic import EmailStr


# CPF validation
CPF_PATTERN = re.compile(r'^\d{11}$')

def validar_cpf(cpf: str) -> bool:
    """Valida CPF usando algoritmo oficial."""
    if not cpf:
        return False
    cpf = re.sub(r'\D', '', cpf)
    if not CPF_PATTERN.match(cpf):
        return False
    if cpf == cpf[0] * 11:  # Todos dígitos iguais
        return False
    
    # Calcula primeiro dígito verificador
    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    resto = soma % 11
    digito1 = 0 if resto < 2 else 11 - resto
    
    # Calcula segundo dígito verificador
    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    resto = soma % 11
    digito2 = 0 if resto < 2 else 11 - resto
    
    return cpf[9] == str(digito1) and cpf[10] == str(digito2)


def formatar_cpf(cpf: str) -> str:
    """Formata CPF: 000.000.000-00"""
    cpf = re.sub(r'\D', '', cpf or '')
    if len(cpf) == 11:
        return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    return cpf


# CNPJ validation
CNPJ_PATTERN = re.compile(r'^\d{14}$')

def validar_cnpj(cnpj: str) -> bool:
    """Valida CNPJ usando algoritmo oficial."""
    if not cnpj:
        return False
    cnpj = re.sub(r'\D', '', cnpj)
    if not CNPJ_PATTERN.match(cnpj):
        return False
    if cnpj == cnpj[0] * 14:
        return False
    
    # Primeiro dígito
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma = sum(int(cnpj[i]) * pesos1[i] for i in range(12))
    resto = soma % 11
    digito1 = 0 if resto < 2 else 11 - resto
    
    # Segundo dígito
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma = sum(int(cnpj[i]) * pesos2[i] for i in range(13))
    resto = soma % 11
    digito2 = 0 if resto < 2 else 11 - resto
    
    return cnpj[12] == str(digito1) and cnpj[13] == str(digito2)


def formatar_cnpj(cnpj: str) -> str:
    """Formata CNPJ: 00.000.000/0000-00"""
    cnpj = re.sub(r'\D', '', cnpj or '')
    if len(cnpj) == 14:
        return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
    return cnpj


# IE (Inscrição Estadual) - validação básica por UF
IE_PATTERNS = {
    'SP': re.compile(r'^\d{12}$'),
    'MG': re.compile(r'^\d{13}$'),
    'RJ': re.compile(r'^\d{8}$'),
    'RS': re.compile(r'^\d{10}$'),
    'PR': re.compile(r'^\d{10}$'),
    'SC': re.compile(r'^\d{9}$'),
    'BA': re.compile(r'^\d{8,9}$'),
    'GO': re.compile(r'^\d{9}$'),
    'PE': re.compile(r'^\d{9,14}$'),
    'CE': re.compile(r'^\d{9}$'),
    'PA': re.compile(r'^\d{9}$'),
    'MA': re.compile(r'^\d{9}$'),
    'MT': re.compile(r'^\d{11}$'),
    'MS': re.compile(r'^\d{9}$'),
    'DF': re.compile(r'^\d{13}$'),
}

def validar_ie(ie: str, uf: str) -> bool:
    """Valida Inscrição Estadual por UF (validação básica de formato)."""
    if not ie or not uf:
        return True  # Opcional em muitos casos
    ie = re.sub(r'\D', '', ie)
    uf = uf.upper()
    pattern = IE_PATTERNS.get(uf)
    if pattern:
        return bool(pattern.match(ie))
    return True  # UF não mapeada: aceita qualquer formato


# CEP validation
CEP_PATTERN = re.compile(r'^\d{8}$')

def validar_cep(cep: str) -> bool:
    """Valida CEP (8 dígitos)."""
    if not cep:
        return True  # Opcional
    cep = re.sub(r'\D', '', cep)
    return bool(CEP_PATTERN.match(cep))


def formatar_cep(cep: str) -> str:
    """Formata CEP: 00000-000"""
    cep = re.sub(r'\D', '', cep or '')
    if len(cep) == 8:
        return f"{cep[:5]}-{cep[5:]}"
    return cep


# Telefone validation
TELEFONE_PATTERN = re.compile(r'^(?:\(?\d{2}\)?\s?)?(?:9\d{4}|\d{4})-?\d{4}$')

def validar_telefone(tel: str) -> bool:
    """Valida telefone brasileiro (fixo ou celular)."""
    if not tel:
        return True  # Opcional
    tel = re.sub(r'\D', '', tel)
    return len(tel) in (10, 11)  # 10 dígitos fixo, 11 celular


def formatar_telefone(tel: str) -> str:
    """Formata telefone: (00) 0000-0000 ou (00) 90000-0000"""
    tel = re.sub(r'\D', '', tel or '')
    if len(tel) == 10:
        return f"({tel[:2]}) {tel[2:6]}-{tel[6:]}"
    elif len(tel) == 11:
        return f"({tel[:2]}) {tel[2:7]}-{tel[7:]}"
    return tel


# Email validation (usa pydantic EmailStr se disponível, senão regex simples)
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

def validar_email(email: str) -> bool:
    """Validação básica de email."""
    if not email:
        return True  # Opcional
    return bool(EMAIL_PATTERN.match(email))


# Validar documento genérico (CPF ou CNPJ)
def validar_cpf_cnpj(doc: str) -> Tuple[bool, str]:
    """
    Valida CPF ou CNPJ automaticamente. Suporta CNPJ alfanumérico
    (Lei 14.823/24) usando base 36 no cálculo do dígito verificador.
    Returns: (is_valid, tipo) onde tipo é 'cpf', 'cnpj' ou 'invalido'
    """
    if not doc:
        return False, 'invalido'
    doc_norm = re.sub(r'[^A-Za-z0-9]', '', doc).upper()
    if len(doc_norm) == 11 and validar_cpf(doc_norm):
        return True, 'cpf'
    elif len(doc_norm) == 14:
        if _validar_dv_cnpj_alfa(doc_norm):
            return True, 'cnpj'
    return False, 'invalido'


def _validar_dv_cnpj_alfa(cnpj):
    """Dígito verificador CNPJ (base 36 p/ alfanumérico, base 10 p/ numérico)."""
    if len(set(cnpj)) == 1:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    try:
        def v(ch):
            return int(ch, 36)
        s1 = sum(v(ch) * p for ch, p in zip(cnpj[:12], pesos1))
        d1 = 0 if s1 % 11 < 2 else 11 - (s1 % 11)
        s2 = sum(v(ch) * p for ch, p in zip(cnpj[:13], pesos2))
        d2 = 0 if s2 % 11 < 2 else 11 - (s2 % 11)
        return d1 == v(cnpj[12]) and d2 == v(cnpj[13])
    except ValueError:
        return False


def formatar_cpf_cnpj(doc: str) -> str:
    """Formata CPF ou CNPJ automaticamente. Mantém letras (alfanumérico)."""
    doc = re.sub(r'[^A-Za-z0-9]', '', doc or '').upper()
    if len(doc) == 11:
        return formatar_cpf(doc)
    elif len(doc) == 14:
        return formatar_cnpj(doc)
    return doc


# Validação completa de cliente/fornecedor
def validar_cliente_fornecedor(
    nome: str,
    tipo_pessoa: str,
    cpf_cnpj: str,
    ie: str = None,
    uf: str = None,
    cep: str = None,
    telefone: str = None,
    celular: str = None,
    email: str = None,
) -> List[str]:
    """
    Valida todos os campos de um cliente/fornecedor.
    Returns: Lista de erros (vazio se válido).
    """
    erros = []
    
    if not nome or not nome.strip():
        erros.append("Nome/Razão Social é obrigatório")
    
    if tipo_pessoa not in ('fisica', 'juridica'):
        erros.append("Tipo de pessoa deve ser 'fisica' ou 'juridica'")
    
    if cpf_cnpj:
        valido, tipo = validar_cpf_cnpj(cpf_cnpj)
        if not valido:
            erros.append(f"CPF/CNPJ inválido")
        elif tipo_pessoa == 'fisica' and tipo != 'cpf':
            erros.append("Pessoa física deve ter CPF")
        elif tipo_pessoa == 'juridica' and tipo != 'cnpj':
            erros.append("Pessoa jurídica deve ter CNPJ")
    elif tipo_pessoa:  # Se tipo_pessoa foi informado mas cpf_cnpj não
        erros.append("CPF/CNPJ é obrigatório")
    
    if ie and uf:
        if not validar_ie(ie, uf):
            erros.append(f"Inscrição Estadual inválida para {uf}")
    
    if cep and not validar_cep(cep):
        erros.append("CEP inválido (deve ter 8 dígitos)")
    
    if telefone and not validar_telefone(telefone):
        erros.append("Telefone inválido")
    
    if celular and not validar_telefone(celular):
        erros.append("Celular inválido")
    
    if email and not validar_email(email):
        erros.append("Email inválido")
    
    return erros


# Função utilitária para limpar documento
def limpar_doc(doc: str) -> str:
    """Remove caracteres não numéricos do documento."""
    return re.sub(r'\D', '', doc or '')


def limpar_cep(cep: str) -> str:
    """Remove caracteres não numéricos do CEP."""
    return re.sub(r'\D', '', cep or '')


def limpar_telefone(tel: str) -> str:
    """Remove caracteres não numéricos do telefone."""
    return re.sub(r'\D', '', tel or '')
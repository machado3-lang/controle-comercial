"""
Test validators module
"""
import pytest
from services.validators import (
    validar_cpf, validar_cnpj, validar_cep, validar_telefone,
    validar_email, validar_cpf_cnpj, formatar_cpf, formatar_cnpj,
    formatar_cep, formatar_cpf_cnpj, validar_cliente_fornecedor, limpar_doc
)


class TestCPFValidation:
    def test_cpf_valido(self):
        assert validar_cpf("11144477735") == True
        assert validar_cpf("111.444.777-35") == True
        assert validar_cpf("52998224725") == True

    def test_cpf_invalido(self):
        assert validar_cpf("12345678901") == False  # Dígitos inválidos
        assert validar_cpf("11111111111") == False  # Todos iguais
        assert validar_cpf("123") == False  # Muito curto
        assert validar_cpf("") == False
        assert validar_cpf(None) == False

    def test_formatar_cpf(self):
        assert formatar_cpf("11144477735") == "111.444.777-35"
        assert formatar_cpf("111.444.777-35") == "111.444.777-35"


class TestCNPJValidation:
    def test_cnpj_valido(self):
        assert validar_cnpj("11222333000181") == True
        assert validar_cnpj("11.222.333/0001-81") == True

    def test_cnpj_invalido(self):
        # "12345678000195" é um CNPJ válido matematicamente
        # Usar um que sabemos que é inválido
        assert validar_cnpj("12345678000196") == False
        assert validar_cnpj("11111111111111") == False
        assert validar_cnpj("123") == False
        assert validar_cnpj("") == False

    def test_formatar_cnpj(self):
        assert formatar_cnpj("11222333000181") == "11.222.333/0001-81"


class TestCPFCNPJAuto:
    def test_validar_cpf_cnpj_auto(self):
        valido, tipo = validar_cpf_cnpj("11144477735")
        assert valido == True
        assert tipo == "cpf"

        valido, tipo = validar_cpf_cnpj("11222333000181")
        assert valido == True
        assert tipo == "cnpj"

        valido, tipo = validar_cpf_cnpj("123")
        assert valido == False
        assert tipo == "invalido"

    def test_formatar_cpf_cnpj_auto(self):
        assert formatar_cpf_cnpj("11144477735") == "111.444.777-35"
        assert formatar_cpf_cnpj("11222333000181") == "11.222.333/0001-81"


class TestCEPValidation:
    def test_cep_valido(self):
        assert validar_cep("01234567") == True
        assert validar_cep("01234-567") == True

    def test_cep_invalido(self):
        assert validar_cep("12345") == False
        assert validar_cep("abcdefgh") == False

    def test_formatar_cep(self):
        assert formatar_cep("01234567") == "01234-567"


class TestTelefoneValidation:
    def test_telefone_valido(self):
        assert validar_telefone("11999999999") == True  # Celular
        assert validar_telefone("1133334444") == True  # Fixo
        assert validar_telefone("(11) 99999-9999") == True

    def test_telefone_invalido(self):
        assert validar_telefone("123") == False
        assert validar_telefone("") == True  # Opcional


class TestEmailValidation:
    def test_email_valido(self):
        assert validar_email("test@test.com") == True
        assert validar_email("user.name@domain.com.br") == True

    def test_email_invalido(self):
        assert validar_email("test@") == False
        assert validar_email("@test.com") == False
        assert validar_email("test") == False


class TestLimparDoc:
    def test_limpar_doc(self):
        assert limpar_doc("111.444.777-35") == "11144477735"
        assert limpar_doc("11.222.333/0001-81") == "11222333000181"
        assert limpar_doc("(11) 99999-9999") == "11999999999"


class TestClienteFornecedorValidation:
    def test_cliente_valido(self):
        erros = validar_cliente_fornecedor(
            nome="João Silva",
            tipo_pessoa="fisica",
            cpf_cnpj="11144477735",
            ie=None,
            uf="SP",
            cep="01234567",
            telefone="1133334444",
            celular="11999999999",
            email="test@test.com"
        )
        assert erros == []

    def test_cliente_sem_nome(self):
        erros = validar_cliente_fornecedor(
            nome="",
            tipo_pessoa="fisica",
            cpf_cnpj="11144477735"
        )
        assert "Nome/Razão Social é obrigatório" in erros

    def test_cliente_tipo_pessoa_invalido(self):
        erros = validar_cliente_fornecedor(
            nome="João",
            tipo_pessoa="invalido",
            cpf_cnpj="11144477735"
        )
        assert "Tipo de pessoa deve ser 'fisica' ou 'juridica'" in erros

    def test_pf_com_cnpj(self):
        erros = validar_cliente_fornecedor(
            nome="João",
            tipo_pessoa="fisica",
            cpf_cnpj="11222333000181"
        )
        assert "Pessoa física deve ter CPF" in erros

    def test_pj_com_cpf(self):
        erros = validar_cliente_fornecedor(
            nome="Empresa",
            tipo_pessoa="juridica",
            cpf_cnpj="11144477735"
        )
        assert "Pessoa jurídica deve ter CNPJ" in erros

    def test_cep_invalido(self):
        erros = validar_cliente_fornecedor(
            nome="João",
            tipo_pessoa="fisica",
            cpf_cnpj="11144477735",
            cep="12345"
        )
        assert "CEP inválido" in erros[0]

    def test_email_invalido(self):
        erros = validar_cliente_fornecedor(
            nome="João",
            tipo_pessoa="fisica",
            cpf_cnpj="11144477735",
            email="email_invalido"
        )
        assert "Email inválido" in erros

    def test_telefone_invalido(self):
        erros = validar_cliente_fornecedor(
            nome="João",
            tipo_pessoa="fisica",
            cpf_cnpj="11144477735",
            telefone="123"
        )
        assert "Telefone inválido" in erros
"""Testes do gerador de PDF de contas a receber/pagar (services/nfse_pdf.py).

Bug: `cell()` do fpdf2 mantem `new_y=TOP`, ou seja, NAO desce a linha. O
gerador de contas nao tinha o `pdf.ln(linha_alt)` que os outros geradores
possuem, entao todas as linhas eram desenhadas na mesma altura: o PDF exibia
apenas a ultima linha seguida do total, com o valor correto (a soma e feita em
Python) e as demais contas invisiveis.

As colunas tambem somavam 190mm numa area util de 186mm, cortando a ultima
coluna na borda da pagina.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from services import nfse_pdf


class FakeStatus:
    name = "PENDENTE"


class FakeParte:
    def __init__(self, nome):
        self.nome = nome


class FakeConta:
    def __init__(self, i, tipo="receber"):
        self.descricao = f"NFSe #{9000 + i}"
        self.valor = Decimal("89.90")
        self.data_vencimento = date.today() - timedelta(days=i + 1)
        self.status = FakeStatus()
        self.cliente = FakeParte(f"CLIENTE {i} INDUSTRIA E COMERCIO DE PRODUTOS")
        self.fornecedor = FakeParte(f"FORNECEDOR {i} INDUSTRIA E COMERCIO DE PRODUTOS")


def test_toda_linha_do_pdf_fica_em_altura_diferente(monkeypatch):
    ys = []
    original = nfse_pdf.FPDFSafe.cell

    def cell_spy(self, *args, **kwargs):
        ys.append(round(self.get_y(), 2))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(nfse_pdf.FPDFSafe, "cell", cell_spy)

    contas = [FakeConta(i) for i in range(12)]
    nfse_pdf.gerar_pdf_contas(contas, None, tipo="receber", filtros="Filtro: Vencido")

    # 12 linhas x 5 celulas, alem do cabecalho e do total. Antes da correcao
    # todas as 60 celulas de dados caiam em uma unica altura.
    assert len(set(ys)) >= 14


def test_colunas_cabem_na_area_util():
    # A4 210mm - 2x15mm de margem = 180mm
    assert sum(nfse_pdf.COL_W_CONTAS) <= 180


def test_pdf_traz_todas_as_contas():
    """Contraprova com o texto extraido: o arquivo tem que listar as 12 contas."""
    fitz = pytest.importorskip("fitz")
    import io

    contas = [FakeConta(i) for i in range(12)]
    pdf = nfse_pdf.gerar_pdf_contas(contas, None, tipo="receber", filtros="Filtro: Vencido")

    doc = fitz.open(stream=io.BytesIO(pdf), filetype="pdf")
    texto = " ".join(" ".join(p.get_text() for p in doc).split())

    for conta in contas:
        assert conta.descricao in texto
    assert "Total: R$ 1.078,80" in texto
    assert "CLIENTE 0 INDUSTRIA E COMERCIO" in texto


def test_cabecalho_nao_sobrepoe_filtro_sobre_a_empresa():
    """'Filtro: ...' e a data sao alinhados abaixo do bloco da empresa."""
    fitz = pytest.importorskip("fitz")
    import io

    class FakeEmpresa:
        razao_social = "ACF MACHADO-ME LTDA"
        nome_fantasia = "MULTICOM"
        cnpj = "13.133.714/0001-10"
        endereco = "RUA ONOFRE PEREIRA DE MATOS, 832"
        cidade = "DOURADOS"
        estado = "MS"

    pdf = nfse_pdf.gerar_pdf_contas([FakeConta(0)], FakeEmpresa(), tipo="receber",
                                   filtros="Filtro: Vencido")
    doc = fitz.open(stream=io.BytesIO(pdf), filetype="pdf")
    palavras = doc[0].get_text("words")

    def y_da(termo):
        return next(w[1] for w in palavras if w[4].startswith(termo))

    # as duas linhas de dados da empresa nao podem sharear a altura do subtitulo
    assert abs(y_da("ACF") - y_da("Filtro:")) > 4
    assert abs(y_da("MULTICOM") - y_da("CONTAS")) < 1


def test_texto_longo_e_truncado_pela_largura_da_celula():
    """Truncamento mede a string; cortar por caracteres desperdicava a coluna."""
    fitz = pytest.importorskip("fitz")
    import io

    conta = FakeConta(0)
    conta.cliente = FakeParte("EMPRESA COM NOME EXTREMAMENTE LONGO E COMPRIDO LTDA ME")
    pdf = nfse_pdf.gerar_pdf_contas([conta], None, tipo="receber")

    doc = fitz.open(stream=io.BytesIO(pdf), filetype="pdf")
    texto = " ".join(" ".join(p.get_text() for p in doc).split())

    assert "EMPRESA COM NOME EXTREMAMEN..." in texto
    assert "LTDA ME" not in texto


def test_geradores_de_estoque_e_movimentacao_descem_a_linha(monkeypatch):
    """Os outros dois geradores ja faziam `ln`; o teste trava o padrao."""
    ys = []
    original = nfse_pdf.FPDFSafe.cell

    def cell_spy(self, *args, **kwargs):
        ys.append(round(self.get_y(), 2))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(nfse_pdf.FPDFSafe, "cell", cell_spy)

    class FakeProduto:
        codigo = "P1"
        nome = "Item"
        categoria = None
        estoque = 5
        estoque_minimo = 1
        preco_custo = 10

    nfse_pdf.gerar_pdf_estoque([FakeProduto() for _ in range(12)], None)

    assert len(set(ys)) >= 14
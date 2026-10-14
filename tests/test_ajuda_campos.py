"""Testes do componente de ajuda dos campos ({{ ajuda('os.equipamento') }}).

O componente precisa ser aditivo e nunca derrubar uma tela:

- chave sem texto devolve vazio (erro de digitacao vira "campo sem icone");
- toda chave usada em template existe em app/core/help_texts.py;
- todo texto cadastrado aparece em algum template (nada de ajuda orfa);
- o botao nao submete o form (type="button") e nao carrega name;
- o form da OS continua enviando os mesmos campos de antes.
"""
import io
import os
import re
from pathlib import Path

import pytest

from app.core.help_texts import TEXTOS_AJUDA
from app.core.help_ui import html_ajuda

RAIZ = Path(__file__).resolve().parent.parent
DIR_TEMPLATES = RAIZ / "templates"
_ARQUIVOS_HTML = sorted(
    p for p in DIR_TEMPLATES.rglob("*.html")
    if "backup" not in p.parts and "_backup" not in p.name
)
_CHAMADAS = re.compile(r"\{\{\s*ajuda\(['\"]([^'\"]+)['\"]\)\s*\}\}")
# Substituicoes literais do Python para os textos do dicionario.
_ESCAPES = {"\u2014": "-", "\u00ab": '"', "\u00bb": '"', "\u2019": "'", "\u201c": '"', "\u201d": '"'}


def _normaliza(texto: str) -> str:
    for k, v in _ESCAPES.items():
        texto = texto.replace(k, v)
    return texto


def _chaves_usadas():
    usadas = {}
    for caminho in _ARQUIVOS_HTML:
        conteudo = io.open(caminho, encoding="utf-8", errors="replace").read()
        for chave in _CHAMADAS.findall(conteudo):
            usadas.setdefault(chave, []).append(caminho.relative_to(RAIZ).as_posix())
    return usadas


def test_toda_chave_usada_existe_no_dicionario():
    """Chave com erro de digitacao nao pode quebrar: vira campo sem icone."""
    faltando = [c for c in _chaves_usadas() if c not in TEXTOS_AJUDA]
    assert faltando == []


def test_nenhum_texto_orfao():
    """Texto cadastrado e nunca exibido e uma promessa quebrada ao usuario."""
    orfaos = sorted(set(TEXTOS_AJUDA) - set(_chaves_usadas()))
    assert orfaos == []


def test_chave_desconhecida_devolve_vazio():
    assert str(html_ajuda("os.nao_existe")) == ""
    assert str(html_ajuda("")) == ""


def test_botao_gerado_e_inerte():
    html = str(html_ajuda("os.equipamento"))
    assert 'type="button"' in html
    assert " name=" not in html
    assert "ajuda-campo" in html
    assert "Descrição do aparelho" in html


def test_balao_vem_no_html_e_nao_e_criado_por_js():
    """
    O balao precisa existir no HTML, dentro do .ajuda-wrap, aberto por CSS.

    Antes ele era criado por JS e appended em document.body: dependia do
    listener rodar e nao podia ser interceptado por script de outra tela —
    foi exatamente o que travou na tela de edicao da OS.
    """
    html = str(html_ajuda("os.equipamento"))
    assert 'class="ajuda-balao"' in html
    assert html.index('class="ajuda-wrap"') < html.index('class="ajuda-campo"')
    assert html.index('class="ajuda-campo"') < html.index('class="ajuda-balao"')
    assert "document.createElement" not in html


def test_css_do_componente_esta_no_base():
    """Sem o CSS o balao fica invisivel (opacity 0). Fica travado por teste."""
    base = io.open(
        DIR_TEMPLATES / "base.html", encoding="utf-8", errors="replace"
    ).read()
    assert ".ajuda-wrap{" in base
    # abre por hover/foco e pela classe que o JS põe no clique (celular)
    assert ".ajuda-wrap:hover .ajuda-balao" in base
    assert ".ajuda-wrap.aberta .ajuda-balao" in base
    assert ".ajuda-balao{" in base and "visibility:hidden" in base


def test_texto_com_aspas_e_escapado():
    texto = 'Use "aspas" e <tags> &_pipe| no texto'
    original = TEXTOS_AJUDA["os.equipamento"]
    TEXTOS_AJUDA["os.equipamento"] = texto
    try:
        html = str(html_ajuda("os.equipamento"))
        assert "&quot;aspas&quot;" in html
        assert "<tags>" not in html
        assert "&amp;" in html and "&lt;tags&gt;" in html
    finally:
        TEXTOS_AJUDA["os.equipamento"] = original


def test_textos_nao_sao_vazios_nem_genericos():
    for chave, texto in TEXTOS_AJUDA.items():
        normalizado = _normaliza(texto).strip()
        assert len(normalizado) >= 25, f"{chave}: texto curto demais"
        assert normalizado.endswith("."), f"{chave}: sem ponto final"


def test_botao_esta_registrado_no_jinja():
    from jinja2 import Environment
    from app.core.help_ui import registra_ajuda

    env = Environment()
    registra_ajuda(env)
    assert "ajuda" in env.globals
    saida = env.from_string("{{ ajuda('os.equipamento') }}").render()
    assert "ajuda-campo" in saida


def test_formulario_da_os_continua_com_os_campos_originais():
    """Nada de campo novo required e nada de sumir campo: o form e o mesmo."""
    html = io.open(
        DIR_TEMPLATES / "ordens_servico" / "form.html", encoding="utf-8", errors="replace"
    ).read()
    for campo in ("cliente_id", "equipamento", "marca_id", "modelo", "numero_serie",
                  "tecnico", "autorizado_por", "numero_requisicao", "defeito_relatado",
                  "observacao", "propagar_observacao", "csrf_token"):
        assert f'name="{campo}"' in html, campo
    assert html.count('type="submit"') >= 1
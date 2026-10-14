"""
Componente de ajuda dos campos: `{{ ajuda('os.equipamento') }}`.

Devolve o botao "?" com o texto ja no HTML. A abertura e feita por CSS
(hover/foco) e o JavaScript so acrescenta a classe `.aberta` no clique — assim o
balao funciona mesmo se o script nao rodar, e nao existe nenhum elemento criado
em `document.body` para poder ser interceptado, empurrado por z-index ou
removido por script de outra tela.

Estrutura:

    <span class="ajuda-wrap">
      <button type="button" class="ajuda-campo" aria-label="Ajuda">?</button>
      <span class="ajuda-balao" role="tooltip">texto</span>
    </span>

Regra: chave sem texto devolve string vazia. Erro de digitacao na chave vira um
campo sem icone, nunca uma tela quebrada.
"""
import html

from markupsafe import Markup

from app.core.help_texts import texto_ajuda

_ESTRUTURA = (
    '<span class="ajuda-wrap">'
    '<button type="button" class="ajuda-campo" aria-label="Ajuda" title="Ajuda">?</button>'
    '<span class="ajuda-balao" role="tooltip">{texto}</span>'
    '</span>'
)


def html_ajuda(chave: str) -> Markup:
    texto = texto_ajuda(chave)
    if not texto:
        return Markup("")
    return Markup(_ESTRUTURA.format(texto=html.escape(texto, quote=True)))


def registra_ajuda(env) -> None:
    """Registra `ajuda()` como global do ambiente Jinja."""
    env.globals["ajuda"] = html_ajuda
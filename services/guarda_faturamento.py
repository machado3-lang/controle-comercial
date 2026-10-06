"""Regra unica de bloqueio de faturamento de um pedido.

Motivo
------
Um pedido pode ser faturado por **um** caminho so. Depois que ele entra em um
agrupamento (`finalizar_grupo`) ou em uma consolidacao (`criar_consolidacao`),
o documento que carrega o faturamento passa a ser o pedido agrupado ou a
consolidacao. Se o pedido de origem tambem for faturado, a mesma venda entra
duas vezes no financeiro e no DRE.

Historico
---------
As guardas existiam inline e em tres lugares, com palavras diferentes e
cobertura parcial:

  routers/pedidos.py:finalizar_pedido  -> so checava consolidacao_id
  routers/nfe.py                      -> checava consolidacao_id e status AGRUPADO
  routers/nfse.py                     -> checava consolidacao_id e status AGRUPADO

Como `finalizar_pedido` era a unica dessas rotas que **gera cobranca**
(quando `acao="recibo"`, ou quando o pedido nao tem itens), era por ela que a
receita podia ser duplicada. Reunindo a regra aqui, os tres pontos passam a
consultar a mesma fonte e nao podem divergir de novo.

Criterio
--------
Bloqueia quando o pedido ja esta representado por outro documento:

  - `consolidacao_id` preenchido, ou status CONSOLIDADO
  - `pedido_agrupado_id` preenchido, ou status AGRUPADO

Checa **o vinculo e o status**: um pedido pode ter o status gravado de forma
inconsistente (edicao manual, dado antigo, script), e o vinculo e a evidencia
mais forte. Status terminal CANCELADO e FATURADO **nao** sao bloqueados aqui:
cancelar e reverter sao operacoes legitimas, tratadas em cada rota.

Sobre a comparacao de status
----------------------------
`StatusPedido` e um `Enum` comum (nao str-based) e `status` e uma coluna
`String(11)`: o atributo em memoria pode conter o Enum (quando atribuido pelo
codigo) ou a string (quando lido do banco). Comparar direto com string
**falha silenciosamente** — `StatusPedido.CONSOLIDADO == "CONSOLIDADO"` e
`False`, e o `.value` e minusculo (`consolidado`). Por isso `_eh_status`
normaliza os dois formatos.
"""

from models import StatusPedido


def _eh_status(status, alvo):
    """Compara status aceitando Enum ou string, em qualquer caixa.

    Sem isso, `StatusPedido.CONSOLIDADO == "CONSOLIDADO"` daria False e a
    guarda passaria batido — exatamente o tipo de bug que este modulo existe
    para evitar.
    """
    if status is None:
        return False
    if status == alvo:
        return True
    nome = getattr(status, "name", None) or str(status)
    valor = getattr(status, "value", None) or ""
    return str(nome).upper() == alvo.name or str(valor).upper() == alvo.name


def bloqueio_faturamento(pedido):
    """Mensagem de bloqueio para faturar `pedido`, ou None se pode faturar.

    `curta` e a versao sem ponto final, para quem monta a resposta JSON
    (nfe/nfse) e nao quer pontuacao duplicada.
    """
    if pedido is None:
        return None

    status = getattr(pedido, "status", None)

    if getattr(pedido, "consolidacao_id", None) is not None \
            or _eh_status(status, StatusPedido.CONSOLIDADO):
        return {
            "curta": "Este pedido pertence a uma consolidação; fature pela consolidação",
            "completa": "Este pedido pertence a uma consolidação; "
                        "o faturamento deve ocorrer pela consolidação.",
        }

    if getattr(pedido, "pedido_agrupado_id", None) is not None \
            or _eh_status(status, StatusPedido.AGRUPADO):
        return {
            "curta": "Este pedido foi agrupado em outro pedido; "
                     "fature pelo pedido agrupado para evitar duplicidade",
            "completa": "Este pedido foi agrupado em outro pedido; "
                        "o faturamento deve ocorrer pelo pedido agrupado "
                        "para evitar receita duplicada.",
        }

    return None
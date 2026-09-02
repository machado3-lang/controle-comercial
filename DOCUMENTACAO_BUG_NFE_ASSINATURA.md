# Documentação — Bug: `PedidoVenda has no attribute 'assinatura_id'` na geração de NFe/NFSe a partir do pedido

## 1. Sintoma

Ao gerar o rascunho de uma **NFe** (ou NFSe) a partir de um **Pedido de Venda**, a operação
falhava com a mensagem:

```
Erro ao salvar rascunho NFe: 'PedidoVenda' object has no attribute 'assinatura_id'
```

O erro era capturado no bloco `except` dos endpoints de emissão (ex.:
`routers/nfe.py::emitir_pedido_submit`) e apresentado ao usuário como erro de rascunho.

## 2. Causa raiz

O modelo **`PedidoVenda`** (`models.py`) **nunca** teve a coluna/atributo `assinatura_id`.
Esse atributo existe apenas em **`NFSe`** (`models_nfe.NFSe.assinatura_id`) e em
**`Assinatura`** (`models.Assinatura.nfse_id`), pois o vínculo assinatura↔nota é feito
pela **nota**, e não pelo pedido.

Mesmo assim, o código de emissão tentava ler `pedido.assinatura_id` para decidir a origem
da nota:

```python
# ANTES (quebrava)
origem="assinatura" if pedido.assinatura_id else "pedido",
```

Como `PedidoVenda` não possui esse atributo, o acesso lançava `AttributeError` e o
rascunho nunca era salvo.

Havia duas ocorrências desse padrão (uma em `nfe.py`, outra em `nfse.py`), além de dois
`UPDATE`s no `lifespan.py` que assumiam a existência da coluna `assinatura_id` na tabela
`pedidos_venda`.

## 3. Correção

**Commit `0ac8d78`** (branch `master`, deploy Railway).

- `routers/nfe.py:739` — emissão a partir de pedido:
  ```python
  origem="pedido",
  ```
- `routers/nfse.py:649` — emissão NFSe a partir de pedido:
  ```python
  origem="pedido",
  ```
- `app/core/lifespan.py` — os `UPDATE`s que marcavam notas como `origem='assinatura'`
  passaram a ser executados **somente se** a coluna `assinatura_id` realmente existir na
  tabela `pedidos_venda` (verificação via `sqlalchemy.inspect` + `try/except`):
  ```python
  insp = sa_inspect(engine)
  cols = [c["name"] for c in insp.get_columns("pedidos_venda")]
  if "assinatura_id" in cols:
      updates += [ ...UPDATE nfe..., ...UPDATE nfse... ]
  ```
- `routers/pedidos.py` — ajuste da condição de geração de boleto
  (`if contas_para_boleto and gerar_boleto:`), removendo a dependência de
  `forma_pagamento == "boleto"` neste trecho.

## 4. Por que o pedido não tem `assinatura_id`?

O vínculo pedido↔assinatura **não existe** no modelo de dados. As assinaturas geram
diretamente **NFSe** (`origem="assinatura"`, com `NFSe.assinatura_id` preenchido) e
**Contas a Receber**, não passando necessariamente por um `PedidoVenda`. Portanto a origem
correta de uma nota criada a partir de um pedido é sempre `"pedido"` (ou `"consolidacao"`,
`"os"`, `"assinatura"`, `"avulsa"`, `"importada"`).

## 5. Regra para o futuro

- **Nunca** acesse `pedido.assinatura_id` — o atributo não existe em `PedidoVenda`.
- Para vincular uma nota a uma assinatura, use `NFSe.assinatura_id` /
  `NFe.assinatura_id` (populados em `routers/assinaturas.py::gerar_nfse_assinatura`),
  conforme documentado em `DOCUMENTACAO_ASSINATURAS.md`.
- Ao escrever migrações/mantenedores que tocam `pedidos_venda`, **cheque a existência da
  coluna** antes de referenciá-la em SQL cru.

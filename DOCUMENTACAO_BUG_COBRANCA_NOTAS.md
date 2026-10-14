# Documentação — Bug: rascunho de NFe a partir de pedido quebrava a cobrança

> Referência citada por `DOCUMENTACAO_FATURAMENTO_COBRANCA.md` §7.2,
> `DOCUMENTACAO_PEDIDOS.md` §6 e `DOCUMENTACAO_CONSOLIDACAO.md` §6.2b.

## 1. Sintoma

Ao emitir uma NFe a partir de um **pedido** (`POST /nfe/emitir/pedido/{id}`):

```
Erro ao salvar rascunho NFe: gerar_contas_receber_para_nota() got an unexpected
keyword argument 'pedido_id'
```

O rascunho **não era criado** (a exceção ocorria antes do `db.commit()`, e o
`except` fazia `rollback`).

## 2. Causa raiz

- `services/parcelamento.py::gerar_contas_receber_para_nota` **não declarava**
  `pedido_id` (só `nfe_id`, `nfse_id`, `consolidacao_id`), embora a função que
  ela encapsula (`gerar_contas_receber`) aceite e grave `ContaReceber.pedido_id`.
- `routers/nfe.py::emitir_pedido_submit` chamava essa função passando
  `pedido_id=pedido.id` (duas vezes: NFe e NFSe).
- Introduzido em **12/09/2026** (commit `b7b57ba` — “rascunho NFe/NFSe, cobrança
  a vista PAGO e botão Gerar Cobrança”).

## 3. Por que passou despercebido

O bloco de cobrança só era executado quando:

```python
_gerar_cobranca = bool(pedido.gerar_cobranca) or (pedido.forma_pagamento == "boleto")
```

Ou seja, **só quebrava com “Gerar Cobrança” ligado** (padrão da tela) ou forma
=Boleto. Como todos os pedidos faturados desde 12/09 estavam com
`gerar_cobranca = False`, o fluxo “que funcionava” era justamente o que **não
cobrava** — o rascunho era criado sem nenhuma conta a receber.

Evidência no banco (pedidos `FATURADO`): 20 com `gerar_cobranca` NULL, 9 `false`,
1 `true`. Exemplo real: pedido 89 (`aprazo`, R$ 3,15) → rascunho NFe 4454 criado
sem nenhuma conta vinculada.

## 4. Correção aplicada

1. **`gerar_contas_receber_para_nota` aceita `pedido_id`** e o repassa a
   `gerar_contas_receber` (defensivo: evita reincidência).
2. **O rascunho de NFe de pedido deixou de gerar cobrança.** Passa a valer a regra
   única do sistema (nenhuma conta antes da nota): a cobrança nasce na
   **transmissão**, em `_garantir_cobranca_nfe`, que já grava `pedido_id`,
   respeita a flag, usa o parcelamento do pedido, quita à vista como `PAGO` e
   dispara `emitir_boleto_para_nota` após autorização.
3. **Mesma regra aplicada à consolidação**: `emitir_consolidacao_nfse` não cria
   mais contas na emissão; `_garantir_cobranca_nfe` / `_garantir_cobranca_nfse`
   criam na transmissão.
4. **Recibo preservado**: `finalizar_pedido` com `acao=recibo` (e
   `finalizar_consolidacao` com `acao=recibo`) continua gerando as contas na
   finalização — nesse fluxo não há nota.

## 5. Bugs satélites encontrados na mesma análise

| Bug | Onde | Correção |
|---|---|---|
| `emitir_consolidacao_nfse` com `tipo=auto` exigia itens de produto **e** de serviço, impedindo emitir consolidação só de serviços/produtos | `nfse.py` | `auto` emite o que existir; `nfe`/`nfse`/`ambas` exigem o correspondente |
| `PedidoConsolidado` não possui a coluna `gerar_cobranca`, mas `nfe.py`/`nfse.py` a acessavam → `AttributeError` ao gerar cobrança de consolidação | `nfe.py`, `nfse.py` | `getattr(cons, "gerar_cobranca", None)`; coluna criada (`default=True`) + checkbox na tela |
| Flag `gerar_cobranca` `NULL` em pedidos antigos: `bool(None)=False` (rascunho) vs `None is False` → falso (transmissão) — comportamento divergente entre etapas | `nfe.py`, `nfse.py` | Regra unificada na transmissão; normalizar `NULL → True` se desejar |
| Campo `primeiro_vencimento` do formulário de finalizar consolidação era recebido e descartado | `consolidacoes.py` | Nova coluna `PedidoConsolidado.primeiro_vencimento`, usada pela cobrança |

## 6. Testes de regressão

- `tests/test_baixa_boleto_exclusao.py`: rascunho de NFe de pedido com
  `gerar_cobranca=True` → NFe criada (`rascunho`) e **0 contas**;
  `gerar_contas_receber_para_nota` grava `pedido_id`; recibo sem NFs continua
  gerando a conta.
- `tests/test_consolidacoes.py`: finalizar consolidação → 0 contas; emissão do
  rascunho → 0 contas; `_garantir_cobranca_nfse` → 3 parcelas (sem duplicar);
  recibo gera 3 contas PENDENTES; flag desligada → nenhuma conta.

## 7. Checklist de validação manual

1. Pedido à prazo com “Gerar Cobrança” marcado → gerar NFe → **rascunho criado,
   sem contas** → transmitir → conta PENDENTE com `pedido_id`.
2. Pedido à vista/cartão → após transmitir → conta **PAGA**.
3. Forma = Boleto → após transmitir → conta + **boleto emitido** (nº da nota).
4. Consolidação só de serviços → `Emitir NFSe` → rascunho criado (antes barrado)
   → transmitir → cobrança gerada.
5. “Gerar recibo (sem NFs)” → contas geradas na finalização.

# Documentação — Ajustes de Faturamento, Cobrança e NFSe/NFe

Documentação das alterações no fluxo de faturamento de **pedidos (avulsos e consolidações)**
e na geração de **NFe/NFS‑e**, com foco em:

- Rascunho (não transmitir direto) na emissão;
- Cobrança (`ContaReceber`) correta por forma de pagamento;
- Recebimento à vista / cartão registrado como **recebido (PAGO)**;
- Flag mestre "Gerar Cobrança";
- Botão "Gerar Cobrança" consistente entre NFe e NFSe.

> **Atenção:** toda alteração de modelo (nova coluna) é aplicada por auto‑migração no
> startup do servidor. **Reinicie o servidor** após o deploy para criar as colunas e
> ativar o novo código.

---

## 1. Emissão de NFe/NFS‑e passa a criar RASCUNHO

Antes, "Gerar NFS‑e" transmitia a nota diretamente. Agora a emissão cria um **rascunho**
e redireciona para a lista (NFSe) ou prévia (NFe), e a transmissão é um passo à parte.

- `routers/nfse.py` → `emitir_nfse` (POST) cria rascunho e redireciona para `/nfse/`.
- `routers/nfe.py` → `emitir_pedido_submit` / `emitir_consolidacao_nfe` criam rascunho e
  redirecionam para `/nfe/{id}/previa`.

---

## 2. Cobrança por forma de pagamento (Opção "à vista = recebido")

Nova regra central em `services/parcelamento.py`:

- `cartao_credito` (e variantes) passou a ser considerado **à vista** (`_FORMAS_A_VISTA`).
- Nova função `quitar_avista(contas, forma_pagamento)`: para formas à vista, a
  `ContaReceber` gerada já recebe `status=PAGO` e `data_recebimento=hoje`, constando nos
  relatórios de recebimento sem virar "a receber" pendente.

### Flag mestre "Gerar Cobrança"
- Nova coluna `PedidoVenda.gerar_cobranca` (default `True`).
- A flag é o interruptor mestre: desmarcada, **nenhuma** cobrança automática é gerada
  (pedido, NFe ou NFSe), mesmo à prazo.
- Respeitada em:
  - `routers/pedidos.py` → `finalizar_pedido` (recibo sem NF / pedido fechado sem NFs);
  - `routers/nfe.py` → `emitir_pedido_submit` e `_garantir_cobranca_nfe` (transmissão);
  - `routers/nfse.py` → `_garantir_cobranca_nfse` (transmissão) e `gerar_cobranca_nfse` (manual).

### Quando a cobrança é gerada
A cobrança é gerada **na transmissão** da nota (não no rascunho):

| Origem | NFe | NFSe |
|---|---|---|
| Pedido avulso | `_garantir_cobranca_nfe` | `_garantir_cobranca_nfse` |
| Consolidação | `_garantir_cobranca_nfe` (agora cobre `consolidacao_id`) | `_garantir_cobranca_nfse` |

Para **consolidação à vista** (ex.: CONS‑000009), após transmitir ambas as notas são geradas
duas contas **PAGO**: a da NFe (produtos) e a da NFSe (serviços), totalizando o valor recebido.
Antes, a parte de produtos (NFe) não gerava cobrança e a de serviços ficava pendente.

---

## 3. NFSe agora armazena `forma_pagamento`

A `NFSe` não tinha a coluna (só a `NFe` tinha), por isso o campo "Forma de Pagamento" não
aparecia na NFSe. Agora:

- Nova coluna `NFSe.forma_pagamento` (`models_nfe.py`).
- Preenchida na emissão (pedido → `pedido.forma_pagamento`; consolidação →
  `consolidacao.forma_pagamento`).
- Exibida em `templates/nfse/detalhe.html` (com fallback para pedido/consolidação).
- A resolução de forma em `_garantir_cobranca_nfse` / `gerar_cobranca_nfse` usa, nesta ordem:
  `nfse.forma_pagamento` → `pedido.forma_pagamento` → `consolidacao.forma_pagamento` → `"NFSe"`.

---

## 4. Botão "Gerar Cobrança" consistente (NFe × NFSe)

- Adicionado o botão "Gerar Cobrança" também na **prévia da NFe** (`templates/nfe/previa.html`),
  espelhando a `detalhe` da NFe e da NFSe.
- **Oculto em rascunho** (em todas as telas): só aparece após a nota ser transmitida
  (NFe `issued` / NFSe `autorizada`), evitando o aviso "emitir a nota primeiro".
- A cobrança automática na transmissão continua valendo; o botão é um recurso manual
  complementar (quita à vista como PAGO também).

---

## 5. Correção de XML (NFSe Nacional/SEFIN)

`services/nfse_betha.py` → `gerar_dps_xml`: `pedido.data` (date) passa a ser convertido para
`datetime` antes de `.tzinfo`, corrigindo o erro `'datetime.date' object has no attribute
'tzinfo'` na montagem do DPS.

---

## 6. Checklist de deploy

1. Fazer o commit/push para o GitHub (Railway faz o build a partir da branch `master`).
2. **Reiniciar** o servidor/deploy para rodar a auto‑migração (colunas novas) e o novo código.
3. Validar um pedido à vista e um à prazo; confirmar cobranças PAGO (à vista) e a receber
   (à prazo) após transmitir as notas.

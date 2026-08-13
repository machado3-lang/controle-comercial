# Documentação — Ordens de Serviço, Conclusão e Cobrança

Este documento descreve o funcionamento do módulo de **Ordens de Serviço (OS)**,
o fluxo de **status**, a **conclusão (entrega)** com data de saída, as regras de
**cancelamento** e como a **cobrança** é gerada a partir da OS (NF-e / NFS-e →
`ContaReceber`).

---

## 1. Fluxo de status (com guardas)

A OS possui 5 estados (`StatusOS`): `aberta`, `em_andamento`, `finalizada`,
`concluida`, `cancelada`.

O fluxo é **dirigido** e validado tanto no backend quanto no frontend:

```
aberta ──▶ em_andamento ──▶ finalizada ──▶ concluida (entregue)
   │            │                │               │
   └────────────┴────────────────┴───────────────┴──▶ cancelada (terminal)
```

| De \ Para        | em_andamento | finalizada | concluida | cancelada |
|-----------------|--------------|------------|-----------|------------|
| aberta          | ✅           | ❌         | ❌        | ✅         |
| em_andamento    | —            | ✅         | ❌        | ✅         |
| finalizada      | ❌           | —          | ✅        | ✅         |
| concluida       | ❌           | ❌         | —         | ✅         |
| cancelada       | ❌           | ❌         | ❌        | — (terminal)|

- **Transições inválidas** (ex.: `concluida → finalizada`, reabrir uma `cancelada`)
  retornam **HTTP 400** com mensagem clara no endpoint `POST /ordens-servico/{id}/status`.
- No modal de finalização (`templates/ordens_servico/detalhe.html`) os botões
  inválidos para o status atual já aparecem **desabilitados** (UX).
- Clicar no botão do status atual é um *no-op* inofensivo (retorna 200 sem alterar).
- A **edição** da OS (`POST /ordens-servico/{id}/editar`) também respeita o fluxo
  dirigido: o `<select>` de status inclui `concluida` e, se a transição informada
  for inválida, o status é **mantido** (com aviso) e os demais campos são salvos.
  Isso corrige o bug em que uma OS `concluida` revertia para `aberta` ao salvar a
  edição (o `<select>` não tinha a opção `concluida` — ver seção 7).

### Concluir (entregar) — `concluida`
- Ao concluir, o sistema registra a **data de saída** (`data_saida`):
  - usa a data informada no campo "Data Saída" do modal (padrão = hoje);
  - se nenhuma for informada e a OS ainda não tiver `data_saida`, usa a data de hoje.
- O campo "Data Saída" fica disponível no modal de emissão/finalização.

---

## 2. Cancelar uma OS

`cancelada` é um estado **terminal** (não se reabre). Pode ser alcançado a partir
de qualquer estado ativo.

**Regra de integridade (implementada):** não é permitido cancelar uma OS que ainda
tenha:
- **nota fiscal emitida** (NF-e/NFS-e com status de transmitida/autorizada/pendente/processando); ou
- **cobrança ativa** (`ContaReceber` diferente de cancelada/excluída).

Nesses casos o cancelamento é **bloqueado (400)** com orientação para first
voidar a(s) nota(s) e/ou cancelar a(s) cobrança(s).

> **Procedimento recomendado para corrigir uma OS concluída errada:**
> 1. Se já houver NF-e/NFS-e **transmitidas**, cancele-as na SEFAZ/prefeitura.
> 2. Cancele a `ContaReceber` vinculada.
> 3. Só então cancele a OS e abra uma nova.
>
> (Alternativa a cancelar: um "reabrir para correção" costuma ser melhor para
> auditoria, mas o modelo adotado aqui é o fluxo direcional + cancelar/criar nova.)

---

## 3. Cobrança a partir da OS

A OS **não gera cobrança diretamente**. Ela alimenta notas fiscais e a cobrança
nasce da nota.

### 3.1 Geração das notas (rascunhos)
A partir do detalhe da OS:
- **Gerar NFSe** (`GET/POST /nfse/emitir/os/{id}`) → cria NFS-e em **rascunho**
  usando `valor_servico` (serviços). **Não cria cobrança.**
- **Gerar NFe** (`GET/POST /nfe/emitir/os/{id}`) → cria NF-e em **rascunho**
  usando `valor_pecas` (produtos/peças). **Não cria cobrança.**

> **Emissão não é travada por status:** por decisão de produto, o rascunho de
> NFe/NFSe pode ser gerado mesmo com a OS ainda `aberta`/`em_andamento` (o
> rascunho é apenas preparação). A transmissão/autorização efetiva é feita
> depois, na tela da nota.

### 3.2 Flag `cobranca_separada`
Campo booleano em `OrdemServico` (`cobranca_separada`, default `False`,
auto-migrado no startup). Define como a cobrança será gerada:
- `False` (padrão): **cobrança única agrupada** (ver 3.3).
- `True`: **cobranças separadas**, uma por nota.

Pode ser definido na **edição da OS** (checkbox "Cobrança separada") ou
sobrescrito pontualmente no formulário de geração de cobrança. A preferência
informada na geração é **persistida** na OS para próximas emissões.

> O faturamento completo de uma OS normalmente envolve **duas notas**: NFS-e
> (serviços, ISS) + NF-e (peças, ICMS). **Por padrão a cobrança é agrupada em
> UMA só** (`ContaReceber` referenciando NFe + NFSe) — e, se a forma de
> pagamento for boleto, emite-se **um único boleto** para o cliente. Caso o
> cliente prefira cobrança isolada, marque `cobranca_separada` (ou no
> formulário de geração) e cada nota gera sua própria `ContaReceber`.

### 3.3 Gerar a cobrança (ContaReceber)

Atalho na OS que agrupa (ou separa) as notas:

- **OS (agrupada/separada):** `POST /ordens-servico/{id}/gerar-cobranca`
  - **Agrupada (padrão):** cria **UMA** `ContaReceber` com `nfe_id` + `nfse_id`
    e `valor = NFe.valor_total + NFSe.valor_liquido` (usa `NFSe.valor_liquido`
    para respeitar ISS retido). Se `forma_pagamento = boleto`, o boleto emitido
    é único (1:1 com a conta) → o cliente recebe **uma só cobrança/boleto**.
  - **Separada** (`cobranca_separada=1`): gera **uma conta por nota**
    (comportamento anterior), útil quando o cliente quer receber as cobranças
    separadas.
  - **Notas parciais:** se a OS tiver só NFe (ou só NFSe) transmitida, a conta
    agrupada referencia apenas a nota existente (sem erro).
  - **Recibo/fatura SEM nota fiscal:** quando não há NFe/NFS-e transmitida e a
    `forma_pagamento` é de um tipo que não exige nota (`pix`, `dinheiro`,
    `cartao_credito`, `cartao_debito`, `avista`, `aprazo`, `garantia`), o
    endpoint gera uma `ContaReceber` (recibo) a partir de `ordem.valor_total`,
    **sem** vincular `nfe_id`/`nfse_id`. Útil para emitir/enviar um recibo ou
    fatura direto ao cliente, pagamento via PIX/dinheiro, ou conserto em
    **garantia** (neste caso `valor = 0`, apenas registro). A conta recebe
    `os_id` para rastreabilidade.
  - **Bloqueio de valor zerado (recibo):** não é permitido gerar recibo de OS
    vazia nem com valor zerado. O endpoint valida que a OS possui **serviços e/ou
    peças lançados** (`servicos_executados`, `pecas_utilizadas` ou `os_pecas`) e,
    para formas com cobrança, que `ordem.valor_total > 0`. Casos:
    - OS sem serviços/peças → `400` ("A OS não possui serviços nem peças lançados…").
    - OS com itens mas `valor_total = 0` e forma ≠ garantia → `400` ("O valor total
      da OS está zerado…").
    - **Garantia** é a única exceção legítima de R$ 0, mas **exige itens lançados**
      (não faz sentido garantia de OS vazia).
  - **Campos de parcelamento condicionais (UI):** no formulário de geração, os
    campos **Parcelas**, **1º Vencimento** e **Intervalo (dias)** são
    **ocultados** quando a forma é `dinheiro`, `pix`, `à vista` ou `garantia`
    (recibo/cobrança única); permanecem visíveis para `a prazo`, `cartao_credito`,
    `cartao_debito` e `boleto`. Os valores padrão (parcelas=1) ainda são enviados,
    então uma forma à vista vira um documento único.
  - **Parcelamento:** `num_parcelas`/`intervalo_dias` → N parcelas = N contas do
    mesmo grupo (e N boletos, se boleto). Pode ser à vista, dinheiro, PIX ou a
    prazo sem boleto (conta fica em aberto para recebimento posterior).
  - **Forma de pagamento:** `boleto`, `pix`, `dinheiro`, `cartao_credito`,
    `cartao_debito`, `avista`, `aprazo`, `garantia` (informada no formulário).
    A emissão de cobrança **não é obrigatória via boleto** — qualquer forma
    acima é válida, inclusive sem nota (recibo).
  - **Pré-requisito:** ao menos uma nota (`NFe` ou `NFSe`) deve estar
    **transmitida** (status em `_STATUS_EMITIDOS_NFE` / `_STATUS_EMITIDOS_NFSE`)
    **OU** a forma de pagamento deve ser de recibo sem nota (PIX, dinheiro,
    cartão, à vista, a prazo, garantia). Caso contrário, retorna erro orientando
    a transmitir as notas ou escolher uma forma sem nota.
  - **Guarda anti-duplicação:** bloqueia se já houver `ContaReceber` ativa
    (status diferente de cancelada/excluída) vinculada a qualquer nota da OS, ou
    já houver um recibo (`os_id` = OS e `nfe_id`/`nfse_id` nulos).
  - **Resposta:** redireciona em navegação normal; se o header `Accept` for
    `application/json`, retorna JSON (usado pelo modal de conclusão para gerar a
    cobrança logo ao concluir).
- **Por nota (mantido):** NFSe `POST /nfse/{id}/gerar-cobranca` e
  NFe `POST /nfe/{id}/gerar-cobranca` (usado no fluxo separado).

### 3.4 Rastro / vínculos
- `OS.cobranca_separada` → define o modo de geração.
- `OS → NFe(os_id) → ContaReceber(nfe_id)` e `OS → NFSe(os_id) → ContaReceber(nfse_id)`.
- Na cobrança **agrupada**, a mesma `ContaReceber` aponta para **ambas** as notas
  (`nfe_id` e `nfse_id` preenchidos), preservando a rastreabilidade fiscal.
- Na cobrança **recibo sem nota**, a `ContaReceber` recebe `os_id` preenchido e
  `nfe_id`/`nfse_id` nulos (visível no card "Cobrança / Faturamento").

### 3.6 Concluir (entregar) com ou sem notas fiscais

O fluxo de status **não exige** notas para concluir (`concluida`). São cenários
válidos, bastando a forma de pagamento/recibo adequada:

- **Concluir sem emissão de NFS** (recibo/fatura direto): marque "Gerar
  cobrança/recibo ao concluir" no modal e escolha `dinheiro`, `pix`, `cartão`,
  `à vista`, `a prazo` ou `garantia`. Gera-se o recibo sem NFe/NFS-e.
- **Concluir somente com a NFSe** (serviços): gere/transmita a NFSe e, se
  quiser, a cobrança agrupada/separada referencia só ela.
- **Concluir com as 2 NFS** (NFe + NFSe): gere/transmita ambas; a cobrança
  agrupada referencia NFe + NFSe numa única conta/boleto.

No modal de finalização há a opção **"Gerar cobrança/recibo ao concluir"** +
seletor de forma de pagamento: ao concluir, a cobrança é gerada na sequência
(recibo se não houver nota transmitida).

### 3.7 Recibo — visualização, impressão e envio por e-mail

O recibo (cobrança sem nota, `ContaReceber` com `os_id` e `nfe_id`/`nfse_id`
nulos) é um documento à parte, com tela própria:

- **Visualizar / Imprimir:** `GET /ordens-servico/{os_id}/recibo/{conta_id}?tipo=a4|termica`
  - `tipo=a4` (padrão) → `templates/ordens_servico/recibo.html` (layout A4, com
    botão "Imprimir / Salvar PDF").
  - `tipo=termica` → `templates/ordens_servico/recibo_termica.html` (layout
    **Térmica 80mm**, espelhando o padrão da OS térmica).
  - No card "Cobrança / Faturamento" o recibo aparece com o selo **"RECIBO
    (sem NFS)"** e o botão **Imprimir** abre o **mesmo modal** da "Emitir/Finalizar"
    OS, com abas **A4** e **Térmica 80mm** e pré-visualização em iframe.
- **Enviar por e-mail:** `POST /ordens-servico/{os_id}/recibo/{conta_id}/enviar-email`
  - Envia o recibo (HTML) ao `cliente.email` via `services.email_service.enviar_email`
    e marca `ContaReceber.email_enviado = True` / `data_envio_email`.
  - Requer SMTP configurado em `Empresa` (senão retorna aviso "SMTP não configurado").
  - No card há o botão **Enviar e-mail** (POST, com CSRF).
- O recibo identifica "Sem emissão de NFS-e / NFe" e, para **garantia**, inclui
  a observação "Conserto em garantia — sem ônus ao cliente."

### 3.5 UI — card "Cobrança / Faturamento" (detalhe da OS)
O `templates/ordens_servico/detalhe.html` exibe um card com:
- Resumo das notas da OS (NFe/NFSe com número e status) e selo
  "Notas transmitidas" / "Emita NFe/NFSe primeiro".
- Lista das `ContaReceber` já geradas (descrição, valor, vencimento, status):
  - Recibos (sem nota) recebem o selo **"RECIBO (sem NFS)"** e os botões
    **Imprimir** (abre o modal de pré-visualização A4/Térmica 80mm) e
    **Enviar e-mail**.
  - Boleto: **Emitir Boleto** se `boleto` e não emitido, ou **Ver Boleto**.
  - Demais formas com nota: texto da forma de pagamento.
- Formulário `POST /ordens-servico/{id}/gerar-cobranca` com: forma de
  pagamento, parcelas, 1º vencimento, intervalo e checkbox "Cobrança separada".
  - **Sem notas transmitidas:** o formulário vira "Gerar Recibo (sem NFS)" com
    as formas `dinheiro`, `pix`, `cartao_*`, `à vista`, `a prazo`, `garantia`
    (mais `boleto` desabilitado, que exige nota).
  - **Campos de parcelamento condicionais:** Parcelas / 1º Vencimento /
    Intervalo são ocultados quando a forma é `dinheiro`, `pix`, `à vista` ou
    `garantia` (ver 3.3).
- Modal de conclusão ("Emitir/Finalizar") com a opção **"Gerar cobrança/recibo
  ao concluir"** + seletor de forma; e o **modal de pré-visualização de recibo**
  (abas A4 / Térmica 80mm, iframe, botão Imprimir), espelhando o modal de
  impressão da OS.

---

## 4. Bug corrigido: spinner ao concluir

Ao concluir (`concluida`), o **frontend** trocava o rótulo do botão por um ícone
de *loader* e **não o restaurava** em caso de erro — qualquer resposta não-200
(CSRF, enum, etc.) deixava o spinner girando ininterruptamente. Além disso, o
`fetch` não enviava `Accept: application/json`, então um 500 do backend voltava
como HTML e o `r.json()` quebrava silenciosamente.

Correções:
- **Frontend (`detalhe.html`):** o botão salva o rótulo original e o restaura
  tanto em erro de validação quanto em falha de rede (`restaura()`); o `fetch`
  envia `Accept: application/json` para que o backend responda JSON (e não HTML)
  mesmo em 500.
- **Backend (`POST /ordens-servico/{id}/status`):** com o header JSON, o
  handler genérico de exceção retorna `{"detail": ...}` (JSON), permitindo o
  tratamento correto no JS.
- **Enum `statusos` (Railway/Postgres):** o valor gravado pelo SQLAlchemy é o
  **valor** do enum — `StatusOS.CONCLUIDA.value = 'concluida'` (minúsculo). O
  `scripts/migracao_os_cobranca.sql` antigo adicionava o rótulo **`CONCLUIDA`
  (maiúsculo)**, que nunca é usado; num DB restaurado só com esse rótulo, o
  `UPDATE ... SET status='concluida'` falhava com `InvalidTextRepresentation
  (500)`. O script e o auto-migration (`lifespan._add_missing_enum_values`) agora
  garantem o rótulo **`concluida`** (minúsculo) correto.

---

## 5. Migração de banco de dados

A coluna `cobranca_separada` em `ordens_servico` é **criada automaticamente**
no startup (auto-migration em `app/core/lifespan.py`), assim como as demais
colunas novas — não requer script manual.

Para ambientes que preferem SQL explícito (ou PostgreSQL fora do auto-migration),
o `scripts/migracao_os_cobranca.sql` cobre:

1. `ALTER TYPE statusos ADD VALUE 'concluida'` (se ainda não existir — minúsculo).
2. `ALTER TABLE nfse ADD COLUMN os_id INTEGER REFERENCES ordens_servico(id)`.
3. `ALTER TABLE ordens_servico ADD COLUMN cobranca_separada BOOLEAN DEFAULT FALSE`.
4. `ALTER TABLE contas_receber ADD COLUMN os_id INTEGER REFERENCES ordens_servico(id)` (recibos sem nota).
5. (Opcional) preencher `os_id` retroativo em NFSe de OS via regex na observação.

```bash
psql -U postgres -d controledb -f scripts/migracao_os_cobranca.sql
```

---

## 6. Arquivos envolvidos

- `models.py` — `OrdemServico.cobranca_separada` (flag de agrupamento); `os_id` na NFSe; novo `os_id` em `ContaReceber` (recibos sem nota).
- `routers/ordens_servico.py`:
  - `POST /ordens-servico/{id}/gerar-cobranca` — cobrança agrupada/separada, **recibo sem nota** (PIX/dinheiro/cartão/garantia), **bloqueio de valor zerado** (OS vazia / total 0, exceto garantia), parcelamento, guarda anti-duplicação (notas ou recibo), resposta JSON quando `Accept: application/json`.
  - `GET /ordens-servico/{id}/recibo/{conta_id}?tipo=a4|termica` — visualiza/imprime o recibo (A4 ou Térmica 80mm).
  - `POST /ordens-servico/{id}/recibo/{conta_id}/enviar-email` — envia o recibo por e-mail ao cliente.
  - `atualizar_ordem` — persiste `cobranca_separada` e respeita o fluxo dirigido de status (não reverte `concluida` para `aberta`).
  - `detalhe_ordem` — carrega notas e `ContaReceber` vinculadas, incluindo recibos (`os_id` + `nfe_id`/`nfse_id` nulos).
  - `POST /ordens-servico/{id}/status` — conclusão; com `Accept: application/json` retorna JSON em erro (evita spinner travado).
- `templates/ordens_servico/detalhe.html` — card "Cobrança / Faturamento" (formulário de recibo sem nota quando não há notas; opção `garantia`; **campos de parcela ocultos** para dinheiro/pix/à vista/garantia); badge "RECIBO (sem NFS)" + botões **Imprimir** (modal A4/Térmica) e **Enviar e-mail**; modal de conclusão com "Gerar cobrança/recibo ao concluir"; spinner restaurado em erro/falha.
- `templates/ordens_servico/recibo.html` — layout A4 do recibo (sem nota).
- `templates/ordens_servico/recibo_termica.html` — layout Térmica 80mm do recibo.
- `templates/ordens_servico/editar.html` — checkbox "Cobrança separada" e opção `concluida` no `<select>` de status.
- `routers/nfe.py` / `routers/nfse.py` — `emitir/os` (gera rascunho; não travado por status, por decisão de produto).
- `services/parcelamento.py` — `gerar_contas_receber` reutilizado (aceita `nfe_id` + `nfse_id` + `os_id` na mesma conta).
- `services/email_service.py` — `enviar_email` usado no envio do recibo.
- `scripts/migracao_os_cobranca.sql` — migração de banco (enum `concluida` minúsculo, colunas `os_id`).

---

## 7. Correção: edição revertia OS `concluida` para `aberta`

Ao editar uma OS `concluida` e salvar, o status voltava para `aberta`.

**Causa:** o `<select>` de status do formulário de edição (`editar.html`) não
listava a opção `concluida`; como nenhuma `<option>` batia com o status atual,
o navegador enviava a primeira opção (`aberta`). O `atualizar_ordem` gravava o
status diretamente, sem validar o fluxo.

**Correção:**
- Adicionada a opção **`concluida`** ao `<select>` (`editar.html`).
- `atualizar_ordem` agora respeita `TRANSICOES_STATUS_VALIDAS` (igual ao
  endpoint `/status`): se a transição for inválida, o status é **mantido** (com
  aviso) e os demais campos são salvos. Transições válidas para `concluida`
  registram a `data_saida` (hoje) quando vazia.

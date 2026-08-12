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
  - **Parcelamento:** `num_parcelas`/`intervalo_dias` → N parcelas = N contas do
    mesmo grupo (e N boletos, se boleto). Pode ser à vista, dinheiro, PIX ou a
    prazo sem boleto (conta fica em aberto para recebimento posterior).
  - **Forma de pagamento:** `boleto`, `pix`, `dinheiro`, `cartao_credito`,
    `cartao_debito`, `avista`, `aprazo` (informada no formulário).
  - **Pré-requisito:** ao menos uma nota (`NFe` ou `NFSe`) deve estar
    **transmitida** (status em `_STATUS_EMITIDOS_NFE` / `_STATUS_EMITIDOS_NFSE`).
    Caso contrário, retorna erro orientando a transmitir as notas.
  - **Guarda anti-duplicação:** bloqueia se já houver `ContaReceber` ativa
    (status diferente de cancelada/excluída) vinculada a qualquer nota da OS.
- **Por nota (mantido):** NFSe `POST /nfse/{id}/gerar-cobranca` e
  NFe `POST /nfe/{id}/gerar-cobranca` (usado no fluxo separado).

### 3.4 Rastro / vínculos
- `OS.cobranca_separada` → define o modo de geração.
- `OS → NFe(os_id) → ContaReceber(nfe_id)` e `OS → NFSe(os_id) → ContaReceber(nfse_id)`.
- Na cobrança **agrupada**, a mesma `ContaReceber` aponta para **ambas** as notas
  (`nfe_id` e `nfse_id` preenchidos), preservando a rastreabilidade fiscal.

### 3.5 UI — card "Cobrança / Faturamento" (detalhe da OS)
O `templates/ordens_servico/detalhe.html` exibe um card com:
- Resumo das notas da OS (NFe/NFSe com número e status) e selo
  "Notas transmitidas" / "Emita NFe/NFSe primeiro".
- Lista das `ContaReceber` já geradas (descrição, valor, vencimento, status e
  ação de boleto: **Emitir Boleto** se `boleto` e não emitido, ou **Ver Boleto**).
- Formulário `POST /ordens-servico/{id}/gerar-cobranca` com: forma de
  pagamento, parcelas, 1º vencimento, intervalo e checkbox "Cobrança separada".

---

## 4. Bug corrigido: spinner ao concluir

Ao concluir (`concluida`) o servidor gravava o **nome** do enum (`CONCLUIDA`,
maiúsculas) e o enum nativo do Postgres `statusos` **não tinha esse rótulo** →
`InvalidTextRepresentation (500)` → o frontend ficava com o spinner girando.

Correção: adicionado o rótulo `CONCLUIDA` ao enum (`ALTER TYPE statusos ADD VALUE 'CONCLUIDA'`)
e revisado o fluxo de conclusão (data de saída + tratamento de erro no JS).

---

## 5. Migração de banco de dados

A coluna `cobranca_separada` em `ordens_servico` é **criada automaticamente**
no startup (auto-migration em `app/core/lifespan.py`), assim como as demais
colunas novas — não requer script manual.

Para ambientes que preferem SQL explícito (ou PostgreSQL fora do auto-migration),
o `scripts/migracao_os_cobranca.sql` cobre:

1. `ALTER TYPE statusos ADD VALUE 'CONCLUIDA'` (se ainda não existir).
2. `ALTER TABLE nfse ADD COLUMN os_id INTEGER REFERENCES ordens_servico(id)`.
3. `ALTER TABLE ordens_servico ADD COLUMN cobranca_separada BOOLEAN DEFAULT FALSE`.
4. (Opcional) preencher `os_id` retroativo em NFSe de OS via regex na observação.

```bash
psql -U postgres -d controledb -f scripts/migracao_os_cobranca.sql
```

---

## 6. Arquivos envolvidos

- `models.py` — `OrdemServico.cobranca_separada` (flag de agrupamento); `os_id` na NFSe já existente.
- `routers/ordens_servico.py`:
  - `POST /ordens-servico/{id}/gerar-cobranca` — cobrança agrupada/separada, parcelamento, guarda anti-duplicação.
  - `atualizar_ordem` — persiste `cobranca_separada` e respeita o fluxo dirigido de status (não reverte `concluida` para `aberta`).
  - `detalhe_ordem` — carrega notas e `ContaReceber` vinculadas (card de cobrança).
- `templates/ordens_servico/detalhe.html` — card "Cobrança / Faturamento" (resumo das notas, lista de contas, botão Emitir Boleto e formulário de geração).
- `templates/ordens_servico/editar.html` — checkbox "Cobrança separada" e opção `concluida` no `<select>` de status.
- `routers/nfe.py` / `routers/nfse.py` — `emitir/os` (gera rascunho; não travado por status, por decisão de produto).
- `services/parcelamento.py` — `gerar_contas_receber` reutilizado (aceita `nfe_id` + `nfse_id` na mesma conta).
- `scripts/migracao_os_cobranca.sql` — migração de banco.

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

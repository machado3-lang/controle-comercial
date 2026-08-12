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

> O faturamento completo de uma OS normalmente envolve **duas notas**: NFS-e
> (serviços, ISS) + NF-e (peças, ICMS). **Por padrão a cobrança é agrupada em
> UMA só** (`ContaReceber` referenciando NFe + NFSe) — e, se a forma de
> pagamento for boleto, emite-se **um único boleto** para o cliente. Caso o
> cliente prefira cobrança isolada, marque `cobranca_separada` na OS (ou no
> formulário de geração) e cada nota gera sua própria `ContaReceber`.

### 3.3 Gerar a cobrança (ContaReceber)

A cobrança nasce da nota, mas para a OS há um atalho que agrupa:

- **OS (agrupada/separada):** `POST /ordens-servico/{id}/gerar-cobranca`
  - Cria **UMA** `ContaReceber` com `nfe_id` + `nfse_id` e
    `valor = NFe.valor_total + NFSe.valor_liquido` (usa `NFSe.valor_liquido`
    para respeitar ISS retido). Se `forma_pagamento = boleto`, o boleto
    emitido é único (1:1 com a conta).
  - Se `cobranca_separada=1`, gera **uma conta por nota** (comportamento
    anterior), útil quando o cliente quer receber as cobranças separadas.
  - Suporta parcelamento (`num_parcelas`/`intervalo_dias`): N parcelas =
    N contas do mesmo grupo (e N boletos, se boleto). Pode ser à vista,
    dinheiro, PIX ou a prazo sem boleto (conta fica em aberto).
  - **Guarda anti-duplicação:** bloqueia se já houver `ContaReceber` ativa
    vinculada a qualquer nota da OS.
- **Por nota (mantido):** NFSe `POST /nfse/{id}/gerar-cobranca` e
  NFe `POST /nfe/{id}/gerar-cobranca` (usado no fluxo separado).

### 3.4 Rastro
`OS → NFe(os_id) → ContaReceber(nfe_id)` e `OS → NFSe(os_id) → ContaReceber(nfse_id)`.

---

## 4. Bug corrigido: spinner ao concluir

Ao concluir (`concluida`) o servidor gravava o **nome** do enum (`CONCLUIDA`,
maiúsculas) e o enum nativo do Postgres `statusos` **não tinha esse rótulo** →
`InvalidTextRepresentation (500)` → o frontend ficava com o spinner girando.

Correção: adicionado o rótulo `CONCLUIDA` ao enum (`ALTER TYPE statusos ADD VALUE 'CONCLUIDA'`)
e revisado o fluxo de conclusão (data de saída + tratamento de erro no JS).

---

## 5. Migração de banco de dados

As mudanças de esquema **não** são versionadas automaticamente. Execute o script
`scripts/migracao_os_cobranca.sql` em cada ambiente (dev/homolog/prod):

1. `ALTER TYPE statusos ADD VALUE 'CONCLUIDA'` (se ainda não existir).
2. `ALTER TABLE nfse ADD COLUMN os_id INTEGER REFERENCES ordens_servico(id)`.
3. (Opcional) preencher `os_id` retroativo em NFSe de OS via regex na observação.

```bash
psql -U postgres -d controledb -f scripts/migracao_os_cobranca.sql
```

---

## 6. Arquivos envolvidos (esta entrega)

- `routers/ordens_servico.py` — guardas de transição, proteção ao cancelar, `data_saida` na conclusão.
- `templates/ordens_servico/detalhe.html` — modal de finalização, campo Data Saída, botões desabilitados por status.
- `models.py` / `models_nfe.py` — `os_id` na NFSe + relacionamentos `OS.nfses` / `NFSe.os`.
- `routers/nfe.py` / `routers/nfse.py` — vincular `os_id` na emissão da OS e bloquear cobrança de rascunho.
- `scripts/migracao_os_cobranca.sql` — migração de banco.

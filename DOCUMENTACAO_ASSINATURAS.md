# Documentação — Módulo de Assinaturas

Documentação focada exclusivamente no módulo **Assinaturas** (`/assinaturas`) do sistema de
Controle de Serviços e na sua integração com a **NFSe** (emissão a partir da assinatura)
e com o **Contas a Receber** (geração recorrente de cobranças).

> Complementa `DOCUMENTACAO.md`, `DOCUMENTACAO_NFE.md` e `DOCUMENTACAO_OS.md`.
> Aqui só tratamos assinaturas, NFSe-originada-de-assinatura e as cobranças recorrentes.

---

## 1. Visão Geral

| Aspecto | Detalhe |
|---------|---------|
| Backend | FastAPI (Python) — `routers/assinaturas.py` |
| Modelo | `models.Assinatura` / `models.AssinaturaHistorico` |
| Emissão NFSe | `routers/assinaturas.py::gerar_nfse_assinatura` → cria `NFSe` (`origem="assinatura"`) |
| Cobrança recorrente | `ContaReceber` com `observacao` contendo `assinatura #<id>` |
| Períodicidade | 1=Mensal, 2=Bimestral, 3=Trimestral, 4=Semestral, 5=Anual, 6=Bianual, 7=Trianual |
| Situação | 0=Inativo, 1=Ativo, 2=Baixado, 3=Isento, 4=Em Avaliação |
| Alerta | "Emitir agora" (vermelho) quando faltam 0–9 dias para o próximo vencimento |

Fluxo típico de uma assinatura ativa:
cria-se a assinatura → o sistema gera as **cobranças recorrentes** (`ContaReceber`) →
próximo ao vencimento aparece o alerta **"Emitir agora"** → emite-se a **NFSe a partir
da assinatura** → gera-se a **cobrança** da NFSe (que "consome" o ciclo e avança o próximo
vencimento). O ciclo se repete a cada periodicidade.

---

## 2. Modelo de Dados (`models.py`)

### Tabela `assinaturas` (classe `Assinatura`)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | int PK | Identificador |
| `cliente_id` | FK `clientes.id` | Cliente dono da assinatura |
| `periodicidade` | int | 1..7 (ver tabela de labels em `PERIODICIDADE_MESES`) |
| `descricao` | Text | Nome/serviço da assinatura |
| `valor` | Numeric(12,2) | Valor do ciclo |
| `quantidade` | int nullable | Ex.: nº de pessoas |
| `data_inicio` | Date | Ancora o mês/ano de referência do vencimento |
| `data_fim` | Date nullable | Fim da vigência (costuma ficar em 2050 = indeterminado) |
| `dia_vencimento` | int | Dia do mês do vencimento (ex.: 20) |
| `mes_vencimento` | int | 0 = mês corrente, 1 = mês seguinte (desloca o 1º vencimento) |
| `situacao` | int | 0..4 (ver `SITUACAO_LABELS`) |
| `fornecedor_id` | FK nullable | Revenda/fornecedor (opcional) |
| `valor_revenda` | Numeric nullable | Custo de revenda (lucro = valor − valor_revenda) |
| `numero_contrato` | str nullable | Nº do contrato |
| `observacao` | Text nullable | Observações livres |
| `travar_cobranca` | bool | Se `True`, a cobrança NÃO é gerada pela assinatura e sim pela NFSe |
| `produto_id` | FK nullable | Serviço (`Produto` tipo `servico`) vinculado |
| `nfse_id` | FK nullable | Última NFSe gerada (relação `nfse`) |
| `bling_*` | — | Campos de sincronização com Bling |
| `created_at` / `updated_at` | DateTime | Auditoria |

Relacionamentos:
- `cliente`, `fornecedor`, `produto`
- `nfse`  → `NFSe` apontada por `Assinatura.nfse_id` (última NFSe)
- `nfses` → todas as `NFSe` com `assinatura_id` (histórico de emissões)
- `historico` → `AssinaturaHistorico` (alterações de valor/revenda/quantidade/dia)

### Tabela `assinaturas_historico` (classe `AssinaturaHistorico`)

Registra alterações de `valor`, `valor_revenda`, `quantidade` e `dia_vencimento`
(antes/depois), usado para auditoria de reajustes.

### Vinculação com NFSe (`models_nfe.py`)

`NFSe` possui:
- `origem` = `"assinatura"` (além de `pedido`, `os`, `consolidacao`, `avulsa`, `importada`)
- `assinatura_id` → `Assinatura` (relação `assinatura` / `nfses`)
- `nfse.assinatura` e `assinatura.nfse` (última) e `assinatura.nfses` (todas)

---

## 3. Rotas (`routers/assinaturas.py`)

| Método | Rota | Ação |
|--------|------|------|
| GET | `/assinaturas/` | Listagem com cálculo de próximo vencimento e alertas |
| POST | `/assinaturas/novo` | Cria assinatura + gera cobranças recorrentes |
| POST | `/assinaturas/{id}/gerar-cobranca` | Gera N próximas cobranças (Renovar) |
| POST | `/assinaturas/{id}/marcar-ciclo-externo` | Marca o ciclo atual como emitido externamente (cria cobrança PAGA e avança o vencimento) |
| POST | `/assinaturas/{id}/gerar-nfse` | Gera **rascunho de NFSe** a partir da assinatura |
| GET | `/assinaturas/{id}/editar` | Formulário de edição |
| POST | `/assinaturas/{id}/editar` | Atualiza + ajusta vencimento das cobranças futuras |
| POST | `/assinaturas/{id}/historico/{hid}/excluir` | Exclui item de histórico (senha) |
| GET | `/assinaturas/{id}/cancelar` | Situação → Inativo (0) |
| POST | `/assinaturas/{id}/excluir` | Exclui assinatura (senha) |

### `gerar_nfse_assinatura` (emissão a partir da assinatura)
Cria um **rascunho** `NFSe` (`status="rascunho"`) com:
- `origem="assinatura"`, `assinatura_id` preenchido,
- `valor_total = assinatura.valor`,
- um `NFSeItem` com a descrição do serviço + assinatura,
- e vincula `assinatura.nfse_id = nfse.id`.

> Atenção: **não gera cobrança** e **não avança o vencimento** por si só. A cobrança
> (e o avanço do ciclo) só ocorre em `routers/nfse.py::gerar_cobranca_nfse`, que cria
> um `ContaReceber` com observação `Cobrança automática - assinatura #<id> (NFSe #<id>)`.

> **Gotcha:** `gerar_cobranca_nfse` **recusa NFSe em `rascunho`** (`routers/nfse.py:1197`):
> só é possível gerar a cobrança após **emitir/transmitir** a nota (status passa a
> `autorizada`). Portanto o fluxo real é: gerar NFSe (rascunho) → revisar → **emitir** →
> gerar cobrança. Tentar gerar cobrança de um rascunho retorna erro e não cria `ContaReceber`.

---

## 4. Cálculo do Próximo Vencimento (núcleo do alerta)

O alerta "Emitir agora" é 100% derivado de uma função Python:
**`proximo_vencimento_exibicao(db, assinatura)`** (`routers/assinaturas.py:106`).

```text
se situacao != 1: retorna None
ultima = ContaReceber mais recente (maior data_vencimento) do mesmo cliente,
         cuja observacao contenha "assinatura #<id>"
         e status NOT IN (CANCELADO, EXCLUIDO)
se ultima existir:
    prox = get_safe_day(_add_months(ultima.data_vencimento, periodo_meses), dia_vencimento)
senao:
    prox = _proximo_vencimento(assinatura)   # baseado em data_inicio (com while data < hoje)
```

**Pontos críticos:**

1. O vencimento exibido = **última cobrança já gerada + periodicidade**. Ele NÃO é
   recalculado a partir de "hoje" quando há cobranças. Ou seja, é uma data **determinística**
   ancorada na última `ContaReceber`, não em um relógio que "anda sozinho".
2. `_proximo_vencimento` (usado só quando NÃO há cobrança alguma) sim tem o loop
   `while data < hoje: data = _add_months(data, periodo)` — este sim avança datas passadas.
   Mas esse caminho raramente é o ativo em assinaturas em operação (que já têm cobranças).
3. `get_safe_day` ajusta dias inválidos (ex.: dia 31 em fevereiro → 1º do mês seguinte).

### Faixas de alerta (listagem, `assinaturas.py:191`)
| Condição | Badge | Cor |
|----------|-------|-----|
| 0–9 dias | `Emitir agora` | vermelho (rose) |
| 10–15 dias | `Emitir NFSe` | ciano |
| 16–30 dias | `Atenção` | âmbar |

O mesmo cálculo alimenta o dashboard (`app/core/lifespan.py:817`), usado para o card
"Assinaturas vencendo".

---

## 5. Geração Recorrente de Cobranças (`_gerar_cobranca`)

Chamada em:
- `criar_assinatura` (ao cadastrar),
- `gerar_cobranca` (botão **Renovar** na listagem, `gerar_proximas=3` por padrão).

Lógica:
- Se `travar_cobranca` for `True` → **não gera nada** (a cobrança virá da NFSe).
- Base = última cobrança da assinatura + periodicidade (ou `data_inicio` se não houver).
- Gera `gerar_proximas` (3) cobranças futuras, evitando duplicar `descricao` já existente
  e descartando datas no passado.

Cada cobrança tem `observacao = "Cobrança automática - assinatura #<id>"`, o que permite
ao sistema rastrear o ciclo e calcular o próximo vencimento (seção 4).

Ao **editar** o `dia_vencimento`, as cobranças futuras (pendentes) são reajustadas para o
novo dia (`assinaturas.py:595`).

---

## 6. Caso Especial: NFSe emitida FORA do sistema (portal da Betha)

### O problema
Quando a NFSe é emitida **diretamente no portal da prefeitura/Betha** (e não pelo nosso
sistema), nenhum `ContaReceber` do ciclo é criado aqui. Consequência:

- A última `ContaReceber` da assinatura continua sendo a do ciclo **anterior**.
- `proximo_vencimento_exibicao` calcula `ultima + periodo` = o ciclo que já foi pago/emitido
  fora → o sistema ainda mostra esse ciclo como **"Emitir agora"**, mesmo sabendo (pro
  usuário) que já foi emitido.

**Não há, hoje, nenhum flag/campo de "NFSe emitida externamente"** que resolva isso
automaticamente. O cálculo só avança quando surge uma `ContaReceber` mais recente
vinculada à assinatura.

### Resposta à dúvida: "ao passar de 20/08 para 21, avança sozinho para 20/09?"
**Não.** Como o vencimento exibido é `ultima_cobranca + periodo` (sem re-ancoragem em
`hoje`), a data mostrada **não muda** quando o relógio passa de 20/08 para 21/08. O alerta
"Emitir agora" (para 20/08) **persiste indefinidamente** até que uma nova `ContaReceber`
vinculada seja gerada — o que desloca a "última" para a frente.

> Em outras palavras: o sistema não "anda" a data sozinho. Ele só avança quando o ciclo
> é de fato registrado (cobrança gerada). Isso já foi analisado e **não há tratamento
> automático anterior** para emissão externa.

### Como resolver (manual, para as 2 NFSe citadas — vencimento 20/08/2026)
Para fazer o próximo vencimento pular de 20/08 para **20/09/2026** sem criar uma NFSe
duplicada, basta registrar a cobrança do ciclo já emitido fora:

1. Na assinatura, crie uma `ContaReceber` com:
   - `cliente_id` = cliente da assinatura,
   - `descricao` = `"Mensal - <descrição> - 08/2026"`,
   - `data_vencimento` = `2026-08-20`,
   - `observacao` = `"Cobrança automática - assinatura #<id>"`  ← **obrigatório** para o rastreio,
   - `status` = **PAGO/RECEBIDO** (pois já foi recebido via emissão externa).
2. Pronto: `ultima` passa a ser 20/08/2026 → `proximo_vencimento_exibicao` retorna **20/09/2026**
   e o alerta "Emitir agora" some, voltando a aparecer só perto de 20/09.

Alternativas (menos precisas):
- **Renovar** (`/assinaturas/{id}/gerar-cobranca`): gera 20/08, 20/09 e 20/10. Resolve o
  alerta, mas cria 3 recebíveis; marque a de 20/08 como PAGA e as demais como pendentes
  futuras (o alerta "pulará" para 20/11, não 20/09).
- Se a assinatura usa `travar_cobranca`, emita uma NFSe e use "Gerar cobrança" — mas isso
  criaria uma NFSe duplicada da emitida fora. Prefira o procedimento manual acima.

> **Implementado:** botão **"Marcar ciclo como emitido externamente"** no formulário de
> edição da assinatura (`routers/assinaturas.py::marcar_ciclo_externo`). Ele cria (ou marca
> como recebida) uma `ContaReceber` PAGA para o próximo vencimento exibido, com
> `observacao = "Cobrança automática - assinatura #<id> (emitida externamente)"`. Isso
> desloca a "última" cobrança para a frente e faz `proximo_vencimento_exibicao` retornar o
> próximo período — eliminando o falso "Emitir agora". Exige confirmação de senha e grava
> auditoria (`marcar_ciclo_externo`).

---

## 7. Relacionamento com Contas a Receber — regras de rastreio

- O vínculo de ciclo é feito pela **`observacao`** conter `assinatura #<id>`
  (padrão: `"Cobrança automática - assinatura #<id>"` ou `"... (NFSe #<id>)"`).
- `StatusConta.CANCELADO` e `StatusConta.EXCLUIDO` são **ignorados** no cálculo do próximo
  vencimento. Logo, uma cobrança cancelada/excluída "some" do histórico e o sistema volta a
  ancorar na anterior (cuidado ao excluir cobranças de ciclos já pagos).
- Cobranças `PAGO` **continuam** contando como "última" — é assim que o ciclo avança após
  receber.

---

## 8. Migrations relevantes (`alembic/versions`)

- `2fd056b77553_baseline_inicial.py` — tabelas `assinaturas` / `assinaturas_historico`.
- `a1b2c3d4e5f6_travar_cobranca_assinatura.py` — campo `travar_cobranca` (cobrança via NFSe).
- `7a1b2c3d4e5f_vinculo_variacao_notas.py` — vínculo NFSe ↔ assinatura (`assinatura_id`/`nfse_id`).

---

## 9. Notas de auditoria / testes

- Testes em `tests/test_assinaturas_vencimento.py` (ordenação, ajuste de dia, badge de janela)
  e `tests/test_assinaturas_vinculo_nfse.py` (geração de NFSe + cobrança + avanço de ciclo).
- `test_assinaturas_vinculo_nfse.py` cobre o vínculo `NFSe ↔ assinatura` e o fato de a cobrança
  gerada a partir da NFSe usar o próximo vencimento da assinatura. **Corrigido** para simular a
  emissão (`status="autorizada"`) antes de chamar `gerar_cobranca_nfse`, pois a rota recusa
  NFSe em `rascunho` (ver gotcha na seção 3). Após a correção, os 5 testes de assinatura passam.
- Alterações sensíveis (excluir assinatura/histórico, **marcar ciclo externo**) exigem confirmação
  de senha e gravam
  em `audit`.

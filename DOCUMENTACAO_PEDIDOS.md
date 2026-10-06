# Documentação da Rota de Pedidos (Pedido de Venda → Consolidação → Impressão → Cobrança → NF)

> Análise técnica do fluxo completo de pedidos do sistema "Controle de Serviços",
> cobrindo criação, consolidação, visualização, impressão (térmica/A4),
> geração de cobranças (contas a receber / boletos) e emissão de NF (NFSe / NFe)
> a partir de pedidos simples ou de consolidações.
> Inclui erros, pendências e sugestões de melhoria com visão de ERP profissional.

---

## 1. Visão Geral do Fluxo

```
┌────────────────────┐
│  NOVO PEDIDO       │  routers/pedidos.py:198 (novo_pedido)
│  (status PENDENTE, │  routers/pedidos.py:216 (salvar_pedido)
│   tipo_pedido      │
│   "venda")         │
└─────────┬──────────┘
          │
          ├── (opcional) virar PRÉ-VENDA ── status=PRE_VENDA  [para poder consolidar]
          │
          ├───────────────────────────────┐
          │                               │
   ROTA A: Pedido simples          ROTA B: Consolidação
          │                               │
   ├─ Imprimir (térmica/A4)        ├─ /consolidacoes/nova  (seleciona PRÉ-VENDAs)
   │   pedidos.py:578              │   consolidacoes.py:103
   │                               │
    ├─ Finalizar (fatura)           ├─ /consolidacoes/criar (agrega itens)
    │   pedidos.py:492              │   consolidacoes.py:244
    │   → status FATURADO           │   → PedidoConsolidado (ABERTO→CONCLUIDO)
    │   → NÃO gera ContaReceber     │   → finalizar_consolidacao:661
    │     (exceto ação "recibo")    │   → NÃO gera ContaReceber
    │   → baixa estoque             │     (exceto ação "recibo")
    │                               │
    ├─ Emitir NFSe  (serviços)      ├─ Emitir NF (NFSe+NFe) a partir da consolidação
    │   nfse.py:362                 │   nfse.py:668  /  nfe.py:918
    │   → rascunho (sem cobrança)   │   → rascunhos (sem cobrança)
    │                               │
    └─ Emitir NFe (produtos)
        nfe.py:652
        → rascunho (sem cobrança)

    Cobrança (ContaReceber) e boleto nascem na TRANSMISSÃO da nota
    ( _garantir_cobranca_nfe / _garantir_cobranca_nfse ), uma por nota.
    Exceção: "Gerar recibo (sem NFs)" gera as contas na finalização.
```

**Modelos centrais** (`models.py`):
- `PedidoVenda` (`:500`) e `PedidoVendaItem` (`:529`)
- `PedidoConsolidado` (`:553`), `PedidoConsolidadoItem` (`:591`),
  `PedidoConsolidadoItemOrigem` (`:616`) — rastreabilidade
- `ContaReceber` (`:218`) — cobranças
- `StatusPedido` (`:475`), `StatusConsolidacao` (`:485`), `FormaPagamento` (`:492`)

---

## 2. Criação de Novo Pedido

**Arquivos:** `routers/pedidos.py`
- `novo_pedido` (`:198`) — tela; monta `itens_json` (apenas tipos
  `produto`, `servico`, `kit`) e calcula próximo número livre.
- `salvar_pedido` (`:216`) — cria/atualiza pedido + itens (inclui
  explosão de kits em pai+filhos).
- `_proximo_numero_pedido` (`:36`) — próximo número livre (trata não
  numéricos e garante unicidade da coluna `numero` UNIQUE).

**Observações:**
- Novo pedido é criado com `status=PENDENTE` e `tipo_pedido="venda"`
  por padrão (`models.py:507`, `:510`). Para entrar em consolidação é
  preciso mudar o **status** para `PRE_VENDA` (via `atualizar_status`,
  `pedidos.py:437`).
- Kits: em `salvar_pedido` (`:285-312`) o kit vira um item-pai + itens-
   filho (composição) persistidos no pedido. O `pedido.total` soma apenas
   o preço do kit-pai (correto), mas os filhos também ficam gravados com
   seus próprios `total`. Ao exibir, é preciso tomar cuidado para não
   somar pai+filhos.

### 2.1. Desconto no resumo (% e R$)

**Regra:** o desconto **não** é "enterrado" no preço unitário. O
subtotal é a soma dos itens-pai e `total` é o **líquido**
(`subtotal - valor_desconto`).

- Modelo `PedidoVenda` (`models.py`): `desconto_percentual`
  (`Numeric(5,2)`), `valor_desconto` (`Numeric(12,2)`) e a property
  `subtotal` (soma só dos itens sem `item_pai_id`).
- Migration: `alembic/versions/b7c8d9e0f1a2_desconto_pedido_venda.py`
  (idempotente; o startup também cria a coluna via `_add_missing_columns`).
- Tela (`templates/pedidos/form.html`): o card **Resumo** fica fora do
  `<form>`, por isso os valores viajam nos hidden
  `desconto_percentual` / `valor_desconto` (mesmo padrão da NFe avulsa,
  que usa `descontoHidden`). Os dois campos se calculam mutuamente e
  **nunca** o campo em foco é reescrito (era isso que impedia digitar
  centavos no R$); o último campo editado é a fonte da verdade quando
  itens mudam (`reaplicarDesconto`).
- Backend (`salvar_pedido`): o R$ prevalece quando informado, senão
  deriva da %; o desconto é limitado ao subtotal e a % é recalculada a
  partir do valor aplicado.
- O desconto aparece em `detalhe.html` e `imprimir.html` (Subtotal /
  Desconto / Total) e o agrupamento de pré-venda (`finalizar_grupo`)
  transporta a soma dos descontos de origem para o pedido agrupado.
- Propagação: o desconto do pedido chega à NFe (como `vDesc` por item) e à
  NFS-e (no valor unitário), rateado proporcionalmente quando o pedido tem
  produtos e serviços (`services/desconto.py`). Na consolidação, o desconto
  é a soma dos descontos dos pedidos de origem (ver §6 e
  `DOCUMENTACAO_NFE.md` §10.2).

---

## 3. Pré-venda e Consolidação (ATENÇÃO: dois mecanismos distintos)

> ⚠️ **Ponto crítico de arquitetura:** existem **duas** formas de
> "agrupar" pedidos no sistema, com modelos e regras diferentes:

### 3.1. Mecanismo A — Agrupamento de Pré-venda (leve)
`routers/pedidos.py`
- `agrupar_pre_venda` (`:111`) — lista PRÉ-VENDAs por cliente.
- `finalizar_grupo` (`:131`) — cria **um único novo `PedidoVenda`**
  FATURADO agregando os itens, e marca os originais como `AGRUPADO`
  (`pedido_agrupado_id`).

**Problemas deste mecanismo:**
- Não usa o modelo `PedidoConsolidado`; é um "pseudo-pedido" FATURADO.
- `finalizar_grupo` (`:157-195`) **não gera ContaReceber nem baixa
  estoque**. O novo pedido FATURADO fica "solto" — só gera cobrança/
  baixa se o usuário depois clicar em "Finalizar" esse pedido agrupado.
- Os pedidos originais (AGRUPADO) continuam no banco. A receita contabilizada
  2x foi **CORRIGIDA** — ver "Receita duplicada por faturamento duplo" no fim
  deste documento. A NF-e e a NFS-e já recusavam pedido agrupado/consolidado;
  faltava a mesma guarda em "Finalizar pedido", que era o caminho que gerava
  a segunda cobrança.

### 3.2. Mecanismo B — Consolidação completa (modelo `PedidoConsolidado`)
`routers/consolidacoes.py`
- `nova_consolidacao` (`:103`) — seleciona PRÉ-VENDAs por cliente/período.
- `criar_consolidacao` (`:244`) — agrega itens (pula filhos de kit),
  marca pedidos originais `CONSOLIDADO` e grava
  `PedidoConsolidadoItemOrigem` para rastreabilidade.
- `_rebuild_itens_consolidacao` (`:407`) — reagrega ao adicionar/remover.
- `adicionar_pedido_consolidacao` (`:461`) / `remover_pedido_consolidacao`
  (`:493`) — mantém a consolidação ABERTA editável.
- `finalizar_consolidacao` (`:661`) — CONCLUIDO + gera ContaReceber
  (parcelamento/boleto).
- `cancelar_consolidacao` (`:746`) — libera pedidos (voltam a PRÉ-VENDA).

**Regras de negócio aplicadas:**
- Só `PRE_VENDA` pode entrar (`:274`, `:478`).
- Pedido que já pertence a consolidação não entra noutra (`:481`).
- Número `CONS-XXXXXX` livre e único (`:215`).
- Permite multi-cliente (o titular = primeiro pedido selecionado) (`:287`).

---

## 4. Visualização da Consolidação

- `detalhe_consolidacao` (`consolidacoes.py:587`) — carrega cliente,
  itens, origens (rastreabilidade), pedidos de origem, contas a receber
  e NFSe; expõe `itens_json` para a tela.
- `pedidos_disponiveis_api` (`:533`) — AJAX para adicionar pedidos em
  aberto (declarada antes da rota dinâmica para evitar 422).

**Pendência:** o `PedidoConsolidado` tem relacionamento `nfse`
(`models.py:579`) mas **não** tem `nfes` (NFe). Uma NFe emitida a partir
da consolidação (`nfe.py:918`, `nfse.py:668`) fica vinculada por
`consolidacao_id` mas **não é exibida nem considerada** em
`detalhe_consolidacao`/cancelamento.

---

## 5. Impressão (Térmica e A4)

A impressão de pedidos e consolidações foi **reformulada por completo** para
seguir o mesmo padrão visual profissional da **Ordem de Serviço**
(`ordens_servico/imprimir_a4.html` / `imprimir_termica.html`): cabeçalho com
logotipo e dados do emitente, blocos de cliente/dados, tabela de itens,
totais, observações e assinaturas. Os templates **não dependem de CDN
externo** (o antigo A4 usava Tailwind via CDN, o que deixava o layout
"desproporcional" e dependente de internet).

### 5.1. Onde imprimir

| Tela | Ação | Destino |
|------|------|---------|
| `pedidos/listar.html` | Botão **Imprimir A4** (ícone printer) | `/pedidos/{id}/imprimir` |
| `pedidos/listar.html` | Botão **Imprimir Térmica** (ícone receipt) | `/pedidos/{id}/imprimir?termica=1` |
| `pedidos/detalhe.html` | Link **Imprimir** (visível p/ pedido faturado de venda) | `/pedidos/{id}/imprimir` |
| `pedidos/detalhe.html` | Link **Térmica** | `/pedidos/{id}/imprimir?termica=1` |
| `pedidos/form.html` | Ao salvar com `acao=emitir` | redireciona para `/pedidos/{id}/imprimir` |
| `consolidacoes/listar.html` | Botões **Imprimir** / **Térmica** | `/consolidacoes/{id}/imprimir[?termica=1]` |
| `consolidacoes/detalhe.html` | Links **Imprimir** / **Térmica** | `/consolidacoes/{id}/imprimir[?termica=1]` |

Todos os botões/links abrem em **nova aba** (`target="_blank"` /
`window.open`), preservando a tela de origem.

### 5.2. Formatos suportados

- **A4** (`pedidos/imprimir.html` / `consolidacoes/imprimir.html`): layout
  paginável, ideal para impressão em impressora comum ou "Salvar como PDF".
- **Térmica 80mm** (`pedidos/imprimir_termica.html` /
  `consolidacoes/imprimir_termica.html`): layout compacto em fonte monoespaçada,
  cortado por `size: 80mm auto`, ideal para impressoras térmicas de cupom.

### 5.3. Botões de impressão na lista de pedidos

Em `templates/pedidos/listar.html` foram adicionados dois botões de ação por
linha (além dos existentes **Ver** e **Excluir**), acessíveis para **qualquer
status** do pedido:

```html
<button onclick="window.open('/pedidos/{{ p.id }}/imprimir','_blank')"
        title="Imprimir A4"><i data-lucide="printer"></i></button>
<button onclick="window.open('/pedidos/{{ p.id }}/imprimir?termica=1','_blank')"
        title="Imprimir Térmica"><i data-lucide="receipt"></i></button>
```

> Observação: na tela de **detalhe** do pedido, os links de impressão só
> aparecem quando `status == 'faturado'` e `tipo_pedido == 'venda'` (mesmo
> critério dos botões de NF). Para impidos em outros status, use os botões da
> lista.

### 5.4. Rotas e parâmetros

- **`GET /pedidos/{pedido_id}/imprimir`** — `imprimir_pedido`
  (`routers/pedidos.py`).
  - `termica` (query, opcional): se presente, usa o template térmico; senão, A4.
  - `tipo` (query): `"faturado"` (padrão) → título "Pedido de Venda";
    `"orcamento"` → título "Orçamento".
  - Carrega `PedidoVenda.itens` **+ `filhos`** (composição de kit) e
    `PedidoVenda.cliente` via `selectinload`.
- **`GET /pedidos/{pedido_id}/pdf`** — `pdf_pedido` (`routers/pedidos.py`).
  Gera **PDF A4 real** via WeasyPrint a partir de `pedidos/imprimir.html`.
  Em caso de falha no WeasyPrint, faz fallback redirecionando para a rota de
  impressão no navegador.
- **`GET /consolidacoes/{consolidacao_id}/imprimir`** — `imprimir_consolidacao`
  (`routers/consolidacoes.py`).
  - `termica` (query): igual ao pedido.
  - `tipo` (query): `"consolidado"` (padrão).
  - Carrega cliente, itens (com `produto`/`variacao`), `pedidos_origem` e `now`.

### 5.5. Variáveis passadas aos templates

| Variável | Conteúdo |
|----------|----------|
| `pedido` / `consolidacao` | objeto principal com itens, cliente, total, etc. |
| `empresa` | primeiro registro de `Empresa` (logotipo, razão social, CNPJ, endereço, contatos) |
| `STATUS_LABELS` | rótulos amigáveis do status (`STATUS_PEDIDO_LABELS` / `STATUS_CONSOLIDACAO_LABELS`) |
| `FORMAS_PAGAMENTO` | mapa de `FormaPagamento` → rótulo ("À Vista", "Boleto", etc.) |
| `now` | `datetime.now()` para o rodapé "Documento gerado em …" |
| `tipo_impressao` | controla o título (Pedido de Venda / Orçamento / Consolidação) |

### 5.6. Layout do template A4 (pedido)

1. **Cabeçalho**: logo + nome fantasia/razão social, CNPJ/IE, endereço e
   contatos do emitente (esquerda); título "Pedido de Venda"/"Orçamento" + nº
   + badge de status (direita).
2. **Bloco Cliente**: nome, CPF/CNPJ, telefone, e-mail e endereço completo.
3. **Bloco Dados do Pedido**: número, data, tipo (Venda/Pré-venda), forma de
   pagamento e flag de boleto.
4. **Itens do Pedido**: tabela com colunas **Cód. · Descrição · UND · Qtd ·
   Unitário · Total**; itens que são **kit/pai** mostram seus componentes
   (`filhos`) indentados e em tom mais claro (a linha do pai já contém o total
   do kit, então os filhos são exibidos apenas para conferência — o Total
   Geral reflete `pedido.total`).
5. **Total Geral**.
6. **Observações** (se houver).
7. **Assinaturas**: "Assinatura do Cliente" e "Emitente".
8. **Rodapé**: "Documento gerado em … • <empresa>".

### 5.7. Layout do template térmico (pedido)

Cabeçalho compacto (logo opcional + emitente + CNPJ + endereço), título
"PEDIDO DE VENDA"/"ORÇAMENTO" + nº + status + data, bloco **CLIENTE** (nome,
doc, tel, endereço, pagamento), lista de **ITENS** (descrição + total, e
abaixo a quantidade/UND × unitário; composição de kit indentada), **TOTAL**,
**OBS** e assinatura do cliente.

### 5.8. Composição de kit (filhos)

Tanto no A4 quanto no térmico, os itens que possuem `item_pai_id` (componentes
de um kit) são renderizados **abaixo** do item-pai, indentados e com tom
reduzido (`↳`), garantindo fidelidade ao que é exibido na tela de detalhe do
pedido. O carregamento dos filhos é feito no router via
`selectinload(PedidoVenda.itens).selectinload(PedidoVendaItem.filhos)`.

### 5.9. Consolidação

Os templates de consolidação (`templates/consolidacoes/imprimir.html` e
`imprimir_termica.html`) foram **alinhados** ao mesmo padrão do pedido/OS:
incluem bloco Cliente + Dados da Consolidação (emissão, fechamento, período,
forma de pagamento, boleto), tabela de **Pedidos Agrupados**, tabela de
**Itens Consolidados** (com colunas Cód./UND) e assinaturas. Isso elimina a
divergência de layout entre pedido e consolidação citada anteriormente.

### 5.10. PDF (WeasyPrint)

`pdf_pedido` renderiza `pedidos/imprimir.html` (contexto igual à rota de
impressão, com `now` e `filhos`) e devolve um `FileResponse` `application/pdf`
nomeado `pedido_<numero>.pdf`. Reutiliza o mesmo template do A4, mantendo
visual idêntico entre "Imprimir/Salvar PDF" no navegador e o download PDF.

### 5.11. Manutenção

- Para alterar o cabeçalho/rodapé de todos os documentos, edite os seletores
  `.cabecalho`, `.titulo-*`, `.assinaturas` e o bloco de rodapé nos templates
  (padrão compartilhado entre pedido, consolidação e OS).
- Para mudar colunas da tabela de itens, edite a `<thead>`/`<tbody>` em
  `pedidos/imprimir.html` e `consolidacoes/imprimir.html` (mantenha ambos
  consistentes).
- O logo vem de `empresa.logo`; se nulo, o cabeçalho usa apenas o nome da
  empresa.

---

## 6. Geração de NF (NFSe / NFe) — a partir de pedido simples ou consolidação

### 6.1. De um pedido simples
- **NFSe (serviços):** `nfse.py:emitir_nfse` (`:362`) — valida LC116
  único (Dourados), emite via Betha, gera PDF e **gera ContaReceber**
  automaticamente (`:461`).
- **NFe (produtos):** `nfe.py:emitir_pedido_submit` (`:652`) — cria
  rascunho NFe (+ NFSe se houver serviços). **Não gera ContaReceber**.

### 6.2. Da consolidação
- `nfse.py:emitir_consolidacao_nfse` (`:668`) — cria rascunho **NFe +
  NFSe** da consolidação (explode itens via `explodir_itens_consolidacao`).
- `nfe.py:emitir_consolidacao_submit` (`:918`) — cria rascunho NFe
  (+ NFSe se serviços).

### 6.3. Explosão de itens (serviço `nfe_notaas.py`)
- `explodir_itens` (`:283`) e `explodir_itens_consolidacao` (`:375`)
  separam produtos (NFe) / serviços (NFSe) e **explodem kits**
  (`_explodir_kit`, `:351`).
- ⚠️ **Discrepância de valor em kits:** a explosão usa
  `insumo.preco` (preço **atual** do produto) e não o preço negociado
  armazenado no item do pedido/consolidação. O valor da NF pode divergir
  do `pedido.total`/`consolidacao.total` (ex.: kit vendido com desconto).

### 6.4. ERROS / PENDÊNCIAS CRÍTICAS em emissão fiscal
1. **Sem guarda contra NF duplicada em pedido simples.**
   `emitir_nfse` (`nfse.py:362`) e `emitir_pedido_submit` (`nfe.py:652`)
   **não verificam** se o pedido já possui NFSe/NFe. Reemitir cria
   documento fiscal duplicado (risco fiscal/SMJ).
   *Correção:* bloquear se `pedido.nfse` / `pedido.nfes` já existirem
   (como já é feito para consolidação em `nfse.py:679`).

2. **Exclusão de pedido apaga NF autorizada sem cancelá-la.**
   `excluir_pedido` (`pedidos.py:368`) com `excluir_contas=1`
   faz `db.delete(nfse)` / `db.delete(nfe)` (`:409-414`) **sem chamar a
   API de cancelamento** da prefeitura/SEFAZ. Isso deixa nota autorizada
   "no ar" e some do sistema → **ilegal para fins fiscais**.
   *Correção:* proibir exclusão de pedido com NF autorizada; exigir
   cancelamento fiscal primeiro.

3. **Cancelamento de consolidação não cancela NFe.**
   `cancelar_consolidacao` (`consolidacoes.py:746`) só marca
   `nfse.status="cancelada"` (`:778`); **ignora NFe** vinculada e **não
   chama a API de cancelamento** de NFSe/NFe. Documento fiscal permanece
   autorizado na SEFAZ/Prefeitura.
   *Correção:* invocar `cancelar_nfe` / cancelamento Betha e só então
   liberar pedidos.

4. **Geração de cobrança assimétrica.** *(corrigido em set/2026)*
   - Antes: NFSe de pedido gerava na emissão, NFe de pedido não gerava, e a
     consolidação dependia do `finalizar_consolidacao` — a cobrança faltava
     conforme o caminho.
   - Agora: **nenhuma conta nasce antes da nota**. NFe e NFSe (pedido ou
     consolidação) geram a cobrança na **transmissão**, uma por documento
     (`_garantir_cobranca_nfe` / `_garantir_cobranca_nfse`). A única exceção é o
     **recibo (sem NFs)**, que gera as contas na finalização.
   - Detalhe da NFe de pedido: o rascunho chegou a gerar cobrança e quebrava com
     `gerar_contas_receber_para_nota() got an unexpected keyword 'pedido_id'`
     (impossibilitando criar o rascunho). Ver `DOCUMENTACAO_BUG_COBRANCA_NOTAS.md`.

5. **Status desacoplado da realidade fiscal.**
   `finalizar_pedido` (`pedidos.py:492`) marca `FATURADO` **sem exigir NF** e sem
   gerar cobrança (salvo recibo); a emissão de NF não marca o pedido como
   FATURADO (somente a transmissão o faz, via `nfe.py`). Continua possível
   "faturar" sem nota — para esse caso existe o botão **"Gerar recibo (sem NFs)"**,
   que gera a cobrança. Amarração recomendada: NF transmitida ⇒ pedido FATURADO ⇒
   cobrança derivada da NF (fonte única de verdade).

---

## 7. Cobranças (Contas a Receber) e Boletos

**Serviço central:** `services/parcelamento.py`
- `gerar_contas_receber` (`:53`) — cria N parcelas (ajuste de centavos
  na última), com `numero_parcela`, `total_parcelas`, `parcelamento_grupo`.
- `contas_receber_existentes` (`:198`) — evita duplicidade (pedido/
  consolidacao/nfse/nfe já faturados).
- `emitir_boletos_contas` (`:224`) — boletos Sicoob (ignora já emitidos).

**Gatilhos de cobrança (regra vigente — set/2026):**
- **Transmissão da nota** (`_garantir_cobranca_nfe` / `_garantir_cobranca_nfse`) —
  caminho normal para pedido e consolidação, uma conta por NFe/NFSe, com
  `pedido_id` / `consolidacao_id` / `nfe_id` / `nfse_id`.
- **Recibo (sem NFs)** (`finalizar_pedido` com `acao=recibo`;
  `finalizar_consolidacao` com `acao=recibo`) — gera as contas na finalização
  (não haverá nota).
- **Pedido sem itens** (fechado sem NFs) — `finalizar_pedido` gera as contas
  quando `gerar_cobranca` está ligado ou a forma é `boleto`.
- Botão manual **"Gerar Cobrança"** na NFe/NFSe (após autorizada).

**Flags e campos:**
- `gerar_cobranca` (pedido e consolidação): interruptor mestre. Desligado →
  nenhuma conta automática.
- `gerar_boleto`: só vale quando **não haverá nota** (recibo / pedido sem itens).
  Com nota, o boleto depende de `forma_pagamento == "boleto"` e sai após a
  autorização (`emitir_boleto_para_nota`).
- `num_parcelas`, `intervalo_dias`, `primeiro_vencimento`: definem o parcelamento
  (o 1º vencimento da consolidação passou a ser persistido).

**Pendências:**
- A cobrança usa `pedido.total` / `consolidacao.total`, mas a NF pode
  ter valor diferente (kits — seção 6.3). Ideal: a ContaReceber deve
  espelhar o valor da NF autorizada, não o do pedido.
- `num_parcelas`/`intervalo` vêm do form mas não há validação de
  `primeiro_vencimento` obrigatório nem de datas passadas.
- Boleto emitido na finalização ignora erros silenciosamente em alguns
  caminhos (apenas loga).

---

## 8. Baixa de Estoque

**Serviço:** `services/estoque_service.py:baixar_pedido` (`:213`)
- Baixa cada item como `SAIDA_VENDA`; **pula kit-pai** (a baixa é feita
  pelos insumos filhos).
- Em `finalizar_pedido` (`pedidos.py:569`) a baixa ocorre **somente se**
  `not pedido.nfse and not pedido.nfes` (evita duplicar com a baixa da NFe).

**Risco de dupla baixa:** se o usuário **finaliza o pedido primeiro**
(baixa ocorre) e **depois emite a NFe** (que também movimenta estoque
pelos componentes do kit), o estoque é reduzido duas vezes. O guard atual
só protege o sentido inverso (NF antes do finalizar).
*Correção:* usar flag única de "estoque já baixado" no pedido
(ex.: `baixado_em = 'pedido'|'nfe'`) e bloquear re-baixa.

---

## Resumo de Erros / Pendências (priorizados)

| # | Severidade | Item | Onde |
|---|-----------|------|------|
| 1 | 🔴 Crítico (fiscal) | Exclusão apaga NF autorizada sem cancelar | `pedidos.py:409-414` | **IMPLEMENTADO** (bloqueio em `excluir_pedido`) |
| 2 | 🔴 Crítico (fiscal) | Cancelar consolidação não cancela NFe nem chama API | `consolidacoes.py:746-782` | **IMPLEMENTADO** (cancela NFSe+NFe via API e aborta em falha) |
| 3 | 🔴 Crítico | NF duplicada em pedido simples (sem guarda) | `nfse.py:362`, `nfe.py:652` | **IMPLEMENTADO** (guarda em `emitir_nfse`/`emitir_pedido_submit`) |
| 4 | 🟠 Alto | Dois mecanismos de consolidação conflitantes | `pedidos.py:131` vs `consolidacoes.py:244` | **CONTIDO**: `finalizar_grupo` gera ContaReceber; emissão de NF bloqueada em pedido `AGRUPADO` (guardas em `nfse.py`/`nfe.py`) |
| 5 | 🟠 Alto | Cobrança assimétrica (NFe pedido e NF consolidação não geram) | `nfe.py:652`, `nfse.py:668` | **PARCIAL**: NFe de pedido agora gera ContaReceber ao autorizar (`transmitir_nfe`/`ver_nfe` → `_garantir_cobranca_nfe`). NF de consolidação segue vindo de `finalizar_consolidacao` (por design) |
| 6 | 🟠 Alto | Risco de dupla baixa de estoque (finalizar antes de NF) | `pedidos.py:569`, `estoque_service.py:213` | **IMPLEMENTADO**: guarda `_pedido_ja_baixado` em `baixar_pedido`/`baixar_nfe`/`baixar_nfse` |
| 7 | 🟡 Médio | Valor de kit na NF diverge do pedido (preço atual) | `nfe_notaas.py:351-372` | **IMPLEMENTADO**: `_explodir_kit` escala preços dos insumos para igualar `item.total` (valor negociado) |
| 8 | 🟡 Médio | Status desacoplado da realidade fiscal | `pedidos.py:492` | **IMPLEMENTADO**: NFSe/NFe autorizada marca pedido como `FATURADO` (`emitir_nfse`, `transmitir_nfe`, `ver_nfe`) |
| 9 | 🟡 Médio | `pdf_pedido` é stub | `pedidos.py:595` | **IMPLEMENTADO**: gera PDF A4 real via WeasyPrint (`pedidos/imprimir.html`); fallback redireciona se indisponível |
| 10 | 🟢 Baixo | `fornecedores_itens` recebido e não usado | `pedidos.py:225` | **IMPLEMENTADO**: parâmetro morto removido de `salvar_pedido` |
| 11 | 🟢 Baixo | `StatusConsolidacao.PROCESSANDO` definido e nunca usado | `models.py:487` | **IMPLEMENTADO**: removido (enum, label e refs nos templates) — nenhum código o atribuía |
| 12 | 🟢 Baixo | Consolidação sem relação `nfes` (NFe ignorada na view) | `models.py:579` | **IMPLEMENTADO**: adicionado `PedidoConsolidado.nfes` ↔ `NFe.consolidacao`; exposto em `detalhe_consolidacao` e usado no cancelamento |

## Sugestões de melhoria (visão ERP profissional)
1. **Fonte única de verdade para cobrança:** derivar a ContaReceber **da
   NF autorizada** (não do pedido), garantindo valor e duplicidade
   controlados por `nfse_id`/`nfe_id`.
2. **Estado fiscal do pedido:** adicionar `data_emissao_nf`,
   `nf_emitida`, `estoque_baixado_em` para travar transições ilegais.
3. **Unificar consolidação:** depreciar o "agrupar pré-venda"
   (`finalizar_grupo`) em favor de `PedidoConsolidado`, ou converter o
   agrupamento para criar uma consolidação de fato.
4. **Cancelamento fiscal real:** toda exclusão/cancelamento de documento
   com NF autorizada deve (a) tentar cancelar na SEFAZ/Prefeitura e
   (b) bloquear se não conseguir.
5. **Validações de entrada:** `primeiro_vencimento` obrigatório e não
   retroativo; `num_parcelas>=1`; CPF/CNPJ do cliente válido antes de faturar.
6. **Kits:** armazenar no item do pedido/NF o preço negociado dos
   componentes (snapshot), para NF e estoque baterem com o valor faturado.
7. **Auditoria fiscal:** logar toda emissão/cancelamento com
   protocolo, usuário e XML, para rastreabilidade (já existe
   `services/audit.py`).
8. **PDF real** para pedido/consolidação (download + envio), reutilizando
   `core/pdf_generator.py`.

---

## Status de Implementação (sessão atual)

Todos os **12 itens** da tabela de pendências foram **implementados** nesta
sessão. Resumo das alterações por arquivo:

### Críticos (fiscais)
- **`routers/pedidos.py` — `excluir_pedido`**: bloqueia (400) a exclusão de
  pedido que possua NFSe (`autorizada`) ou NFe (`issued`); exige cancelamento
  fiscal prévio.
- **`routers/consolidacoes.py` — `cancelar_consolidacao`**: cancela NFSe (Betha)
  e NFe(s) (SEFAZ/NotaAs) **antes** de liberar os pedidos; qualquer falha no
  cancelamento fiscal aborta o cancelamento da consolidação (sem efeito
  colateral). Passou a usar `consolidacao.nfes`.
- **`routers/nfse.py` — `emitir_nfse`** e **`routers/nfe.py` —
  `emitir_pedido_submit`**: guarda contra NF duplicada (bloqueia se já existir
  NFSe/NFe para o pedido).

### Altos
- **Mecanismo A (agrupar pré-venda)**: `finalizar_grupo` agora gera
  `ContaReceber`; emissão de NF bloqueada em pedido `AGRUPADO`
  (`emitir_nfse`/`emitir_pedido_submit`). *Unificação completa em
  `PedidoConsolidado` ficou como contenção — sugestão #3.*
- **Cobrança assimétrica**: `_garantir_cobranca_nfe` em `routers/nfe.py`
  gera `ContaReceber` para a NFe de pedido ao autorizar (`transmitir_nfe` e
  `ver_nfe`).
- **Dupla baixa de estoque**: `_pedido_ja_baixado` em `services/estoque_service.py`
  impede re-baixa em `baixar_pedido`/`baixar_nfe`/`baixar_nfse`.

### Médios
- **Valor de kit na NF**: `_explodir_kit` (`services/nfe_notaas.py`) recebe
  `valor_kit` (negociado = `item.total`) e escala os preços dos insumos para
  igualar o total da NF ao do pedido/consolidação.
- **Status desacoplado**: NFSe/NFe autorizada marca o pedido como `FATURADO`
  (`emitir_nfse`, `transmitir_nfe`, `ver_nfe`).

### Baixos
- **`pdf_pedido`**: gera PDF A4 real via WeasyPrint (fallback redireciona).
- **`fornecedores_itens`**: parâmetro morto removido de `salvar_pedido`.
- **`StatusConsolidacao.PROCESSANDO`**: removido (enum, label e refs de template).
- **`PedidoConsolidado.nfes`**: novo relacionamento ↔ `NFe.consolidacao`;
  exposto no `detalhe_consolidacao` e usado no cancelamento.

### Sugestões de melhoria (PENDENTES — trabalho futuro)
As sugestões 1 a 8 da seção anterior permanecem como aprimoramentos
arquiteturais não implementados nesta sessão (foco foi fechadura fiscal/estoque
e consistência). Destaque:
1. Fonte única de verdade para cobrança (derivar `ContaReceber` da NF autorizada).
2. Campos de estado fiscal no pedido (`data_emissao_nf`, `nf_emitida`,
   `estoque_baixado_em`).
3. Unificar de fato o "agrupar pré-venda" em `PedidoConsolidado`.
4. Cancelamento fiscal real (já iniciado nos críticos 1 e 2).
5. Validações de entrada (`primeiro_vencimento`, `num_parcelas`, CPF/CNPJ).
6. Snapshot do preço negociado dos componentes de kit (atualmente escalado).
7. Auditoria fiscal de emissão/cancelamento.
8. PDF real de consolucação.



---

## Receita duplicada por faturamento duplo (corrigido)

### O problema

Um pedido que entra em **agrupamento** (`finalizar_grupo`) ou em
**consolidação** (`criar_consolidacao`) passa a ser faturado pelo documento que
o representa: o pedido agrupado ou a consolidação. Se o pedido de origem
também fosse faturado, a mesma venda entraria duas vezes no financeiro e no
DRE.

Quem gerava a duplicidade era `POST /pedidos/{id}/finalizar`:

- `nfe.py` e `nfse.py` já recusavam pedido `CONSOLIDADO`/`AGRUPADO` na emissão;
- `atualizar_status` já bloqueia status terminais;
- **`finalizar_pedido` só checava `consolidacao_id`** — não checava o
  agrupamento. E era a única rota que gerava cobrança direta (`acao="recibo"`,
  ou pedido sem itens).

Com a_group, o caminho era:

```
finalizar_grupo   -> cria pedido agrupado FATURADO + gera cobrança
                    (originais marcados AGRUPADO + pedido_agrupado_id)
finalizar_pedido(ORIGINAL, acao="recibo")
                  -> SEM guarda de agrupamento -> gera 2ª cobrança  <-- receita 2x
```

### A correção

A regra foi reunida em **`services/guarda_faturamento.py`**, função
`bloqueio_faturamento(pedido)`, aplicada nos três pontos de faturamento:

| Ponto | Antes | Agora |
|---|---|---|
| `pedidos.py:finalizar_pedido` | só `consolidacao_id` | guarda única (**o buraco**) |
| `nfe.py` | guarda inline, texto próprio | guarda única |
| `nfse.py` | guarda inline, texto próprio | guarda única |

A regra bloqueia quando o pedido já tem representação própria, checando **o
vínculo e o status**:

- `consolidacao_id` preenchido **ou** status `CONSOLIDADO`
- `pedido_agrupado_id` preenchido **ou** status `AGRUPADO`

Checar os dois porque `status` é uma coluna `String`: pode estar gravado de
forma inconsistente, e o vínculo é a evidência mais forte. Consolidação tem
prioridade sobre agrupamento quando ambos existem.

`CANCELADO` e `FATURADO` **não** são bloqueados — cancelar e reverter são
operações legítimas, tratadas em cada rota.

### Armadilha evitada: comparar Enum com string

`StatusPedido` é um `Enum` comum (não str-based) e `.value` é minúsculo:

```python
StatusPedido.CONSOLIDADO == "CONSOLIDADO"   # False!
```

A guarda original comparava direto com string em um ponto e passaria batido.
Por isso `_eh_status` normaliza Enum, string e caixa.

### Testes

`tests/test_receita_duplicada.py` (11 testes), incluindo a contraprova de que
pedido normal com recibo **continua** gerando cobrança.

Os 3 testes de regressão foram validados **revertendo a guarda**: sem ela,
exatamente 3 falham (os que reproduzem a duplicidade). Não são testes vazios.

> Detalhe de teste: o middleware de CSRF barra o POST antes da rota rodar. Sem
> extrair o token, os testes de "deve bloquear" dariam verde por vazio. O helper
> `_csrf()` lê o token do próprio form de finalização.

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
   │   → gera ContaReceber         │   → finalizar_consolidacao:661
   │   → baixa estoque             │   → gera ContaReceber (parcelamento/boleto)
   │   → (opcional) boleto         │   → imprimir_consolidacao:785
   │                               │
   ├─ Emitir NFSe  (serviços)      ├─ Emitir NF (NFSe+NFe) a partir da consolidação
   │   nfse.py:362                 │   nfse.py:668  /  nfe.py:918
   │   → gera ContaReceber         │
   │                               │
   └─ Emitir NFe (produtos)        └─ (NFe de consolidação SEM geração de cobrança
       nfe.py:652                     própria; a cobrança vem de finalizar_consolidacao)
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
- Os pedidos originais (AGRUPADO) continuam no banco e **não têm guarda
  contra re-emissão de NF** (ver seção 6). Risco de nota fiscal duplicada
  e de receita contabilizada 2x (pedido agrupado + original).

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

- Pedido: `imprimir_pedido` (`pedidos.py:578`) — escolhe
  `imprimir_termica.html` (param `?termica=1`) ou `imprimir.html`
  (A4). Params: `tipo` (faturado) e `termica`.
- Consolidação: `imprimir_consolidacao` (`consolidacoes.py:785`) —
  mesmo padrão (`imprimir_termica.html` / `imprimir.html`).
- `pdf_pedido` (`pedidos.py:595`) é **stub**: apenas redireciona para
  `/imprimir` (sem gerar PDF real via `core/pdf_generator.py`).

**Melhorias:**
- Implementar `pdf_pedido` real (usar `core/pdf_generator.py` já existente)
  para download A4 e integração com e-mail/whatsapp.
- Homogenizar os templates de térmica (pedido x consolidação) para evitar
  divergência de layout.

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

4. **Geração de cobrança assimétrica.**
   - NFSe de pedido → gera ContaReceber (`nfse.py:461`).
   - NFe de pedido (`nfe.py:652`) → **não gera** ContaReceber.
   - NF de consolidação (`nfse.py:668`) → **não gera** ContaReceber
     (depende de `finalizar_consolidacao`).
   Resultado: cobrança pode faltar conforme o caminho percorrido.

5. **Status desacoplado da realidade fiscal.**
   `finalizar_pedido` (`pedidos.py:492`) marca `FATURADO` e gera
   cobrança **sem exigir NF**; e a emissão de NF não marca o pedido como
   FATURADO. É possível "faturar" (cobrar) sem nota e emitir nota sem
   faturar. Um ERP deve amarrar: NF emitida ⇒ pedido FATURADO; e cobrança
   derivada da NF (fonte única de verdade).

---

## 7. Cobranças (Contas a Receber) e Boletos

**Serviço central:** `services/parcelamento.py`
- `gerar_contas_receber` (`:53`) — cria N parcelas (ajuste de centavos
  na última), com `numero_parcela`, `total_parcelas`, `parcelamento_grupo`.
- `contas_receber_existentes` (`:198`) — evita duplicidade (pedido/
  consolidacao/nfse/nfe já faturados).
- `emitir_boletos_contas` (`:224`) — boletos Sicoob (ignora já emitidos).

**Gatilhos de cobrança:**
- `finalizar_pedido` (`pedidos.py:492`) — gera ContaReceber + boleto.
- `finalizar_consolidacao` (`consolidacoes.py:661`) — gera ContaReceber
  + boleto.
- `emitir_nfse` (`nfse.py:362`) — gera ContaReceber vinculada à NFSe.

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


# Documentação de Consolidação de Pedidos

> Documento exclusivo sobre a consolidação de pedidos de venda.
> Para o fluxo geral de pedidos (criação, impressão, NF), veja `DOCUMENTACAO_PEDIDOS.md`.

## 1. Conceito

A **consolidação** agrupa vários pedidos de venda em um único "pedido consolidado"
(`PedidoConsolidado`) para faturamento e cobrança conjuntos. É usada tipicamente
para fechar o faturamento mensal de um cliente (matriz/filiais) em uma só nota e
em uma só conta a receber.

### Modelos (`models.py`)

| Modelo | Tabela | Papel |
|--------|--------|-------|
| `PedidoConsolidado` | `pedidos_consolidados` | Cabeçalho da consolidação (número, cliente titular, status, total) |
| `PedidoConsolidadoItem` | `pedidos_consolidados_itens` | Itens **agregados** (soma de todos os pedidos de origem) |
| `PedidoConsolidadoItemOrigem` | (`pedidos_consolidados_itens_origem`) | Rastreabilidade: de qual pedido/item veio cada fração do item agregado |
| `PedidoVenda.consolidacao_id` | — | Liga o pedido de origem à consolidação |

`StatusConsolidacao` (`models.py:494`): `ABERTO`, `CONCLUIDO`, `CANCELADO`.
`PedidoVenda.status` relevante aqui: `PENDENTE`, `PRE_VENDA`, `CONSOLIDADO`,
`FATURADO`, `CANCELADO`, `AGRUPADO`, `APROVADO`.

## 2. Regras de negócio (importante)

- **Só entram pedidos em `PRE_VENDA`.** Tanto na criação (`criar_consolidacao`,
  `consolidacoes.py:273`) quanto na adição posterior (`adicionar_pedido_consolidacao`,
  `consolidacoes.py:477`), o pedido precisa estar `PRE_VENDA` e **não** pode já
  pertencer a outra consolidação (`consolidacao_id is None`).
- **Cliente titular = 1º pedido selecionado.** O cliente da consolidação é o do
  primeiro pedido da lista; pedidos de outros clientes são permitidos (ex.: filiais
  no CNPJ da matriz), mas o faturamento/cobrança sai em nome do titular, com aviso.
- **Consolidação ABERTA é editável:** dá para adicionar ou remover pedidos.
  Ao remover, o pedido **volta para `PRE_VENDA`**; se a consolidação fica vazia,
  ela é **excluída** (`remover_pedido_consolidacao`, `consolidacoes.py:492`).
- **`CONCLUIDO` é imutável** quanto aos pedidos de origem. O faturamento ocorre
  pela consolidação — um pedido que já pertence a uma consolidação **não pode**
  ser faturado diretamente (`pedidos.py:488` e `:547`).
- **Cancelamento** libera os pedidos (voltam a `PRE_VENDA`), estorna as contas a
  receber e cancela fiscalmente a NFSe (Betha) e as NFes (SEFAZ) vinculadas; a
  falha no cancelamento fiscal **aborta** o cancelamento da consolidação.

## 3. Telas (templates)

| Template | Função |
|----------|--------|
| `consolidacoes/listar.html` | Lista, busca, filtros e paginação de consolidações |
| `consolidacoes/nova.html` | Seleção de pré-pedidos por cliente/período + dados da consolidação |
| `consolidacoes/detalhe.html` | Itens, pedidos de origem, contas, NF, finalizar/cancelar |
| `consolidacoes/imprimir.html` / `imprimir_termica.html` | Impressão A4 / térmica 80mm |

### 3.1. Nova consolidação (`/consolidacoes/nova`)
- Filtra `PedidoVenda` com `status == PRE_VENDA` e `consolidacao_id is None`
  (`consolidacoes.py:115`), agrupando por cliente.
- Campo **Cliente** com autocomplete (busca em `/clientes/buscar`).
- Ao enviar (`POST /consolidacoes/criar`), os `pedido_ids` selecionados viram a
  consolidação; os pedidos passam a `CONSOLIDADO` e `consolidacao_id` é preenchido.

> ⚠️ **Bug corrigido (ver seção 5):** o autocomplete de cliente nesta tela tinha
> dois defeitos — não permitia clicar no resultado (erro de JS) e o dropdown
> aparecia atrás de outros elementos (`z-[100]` em vez de `z-[1000]`).

### 3.2. Detalhe (`/consolidacoes/{id}`)
- Mostra itens agregados, botão "Ver origem" (modal de rastreabilidade) e tabela
  de pedidos de origem com status e ação de **Remover** (se ABERTO).
- Se ABERTO: permite **Adicionar Pedido** (select de `PRE_VENDA` disponíveis) e
  exibe os cards **Finalizar Consolidação** e **Cancelar Consolidação**.
- Se CONCLUIDO: disponibiliza **Emitir NFe** (itens tipo produto) e **Emitir NFSe**
  (itens tipo serviço).

## 4. Endpoints (`routers/consolidacoes.py`)

| Método/Rota | Função | Linha |
|-------------|--------|-------|
| `GET /consolidacoes/` | Listar | `:28` |
| `GET /consolidacoes/nova` | Tela de seleção | `:102` |
| `POST /consolidacoes/criar` | Cria a consolidação (agrega itens) | `:243` |
| `GET /consolidacoes/pedidos-disponiveis` | API AJAX de pré-pedidos | `:532` |
| `GET /consolidacoes/{id}` | Detalhe | `:586` |
| `POST /consolidacoes/{id}/adicionar` | Adiciona `PRE_VENDA` a consolidação ABERTA | `:460` |
| `POST /consolidacoes/{id}/remover/{pid}` | Remove (volta a `PRE_VENDA` / exclui se vazia) | `:492` |
| `POST /consolidacoes/{id}/finalizar` | `CONCLUIDO` + gera `ContaReceber` (parcelada). **Não** emite boleto (ver §6) | `:661` |
| `POST /consolidacoes/{id}/cancelar` | Cancela fiscalmente e libera pedidos | `:748` |
| `GET /consolidacoes/{id}/imprimir` | Impressão A4/térmica | `:847` |

Helpers internos: `_parse_pedido_ids` (`:159`), `_gerar_numero_consolidacao`
(`:214`, garante `numero` único `CONS-XXXXXX`), `_rebuild_itens_consolidacao`
(`:406`, reagrega itens ao adicionar/remover).

## 5. Tornar pedido "pendente" em pré-venda (passo anterior à consolidação)

Como só `PRE_VENDA` entra na consolidação, um pedido em `PENDENTE` precisa ser
promovido antes. Isso já é suportado pelo endpoint genérico de status:

```
POST /pedidos/{id}/status   (form: status=pre_venda)
```

Implementado em `pedidos.py:469` (`atualizar_status`). Ele **bloqueia apenas**
pedidos `FATURADO` e o faturamento direto de pedido já em consolidação — ou seja,
`PENDENTE → PRE_VENDA` é permitido sem novo código no backend.

A UI disponibiliza o botão **"Tornar Pré-venda"** na coluna **Ações** de
`pedidos/listar.html`, visível somente quando `p.status == 'pendente'`. Após o
clique, o pedido passa a aparecer na tela de nova consolidação.

## 6. Geração de NF e cobrança

- **Finalizar** (`finalizar_consolidacao`, `consolidacoes.py`) gera a
  `ContaReceber` (parcelada) via `services.parcelamento.gerar_contas_receber`,
  mas **não emite boleto**. A emissão do boleto foi deliberadamente desacoplada
  do finalizar para evitar cobrar antes de existir documento fiscal.
- **Boleto é emitido somente após gerar as notas.** A rota
  `emitir_consolidacao_nfse` (`POST /nfse/emitir/consolidacao/{id}`,
  `nfse.py`) salva os rascunhos de NFe/NFSe e, **depois**, se a consolidação
  tiver `gerar_boleto=True` ou `forma_pagamento == "boleto"`, emite os boletos
  Sicoob das parcelas (via `emitir_boletos_contas`). Assim o boleto nasce
  vinculado às NFs já geradas, usando o `numero` da consolidação e o vencimento
  informado no finalizar.
- **NF da consolidação não gera cobrança própria** — a cobrança vem das contas a
  receber criadas no finalizar. Ver `DOCUMENTACAO_NFE.md` e
  `DOCUMENTACAO_BOLETOS.md`.
- Rotas de emissão: `GET /nfe/emitir/consolidacao/{id}`,
  `GET /nfse/emitir/consolidacao/{id}`.

### 6.1. Correções aplicadas (commit `91513b6`)

- **Boleto só após as NFs:** removida a emissão imediata em
  `finalizar_consolidacao`; boleto passou para `emitir_consolidacao_nfse`, após
  salvar os rascunhos NFe/NFSe.
- **Valor da NFSe corrigido:** `valor_servicos` em `nfse.py` deixou de multiplicar
  `item.total * item.quantidade` (contagem em dobro, pois `item.total` já é
  `quantidade × preço_unitario` agregado) e passou a somar `item.total`. O
  cabeçalho da NFSe agora bate com o total dos serviços.
- **Cliente visível no rascunho NFSe:** o formulário de edição
  (`templates/nfse/editar.html`) pré-preenche a caixa de busca `#clienteSearch`
  com o nome do cliente já vinculado (antes só o `hidden #clienteId` vinha
  preenchido, e o campo parecia vazio).

## 7. Notas de implementação / pendências

- `PedidoConsolidado.finalizado_por` existe no modelo (`models.py:581`) mas **não
  é preenchido** (TODO em `consolidacoes.py:693`). Adicionar auditoria de quem
  finalizou.
- Dois mecanismos de "agrupar" coexistem: a consolidação de fato
  (`consolidacoes.py`) e o "agrupar pré-venda" legado (`pedidos.py:111` →
  `finalizar_grupo`), que gera um pedido `FATURADO` e marca os originais como
  `AGRUPADO`. São fluxos distintos; prefira a consolidação para novo desenvolvimento.

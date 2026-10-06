# Classificação Contábil Automática

Documentação da mudança que eliminou a edição manual da conta a receber para
ligá-la ao plano de contas.

**Problema original:** toda conta a receber gerada automaticamente (NF-e,
NFS-e, OS, pedido, consolidação, assinatura, importação de boleto) nascia com
`plano_conta_id = NULL`. O usuário tinha que abrir cada conta e escolher a
conta na mão. O DRE jogava quase tudo na linha sintética "Sem Classificação".

---

## O conceito

A conta de receita mora no **cadastro do produto/serviço**, não no título
financeiro.

```
Produto/Serviço  ──conta_receita──▶  Item do pedido  ──▶  Item da NF  ──▶  ContaReceber
   produtos.conta_receita_id                                             plano_conta_id
```

Por que na origem e não no título:

- O mesmo produto é vendido por vários tipos de documento (NF-e, NFS-e, OS,
  pedido). Definir no título obrigaria a repetir a conta em todo caminho.
- Uma única nota pode **misturar** itens com contas diferentes. A
  `ContaReceber` atual é 1:N por valor total (não há rateio por linha), então
  classificar no título seria insuficiente por construção.

> Produto e Serviço são linhas da **mesma** tabela `produtos`, discriminadas por
> `tipo` (`models.py:439`). Não existe modelo `Servico` separado — por isso um
> único campo `conta_receita_id` cobre os dois.

---

## A cascata de resolução

Implementada em `services/classificacao_contabil.py`. Primeira que resolver
vence:

| Ordem | Origem | Quando |
|---|---|---|
| 1 | `plano_conta_id` explícito | telas manuais de `/contas/receber` |
| 2 | **Itens do documento** | NF-e / NFS-e / pedido / OS, quando **todos** os itens apontam para a mesma conta |
| 3 | `empresa.conta_receita_padrao_id` | fallback final (nota mista, origem sem itens, boleto importado) |

Sem resultado, devolve `(None, True)` — **nunca levanta exceção**. A
classificação contábil não pode derrubar o faturamento.

### Nota mista

Se os itens divergirem entre si, a resolução vai para o fallback e a conta é
marcada com `classificacao_revisar = True`. Escolher a conta majoritária
distorceria o DRE, então preferimos um valor correto com sinalização a um
valor silenciosamente errado.

### Conta inativa não é usada

O DRE não filtra por `ativo` (decisão deliberada, ver abaixo). Mas a resolução
**recusa** conta inativa e cai no fallback: classificar em conta desativada
esconderia o valor de todo jeito, e a tela de seleção também não a mostraria.

---

## Onde a resolução é chamada

**Um único ponto:** dentro de `gerar_contas_receber`
(`services/parcelamento.py:113`).

```python
if not plano_conta_id:
    plano_conta_id, revisar = resolver_conta_receita(
        db, nfe_id=nfe_id, nfse_id=nfse_id, pedido_id=pedido_id, os_id=os_id,
    )
```

Não foi preciso editar os 14 call sites. `ContaReceber` já carregava as FKs de
origem (`nfe_id`, `nfse_id`, `pedido_id`, `os_id`), então a resolução usa
`SELECT` sobre as tabelas de itens — em SQLAlchemy **Core**, não ORM, porque os
mappers de `models_nfe` referenciam `PedidoVenda`/`PedidoConsolidado` (que vivem
em `models.py`) e quebram se `models_nfe` for importado isoladamente.

Cobertura: 14 fluxos de faturamento corrigidos de uma vez, e nenhum fluxo novo
vai esquecer a conta.

---

## Onde a classificação é exigida

| Local | Comportamento |
|---|---|
| **Cadastro de produto/serviço** | Conta de receita **obrigatória** (`required` no HTML + validação em `routers/produtos.py`). |
| **Emissão de NF-e / NFS-e** | **Nunca bloqueia.** A cascata sempre resolve algo (há conta padrão). Se cair no fallback, marca para revisão. |

### Por que não bloquear a emissão

Emissão fiscal tem prazo legal e o documento é regulado pelo fisco. Acoplar
isso a um campo de configuração contábil cria uma armadilha: alguém cadastra um
produto novo sem conta → a empresa inteira para de emitir nota.

O bloqueio fica no **cadastro**, que é onde o problema nasce, é mais barato e
nunca interfere no faturamento. Esse é o padrão em TOTVS/Protheus, Omie e
NetSuite.

Se mesmo assim for desejado o bloqueio duro na emissão, é uma linha em
`routers/nfe.py` / `routers/nfse.py` — avise que eu adiciono.

---

## Dados

| Tabela | Campo |
|---|---|
| `produtos` | `conta_receita_id` → `plano_contas.id` |
| `empresa` | `conta_receita_padrao_id` → `plano_contas.id` (fallback) |
| `contas_receber` | `classificacao_revisar` (bool, indica fallback) |

Migration: `alembic/versions/c9d0e1f2a3b4_conta_receita_produto.py`
(idempotente; o app também cria colunas faltantes no startup).

O `plano_contas` **não** é seedado à força: o seed só age com a tabela vazia
(instalação nova). Em base já migrada, o plano do usuário é preservado
integralmente. O mesmo vale para `empresa.conta_receita_padrao_id`, que só é
preenchido se estiver vazio.

Startup: `services/seed_classificacao.py`, chamado de
`app/core/lifespan.py`. Faz seed mínimo → configura conta padrão → backfill.
Idempotente.

---

## Classificação retroativa

### Produtos (script, rode uma vez)

```bash
python scripts/classificar_produtos_receita.py          # so os vazios
python scripts/classificar_produtos_receita.py --forcar # reescreve todos
```

Heurística: `tipo == 'servico'` → 3.2; produto/kit com indício de serviço no
nome (manutenção, instalação, visita técnica, mensalidade, locação, …) → 3.2;
demais → 3.1.

> **A heurística precisa de revisão humana.** Produtos marcados como
> `tipo=produto` mas cujo nome indica serviço foram para 3.2. Confira o cadastro
> — a partir daí a propagação é automática.

### Contas a receber (automático)

`seed_classificacao_contabil` roda no startup e classifica as contas com
`plano_conta_id IS NULL` pela origem. Boletos importados do Sicoob e mensalidades
de assinatura não têm documento de origem: vão para a conta padrão e ficam
marcados para revisão.

Resultado desta base: 300 contas reclassificadas, 0 sem conta.

---

## Interface

**`/contas/receber`**
- Coluna **Conta** com o código e o nome
- Selo `revisar` quando veio do fallback; selo `sem conta` quando falta
- Filtro **Conta**: `Sem conta (N)`, `Para revisar (N)`, ou uma conta específica
- **Classificar selecionadas**: usa o `conta_ids` que a tabela já produzia (o
  select-all existia sem nenhuma ação consumindo)

**`/produtos` (cadastro)**
- Select **Conta de Receita**, obrigatório, sugerido pela conta padrão da
  empresa; sem plano de contas mostra aviso com link para `/plano-contas`

Classificar manualmente (pelo ícone de lápis) limpa a flag de revisão.

---

## Correções no DRE

| Antes | Agora |
|---|---|
| `SUM(valor)` — juros e desconto saíam do resultado | `COALESCE(SUM(valor_total), SUM(valor))` |
| Filtro `ativo == True` — desativar uma conta **apagava** seu histórico | Sem filtro; conta inativa continua no relatório |
| Linha plana, sem hierarquia | Rollup dos grupos + indentação por `nivel` |
| Total somava todas as linhas — **grupo e filho contavam em dobro** | Soma só as raízes + a linha avulsa |

Estrutura de linha: `codigo, nome, nivel, parent_id, valor, filhos`. `filhos`
distingue grupo de folha (o template usa para negrito/indentação).

> Desativar uma conta é para **impedir seleção futura**, não esconder números
> já lançados. Por isso o DRE não filtra por `ativo`.

**Base de caixa, não competência:** só entra o que foi efetivamente recebido
(`status == PAGO` + `data_recebimento`/`data_pagamento`). É o comportamento
histórico do sistema e **não** foi alterado.

---

## Fora do escopo (deliberado)

- **Partida dobrada / lançamentos / razão** — não existem no sistema. `DOCUMENTACAO.md:464` já lista "razão contábil" como lacuna. É projeto de semanas (tabelas `lancamentos`, `periodos`, contas correntes).
- **Competência (regime de competência)** — o DRE é caixa.
- **Centro de custo** — o conceito não existe no codebase.
- **`PlanoDeContas.tipo` é `String(10)`** com só `receita`/`despesa`. Não comporta `ativo`/`passivo`/`patrimônio`. Não atrapalha o DRE atual (só usa receita e despesa), mas trava contabilidade real.
- **Bug de receita 2x** em pedido agrupado + original (`DOCUMENTACAO_PEDIDOS.md:131`) — pré-existente, não relacionado. Vai contaminar o DRE agora que os valores estão classificados. Tratar à parte.

---

## Scripts

| Script | Para quê |
|---|---|
| `scripts/classificar_produtos_receita.py` | classifica produtos por tipo/nome (`--forcar` reescreve) |
| `scripts/validar_classificacao_app.py` | 21 verificações end-to-end com o app real (não destrutivo) |
| `scripts/restaurar_senha_usuario.py` | restaura hash de senha de um usuário a partir do backup |

### Rodar as verificações

```bash
python -m pytest tests/test_classificacao_contabil.py   # 23 testes
python scripts/validar_classificacao_app.py             # end-to-end
```

O validador cria um usuário descartável próprio
(`validador_classificacao@local`) e **nunca** redefine a senha de um usuário
real. Antes ele fazia isso — e quebrou o login do administrador. Não repita o
erro: **nunca** reescreva a senha de um usuário existente em script de teste.
# Motor Contábil — Estado Atual e Roteiro

Documento de transição. Registra **o que existe hoje** (verificado no banco),
**os bloqueios concretos** para uma contabilidade de verdade, e a ordem
sugerida de implementação.

Não é um plano de código: é o mapa que torna o próximo passo mecânico.

Documentos relacionados:
- [DOCUMENTACAO_CLASSIFICACAO_CONTABIL.md](DOCUMENTACAO_CLASSIFICACAO_CONTABIL.md) — classificação automática (feito)
- [DOCUMENTACAO_PEDIDOS.md](DOCUMENTACAO_PEDIDOS.md) — receita duplicada (corrigido)

Para reexecutar o inventário a qualquer momento:
```bash
python scripts/inventario_motor_contabil.py
```

---

## 1. Onde estamos

O sistema **não tem contabilidade**. Tem contas a receber/pagar com um campo
de classificação. Confirmado no banco: **nenhuma** tabela contábil existe
(`lancamentos`, `partidas`, `periodos`, `contas_correntes`, `centro_custo`,
`saldo` — zero de 39 tabelas).

O que existe e já funciona:

| Entregue | Estado |
|---|---|
| `plano_contas` hierárquico (13 contas, 2 níveis) | ✅ |
| Classificação automática por origem do documento | ✅ 589/589 contas a receber |
| Conta de receita no cadastro de produto | ✅ 160/160 produtos |
| DRE com rollup e subtotais | ✅ base de caixa |
| Receita duplicada (pedido agrupado) | ✅ corrigida e testada |
| Partida dobrada / lançamentos | ❌ não existe |
| Razão / balancete / balanço | ❌ não existe |
| Regime de competência | ❌ não existe (é caixa) |
| Centro de custo | ❌ conceito inexistente |
| Extrato por cliente/fornecedor | ❌ não existe |

---

## 2. Cinco bloqueios concretos

Verificados no código e no banco. Nenhum é cosmeticamente difícil.

### B1 — `PlanoDeContas.tipo` não comporta contas patrimoniais

```python
tipo = Column(String(10), nullable=False)   # models.py — só "receita" | "despesa"
```

`String(10)` aceita `patrimonio` (10 chars) e `passivo_patrim`, mas o formulário
e os filtros de toda a aplicação assumem binário. Ver em uso hoje:

```
despesa: 8    receita: 5
```

Uma contabilidade real precisa de pelo menos: `ativo`, `passivo`, `patrimonio`,
`receita`, `despesa`. **Decisão de projeto pendente:** enum nativo (como
`StatusConta`, exige `ALTER TYPE`) ou `String(20)` com `CHECK`. O padrão do
projeto é enum nativo; seguir isso evita uma segunda migração depois.

Enquanto isso não existir, `1.1.2 Clientes a Receber` não pode ser representada
— e essa é exatamente a conta que o jogo de partidas dobradas exige.

### B2 — `valor_total` inconsistente: 94% dos recebimentos não preenchem

O campo `valor_total` (`= valor + juros - desconto`) só é calculado no
endpoint `baixar`. Existem **quatro** pontos que marcam `PAGO` sem preenchê-lo:

| Local | Caminho |
|---|---|
| `routers/sicoob.py:929` | sync de pagamentos |
| `routers/sicoob.py:973` | webhook de liquidação |
| `routers/sicoob.py:1239` | importação de boleto (conta nasce PAGO) |
| `routers/assinaturas.py:482` | ciclo externo marcado à mão |

Medição real:

| Métrica | Valor |
|---|---|
| Contas PAGO | 341 |
| PAGO **com** `valor_total` | 30 (soma R$ 4.744,17) |
| PAGO **sem** `valor_total` | 311 (soma R$ 75.530,68 em `valor`) |
| Das 311 sem, com boleto Sicoob | 294 |

Ou seja: **94% da receita recebida nunca passou por `valor_total`.** Por isso o
DRE usa `COALESCE(valor_total, valor)`.

Consequência para o motor: **não existe hoje um valor liquidado canônico.**
Um razão contábil que some `valor_total` perde 94% da receita; que some
`valor` perde juros e desconto. Precisa de uma função única de
"valor liquidado" e de backfill dos 311 registros.

### B3 — `ContaReceber` não tem rateio por linha

`ContaReceber` é 1:N por **valor total**: um título, N parcelas. Não guarda
rateio por item.

Consequência: **nota mista não pode ser escriturada corretamente.** Uma NF-e
com R$ 300 de produtos (3.1) e R$ 200 de serviço (3.2) gera **uma** conta a
receber de R$ 500. Hoje a classificação resolve por unanimidade e cai no
fallback quando diverge — mas quando o lançamento contábil existir, os R$ 300 e
os R$ 200 precisam virar lançamentos distintos, e não há onde guardar isso.

O modelo atual já tem a informação: `nfe_itens` / `nfse_itens` /
`pedidos_venda_itens` com `produto_id` e `conta_receita_id`. Falta (a) decidir o
descritivo (lançamento por item? por documento?), (b) modelar.

### B4 — Risco de dupla contagem na transição

Hoje o DRE lê **de `contas_receber`**:

```sql
SUM(COALESCE(valor_total, valor)) WHERE status = PAGO AND plano_conta_id = X
```

No momento em que os lançamentos passarem a existir, they'll registrar a mesma
receita. Se o DRE continuar lendo `contas_receber`, **cada real é contado duas
vezes**.

Mitigação obrigatória: o DRE deve passar a ler o razão como fonte única, com um
interruptor explícito e período de verificação lado a lado antes de apagar a
leitura antiga. Esta é a parte mais perigosa do projeto.

### B5 — Sem competência e `StatusConta` incompleto para contabilidade

- Não existe `periodos` (competência aberta/fechada). Um razão sem fechamento de exercício permite editar lançamentos de período fechado.
- `StatusConta` = `PENDENTE, PAGO, VENCIDO, CANCELADO, BAIXA_SOLICITADA, EXCLUIDO`. Não há estado de **estorno** nem **conciliado**, que o jogo de partidas dobradas exige (estorno é o mecanismo de reversão padrão).
- O DRE é **caixa**: recognition em `data_recebimento`/`data_pagamento`. Competência reordena tudo.

---

## 3. Schema proposto

Nomes e colunas Hunting; ajustar antes de escrever a migration.

```
lancamentos
  id, data (DATE, competência), numero (sequencial por ano),
  descricao, documento_tipo, documento_id,  -- polimórfico: 'nfe'|'nfse'|'os'|'manual'
  status: 'lancado'|'estornado',
  estorno_de_id -> lancamentos.id,             -- reversão, nunca exclusão
  usuario_id, created_at

partidas                             -- 2+ linhas por lançamento (partida dobrada)
  id, lancamento_id, conta_id (FK plano_contas),
  tipo: 'D'|'C', valor NUMERIC(14,2),
  centro_custo_id (NULL no começo), cliente_id / fornecedor_id (NULL)
  -- invariante: SUM(D) == SUM(C) por lançamento, validado no serviço

periodos
  id, competencia (YYYY-MM), data_inicio, data_fim,
  status: 'aberto'|'fechado', fechado_em, fechado_por

contas_correntes                      -- subledger por cliente/fornecedor
  cliente_id ou fornecedor_id, plano_conta_id (controle),
  -- concilia com 1.1.2 Clientes a Receber
```

Decisões de modelagem que valem a pena tomar agora:

- **Estorno por referência, nunca UPDATE destrutivo.** `estorno_de_id` preserva a trilha. Contabilidade não aceita apagar.
- **Partidas em tabela separada, não colunas `debito_id`/`credito_id`.** Permite N-sided (rateio por item do B3) sem mudar o schema.
- **Conta corrente é uma visão sobre `contas_receber`**, não uma cópia. Senão duplica a verdade.
- **Toda escrita em razão passa por um serviço único** que valida a soma. Nunca `db.add()` de lançamento espalhado pelas rotas.

---

## 4. Ordem sugerida

Cada fase é entregável e verificável isoladamente.

| # | Fase | Entrega | Risco |
|---|---|---|---|
| 0 | Decidir B1, B2, B3, B5 | ADRs curtos em `docs/adr/` | baixo |
| 1 | Ampliar `PlanoDeContas.tipo` + `01`/`02`/`1.1.2` | contas patrimoniais e de controle | baixo |
| 2 | `valor_liquidado()` único + backfill dos 311 | base de caixa consolidada | **médio** |
| 3 | `lancamentos` + `partidas` + serviço de escrita | *(nenhum relatório muda)* | **alto** |
| 4 | Fazer NF-e/NFS-e/lowercase gerarem lançamentos no ato | receita passa a ter Partida D/C | **alto** |
| 5 | Baixa gera lançamento caixa, com estorno | fecha o ciclo caixa↔razão | alto |
| 6 | `periodos` + fechamento | congelar exercício | médio |
| 7 | Migrar o DRE para o razão, lado a lado | fim do risco B4 | **crítico** |
| 8 | Razão, balancete, extrato, CMV | relatórios | médio |

O ponto de retorno depois da fase 6 é o mais confortável. **A fase 7 não pode ser feita com pressa** — é onde nasce a dupla contagem.

---

## 5. Decisões que dependem do negócio

Nenhuma destas é técnica; todas mudam o desenho.

1. **Escopo da contabilidade.** Só resultado (DRE) ou também patrimônio (balanço)? Se for só resultado, o motor pode ser bem mais simples e B1 fica menor.
2. **Regime.** Competência (obrigatório para fechar com contador, ECF/ECD) ou caixa (mais simples, insuficiente para quem assina)? Se há contador, é competência.
3. **Rateio por item.** Uma NF-e de R$ 500 entre produtos e serviços vira 2 lançamentos ou 1? Isso define se `partidas` aceita N linhas por conta.
4. **Conta deISS/IR.** separate ou somado na receita?
5. **Custo.** CMV por nota (peças/serviços) ou custo mensal simplificado? Se só receita e despesa fixa, dá para pular CMV.

---

## 6. Verificação de cada fase

- Fases 0–2: `scripts/inventario_motor_contabil.py` mostra as métricas mudando.
- Fase 3: property test do serviço — `SUM(D) == SUM(C)` para lançamento aleatório.
- Fase 4–5: **soma do razão == soma do DRE antigo**, para o mesmo período. É a prova de que nada se perdeu nem duplicou.
- Fase 7: manter os dois relatórios e comparar linha a linha até divergirem só por arredondamento.
- Suite existente (153 testes) é contrato: nenhuma fase pode quebrá-la.
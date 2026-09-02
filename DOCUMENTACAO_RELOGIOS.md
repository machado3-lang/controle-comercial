# Documentação — Módulo Equipamentos Vendidos

Módulo **standalone** (item de menu: **Equip. Vendidos**) para cadastro e consulta de
relógios de ponto (REPs), catracas e controladores de acesso vendidos. Substitui a
planilha manual de controle, mantendo os dados no banco do sistema e permitindo
vínculos **opcionais** com cadastros já existentes (Clientes, Itens/Produtos e
Fornecedores).

> URL base: `/relogios-ponto`. O nome interno do modelo permanece `RelogioPonto`
> (tabela `relogios_ponto`) por compatibilidade, mas a UI usa "Equipamentos Vendidos".

---

## 1. Objetivo

- Registrar novos equipamentos vendidos (data, cliente, modelo, serial, valor etc.).
- Consultar quando o cliente comprou e a que valor.
- Apurar quantidades vendidas por modelo, marca, fornecedor e cliente.
- Controlar a emissão do atestado técnico por aparelho.
- Referenciar a Nota Fiscal (NF-e/NFSe), Ordem de Serviço ou Pedido de origem.
- (Futuro) importar a planilha original — por ora o preenchimento é manual.

---

## 2. Decisões de design

1. **Sem importação inicial** — os dados são lançados manualmente via tela,
   eliminando a necessidade de casar texto livre (nome/cliente) com IDs.
2. **Vínculos opcionais** — `cliente_id`, `produto_id` (modelo) e `fornecedor_id`
   são `ForeignKey` com `ondelete="SET NULL"`. Clientes/Fornecedores no sistema
   não são excluídos (apenas inativados), e mesmo que um vínculo desapareça o
   histórico de vendas permanece íntegro.
3. **Colunas de cache** — `cliente_nome_cache`, `cpf_cnpj_cache`, `contato_cache`,
   `modelo_cache`, `marca_cache` e `fornecedor_nome_cache` são preenchidas a
   partir dos cadastros no momento do salvamento. Exibição e relatórios não
   dependem de joins; o valor histórico é preservado mesmo se o cadastro mudar.
4. **Marca vem do Produto** — não há campo próprio de marca: o valor é copiado, ao
   vincular o modelo, de `Produto.marca_rel.nome` (marca via `marca_id`); se não
   houver relação, cai no texto livre `Produto.marca`.
5. **Sem categoria própria** — o Produto já carrega sua categoria; puxa-se de lá.
6. **Número de série único** — `numero_serial` tem restrição única para evitar
   duplicidade do mesmo aparelho.
7. **Referência de documento livre** — `documento_referencia` é um texto livre para
   informar o Nº da Nota (NF-e/NFSe), OS ou Pedido. Fica no módulo (sem FK
   obrigatória) para honrar o requisito original de não vincular a nada do sistema.
8. **Parser de valor robusto** — o valor aceita formato brasileiro (`1.500,00`,
   `R$ 1.500,00`, `1500,00`) via `_parse_valor_br`.
9. **Ordem de rotas** — `/relatorio` é registrada **antes** de `/{relogio_id}` para
   não ser capturada como id.

---

## 3. Modelo de dados — `RelogioPonto` (`models_relogios.py`)

Tabela: `relogios_ponto`

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | PK | Identificador |
| `cliente_id` | FK→`clientes.id` (SET NULL) | Cliente vinculado (planilha: coluna C) |
| `produto_id` | FK→`produtos.id` (SET NULL) | Modelo/Item vinculado (planilha: coluna D) |
| `fornecedor_id` | FK→`fornecedores.id` (SET NULL) | Fornecedor/Revenda |
| `usuario_id` | FK→`usuarios.id` (SET NULL) | Quem registrou (auditoria) |
| `cliente_nome_cache` | String | Cache do nome do cliente |
| `cpf_cnpj_cache` | String | Cache do CPF/CNPJ do cliente |
| `contato_cache` | String | Cache do contato do cliente |
| `modelo_cache` | String | Cache do nome do modelo/produto |
| `marca_cache` | String | Cache da marca (do produto) |
| `fornecedor_nome_cache` | String | Cache do nome do fornecedor |
| `data_venda` | Date (índice) | Data da venda (planilha: coluna B) |
| `numero_serial` | String **único** (índice) | Nº de série (planilha: coluna E) |
| `documento_referencia` | String (índice) | Nº da Nota / OS / Pedido (livre) |
| `valor` | Numeric(12,2) | Valor da venda (planilha: coluna H) |
| `atestado_tecnico` | Boolean | Atestado técnico emitido (planilha: coluna A "X") |
| `observacao` | Text | Observação (planilha: coluna I) |
| `observacao2` | Text | Observação 2 (planilha: coluna J) |
| `created_at` / `updated_at` | DateTime | Timestamps |

### Mapeamento da planilha original → campos

| Planilha | Campo | Tratamento |
|---|---|---|
| A (X) | `atestado_tecnico` | X → `True` |
| B | `data_venda` | Data |
| C | `cliente_id` + `cliente_nome_cache` | Vincula Cliente; cache do nome |
| D | `produto_id` + `modelo_cache` | Vincula Item; cache do nome |
| E | `numero_serial` | Único |
| F | `cpf_cnpj_cache` | Copiado do Cliente vinculado |
| G | `contato_cache` | Copiado do Cliente vinculado |
| H | `valor` | Parser BR |
| I / J | `observacao` / `observacao2` | Texto livre |
| (nova) | `marca_cache` | Vem do Produto (marca_id) |
| (nova) | `fornecedor_id` + `fornecedor_nome_cache` | Vincula Fornecedor |
| (nova) | `documento_referencia` | Nº Nota/OS/Pedido livre |

---

## 4. Rotas — `routers/relogios_ponto.py`

| Método | Rota | Ação |
|---|---|---|
| GET | `/relogios-ponto/` | Listagem com filtros (busca, marca, fornecedor, atestado, período) e paginação |
| GET | `/relogios-ponto/novo` | Formulário de cadastro |
| POST | `/relogios-ponto/novo` | Cria registro (preenche caches) |
| GET | `/relogios-ponto/relatorio` | Relatório agregado (registrada antes de `/{id}`) |
| GET | `/relogios-ponto/{id}` | Detalhe do registro |
| GET | `/relogios-ponto/{id}/editar` | Formulário de edição |
| POST | `/relogios-ponto/{id}/editar` | Atualiza registro (repreenche caches) |
| POST | `/relogios-ponto/{id}/excluir` | Exclusão (exige senha; grava auditoria) |

Filtros da listagem/relatório são reutilizados pela função auxiliar `_aplicar_filtros`.
Formatação de valor usa o filtro `format_reais` (já existente no app).

### Funções auxiliares

- **`_preencher_cache(db, r, cliente_id, produto_id, fornecedor_id)`** — copia nome,
  CPF/CNPJ, contato, modelo e marca (de `marca_rel.nome` com fallback para o texto
  livre) dos cadastros vinculados para as colunas de cache.
- **`_parse_valor_br(valor)`** — converte texto em `float` aceitando formato BR.
  Remove `R$`, trata separador de milhar/decimal (o último separador presente é o
  decimal) e devolve `None` se não conseguir interpretar.
- **`_aplicar_filtros(query, busca, marca, fornecedor_id, atestado, data_ini, data_fim)`**
  — aplica os filtros de busca/período/atestado; `fornecedor_id` aqui já é `int` ou `None`.

---

## 5. Templates — `templates/relogios_ponto/`

- `listar.html` — tabela de registros (Data, Cliente, Modelo, Marca, Serial,
  Fornecedor, Doc. Ref., Valor, Atestado, Ações) + formulário de filtros + botões
  **Novo** e **Relatório**. A busca de fornecedor no filtro usa o mesmo padrão de
  dropdown dinâmico das contas.
- `form.html` — formulário único de novo/edição. Cliente, Modelo (Item) e
  Fornecedor usam busca dinâmica (`/clientes/buscar`, `/produtos/buscar`,
  `/fornecedores/buscar`), igual ao padrão das contas. Campos: data, nº de série,
  nº nota/OS/pedido, cliente, modelo, fornecedor, valor, atestado técnico,
  observação e observação 2.
- `detalhe.html` — visualização completa com links para Cliente/Produto/Fornecedor
  (quando vinculados) e botões Editar/Excluir.
- `relatorio.html` — cards de totais (quantidade, valor total, atestados emitidos)
  e tabelas agrupadas por Modelo, Marca, Fornecedor e Cliente.

---

## 6. Menu

Item **"Equip. Vendidos"** na navegação superior (`templates/base.html`), nas versões
desktop e mobile, apontando para `/relogios-ponto`.

---

## 7. Registro no app — `app/core/lifespan.py`

- `import models_relogios` — registra o modelo no `Base.metadata` (a auto-migração
  cria a tabela em `run_migrations` → `Base.metadata.create_all`).
- `include_router(relogios_ponto.router)` na lista de routers.

Como a tabela é nova, a criação é automática no próximo start — **não é necessário**
rodar Alembic manualmente. Colunas novas em tabelas já existentes também são
adicionadas automaticamente por `_add_missing_columns()` na inicialização.

---

## 8. Como usar

1. Subir a aplicação (a tabela `relogios_ponto` é criada automaticamente).
2. Menu → **Equip. Vendidos** → **Novo**.
3. Informar data, selecionar Cliente / Modelo (Item) / Fornecedor (opcional),
   número de série, nº da Nota/OS/Pedido (opcional) e valor; marcar atestado
   técnico se emitido.
4. Salvar. O sistema preenche os caches a partir dos cadastros vinculados.
5. Para consultas/relatórios: usar os filtros da listagem ou o **Relatório**.

> Registros criados antes de ajustes pontuais (ex.: cache de marca) podem precisar
> de **editar/salvar** para repovoar os caches.

---

## 9. Segurança e auditoria

- Todas as rotas passam pelo middleware de autenticação/sessão do app.
- Exclusão exige confirmação de senha (`confirma_senha_usuario`) e registra
  auditoria (`registrar_auditoria`).
- CSRF: formulários incluem `csrf_token`; exclusão usa o modal padrão do sistema.

---

## 10. Histórico de correções

- **Valor não salvava** — o parser original só aceitava números simples; valores
  formatados BR (`1.500,00`, `R$ 1.500,00`) falhavam silenciosamente. Substituído
  por `_parse_valor_br`.
- **Erro de filtro `fornecedor_id`** — parâmetro tipado como `int` quebrava com
  string vazia. Alterado para `str` + conversão segura para `int` (`fid`).
- **Erro ao abrir Relatório** (`int_parsing` em `relogio_id`) — `/{relogio_id}`
  capturava "relatorio". Rota `/relatorio` movida para antes de `/{relogio_id}`.
- **Marca em branco** — vinculada de `marca_id → MarcaProduto.nome` (com fallback
  para o texto livre `Produto.marca`).

---

## 11. Próximos passos (fora do escopo atual)

- Importação da planilha original (`openpyxl` ou CSV) mapeando colunas A–J, com
  casamento opcional de cliente/modelo/fornecedor por nome/CPF.
- Vínculo real (FK) com Ordens de Serviço (manutenção) e com a Nota Fiscal da venda,
  se desejado no futuro.

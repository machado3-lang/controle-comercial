# Documentação — Módulo Equipamentos Vendidos

Módulo standalone (título no menu: **Equip. Vendidos**) para cadastro e consulta de
relógios de ponto (REPs), catracas e controladores de acesso vendidos. Substitui a
planilha manual, mantendo os dados no banco do sistema e permitindo vínculos
opcionais com cadastros já existentes (Clientes, Itens/Produtos e Fornecedores).

## Objetivo

- Registrar novos relógios vendidos (data, cliente, modelo, serial, valor etc.).
- Consultar quando o cliente comprou e a que valor.
- Apurar quantidades vendidas por modelo, marca, fornecedor e cliente.
- Controlar a emissão do atestado técnico por aparelho.
- (Futuro) importar a planilha original — por ora o preenchimento é manual.

## Decisões de design

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

## Modelo de dados — `RelogioPonto` (`models_relogios.py`)

Tabela: `relogios_ponto`

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | PK | Identificador |
| `cliente_id` | FK→`clientes.id` (SET NULL) | Cliente vinculado (coluna C da planilha) |
| `produto_id` | FK→`produtos.id` (SET NULL) | Modelo/Item vinculado (coluna D) |
| `fornecedor_id` | FK→`fornecedores.id` (SET NULL) | Fornecedor/Revenda (coluna nova) |
| `usuario_id` | FK→`usuarios.id` (SET NULL) | Quem registrou (auditoria) |
| `cliente_nome_cache` | String | Cache do nome do cliente |
| `cpf_cnpj_cache` | String | Cache do CPF/CNPJ do cliente |
| `contato_cache` | String | Cache do contato do cliente |
| `modelo_cache` | String | Cache do nome do modelo/produto |
| `marca_cache` | String | Cache da marca (do produto) |
| `fornecedor_nome_cache` | String | Cache do nome do fornecedor |
| `data_venda` | Date (índice) | Data da venda (coluna B) |
| `numero_serial` | String **único** | Nº de série (coluna E) |
| `valor` | Numeric(12,2) | Valor da venda (coluna H) |
| `atestado_tecnico` | Boolean | Atestado técnico emitido (coluna A "X") |
| `observacao` | Text | Observação (coluna I) |
| `observacao2` | Text | Observação 2 (coluna J) |
| `created_at` / `updated_at` | DateTime | Timestamps |

## Rotas — `routers/relogios_ponto.py`

| Método | Rota | Ação |
|---|---|---|
| GET | `/relogios-ponto/` | Listagem com filtros (busca, marca, fornecedor, atestado, período) e paginação |
| GET | `/relogios-ponto/novo` | Formulário de cadastro |
| POST | `/relogios-ponto/novo` | Cria registro (preenche caches) |
| GET | `/relogios-ponto/{id}` | Detalhe do registro |
| GET | `/relogios-ponto/{id}/editar` | Formulário de edição |
| POST | `/relogios-ponto/{id}/editar` | Atualiza registro (repreenche caches) |
| POST | `/relogios-ponto/{id}/excluir` | Exclusão (exige senha; grava auditoria) |
| GET | `/relogios-ponto/relatorio` | Relatório agregado por modelo, marca, fornecedor e cliente |

Filtros da listagem/relatório são reutilizados pela função auxiliar
`_aplicar_filtros`.

## Templates — `templates/relogios_ponto/`

- `listar.html` — tabela de registros + formulário de filtros + botões Novo/Relatório.
- `form.html` — formulário único de novo/edição. Cliente, Modelo e Fornecedor usam
  busca dinâmica (endpoints `/clientes/buscar`, `/produtos/buscar`,
  `/fornecedores/buscar`), igual ao padrão das contas.
- `detalhe.html` — visualização completa com links para cliente/produto/fornecedor.
- `relatorio.html` — totais (qtd, valor, atestados) e tabelas agrupadas.

## Menu

Item **"Relógios (REP)"** adicionado na navegação superior (`base.html`), nas
versões desktop e mobile, apontando para `/relogios-ponto`.

## Registro no app — `app/core/lifespan.py`

- `import models_relogios` para registrar o modelo no `Base.metadata` (auto-migração
  cria a tabela em `run_migrations` → `create_all`).
- `include_router(relogios_ponto.router)` na lista de routers.

Como a tabela é nova, a criação é automática no próximo start do sistema — não é
necessário rodar alembic manualmente para ambiente já existente.

## Como usar

1. Subir a aplicação (a tabela `relogios_ponto` é criada automaticamente).
2. Menu → **Relógios (REP)** → **Novo**.
3. Informar data, selecionar Cliente / Modelo (Item) / Fornecedor (opcional),
   número de série, valor e marcar atestado técnico se emitido.
4. Salvar. O sistema preenche os caches a partir dos cadastros vinculados.
5. Para consultas/relatórios: usar os filtros da listagem ou o **Relatório**.

## Notas de segurança

- Todas as rotas passam pelo middleware de autenticação/sessão do app.
- Exclusão exige confirmação de senha (`confirma_senha_usuario`) e registra
  auditoria (`registrar_auditoria`).

## Próximos passos (fora do escopo atual)

- Importação da planilha original (`openpyxl` ou CSV) mapeando colunas A–J, com
  casamento opcional de cliente/modelo/fornecedor por nome/CPF.
- Vínculo com Ordens de Serviço (manutenção futura do aparelho) e com a Nota
  Fiscal da venda.

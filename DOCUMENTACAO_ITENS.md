# Documentação — Cadastro de Itens (Produtos)

Este documento descreve o módulo de **cadastro de itens** do sistema (tela "Produtos"),
localizado em:

- Modelo: `models.py` (classe `Produto`, `ProdutoVariacao`, `ProdutoComposicao`)
- Rotas/Backend: `routers/produtos.py`
- Templates: `templates/produtos/` (`form.html`, `listar.html`, `categorias.html`, `marcas.html`, `pdf_selecionados.html`)
- Integrações: `routers/bling.py`, `routers/nfe.py`, `routers/nfse.py`

---

## 1. Visão Geral

Um "item" no sistema é representado pela entidade **`Produto`** e pode assumir três
tipos (`Produto.tipo`):

| Tipo | Descrição | Estoque | Composição (insumos) | Campos serviço |
|------|-----------|---------|----------------------|----------------|
| `produto` | Item físico com estoque | Sim (principal ou por variação) | Não | Ocultos |
| `servico` | Serviço (NFS-e) | Desabilitado (opaco) | Sim | Obrigatórios (LC 116 / NBS) |
| `kit` | Combo / Kit | Desabilitado (calculado por insumos) | Sim | Ocultos |

A tela de cadastro (`/produtos/novo` e `/produtos/{id}/editar`) usa o mesmo
template `templates/produtos/form.html`, controlado pela variável `editar`.

---

## 2. Campos do Formulário × Modelo

| Campo (label) | `name` no form | Modelo (`Produto`) | Tipo | Padrão | Observações |
|---------------|----------------|--------------------|------|--------|-------------|
| Código | `codigo` | `codigo` | String(50) único | gerado auto | Se vazio, usa `_proximo_codigo_produto()` |
| Nome * | `nome` | `nome` | String(200) | — | **Obrigatório** |
| Descrição | `descricao` | `descricao` | Text | — | |
| Marca | `marca_id` | `marca_id` / `marca` | FK | — | Salva também o nome em `marca` |
| Categoria | `categoria_id` | `categoria_id` | FK | — | |
| Tipo | `tipo` | `tipo` | String(20) | `produto` | produto / servico / kit |
| Código LC 116/03 | `codigo_lc116` | `codigo_lc116` | String(10) | — | Só serviço; **obrigatório p/ serviço** |
| Código NBS | `codigo_tributacao_municipal` | `codigo_tributacao_municipal` | String(20) | — | Só serviço |
| Fornecedor | `fornecedor_id` | `fornecedor_id` | FK | — | Busca dinâmica no form |
| NCM | `ncm` | `ncm` | String(10) | — | |
| **Unidade** | `unidade` | `unidade` | String(10) | `UN` | Unidade **comercial** (ver seção 3) |
| Origem | `origem` | `origem` | Integer | `0` | 0–8 (tabela IBPT) |
| Preço Custo | `preco_custo` | `preco_custo` | Numeric(12,2) | — | |
| Margem % | `margem` | (calculado) | — | — | Campo auxiliar (não salvo) |
| Preço Venda * | `preco` | `preco` | Numeric(12,2) | `0` | **Obrigatório** |
| Altura | `altura` | `altura` | Float | — | |
| Largura | `largura` | `largura` | Float | — | |
| Profundidade | `profundidade` | `profundidade` | Float | — | |
| **Unid. Med.** | `unidade_medida` | `unidade_medida` | String(20) | `cm` | Unidade das **dimensões** (ver seção 3) |
| Peso Líq | `peso_liq` | `peso_liq` | Float | — | |
| Peso Brut | `peso_bruto` | `peso_bruto` | Float | — | |
| Estoque Mín. | `estoque_minimo` | `estoque_minimo` | Float | `0` | |
| Estoque | `estoque` | `estoque` | Float | `0` | Desabilitado p/ serviço e kit |
| Situação | `situacao` | `situacao` | String(1) | `A` | A=Ativo / I=Inativo (toggle no form) |
| Foto | `foto` | `foto` | UploadFile | — | Salva em `static/uploads/produtos/` |

> `bling_pending_sync` é marcado como `True` a cada criação/edição para indicar
> que o item precisa ser reenviado ao Bling.

---

## 3. As Duas Unidades (importante)

O formulário possui **dois campos distintos de unidade** que NÃO são redundantes:

### 3.1. `unidade` — Unidade Comercial
- Modelo: `Produto.unidade` (String(10), padrão `"UN"`).
- É a **unidade em que o item é vendido** (ex.: UN, CX, KG, PC).
- Usada em **NFe**, **NFS-e** e **Bling** como a unidade do produto
  (`produto.unidade` em `routers/bling.py:1321`, `routers/nfe.py`, `routers/nfse.py`).
- **Implementação no form:** `<input type="text" list="unidadesComerciais">` +
  `<datalist>` com opções predefinidas (`UNIDADES_COMERCIAIS` em `routers/produtos.py:30`).
  Permite **selecionar** uma das opções padrão **ou digitar** um valor livre.
- Lista padrão (`UNIDADES_COMERCIAIS`):
  `UN, PC, PÇ, CX, Cx, KG, G, L, ML, M, M2, M3, PAR, KIT, FD, ROL, MIL, DZ`

> 💡 O "Cx" (caixa) entra **aqui**. Como é `datalist`, basta escolher "CX"/"Cx"
> ou digitar qualquer outra unidade.

### 3.2. `unidade_medida` — Unidade de Medição das Dimensões
- Modelo: `Produto.unidade_medida` (String(20), padrão `"cm"`).
- É a **unidade em que se medem altura/largura/profundidade** do produto/embalagem.
- Usada no Bling em `dimensoes.unidadeMedida` (`routers/bling.py:1339`).
- **Implementação no form:** `<select>` populado por `UNIDADES_MEDIDA`
  (`routers/produtos.py:28`).
- Lista atual (`UNIDADES_MEDIDA`): `cm, m, mm, in`
- Para **não quebrar itens já salvos** com valores antigos (`UN`, `KG`, `L`),
  o template renderiza uma opção extra `"(atual)"` quando o valor salvo não
  consta na lista.

> ⚠️ "Cx" **não pertence** a `unidade_medida` — não se mede o tamanho de uma
> caixa "em caixas". Mantenha essa lista restrita a unidades físicas de comprimento.

---

## 4. Variações (apenas tipo `produto`)

Quando o tipo é `produto`, o formulário permite adicionar **variações**
(tabela `produto_variacoes` → modelo `ProdutoVariacao`):

| Campo variação | Modelo | Descrição |
|----------------|--------|-----------|
| Nome | `nome_variacao` | Ex.: Cor, Tamanho |
| SKU | `sku` | **Único** em todo o banco (`unique=True`) |
| Preço + | `preco_adicional` | Acréscimo sobre o preço base |
| Est. At. | `estoque_atual` | Estoque da variação |
| Est. Min. | `estoque_minimo` | Estoque mínimo da variação |

Regras:
- Sempre existe (pelo menos) uma variação `"Padrão"` com SKU automático.
- Geração de SKU: `_proximo_sku_produto()` → formato `SKU-NNNNN`, pulando os já usados.
- O estoque principal do produto (`Produto.estoque`) é a **soma** das variações
  via `_recalcular_estoque_produto()` (quando há variações).
- Ao editar, as variações antigas são removidas e recriadas.

---

## 5. Composição / Insumos (tipos `kit` e `servico`)

Para `kit` e `servico`, o formulário permite adicionar **itens de composição**
(tabela `produto_composicao` → modelo `ProdutoComposicao`):

| Campo | Modelo | Descrição |
|-------|--------|-----------|
| Item da Composição | `insumo_id` | FK para outro `Produto` |
| Qtd | `quantidade_padrao` | Quantidade do insumo no kit/serviço |

Regras:
- Preço de venda do kit é **calculado automaticamente** pela soma
  `preco_insumo × quantidade` (função JS `calcularPrecoKit()` no form).
- Em serviços, o insumo pode ser baixado como `SAIDA_INSUMO` na NFS-e
  (flag `Produto.eh_insumo`).
- Ao editar, as composições antigas são removidas e recriadas.

---

## 6. Rotas (Backend)

| Método | Rota | Ação |
|--------|------|------|
| GET | `/produtos` | Listar (busca, filtros de situação/fornecedor/categoria/marca/estoque/tipo, paginação) |
| GET | `/produtos/buscar?q=` | Busca AJAX de itens (nome/código) p/ pedidos |
| GET | `/produtos/buscar-insumos?q=` | Busca AJAX de insumos (apenas tipo `produto`) |
| GET | `/produtos/proximo-sku` | Próximo SKU disponível |
| GET | `/produtos/novo` | Formulário de criação |
| POST | `/produtos/novo` | Criar produto + variações/insumos |
| GET | `/produtos/{id}/editar` | Formulário de edição |
| POST | `/produtos/{id}/editar` | Atualizar produto + variações/insumos |
| POST | `/produtos/{id}/excluir` | **Exclusão lógica** (define `situacao="I"`); exige senha |
| GET | `/produtos/categorias` | Listar categorias |
| POST | `/produtos/categorias/nova` | Criar categoria |
| GET/POST | `/produtos/categorias/editar/{id}` | Editar categoria |
| POST | `/produtos/categorias/excluir/{id}` | Excluir categoria (bloqueia se houver produtos vinculados) |
| GET | `/produtos/marcas` | Listar marcas |
| POST | `/produtos/marcas/nova` | Criar marca |
| GET/POST | `/produtos/marcas/editar/{id}` | Editar marca |
| POST | `/produtos/marcas/excluir/{id}` | Excluir marca (bloqueia se houver produtos vinculados) |
| GET | `/produtos/pdf-selecionados?ids=` | Gera PDF de itens selecionados |

> **Exclusão:** não há `DELETE` físico de produto — `excluir_produto()` apenas
> marca `situacao="I"` (inativo) e registra auditoria.

---

## 7. Integração com Bling / NFe / NFS-e

- **Bling** (`routers/bling.py`):
  - `_produto_to_api()` mapeia `unidade` → `unidade` e `unidade_medida` → `dimensoes.unidadeMedida`.
  - `_api_to_produto()` lê `unidade` e `dimensoes.unidadeMedida` ao importar.
- **NFe** (`routers/nfe.py`): utiliza `produto.unidade` como unidade do item (`p.unidade or "UN"`).
- **NFS-e** (`routers/nfse.py`): utiliza `produto.unidade` para serviços (`p.unidade or "UN"`).

Portanto, **`unidade` (comercial) é o campo que alimenta a emissão fiscal**.
`unidade_medida` afeta apenas dimensões/logística no Bling.

---

## 8. Validações e Regras de Negócio

- `nome` é obrigatório (validado no backend via `Form(...)`).
- `preco` (venda) é obrigatório.
- Serviço exige `codigo_lc116`; caso contrário, redireciona com erro.
- Para serviço/kit, o estoque principal é desabilitado no form (opaco) e, no
  caso de kit, derivado dos insumos.
- Código é único; se omitido na criação, é gerado automaticamente.
- SKU de variação é único em todo o banco.

---

## 9. Como Alterar as Listas de Unidades

- **Unidades comerciais** (`unidade`): editar a constante `UNIDADES_COMERCIAIS`
  em `routers/produtos.py`. O `datalist` no form exibe as opções; valores fora
  da lista ainda são aceitos (texto livre).
- **Unidades de dimensão** (`unidade_medida`): editar a constante
  `UNIDADES_MEDIDA` em `routers/produtos.py`. É um `<select>` fechado; itens já
  salvos com valor fora da lista continuam exibindo a opção `"(atual)"`.

Não é necessário migração de banco para alterar essas listas (são apenas
valores de texto já persistidos).

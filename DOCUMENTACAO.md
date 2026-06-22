# Sistema de Controle Comercial

Sistema web completo para gestão comercial com integração Bling ERP v3.

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python 3.14 + FastAPI |
| Frontend | Jinja2 Templates + Bootstrap 5 Dark |
| Banco | SQLite via SQLAlchemy 2.0 (develop) / PostgreSQL (production) |
| Autenticação | Bling OAuth 2.0 |
| HTTP | httpx |

## Estrutura do Projeto

```
C:\Controle de Serviços\
├── main.py                    # Inicialização, rotas, dashboard
├── database.py                # SQLAlchemy engine + session
├── models.py                  # Todas as tabelas do banco
├── requirements.txt           # Dependências Python
├── controle.db                # Banco SQLite (gerado automaticamente)
├── routers/
│   ├── clientes.py            # CRUD Clientes
│   ├── fornecedores.py        # CRUD Fornecedores
│   ├── contas.py              # Contas a Pagar/Receber
│   ├── assinaturas.py         # Assinaturas com histórico
│   ├── ordens_servico.py      # Ordens de Serviço
│   ├── configuracoes.py       # Configurações da empresa
│   ├── bling.py               # Integração Bling ERP v3
│   ├── produtos.py            # CRUD Itens (produtos/serviços/kits)
│   ├── pedidos.py             # Pedidos de Venda
│   └── sicoob.py              # Integração Sicoob API Cobrança
├── templates/
│   ├── base.html              # Layout base (navbar, flash messages)
│   ├── index.html             # Dashboard
│   ├── clientes/              # CRUD Clientes
│   ├── fornecedores/          # CRUD Fornecedores
│   ├── contas/                # Contas a Pagar/Receber
│   ├── assinaturas/           # Assinaturas
│   ├── ordens_servico/        # Ordens de Serviço
│   ├── configuracoes/         # Configurações
│   ├── bling/                 # Integração Bling
│   ├── produtos/              # CRUD Itens
│   └── pedidos/               # Pedidos de Venda
├── static/
│   ├── css/styles.css         # Estilos customizados
│   ├── js/scripts.js          # Máscaras CPF/CNPJ/CEP
│   └── uploads/               # Upload de logo
└── __pycache__/               # Cache Python
```

## Como Executar

```bash
cd "C:\Controle Comercial"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Acessar: http://localhost:8000

### Migrations

As colunas são adicionadas automaticamente via `ALTER TABLE` no startup do `main.py`. Não há migrações formais.

## Modelos de Dados

### Cliente / Fornecedor

Duas tabelas separadas (Bling usa tabela única com `tiposContato`).

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | Integer PK | Auto incremento |
| codigo | String(20) | `CLI-NNNN` / `FOR-NNNN` |
| bling_id | Integer (unique) | ID do contato no Bling |
| bling_updated_at | DateTime | Última sincronia |
| bling_pending_sync | Boolean | Pendente de enviar ao Bling |
| nome | String(200) | Nome / Razão Social |
| cpf_cnpj | String(20) | Só dígitos (formatado na view) |
| tipo_pessoa | String(10) | `fisica` / `juridica` |
| email, telefone, celular | String | Contatos |
| endereco, bairro, cidade, estado, cep | | Endereço |
| contato | String(200) | Pessoa de referência |
| observacao | Text | Observações |
| created_at, updated_at | DateTime | Timestamps |

### Assinatura

| Campo | Descrição |
|-------|-----------|
| cliente_id FK | Cliente vinculado |
| tipo | `mensalidade` / `anuidade` |
| valor | Valor cobrado do cliente |
| valor_revenda | Custo do fornecedor/revenda |
| quantidade | Número de pessoas/serviços |
| fornecedor_id FK | Fornecedor vinculado (revenda) |
| dia_vencimento | Dia do mês para cobrança |
| status | `ativa`, `inadimplente`, `cancelada`, `encerrada` |

**Lucro** = `valor - valor_revenda` (calculado na view, não armazenado).

### AssinaturaHistorico

Registra alterações em `valor`, `valor_revenda`, `quantidade`. Exclusão protegida por senha admin.

### OrdemServico

| Campo | Descrição |
|-------|-----------|
| cliente_id FK | Cliente |
| equipamento, marca, modelo, nº série | Dados do equipamento |
| defeito_relatado | Problema reportado |
| servicos_executados | O que foi feito |
| pecas_utilizadas | Peças usadas |
| valor_servico, valor_pecas, valor_total | Valores |
| data_entrada, data_saida | Datas (formato DD/MM/YYYY) |
| tecnico | Técnico responsável |
| autorizado_por | Quem autorizou |
| numero_requisicao | Nº requisição do cliente |
| status | `aberta`, `em_andamento`, `finalizada`, `cancelada` |

### Empresa (Configurações)

Dados da empresa para impressão e integração Bling:
- Dados cadastrais (razão social, CNPJ, IE, IM, endereço)
- Logo (upload JPG/PNG)
- `senha_admin` — protege exclusão de histórico
- `bling_token`, `bling_client_id`, `bling_client_secret`, `bling_refresh_token`, `bling_token_expires_at`, `bling_webhook_secret`
- `sicoob_client_id`, `sicoob_token`, `sicoob_conta_corrente`, `sicoob_beneficiario`, `sicoob_cert_path`, `sicoob_cert_key_path`, `sicoob_cert_password`

### ContaPagar / ContaReceber

| Campo | Descrição |
|-------|-----------|
| cliente_id / fornecedor_id FK | Vinculado |
| descricao | Descrição |
| valor | Valor |
| data_vencimento | Vencimento |
| data_pagamento / data_recebimento | Baixa |
| status | `pendente`, `pago`, `vencido`, `cancelado` |

### Produto (unificado - Itens)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | Integer PK | Auto incremento |
| codigo | String(50) unique | Código interno |
| nome | String(200) | Nome do item |
| descricao | Text | Descrição |
| preco | Float | Preço de venda |
| preco_custo | Float | Custo do produto |
| ncm | String(10) | NCM fiscal |
| unidade | String(10) | Unidade de medida |
| categoria_id FK | Integer | Categoria |
| fornecedor_id FK | Integer | Fornecedor |
| marca_id FK | Integer | Marca |
| marca | String(100) | Nome da marca |
| estoque | Float | Quantidade em estoque |
| estoque_minimo | Float | Estoque mínimo alerta |
| situacao | String(1) | `A` (Ativo) / `I` (Inativo) |
| tipo | String(20) | `produto`, `servico` ou `kit` |
| altura, largura, profundidade | Float | Dimensões (cm) |
| peso_liq, peso_bruto | Float | Pesos (kg) |
| foto | String(500) | Caminho da imagem |

### ProdutoVariacao (Variações)

| Campo | Descrição |
|-------|-----------|
| produto_id FK | Produto pai |
| nome_variacao | Nome da variação (ex: "Padrão", "P", "M", "G") |
| sku | String(50) unique | SKU único |
| preco_adicional | Float | Preço adicional (soma ao preço base) |
| estoque_atual | Float | Estoque da variação |
| estoque_minimo | Float | Estoque mínimo |

### ProdutoComposicao (Kits)

| Campo | Descrição |
|-------|-----------|
| produto_pai_id FK | Kit pai |
| insumo_id FK | Produto usado como insumo |
| quantidade_padrao | Float | Quantidade necessária |

### PedidoVenda / PedidoVendaItem

| Campo Pedido | Descrição |
|--------------|---------|
| cliente_id FK | Cliente |
| numero | String | Número do pedido |
| data | Date | Data do pedido |
| status | Enum | `pendente`, `aprovado`, `faturado`, `pre_venda`, `cancelado` |
| total | Float | Total do pedido |
| forma_pagamento | String | `avista`, `aprazo`, `cartao_credito`, `cartao_debito`, `boleto` |
| tipo_pedido | String | `venda` ou `pre_venda` |

| Campo Item | Descrição |
|------------|---------|
| pedido_id FK | Pedido |
| produto_id FK | Produto |
| variacao_id FK | Variação (opcional) |
| item_pai_id FK | Item pai (para kits) |
| descricao | String | Descrição |
| quantidade | Float | Quantidade |
| preco_unitario | Float | Preço unitário |
| total | Float | Total |

## Rotas da API

### Clientes (`/clientes/`)
- `GET /` — Listar (com busca)
- `GET /novo` — Formulário novo
- `POST /novo` — Criar
- `GET /{id}` — Detalhe (com contas, assinaturas, ordens)
- `GET /{id}/editar` — Formulário edição
- `POST /{id}/editar` — Atualizar
- `GET /{id}/excluir` — Excluir

### Fornecedores (`/fornecedores/`)
- Mesma estrutura de Clientes

### Contas (`/contas/`)
- `GET /pagar` — Listar contas a pagar
- `POST /pagar/novo` — Criar
- `GET /pagar/{id}/pagar` — Baixar pagamento
- `GET /pagar/{id}/excluir` — Excluir
- `GET /receber` — Listar contas a receber
- `POST /receber/novo` — Criar
- `POST /receber/{id}/receber` — Baixar recebimento (modal com edição de data)
- `GET /receber/{id}/excluir` — Excluir

### Assinaturas (`/assinaturas/`)
- `GET /` — Listar (com lucro total)
- `POST /novo` — Criar
- `GET /{id}/gerar-cobranca` — Gerar conta a receber
- `GET /{id}/editar` — Formulário edição
- `POST /{id}/editar` — Atualizar (gera histórico)
- `POST /{id}/historico/{hid}/excluir` — Excluir histórico (requer senha)
- `GET /{id}/cancelar` — Cancelar
- `GET /{id}/excluir` — Excluir

### Ordens de Serviço (`/ordens-servico/`)
- `GET /` — Listar
- `GET /nova` — Formulário nova
- `POST /nova` — Criar
- `GET /{id}` — Detalhe
- `POST /{id}/editar` — Atualizar
- `GET /{id}/imprimir?tipo=a4|termica` — Página de impressão
- `POST /{id}/excluir` — Excluir

### Pedidos (`/pedidos/`)
- `GET /` — Listar pedidos
- `GET /novo` — Formulário novo
- `POST /salvar` — Salvar (acao=salvar ou acao=emitir)
- `GET /{id}` — Detalhe do pedido
- `GET /{id}/editar` — Formulário edição
- `GET /{id}/imprimir?tipo=a4|termica|orcamento` — Página de impressão
- `POST /{id}/excluir` — Excluir (requer senha)
- `POST /{id}/finalizar` — Finalizar pedido
- `GET /pre-venda/agrupar` — Agrupar pré-vendas

### Configurações (`/configuracoes/`)
- `GET /` — Página de configurações
- `POST /` — Salvar

### Bling (`/bling/`)
- `GET /` — Página de integração
- `POST /salvar-credenciais` — Salvar Client ID/Secret
- `GET /autorizar` — Iniciar OAuth (redireciona ao Bling)
- `GET /callback` — Callback OAuth (troca code por token)
- `POST /importar-contatos` — Importar contatos do Bling
- `POST /limpar-importar` — Limpar tabelas + reimportar
- `POST /sincronizar-pendentes` — Enviar pendentes ao Bling
- `GET /webhook` — Health-check do webhook
- `POST /webhook` — Receiver de webhook
- `POST /gerar-webhook-secret` — Regenerar secret

### Produtos (`/produtos/`)
- `GET /` — Listar itens (busca, filtros: situação, fornecedor, categoria, marca, estoque, tipo)
- `GET /buscar?q=` — Buscar itens (API para autocomplete)
- `GET /novo` — Formulário novo
- `POST /novo` — Criar
- `GET /{id}/editar` — Formulário edição
- `POST /{id}/editar` — Atualizar
- `GET /{id}/excluir` — Excluir
- Variações: `variacoes/nova`, `variacoes/editar/{id}`, `variacoes/excluir/{id}`
- Categorias: `/categorias`, `/categorias/nova`, `/categorias/editar/{id}`, `/categorias/excluir/{id}`
- Marcas: `/marcas`, `/marcas/nova`, `/marcas/editar/{id}`, `/marcas/excluir/{id}`

### Dashboard (`/`)
- Indicadores: total clientes, fornecedores, contas pendentes, vencidas, assinaturas ativas, OS abertas, pedidos pendentes
- Listas: últimas 5 OS, próximos vencimentos
- Status Bling: badge verde (conectado) / laranja (pendentes)

## Integração Bling ERP v3

Consulte a documentação anterior (seção Integração Bling ERP v3).

## Funcionalidades Implementadas

- [x] CRUD Clientes (com máscaras CPF/CNPJ/CEP, PF/PJ, UF dropdown)
- [x] CRUD Fornecedores (mesma estrutura)
- [x] CRUD Itens (tipo: produto/servico/kit, variações/SKU, ficha técnica)
- [x] Contas a Pagar / Receber (baixa com modal e edição de data)
- [x] Assinaturas (com histórico, revenda, cálculo de lucro)
- [x] Geração de cobrança a partir de assinatura
- [x] Ordens de Serviço (com impressão térmica 80mm)
- [x] Pedidos de Venda (com impressão A4/térmica)
- [x] Modal de preview unificado para impressão
- [x] Configurações da empresa (logo, senha admin)
- [x] Dashboard com indicadores e status Bling
- [x] Integração Bling OAuth 2.0 (importação e exportação)
- [x] Webhook para sincronia em tempo real
- [x] Máscaras JS para CPF, CNPJ, CEP, telefone
- [x] Formatação CPF/CNPJ na exibição (filtro Jinja)
- [x] Impressão térmica 80mm e A4

## Próximos Passos / Melhorias

### Prioritárias
- [ ] Testar fluxos de salvar orçamento vs emitir/finalizar
- [ ] Verificar impressão térmica funcionando corretamente
- [ ] Validar cálculo de total com kits compostos

### Sugeridas
- [ ] Relatórios (financeiro, OS por período, produtos mais vendidos)
- [ ] Notificações de vencimento (email/WhatsApp)
- [ ] Múltiplas empresas/usuários com permissões
- [ ] Testes automatizados
- [ ] Cache de token Bling em Redis
- [ ] Filas de sincronia para operações em lote
- [ ] Integração completa com Sicoob (boleto bancário)

## Observações Importantes

- **Senha Admin:** Configurada em Configurações > Segurança. Usada para proteger exclusão de histórico.
- **Código Sequencial:** Clientes = `CLI-NNNN`, Fornecedores = `FOR-NNNN`. Bling-importados mantêm o código original formatado.
- **Tipos de Itens:** `produto` (venda), `servico` (execução), `kit` (composição de insumos).
- **Impressão Modal:** Preview antes de finalizar - permite escolher A4 ou térmica 80mm.
- **Webhook:** Requer URL pública (ngrok para testes, HTTPS em produção).
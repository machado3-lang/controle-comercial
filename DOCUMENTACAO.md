# Sistema de Controle de Serviços

Sistema web completo para gestão de oficina/prestador de serviços com integração Bling ERP v3.

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python 3.14 + FastAPI |
| Frontend | Jinja2 Templates + Bootstrap 5 Dark |
| Banco | SQLite via SQLAlchemy 2.0 |
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
│   └── bling.py               # Integração Bling ERP v3
├── templates/
│   ├── base.html              # Layout base (navbar, flash messages)
│   ├── index.html             # Dashboard
│   ├── clientes/              # CRUD Clientes
│   ├── fornecedores/          # CRUD Fornecedores
│   ├── contas/                # Contas a Pagar/Receber
│   ├── assinaturas/           # Assinaturas
│   ├── ordens_servico/        # Ordens de Serviço
│   ├── configuracoes/         # Configurações
│   └── bling/                 # Integração Bling
├── static/
│   ├── css/styles.css         # Estilos customizados
│   ├── js/scripts.js          # Máscaras CPF/CNPJ/CEP
│   └── uploads/               # Upload de logo
└── __pycache__/               # Cache Python
```

## Como Executar

```bash
cd "C:\Controle de Serviços"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Acessar: http://localhost:8000

### Migrations

As colunas do Bling são adicionadas automaticamente via `ALTER TABLE` no startup do `main.py`. Não há migrações formais.

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

### ContaPagar / ContaReceber

| Campo | Descrição |
|-------|-----------|
| cliente_id / fornecedor_id FK | Vinculado |
| descricao | Descrição |
| valor | Valor |
| data_vencimento | Vencimento |
| data_pagamento / data_recebimento | Baixa |
| status | `pendente`, `pago`, `vencido`, `cancelado` |

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
- `GET /{id}/imprimir` — Página de impressão (formato 80mm)
- `GET /{id}/excluir` — Excluir

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

### Dashboard (`/`)
- Indicadores: total clientes, fornecedores, contas pendentes, vencidas, assinaturas ativas, OS abertas
- Listas: últimas 5 OS, próximos vencimentos
- Status Bling: badge verde (conectado) / laranja (pendentes)

## Integração Bling ERP v3

### Autenticação

A API v3 do Bling usa OAuth 2.0 (NÃO usa API key como a v2).

**Fluxo:**
1. Criar aplicativo em https://developer.bling.com.br/aplicativos
2. Configurar "Link de Redirecionamento" = `http://localhost:8000/bling/callback`
3. Inserir Client ID e Client Secret no sistema
4. Clicar em "Autorizar com Bling"
5. Fazer login no Bling e autorizar
6. Sistema recebe `access_token` (válido 6h) + `refresh_token`

**Token Refresh:** Automático via `_refresh_token()` quando detecta expiração.

**Endpoints OAuth:**
- Authorize: `https://www.bling.com.br/Api/v3/oauth/authorize`
- Token: `https://www.bling.com.br/Api/v3/oauth/token`

**Auth nas chamadas:**
- Token: Basic Auth (client_id:client_secret Base64)
- Body: JSON (`grant_type`, `code`/`refresh_token`, `redirect_uri`)

### Sincronia de Contatos

**Importação (Bling → Local):**
1. Lista todos os IDs via `GET /contatos?pagina=N&limite=100`
2. Busca detalhes individuais com `GET /contatos/{id}` (3 threads paralelas, retry com backoff)
3. Classifica como Cliente ou Fornecedor por `tiposContato[].descricao` ou `tiposContato[].nome`
4. Mapeia campos do Bling para o modelo local:
   - `nome` → `nome`
   - `numeroDocumento` → `cpf_cnpj` (só dígitos)
   - `tipo` (`F`/`J`) → `tipo_pessoa` (`fisica`/`juridica`)
   - `telefone` / `celular` → top-level ou `pessoasContato[]`
   - `endereco.geral.*` → endereço
   - `email` → `email`
   - `fantasia` → `contato`
   - `codigo` → `codigo` (formatado CLI-NNNN / FOR-NNNN)
   - `dataCriacao` → `created_at`
5. Se `bling_id` já existe, atualiza; senão, cria novo

**Exportação (Local → Bling):**
- Registros com `bling_pending_sync = True` são enviados
- Cliente: `tiposContato = [{"id": 1}]`
- Fornecedor: `tiposContato = [{"id": 2}]`
- Dados completos enviados via POST (criar) ou PUT (atualizar)

**Webhook:**
- Bling notifica alterações em tempo real
- Sistema busca o contato atualizado e aplica localmente
- Evento "excluir" remove o `bling_id` local

### Endpoints da API Bling v3 Utilizados

| Método | Endpoint | Uso |
|--------|----------|-----|
| GET | `/contatos?pagina=N&limite=100` | Listar contatos |
| GET | `/contatos/{id}` | Detalhe do contato |
| POST | `/contatos` | Criar contato |
| PUT | `/contatos/{id}` | Atualizar contato |

### Observações sobre a API Bling

- **Listagem vs Detalhe:** O endpoint de listagem retorna dados simplificados (id, nome, codigo, situacao, numeroDocumento, telefone, celular). O detalhe completo exige `GET /contatos/{id}`.
- **Rate Limit:** Recomendado no máximo 3 requisições simultâneas. Implementado retry com backoff (2s, 4s) para respostas 429.
- **Base URL API:** `https://api.bling.com.br/Api/v3` (diferente do authorize/token que usam `www.bling.com.br`).
- **Tipos de Contato:** Bling usa tabela única com `tiposContato[]` para classificar (Cliente, Fornecedor, Técnico, etc.). O sistema local separa em duas tabelas (clientes e fornecedores).

## Funcionalidades Implementadas

- [x] CRUD Clientes (com máscaras CPF/CNPJ/CEP, PF/PJ, UF dropdown)
- [x] CRUD Fornecedores (mesma estrutura)
- [x] Contas a Pagar / Receber (baixa com modal e edição de data)
- [x] Assinaturas (com histórico, revenda, cálculo de lucro)
- [x] Geração de cobrança a partir de assinatura
- [x] Ordens de Serviço (com impressão térmica 80mm)
- [x] Configurações da empresa (logo, senha admin)
- [x] Dashboard com indicadores e status Bling
- [x] Integração Bling OAuth 2.0 (importação e exportação)
- [x] Webhook para sincronia em tempo real
- [x] Máscaras JS para CPF (`000.000.000-00`), CNPJ (`00.000.000/0000-00`), CEP (`00000-000`)
- [x] Formatação CPF/CNPJ na exibição (filtro Jinja)

## Próximos Passos (Sugestões)

- [ ] Deploy em VPS com HTTPS (necessário para webhook em produção)
- [ ] Autenticação/login para o sistema
- [ ] Relatórios (financeiro, OS por período, etc.)
- [ ] Notificações de vencimento (email/WhatsApp)
- [ ] Módulo de estoque
- [ ] Múltiplas empresas/usuários
- [ ] Testes automatizados
- [ ] Migrar para PostgreSQL (escala)
- [ ] Cache de token Bling em Redis ou similar
- [ ] Filas de sincronia para operações em lote

## Observações Importantes

- **Senha Admin:** Configurada em Configurações > Segurança. Usada para proteger exclusão de histórico.
- **Código Sequencial:** Clientes = `CLI-NNNN`, Fornecedores = `FOR-NNNN`. Bling-importados mantêm o código original formatado.
- **Máscaras JS:** Aplicadas via `static/js/scripts.js`. O filtro Jinja `format_cpf_cnpj` formata na exibição das listas/detalhes.
- **Lucro em Assinaturas:** Exibido por linha e total. Só mostra alerta de lucro total se > 0.
- **Impressão OS:** Layout otimizado para impressora térmica 80mm (monospace, @page margins, sem logo).
- **Webhook:** Requer URL pública (ngrok para testes, HTTPS em produção).

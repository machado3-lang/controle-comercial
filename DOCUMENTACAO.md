# Sistema de Controle Comercial

Sistema web completo para gestão comercial com integração Bling ERP v3.

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python 3.14 + FastAPI |
| Frontend | Jinja2 Templates + Bootstrap 5 Dark |
| Banco | SQLite via SQLAlchemy 2.0 (develop) / PostgreSQL (production) |
| Autenticação | Sessão (senha admin) |
| PDF | fpdf2 |
| HTTP | httpx, requests |
| NFe | API NotaAs + SEFAZ (certificado A1) |
| NFSe | API Betha + ADN (certificado A1) |

## Estrutura do Projeto

```
C:\Controle de Serviços\
├── main.py                    # Inicialização, rotas, dashboard, migrações
├── database.py                # SQLAlchemy engine + session
├── models.py                  # Todas as tabelas do banco
├── models_nfe.py              # Modelos NFe, NFSe, NFeItem, NFSeItem
├── requirements.txt           # Dependências Python
├── controle.db                # Banco SQLite (gerado automaticamente)
├── routers/
│   ├── clientes.py            # CRUD Clientes
│   ├── fornecedores.py        # CRUD Fornecedores
│   ├── contas.py              # Contas a Pagar/Receber (com filtros, PDF, Excel)
│   ├── assinaturas.py         # Assinaturas com histórico
│   ├── ordens_servico.py      # Ordens de Serviço
│   ├── configuracoes.py       # Configurações da empresa
│   ├── bling.py               # Integração Bling ERP v3
│   ├── produtos.py            # CRUD Itens (produtos/serviços/kits)
│   ├── pedidos.py             # Pedidos de Venda
│   ├── nfe.py                 # NFe (emissão NotaAs, distribuição SEFAZ, importação)
│   ├── nfse.py                # NFSe (emissão Betha, ADN consulta)
│   └── sicoob.py              # Integração Sicoob API Cobrança
├── services/
│   ├── nfe_distribuicao.py    # Consulta SEFAZ (distDFeInt, consChNFe)
│   ├── nfe_notaas.py          # Emissão NFe via API NotaAs
│   ├── nfe_danfe.py           # Geração DANFE PDF local
│   ├── nfse_betha.py          # Emissão NFSe + ADN (Ambiente de Dados Nacional)
│   ├── nfse_pdf.py            # Geração PDF NFSe + relatório contas
│   ├── nfse_service.py        # Lógica NFSe
│   └── backup.py              # Backup/restore
├── templates/
│   ├── base.html              # Layout base (navbar, flash messages, modal exclusão)
│   ├── index.html             # Dashboard
│   ├── clientes/              # CRUD Clientes
│   ├── fornecedores/          # CRUD Fornecedores
│   ├── contas/                # Contas a Pagar/Receber
│   ├── assinaturas/           # Assinaturas
│   ├── ordens_servico/        # Ordens de Serviço
│   ├── configuracoes/         # Configurações
│   ├── bling/                 # Integração Bling
│   ├── produtos/              # CRUD Itens
│   ├── pedidos/               # Pedidos de Venda
│   ├── nfe/                   # NFe (lista, emissão, detalhe, config)
│   └── nfse/                  # NFSe (lista, emissão, detalhe)
├── static/
│   ├── css/styles.css         # Estilos customizados
│   ├── js/scripts.js          # Máscaras CPF/CNPJ/CEP, exclusão, estorno
│   └── uploads/               # Upload de logo
├── certs/                     # Certificados digitais A1
├── Procfile                   # Deploy Railway (uvicorn)
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

Dados da empresa para impressão, NFe, NFSe e integrações:
- Dados cadastrais (razão social, CNPJ, IE, IM, endereço, IBGE)
- Logo (upload JPG/PNG)
- `senha_admin` — protege exclusão de histórico, assinaturas, contas
- **Bling**: `bling_token`, `bling_client_id`, `bling_client_secret`, `bling_refresh_token`, `bling_token_expires_at`, `bling_webhook_secret`, `bling_api_key_v2`
- **Sicoob**: `sicoob_client_id`, `sicoob_token`, `sicoob_conta_corrente`, `sicoob_beneficiario`, `sicoob_cert_path`, `sicoob_cert_key_path`, `sicoob_cert_password`, `sicoob_cert_base64`, `sicoob_cert_key_base64`
- **NFe/NFSe**: `notaas_api_key`, `notaas_ambiente` (1=prod, 2=homolog), `serie_nfe`, `ultimo_numero_nfe`, `ultimo_numero_nfse`, `cfop_padrao`, `nfe_aliquota_federal`, `nfe_aliquota_estadual`, `cert_path`, `cert_password`, `cert_base64`, `cert_validade`, `nfe_ultnsu`
- **NFSe**: `aliquota_iss`, `aliquota_federal`, `aliquota_estadual`, `aliquota_municipal`

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
- `GET /pagar` — Listar contas a pagar (filtros: busca, status, data)
- `GET /pagar/pdf` — Exportar PDF das contas a pagar
- `GET /pagar/nova` — Formulário nova conta
- `POST /pagar/nova` — Criar
- `GET /pagar/{id}/editar-form` — Formulário edição (modal)
- `POST /pagar/{id}/editar` — Atualizar
- `POST /pagar/{id}/excluir` — Excluir (requer senha admin, retorna JSON)
- `POST /pagar/{id}/baixar` — Baixar pagamento
- `POST /pagar/{id}/estornar` — Estornar baixa
- `GET /pagar/{id}` — Detalhe
- `GET /receber` — Listar contas a receber (filtros: busca, status, data)
- `GET /receber/pdf` — Exportar PDF das contas a receber
- `GET /receber/nova` — Formulário nova conta
- `POST /receber/nova` — Criar
- `GET /receber/{id}/editar-form` — Formulário edição (modal)
- `POST /receber/{id}/editar` — Atualizar
- `POST /receber/{id}/excluir` — Excluir (requer senha admin, retorna JSON)
- `POST /receber/{id}/baixar` — Baixar recebimento
- `POST /receber/{id}/estornar` — Estornar baixa
- `GET /receber/{id}` — Detalhe
- `GET /previsao-recebimentos` — Previsão de recebimentos (próximos 30 dias)
- `GET /inadimplencia` — Contas vencidas (filtro por dias)

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

### Contas a Pagar/Receber
- `GET /contas/pagar/pdf` — Exportar PDF (retrato, quebra automática de linha)
- `GET /contas/receber/pdf` — Exportar PDF
- Filtros: busca textual, status (pendente/pago/vencido/cancelado), período (data início/fim)
- Baixa/estorno com modal
- Exclusão protegida por senha admin (retorno JSON)

### NFe (`/nfe/`)
- **Emissão**: via API NotaAs (a partir de pedido, OS ou avulsa)
- **Distribuição SEFAZ**: busca NFe por período (recebidas + emitidas via consChNFe)
- **Importação**: por chave de acesso (44 dígitos) ou upload de XML
- **Cache local**: consultas seguintes carregam do banco sem bater na SEFAZ
- **DANFE**: PDF gerado localmente com brazilfiscalreport ou baixado do NotaAs
- **Certificado A1**: upload pelo formulário de configuração, armazenado em base64
- **Webhook**: NotaAs atualiza status automaticamente

### NFSe (`/nfse/`)
- **Emissão**: via API Betha (Dourados-MS)
- **ADN (Ambiente de Dados Nacional)**: consulta NFS-es emitidas/recebidas por período
- **PDF** e **XML** baixáveis via proxy do servidor (com certificado)
- **Certificado A1**: compartilhado com o módulo NFe

### Sicoob (`/sicoob/`)
- **Boleto bancário**: emissão e consulta via API Sicoob
- **Certificado**: upload A1 (PEM) pelo formulário de configuração

## Funcionalidades Implementadas

- [x] CRUD Clientes (com máscaras CPF/CNPJ/CEP, PF/PJ, UF dropdown)
- [x] CRUD Fornecedores (mesma estrutura)
- [x] CRUD Itens (tipo: produto/servico/kit, variações/SKU, ficha técnica)
- [x] Contas a Pagar / Receber (baixa com modal, filtros, PDF)
- [x] Assinaturas (com histórico, revenda, cálculo de lucro)
- [x] Geração de cobrança a partir de assinatura
- [x] Ordens de Serviço (com impressão térmica 80mm)
- [x] Pedidos de Venda (com impressão A4/térmica)
- [x] Modal de preview unificado para impressão
- [x] Configurações da empresa (logo, senha admin, certificados)
- [x] Dashboard com indicadores e status Bling
- [x] Integração Bling OAuth 2.0 (importação e exportação)
- [x] Webhook para sincronia em tempo real
- [x] Máscaras JS para CPF, CNPJ, CEP, telefone
- [x] Formatação CPF/CNPJ na exibição (filtro Jinja)
- [x] Impressão térmica 80mm e A4
- [x] NFe — Emissão NotaAs + Distribuição SEFAZ + Importação (chave/XML)
- [x] NFSe — Emissão Betha + ADN (emitidas/recebidas)
- [x] Sicoob — Boleto bancário (emissão e consulta)
- [x] Backup/Restore — Exportação e importação do banco

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
## Auditoria Geral e Melhorias (Sessão de Verificação)

Revisão técnica completa do sistema cobrindo segurança, tratamento de erros, consistência de dados e melhorias. Abaixo o que foi corrigido nesta sessão.

### Problemas Críticos Corrigidos

| # | Problema | Arquivo | Correção |
|---|-----------|---------|----------|
| 1 | `Decimal(valor)` sem tratamento → HTTP 500 em formulários com valor inválido/vazio (Contas e Produtos) | `routers/contas.py`, `routers/produtos.py` | Helper `to_decimal(v, default)` aplicado a todos os pontos de conversão de valor/preço. |
| 2 | `except: pass` silenciava perda de itens do pedido em `salvar_pedido` | `routers/pedidos.py:294` | Agora faz `db.rollback()`, loga o erro e exibe mensagem de erro (sem perda silenciosa de dados). |
| 3 | Webhooks Sicoob/NFSe abertos se o segredo de ambiente estivesse vazio | `routers/sicoob.py:731`, `routers/nfe.py:1644` | **Fail-closed**: se `WEBHOOK_*_SECRET` não definido, retorna 403. |
| 4 | Vazamento parcial de token Sicoob (últimos 10 caracteres) em `testar_token` | `routers/sicoob.py:767` | Removido; endpoint retorna apenas booleano de sucesso. |
| 5 | `verify=False` desabilitava validação TLS em download de DANFSe (MITM) | `routers/nfse.py:794,1202` | Alterado para `verify=True`. |
| 6 | Baixar/estornar contas sem confirmação de senha e sem validação de estado (qualquer usuário logado afeta saldo) | `routers/contas.py` (baixar/estornar) | Agora exigem `confirma_senha_usuario` e só permitem baixa de contas PENDENTE/VENCIDO. Modal ganhou campo "Senha de Confirmação". |
| 7 | `finalizar_pedido` faturava pedido mesmo pertencendo a consolidação (duplicidade) | `routers/pedidos.py:439` | Bloqueia faturamento direto de pedido vinculado a consolidação. |
| 8 | **PDF de Contas quebrado** (`gerar_pdf_contas`): para `tipo="receber"` caía no fallback `c.fornecedor` (inexistente em `ContaReceber`) quando `c.cliente` era `None` → `AttributeError`; lógica de `parte` frágil e soma de total sem `Decimal` | `services/nfse_pdf.py:226`, `routers/contas.py` | Duck-typing com `getattr` por tipo (cliente/fornecedor), None-safe, total em `Decimal`, `pdf.output()` tratado (bytes/bytearray), cabeçalho repetido na quebra de página e rótulo de filtro. Validado: receber 2752 B, pagar 2405 B, e caso "receber sem cliente" (antes quebrava). |

### Funcionalidades Implementadas (Histórico da Sessão)

- **Consolidação de Pedidos:** regras de negócio (somente `PRE_VENDA` entra), cliente titular = 1º pedido, cliente diverso permitido em criação e consolidação aberta (com aviso), remoção de pedido de consolidação aberta (volta a `PRE_VENDA`; se vazia, exclui a consolidação), trava de status `FATURADO` imutável, correção de bug de comparação `forma_pagamento` (string vs enum).
- **Ordenação de listagens:** Assinaturas (cliente, descrição, revenda, data início, vencimento), Ordens de Serviço (cliente, equipamento, entrada, saída, valor, técnico, autorizado, requisição, status), Logs de Auditoria (data, usuário, ação, entidade, detalhes).
- **Logs de Auditoria:** filtros por data (início/fim), usuário, ação, entidade e detalhes; ordenação por colunas.
- **Contas a Pagar/Receber — Juros/Desconto/Valor Real:** modelos ganharam `valor_juros`, `valor_desconto`, `valor_total`; na baixa informa-se valor pago/recebido + juros − desconto = total real; exibido em detalhe e modal. Padrão ERP profissional.

### Itens Pendentes (Recomendados, Não Críticos)

- **Middleware `/api/` público:** `/api/` está em `_public_prefixes` (lifespan.py) — qualquer nova rota sob `/api/` nasce sem checagem de login. Recomenda-se remover de público e exigir sessão no middleware (manter só webhooks).
- **`SETUP_TOKEN` obrigatório em produção:** se vazio, `auth.py:53` cria admin sem autenticação. Validar obrigatoriedade em produção (como já feito para `SECRET_KEY`).
- **`finalizado_por` da consolidação:** campo existe no modelo mas nunca é preenchido (`consolidacoes.py`). Adicionar auditoria de quem finalizou.
- **Corrida de numeração:** cálculo de próximo número via `max()` sem lock no fallback. `Empresa.with_for_update()` cobre o caso feliz.
- **Eager-load `cliente` em `detalhe_pedido`** para evitar AttributeError se cliente for null.
- **Exposição de traceback:** `generic_exception_handler` expõe stack trace quando não é produção — garantir `ENVIRONMENT=production` em produção.

### Relatórios Disponíveis vs. ERP Profissional

**Existentes e funcionais:**
- PDF listas: Contas a Pagar/Receber (corrigido), Pedidos (`/pedidos/{id}/pdf`), Produtos selecionados (`/produtos/pdf-selecionados`), Boleto Sicoob, DANFE (NFe) e DANFSE (NFSe).
- HTML financeiros: Previsão de Recebimentos (`/contas/previsao-recebimentos`), Inadimplência (`/contas/inadimplencia`) e **DRE** (`/contas/dre`, com plano de contas + não-classificados).
- Excel export: Contas a Pagar/Receber (`/pagar/exportar`, `/receber/exportar`).

**Lacunas (sugestões de implementação futura):**
- Recibo individual de pagamento/recebimento (1 conta) em PDF.
- Relatório de vendas por período/cliente e extrato por cliente/fornecedor.
- Razão contábil e fluxo de caixa projetado (além da previsão atual).

### Certificados Digitais no Railway (PENDENTE — impacto em produção)

O sistema usa **dois tipos de certificado** conforme o módulo:
- **Sicoob (boleto):** campo dedicado na aba Sicoob, formato **PEM** (cert + key). Carregado via `Empresa.sicoob_cert_id`/`sicoob_cert_path` e usado em `routers/sicoob.py` como `httpx.Client(cert=(cert.pem, key.pem))`.
- **NFSe / NFe / Betha (demais):** formato **PFX** (`Empresa.cert_path`/`cert_id`/`cert_password`, default `./certs/certificado.pfx`), usado pelas emissões.

**RESOLVIDO:** `services/cert_store.py` agora persiste os certificados **criptografados (AES-256-GCM) no PostgreSQL** (colunas `cert_base64`/`sicoob_cert_base64`/`sicoob_cert_key_base64` da tabela `empresa`), não mais em arquivo de disco. Assim sobrevive aos redeploys do Railway (filesystem efêmero). Mapeamento: `empresa`→`cert_base64`, `sicoob`→`sicoob_cert_base64`, `sicoob_key`→`sicoob_cert_key_base64`. A chave mestra vem de `CERT_MASTER_KEY` (fallback `SECRET_KEY` em dev). Sicoob (PEM), NFSe e NFe (PFX) já usam `cert_store`.

### Como Testar

Servidor: `run_server.bat` (uvicorn, porta 3000, `--reload`). Login admin padrão: `admin@controle.com` / `admin123` (senha de teste — alterar em produção).

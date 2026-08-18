# Documentação — Boletos (Cobrança Sicoob)

Este documento reúne **tudo o que o sistema possui em relação a boletos**. O
módulo de boletos é uma integração com a **API de Cobrança Bancária do Sicoob
(v3)** e atua sobre o modelo `ContaReceber`: cada conta a receber pode ter um
boleto emitido, consultado, baixado, alterado e pago via Sicoob.

---

## 1. Visão geral

- O boleto **não é uma entidade própria**: ele é o reflexo de uma
  `ContaReceber` (`models.py:218`) cujo campo `boleto_emitido` está `True`.
- Toda a integração com o Sicoob vive em `routers/sicoob.py` (1122 linhas),
  com apoio de:
  - `services/parcelamento.py` → emissão em lote das parcelas (`emitir_boletos_contas`).
  - `services/email_service.py` → envio do PDF do boleto por e-mail.
  - `services/cert_store.py` → armazenamento seguro do certificado.
  - `app/core/config.py` → URLs base da API/auth.
- As telas ficam em `templates/sicoob/boletos.html` e `templates/sicoob/index.html`.

### URLs de integração (`app/core/config.py:54-55`)
```
SICOOB_API_URL  = https://api.sicoob.com.br/cobranca-bancaria/v3
SICOOB_AUTH_URL = https://auth.sicoob.com.br/auth/realms/cooperado/protocol/openid-connect/token
```

### Scopes OAuth usados
| Scope | Uso |
|-------|-----|
| `boletos_consulta` | consultar boletos, listar, PDF, sincronizar pagamentos, webhook, importar |
| `boletos_inclusao` | emitir boleto (`POST /boletos`) |
| `boletos_alteracao` | baixar e alterar boleto (`PATCH`/`POST baixar`) |

---

## 2. Modelo de dados

### `ContaReceber` (`models.py:218-269`) — campos de boleto
| Campo | Tipo | Papel |
|-------|------|-------|
| `nosso_numero` | String(30) unique | nosso número **local** (`AAAAMMDD` + id da conta, 8 dígitos) |
| `api_nosso_numero` | String(30) | nosso número **retornado pela API Sicoob** (usado em consultas/PDF) |
| `boleto_emitido` | Boolean (index) | marca se o boleto já foi emitido |
| `boleto_url` | String(500) | código de barras / linha digitável retornados pela API |
| `boleto_txid` | String(50) | txid retornado pela API |
| `numero_documento` | String(30) | número do documento informado no momento da emissão (vira `seuNumero`) |
| `data_emissao` | Date | data de emissão (retornada ou hoje) |
| `motivo_baixa` | String(100) | motivo informado na baixa |
| `forma_pagamento` | String(100) | pode ser `boleto` (ver `FormaPagamento.BOLETO`, `models.py:497`) |

### `Empresa` (`models.py:664-673`) — credenciais Sicoob
| Campo | Tipo | Obs |
|-------|------|-----|
| `sicoob_client_id` | String(200) | obrigatório |
| `sicoob_token` | String(3000) | access_token em cache (renovado por `refresh_sicoob_token`) |
| `sicoob_conta_corrente` | String(30) | ex.: `110558` |
| `sicoob_beneficiario` | String(20) | número do beneficiário (ex.: `91820`) |
| `sicoob_cert_path` / `sicoob_cert_key_path` | String(500) | **fallback legado** (arquivo em disco) |
| `sicoob_cert_password` | String(100) | **DEPRECATED** → use `cert_store` |
| `sicoob_cert_base64` / `sicoob_cert_key_base64` | Text | **DEPRECATED** (deferred) |
| `sicoob_cert_id` | Integer | ID do certificado no `cert_store` (caminho preferencial) |

> Ordem de resolução do certificado (`get_cert_config`, `sicoob.py:125`):
> 1. `sicoob_cert_id` (cert_store → PEM temporário) — **preferencial**;
> 2. `sicoob_cert_base64` / `sicoob_cert_key_base64` (base64 legacy);
> 3. `sicoob_cert_path` / `sicoob_cert_key_path` (arquivo em disco).

### `PedidoVenda` e `PedidoConsolidado` — flags de geração
- `forma_pagamento` (String) pode ser `boleto`.
- `gerar_boleto` (Boolean) — pede emissão automática ao finalizar.
- `terminos_boleto` (Text) — textos/termos do boleto (`models.py:512-513`, `566-567`).

---

## 3. Emissão de boleto

### 3.1 Emissão unitária — `emitir_boleto(db, conta)` (`sicoob.py:181`)
Fluxo:
1. `refresh_sicoob_token(db, "boletos_inclusao")`.
2. Valida `sicoob_conta_corrente`, `sicoob_client_id` e existência de `conta.cliente`.
3. Monta `nosso_numero = AAAAMMDD + id(8d)` e `seuNumero` (prioriza `numero_documento`).
4. Monta o body (`seuNumero`, `valor`, `dataVencimento`, `dataEmissao`,
   `codigoModalidade=1`, `codigoEspecieDocumento="DM"`, `numeroParcela`, pagador
   com CPF/CNPJ, endereço, etc.).
5. `POST {SICOOB_API_URL}/boletos` com retentativa (3x) e refresh de token em 401.
6. Em caso de 200/201: preenche `conta.api_nosso_numero`, `conta.data_emissao`,
   `conta.boleto_emitido=True`, `conta.boleto_url` (código de barras/linha
   digitável), `conta.boleto_txid`, e faz `db.commit()`.

### 3.2 Emissão em lote — `emitir_boletos_contas(db, contas, forcar=False)` (`parcelamento.py:226`)
- Itera as `ContaReceber` e chama `emitir_boleto`.
- **Ignora** contas com `boleto_emitido=True` (evita duplicidade) a menos que
  `forcar=True`.
- Retorna `(qtd_ok, lista_de_erros)`.

### 3.3 Gatilhos de emissão automática
- **Contas a receber** (`routers/contas.py:395-429` e `474-521`): flag `emitir_boletos` no formulário chama `emitir_boletos_contas`.
- **Consolidações** (`routers/consolidacoes.py:730-739`): se `gerar_boleto` ou `forma_pagamento == "boleto"`, emite os boletos de todas as parcelas geradas.
- **Em lote manual** (`POST /sicoob/emitir-em-lote`, `sicoob.py:560`): emite todos os boletos pendentes (status `PENDENTE`, não emitidos, vencimento ≥ hoje).

---

## 4. Autenticação e token

- `refresh_sicoob_token(db, scope)` (`sicoob.py:75`): `POST` no `SICOOB_AUTH_URL`
  com `grant_type=client_credentials` + `client_id` + `scope`, usando o
  certificado mTLS (`httpx.Client(cert=...)`). Atualiza `Empresa.sicoob_token` e
  faz commit. Retenta 3x em 429/502/503/504 com backoff exponencial.
- `get_token_or_error(db, scope)` (`sicoob.py:110`): valida empresa, `client_id`
  e certificado e devolve `(token, erro)`.
- `get_cert_config(db)` (`sicoob.py:125`): resolve o certificado (ver seção 2).

---

## 5. PDF do boleto (segunda via)

- `obter_pdf_boleto_bytes(nosso_numero, db)` (`sicoob.py:498`): `GET
  {SICOOB_API_URL}/boletos/segunda-via?gerarPdf=true` e decodifica o
  `pdfBoleto` (base64) retornado. Retenta 3x.
- `GET /sicoob/boleto-pdf/{nosso_numero}` (`sicoob.py:549`): devolve o PDF
  inline (`application/pdf`).
- `_pdf_boleto_bytes(conta, db)` (`email_service.py:177`): wrapper usado pelo
  e-mail; exige `boleto_emitido` e `api_nosso_numero`.

---

## 6. Baixa, alteração e exclusão

- **Baixar** — `POST /sicoob/baixar-boleto/{nosso_numero}` (`sicoob.py:594`):
  marca a conta como `BAIXA_SOLICITADA`, consulta o nosso número real na API,
  depois `POST {SICOOB_API_URL}/boletos/{nn}/baixar` (scope `boletos_alteracao`).
  Em sucesso, status → `CANCELADO`.
- **Alterar** — `PATCH /sicoob/alterar-boleto/{nosso_numero}` (`sicoob.py:1044`):
  a API Sicoob exige **uma alteração por objeto PATCH**. Suporta
  `prorrogacaoVencimento` (data) e `valorNominal` (valor). Só então atualiza a
  `ContaReceber`.
- **Excluir (lógico)** — `POST /sicoob/boleto/{nosso_numero}/excluir`
  (`sicoob.py:1101`): exige confirmação de senha do usuário; define status
  `CANCELADO` e registra auditoria (`registrar_auditoria`).

---

## 7. Sincronização de pagamentos

- **Manual** — `POST /sicoob/sync-pagamentos` (`sicoob.py:680`): percorre as
  contas com boleto emitido e status `PENDENTE`/`VENCIDO`/`BAIXA_SOLICITADA`,
  consulta a API e, via `extrair_situacao` / `extrair_data_liquidacao`,
  atualiza para `PAGO` (com `data_recebimento`) ou `CANCELADO`.
- **Webhook** — `POST /sicoob/webhook` (`sicoob.py:766`): protegido por
  `WEBHOOK_SICOOB_SECRET` (header `X-Webhook-Secret` ou query `secret`).
  Processa `LIQUIDADO`/`PAGO` → `PAGO` e `BAIXADO` → `CANCELADO`.
- **Situação** — `extrair_situacao(boleto)` (`sicoob.py:29`) normaliza os
  diversos campos da API; `extrair_data_liquidacao` (`sicoob.py:41`) lê a data
  de liquidação a partir de `listaHistorico` (tipo 6).

---

## 8. Listagem e importação

- `GET /sicoob/api/listar-boletos` (`sicoob.py:430`): lista `ContaReceber`
  `boleto_emitido=True` com filtros de situação (PAGO/CANCELADO/VENCIDO/PENDENTE),
  busca por cliente, intervalo de vencimento e ordenação. Retorna também
  `total_valor`.
- `GET /sicoob/api/listar-boletos-sicoob` (`sicoob.py:857`): consulta a API por
  pagador (`/pagadores/{cpfCnpj}/boletos`) e cruza com os nossos números locais
  (`nossoSistema`).
- `POST /sicoob/importar-boleto` (`sicoob.py:908`): importa/atualiza um boleto
  manualmente a partir do JSON (nosso número, cliente, valor, datas, linha
  digitável, situação). Situação do Sicoob: `1`=Em Aberto, `2`=Baixado, `3`=Liquidado.
- `GET /sicoob/api/inadimplencia` (`sicoob.py:1020`): boletos emitidos e não
  pagos cujo vencimento já passou, com `diasVencido`.

---

## 9. Envio por e-mail

- `_pdf_boleto_bytes(conta, db)` (`email_service.py:177`).
- `enviar_documentos_cliente(...)` (`email_service.py:188`) anexa o PDF do
  boleto (`Boleto_{api_nosso_numero}.pdf`) junto com NFSe/NFe.
- `enviar_notificacao_conta(conta_id)` (`email_service.py:279`): disparada em
  background após emissão (`emitir_boleto_route`, `emitir_em_lote`). Só envia
  após NFSe autorizada e marca `email_enviado`/`data_envio_email`.

---

## 10. Telas

- `GET /sicoob/` → `templates/sicoob/index.html` (credenciais).
- `GET /sicoob/boletos` → `templates/sicoob/boletos.html` (listagem/painel).
- Formulário de credenciais: `POST /sicoob/salvar-credenciais`
  (`sicoob.py:335`) — aceita `cert_file` e `key_file` (PEM) e grava no
  `cert_store` via `store_certificate("sicoob"/"sicoob_key", emp.id, ...)`.
- Teste de token: `GET /sicoob/api/testar-token` (`sicoob.py:797`).

---

## 11. Tratamento de erros / resiliência

- Retentativas com backoff (`2**tentativa`) em 429/502/503/504 nas chamadas de
  emissão, PDF, sincronização e baixa.
- Renovação de token em `401` durante a emissão.
- `emitir_boletos_contas` captura exceções por parcela e acumula erros sem
  interromper as demais.
- Webhook e imports são defensivos (sem exceção que derrube o fluxo).

---

## 12. Arquivos envolvidos (índice)

| Arquivo | Responsabilidade |
|---------|------------------|
| `routers/sicoob.py` | toda a integração Sicoob (emissão, consulta, PDF, baixa, alteração, sync, webhook, importação, inadimplência) |
| `services/parcelamento.py` | `emitir_boletos_contas` (emissão em lote das parcelas) |
| `services/email_service.py` | anexa e envia PDF do boleto por e-mail |
| `services/cert_store.py` | armazenamento seguro do certificado Sicoob |
| `app/core/config.py` | `SICOOB_API_URL`, `SICOOB_AUTH_URL` |
| `models.py` | `ContaReceber`, `Empresa` (campos sicoob), `FormaPagamento`, `PedidoVenda`/`PedidoConsolidado` (`gerar_boleto`) |
| `routers/contas.py`, `routers/consolidacoes.py` | gatilhos de emissão automática |
| `routers/configuracoes.py` | salvamento de credenciais Sicoob |
| `templates/sicoob/*.html` | telas de credenciais e boletos |
| `386bf6bc...Sicoob-V3-3.postman_collection` | coleção Postman de referência da API |

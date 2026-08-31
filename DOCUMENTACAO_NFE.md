# Documentação NFe (Nota Fiscal Eletrônica — Modelo 55)

Documentação focada exclusivamente no módulo **NFe** do sistema de Controle de Serviços.
A NFe é emitida via **API NotaAs** (`https://platform.notaas.com.br/api/v1`) e consultada/importada
via **Distribuição DF-e da SEFAZ** (certificado A1). NFe de produtos e NFSe de serviços convivem
no mesmo pedido/OS, mas aqui só tratamos a NFe.

---

## 1. Visão Geral

| Aspecto | Detalhe |
|---------|---------|
| Backend | FastAPI (Python) |
| Emissão | API NotaAs (`POST /nfe/emitir`) — assíncrona (retorna `invoiceId`) |
| Distribuição/Importação | SEFAZ Distribuição DF-e (SOAP, certificado A1) |
| DANFE | `brazilfiscalreport` (local) ou PDF do NotaAs |
| Status | `rascunho → queued → issued` (ou `error`/`cancelled`) |
| Webhook | NotaAs notifica `POST /nfe/webhook` (secret `WEBHOOK_NFE_SECRET`) |

Fluxo típico: cria-se um **rascunho** (de pedido, OS, consolidação ou avulsa) → **revisa** →
**transmite** (chama NotaAs) → polling/webhook atualiza para `issued` → baixa DANFE/XML.

---

## 2. Modelo de Dados (`models_nfe.py`)

### Tabela `nfe` (classe `NFe`)
Principais campos:

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `numero` / `serie` | int | Numeração da nota (controlada por `empresa.ultimo_numero_nfe`) |
| `chave_acesso` | str | Chave 44 dígitos (após autorização) |
| `invoice_id` | str | ID interno da NotaAs (usado em consulta/cancelamento/CC-e) |
| `protocolo` | str | Protocolo SEFAZ |
| `cliente_id` | FK | Destinatário |
| `origem` | str | `pedido`, `os`, `consolidacao`, `assinatura`, `avulsa`, `importada` |
| `status` | str | `rascunho`, `queued`, `processing`, `issued`, `error`, `cancelled` |
| `modelo` | int | 55 (NF-e) |
| `natureza_operacao` | str | Ex.: "Venda de mercadoria" |
| `cfop` | str | CFOP padrão dos itens |
| `finalidade` | str | `normal`, `complementar`, `ajuste`, `devolucao`, `credito`, `debito` |
| `indicador_presenca` | int | 0..9 (presença do comprador) |
| `forma_pagamento` | str | dinheiro/pix/boleto/cartao_credito/... |
| `modalidade_frete` | int | **0..9** — ver seção 5 |
| `observacoes` | Text | Informações complementares (infCpl) |
| `valor_total` | Numeric | Valor total da nota |
| `xml_text` / `xml_path` | Text/str | XML autorizado persistido |
| `pdf_path` | str | Caminho do DANFE |

Relacionamentos: `itens` (NFeItem), `pedido`, `os`, `cliente`, `consolidacao`,
`cartas_correcao` (NFeCartaCorrecao).

### Tabela `nfe_itens` (classe `NFeItem`)
Item da nota: `produto_id`, `variacao_id`, `descricao`, `ncm`, `cfop`, `unidade`,
`quantidade`, `preco_unitario`, `total`.

### Tabela `nfe_cartas_correcao` (classe `NFeCartaCorrecao`) — NOVO
Histórico de Cartas de Correção Eletrônica (CC-e):

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `nfe_id` | FK | Nota vinculada |
| `sequencia` | int | 1, 2, 3... (incremental por nota) |
| `correcao` | Text | Texto da correção (15–1000 chars) |
| `protocolo` | str | Protocolo SEFAZ da CC-e |
| `chave_acesso` | str | Chave da nota |
| `status` | str | `pendente`, `issued`, `error` |
| `mensagem_retorno` | Text | Resposta bruta da API |
| `data_hora` | DateTime | Quando foi enviada |

---

## 3. Serviço de Emissão (`services/nfe_notaas.py`)

Funções principais:

- `emitir_nfe(empresa, payload)` → `POST /nfe/emitir` (sem retry — não idempotente)
- `consultar_status(empresa, invoice_id)` → `GET /nfe/invoices/{id}/status`
- `baixar_pdf(empresa, invoice_id)` → `GET /nfe/invoices/{id}/danfe`
- `baixar_xml(empresa, invoice_id)` → `GET /nfe/invoices/{id}/xml`
- `cancelar_nfe(empresa, invoice_id, motivo)` → `POST /nfe/cancelar`
- `consultar_municipios(empresa, uf)` → `GET /municipios`
- `montar_payload_nfe(...)` → monta o JSON enviado à NotaAs
- **`carta_correcao_nfe(empresa, invoice_id, correcao)`** → **NOVO**
  `POST /nfe/invoices/{id}/correcao` (CC-e síncrona, sem retry)

### Payload enviado à NotaAs (`montar_payload_nfe`)
Inclui: `modelo`, `serie`, `numero`, `finalidade`, `naturezaOperacao`, `destinoOperacao`,
`presencaComprador`, `consumidorFinal`, `dest{...}`, `items[]`, `pagamentos[]`, `cobranca{...}`,
`infCpl`, e agora também:

```json
"transporte": { "modalidadeFrete": 9 }
```

#### Cobrança / duplicatas (`<cobr>` do XML)
O contrato da NotaAs é **exatamente** este (nomes divergentes são ignorados em
silêncio: o XML sai com `<fat>` e **nenhum** `<dup>`/`<dVenc>`):

```json
"cobranca": {
  "fatura":   { "numero": "4439", "valorOriginal": 1790.00, "desconto": 0, "valorLiquido": 1790.00 },
  "parcelas": [ { "numero": "001", "vencimento": "2026-09-24", "valor": 596.68 } ]
}
```

- A chave é `parcelas` (**não** `duplicatas`) e a data é `vencimento` (**não** `dataVencimento`);
  o desconto da fatura é `desconto` (**não** `valorDesconto`).
- As parcelas vêm do `ContaReceber` vinculado (`nfe_id` → `pedido_id` → `consolidacao_id`).
  Sem conta a receber, nenhum grupo `<cobr>` é enviado (venda à vista).
- `valorOriginal`/`valorLiquido` são a **soma das parcelas**, para não cair na
  rejeição 617 (somatório das duplicatas difere do valor líquido da fatura).
- `indPag` não existe no contrato da NotaAs — é inferido pela API a partir do `tipoPagamento`.

---

## 4. Rotas (`routers/nfe.py`)

| Método | Rota | Descrição |
|--------|------|-----------|
| GET/POST | `/nfe/emitir/avulsa` | Rascunho avulso (form + submit) — **agora com frete** |
| GET/POST | `/nfe/emitir/pedido/{id}` | Rascunho a partir de pedido |
| GET/POST | `/nfe/emitir/os/{id}` | Rascunho a partir de OS |
| GET/POST | `/nfe/emitir/consolidacao/{id}` | Rascunho a partir de consolidação |
| GET | `/nfe/{id}/previa` | Revisão antes de transmitir |
| GET/POST | `/nfe/{id}/editar` | Editar rascunho — **agora com frete** |
| POST | `/nfe/{id}/transmitir` | Envia à NotaAs — **envia modalidade_frete** |
| GET | `/nfe/{id}` | Detalhe (mostra frete + histórico de CC-e) |
| GET | `/nfe/{id}/pdf`, `/xml` | Download DANFE / XML |
| POST | `/nfe/{id}/cancelar` | Cancelamento (motivo 15–255 chars) |
| GET/POST | `/nfe/{id}/carta-correcao` | **NOVO** — Carta de Correção Eletrônica |
| POST | `/nfe/{id}/sincronizar` | Consulta status na SEFAZ (notas sem invoice_id) |
| GET | `/nfe/distribuicao` | Distribuição DF-e (emitidas + recebidas) |
| POST | `/nfe/importar-xml`, `/importar-chave` | Importar NF-e |
| GET | `/nfe/recebidas` | NF-e recebidas (somos destinatário) |
| POST | `/nfe/webhook` | Webhook NotaAs (atualiza status) |

---

## 5. Modalidade de Frete (Transporte) — NOVO

Espelha o padrão do Bling/SEFAZ. Valores aceitos pela NotaAs (`transporte.modalidadeFrete`):

| Código | Significado |
|--------|-------------|
| `0` | Frete por conta do Remetente (CIF) |
| `1` | Frete por conta do Destinatário (FOB) |
| `2` | Frete por conta de Terceiros |
| `3` | Transporte Próprio do Remetente |
| `4` | Transporte Próprio do Destinatário |
| `9` | Sem ocorrência de Transporte (padrão) |

- Campo no modelo: `NFe.modalidade_frete` (default `9`).
- Selecionável no formulário **avulso** e na **edição** de rascunho.
- Enviado à NotaAs em `transporte.modalidadeFrete` no momento da transmissão.
- Exibido no detalhe da nota (`FRETE_LABELS`).
- Histórico de CC-e também exibido no detalhe.

---

## 6. Carta de Correção Eletrônica (CC-e) — NOVO

Permite corrigir campos específicos de uma NF-e **já autorizada** (não altera impostos,
emitente/destinatário ou data de saída).

Regras (SEFAZ via NotaAs):
- Só notas com `status == 'issued'` e com `invoice_id` (emitidas pela NotaAs).
- Texto entre **15 e 1000 caracteres**.
- Cada envio gera uma nova sequência (1, 2, 3...).
- Endpoint: `POST /nfe/invoices/{invoiceId}/correcao` com `{"correcao": "..."}`.

Fluxo no sistema:
1. Em **NFe detalhe** (autorizada), clica em **Carta de Correção**.
2. `GET /nfe/{id}/carta-correcao` lista o histórico e abre o formulário (próxima sequência).
3. `POST /nfe/{id}/carta-correcao` valida, envia à NotaAs, e persiste em
   `nfe_cartas_correcao` (status `issued` ou `error`, com protocolo).
4. O histórico de CC-e aparece tanto na página da carta quanto no detalhe da NFe.

---

## 7. Configuração (Empresa / `routers/nfe.py` `/nfe/config`)
Campos usados pela NFe: `notaas_api_key`, `notaas_ambiente` (1=prod, 2=homolog),
`serie_nfe`, `ultimo_numero_nfe`, `cfop_padrao`, `nfe_aliquota_federal`,
`nfe_aliquota_estadual`, certificado A1 (para Distribuição SEFAZ).

---

## 8. Migração Automática
O `lifespan` cria automaticamente a nova tabela `nfe_cartas_correcao` (`Base.metadata.create_all`)
e adiciona a coluna `modalidade_frete` na tabela `nfe` (`_add_missing_columns`). Nenhuma
migration Alembic manual é necessária para essas duas alterações.

---

## 9. Notas de Implementação
- `montar_payload_nfe` **sempre** envia `transporte.modalidadeFrete` (default 9 quando ausente).
- A CC-e é síncrona e **sem retry** (reatualizar a página não reenvia — evita duplicata).
- O cancelamento segue o mesmo padrão (sem retry) e exige `invoice_id`.
- Toda NFe autorizada gera/atualiza `ContaReceber` (cobrança) quando aplicável.

---

## 10. Regras de Impostos, Desconto, Frete e Transportadora (Atualizado)

A emissão NFe é feita via **API NotaAs** (`services/nfe_notaas.py` → `montar_payload_nfe`). O XML
é gerado pela NotaAs a partir do payload JSON; o DANFE é desenhado por `brazilfiscalreport` a partir
do XML. Não há uso de biblioteca `erpbrasil`.

### 10.1 Tributos por item (ICMS / PIS / COFINS / CST / CSOSN / CEST)
Antes, os itens eram enviados sem nó fiscal → "não saía nada nos cálculos de impostos". Agora:

- **`Empresa.crt`** (1=Simples Nacional, 2=SN Excesso, 3=Regime Normal, 4=MEI) define se o item
  usa **CST** (CRT 2/3) ou **CSOSN** (CRT 1/4).
- **`Produto`** ganhou: `cst`, `csosn`, `aliquota_icms`, `aliquota_pis`, `aliquota_cofins`,
  `cest`, `codigo_beneficio_fiscal`. Preenchidos no cadastro de itens.
- `montar_payload_nfe` envia por item: `cst`/`csosn`, `aliquotaIcms`, `aliquotaPis`, `aliquotaCofins`,
  `cest`, `codigoBeneficioFiscal`. Há consistência com o CRT da empresa (remove o campo incompatível)
  para evitar rejeição da SEFAZ.

### 10.2 Desconto (vDesc real)
O desconto global não é mais "enterrado" no preço unitário de cada item. Agora vira
`items[].desconto` (alias `vDesc`), distribuído proporcionalmente entre os itens, mantendo o
preço unitário intacto. Válido em avulsa, pedido, OS e consolidação.

### 10.3 Frete
- `NFe.valor_frete` + `NFe.modalidade_frete` (0=CIF, 1=FOB, 2=Terceiros, 3=Próprio Remetente,
  4=Próprio Destinatário, 9=Sem transporte).
- Enviado como `valorFrete` na raiz (a NotaAs distribui proporcionalmente nos itens).
- Na emissão avulsa há campo manual de frete; em pedido/OS/consolidação o frete é informado na
  **prévia** da NFe (card "Frete / Transportadora" → `POST /nfe/{id}/transporte`).

### 10.4 Transportadoras
- Nova tabela **`transportadoras`** (cadastro em `/transportadoras`) e `Transportadora` em `models.py`.
- `NFe.transportadora_id` relaciona a nota à transportadora.
- Enviado como `transporte.transportadora.{cnpj/cpf, nome, ie, endereco, cidade, uf}`.
- Selecionada no formulário de emissão avulsa ou na prévia da NFe.

### 10.5 Prévia
A `previa.html` exibe por item: Trib. (CST/CSOSN + %ICMS), Desconto, e no rodapé: Desconto Total,
Frete e Transportadora.

### 10.6 Migração
`alembic/versions/b2c3d4e5f6a7_tributos_nfe_frete_transportadora.py` cria as colunas/tabelas acima.
Obs.: a migration antiga `7a1b2c3d4e5f_vinculo_variacao_notas.py` foi removida deste repositório
pois estava quebrada e nunca havia sido aplicada (o `alembic_version` estava em `a1b2c3d4e5f6`);
sua função (FK de `variacao_id`) foi incorporada à nova migration.

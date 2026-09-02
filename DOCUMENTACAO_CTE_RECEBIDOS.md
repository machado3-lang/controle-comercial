# Importação de CT-e (Conhecimentos de Transporte) e Geração de Contas a Pagar

> Status: ANÁLISE (não implementado). Pendente de retomada.

## Objetivo

Importar Conhecimentos de Transporte Eletrônicos (CT-e, modelo 57) da SEFAZ,
de forma análoga às NFe recebidas, e a partir deles gerar **Contas a Pagar**
ao emitente (transportadora), reaproveitando o fluxo existente de NFe.

## Como funciona hoje (NFe recebidas) — base de reuso

1. `services/nfe_distribuicao.py`
   - `NFeDistribuicaoService` consulta SEFAZ via webservice **NFeDistribuicaoDFe**
     (`distDFeInt` por CNPJ da empresa).
   - Response traz `docZip` (gzip + base64) em `_parse_response`.
   - `_extract_nfe_info` extrai chave, número, dhEmi, vNF, emitente, destinatário.
   - Salva em `NFeDistribuida`.
   - Helpers reutilizáveis: `extrair_duplicatas_nfe`, `extrair_emitente_nfe`.
2. Model `NFeDistribuida` (`models.py:14`): chave_acesso, numero, dh_emi, valor,
   emitente_*, destinatario_*, nsu, schema_nfe, xml, fornecedor_id.
3. Rotas em `routers/nfe.py`:
   - `/nfe/recebidas` (lista) — `listar_nfe_recebidas`
   - `/nfe/recebidas/importar-xml`
   - `/recebidas/{id}/vincular-fornecedor`
   - `/recebidas/{id}/parcelas-info`
   - `/recebidas/{id}/gerar-conta` → usa `gerar_contas_pagar_parcelas`
     (`services/parcelamento.py`).
   - `_resolver_fornecedor_nfe_recebida`, `_url_cadastro_fornecedor_nfe`.
4. Config: `NFE_DIST_URL_PROD` / `NFE_DIST_URL_HOMOL` (`app/core/config.py:78-79`).
   Código fixa `cUFAutor=50` (MS).

## O que muda para CT-e

- **Webservice**: usar `CTeDistribuicaoDFe`
  (`https://www1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx`).
  Importante: a Distribuição DF-e é única por CNPJ e já devolve NFe, CT-e e
  MDF-e juntos — o código atual provavelmente JÁ recebe CT-e, só não os
  interpreta (o parser é NFe-específico).
- **XML do CT-e**: chave com modelo `57` (posições 21-22); emitente =
  **transportadora**; nós somos o **tomador**; valor total em
  `<vPrest><vTPrest>`. CT-e normalmente NÃO traz `<cobr>/<dup>` (duplicatas),
  então a geração de contas seria conta única ou parcelas manuais.
- **Vínculo**: CT-e é pagável à transportadora. Usar model `Transportadora`
  (já existente) ou gerar `Fornecedor` a partir dela (ContaPagar liga em
  `fornecedor_id`).

## Componentes a criar (quando retomar)

1. Model `CTeDistribuido` (espelho de `NFeDistribuida` + `transportadora_id`/
   `fornecedor_id`).
2. `services/cte_distribuicao.py`
   - Reutiliza certificado/sessão SOAP existentes.
   - Novo envelope com `tpDFe="CTe"` OU URL própria do CT-e.
   - Parser de `cteProc` para: chave, número, dhEmi, `vTPrest`, emitente
     (transportadora), tomador.
   - Helpers: `extrair_emitente_cte`, e leitura de componentes de `vPrest`.
3. Rotas + template (cópia do fluxo de NFe recebidas):
   - lista CT-e recebidos, importar XML, vincular transportadora, gerar ContaPagar.
4. Migration Alembic para a nova tabela.

## Riscos / pontos de atenção

- Confirmar URL e ambiente (prod/homolog) do CT-e para o estado usado
  (hoje fixa `cUFAutor=50` = MS).
- CT-e não traz duplicatas padronizadas → UI de parcelas deve aceitar
  entrada manual ou gerar parcela única com `vTPrest`.
- Decisão de modelo de vínculo: `Transportadora` (recomendado, já existe)
  vs criar `Fornecedor`.

## Decisão pendente (perguntar ao usuário na retomada)

- Criar tabela/service/rotas próprias (`CTeDistribuido`) — abordagem recomendada?
- Ou capturar CT-e dentro da tabela `NFeDistribuida` existente?

# Migração NFSe — Portal Betha → Ambiente Nacional

> Documento de planejamento e análise de impacto. Criado em 22/08/2026 para
> organizar a transição da emissão de NFSe via portal/cloud da Betha para o
> Ambiente Nacional (padrão nacional da LC 214 / NFS-e).
>
> ⚠️ **ATENÇÃO — DOURADOS/MS MANTÉM 01/09:** embora o Comitê Gestor do Simples
> Nacional tenha publicado a Resolução CGSN nº 191/2026 prorrogando a
> obrigatoriedade federal para **01/11/2026**, a **Prefeitura de Dourados-MS
> DECIDIU MANTER O PRAZO DE 01/09/2026**. Ou seja, para este sistema (município
> de Dourados) a migração está em vigor **a partir de hoje, 01/09/2026**.
> A prorrogação federal NÃO se aplica à decisão local do município.
>
> **Ambiente de testes (homologação nacional):**
> https://www.producaorestrita.nfse.gov.br/ — usar para validar a integração.

---

## 1. Contexto

Hoje o sistema emite NFSe através do webservice da **Betha Cloud**
(`https://nota-eletronica.betha.cloud/dps/ws`), que implementa o padrão
nacional de DPS, mas hospedado nos servidores da Betha (um "portal do
município" / software house).

A partir de **01/09/2026** (Dourados-MS manteve este prazo; a prorrogação
federal CGSN nº 191/2026 para 01/11 NÃO foi adotada pelo município) o município
deixa de aceitar emissão por esse portal e a nota passará a ser transmitida
**diretamente ao Ambiente Nacional**
(webservice oficial da Receita/Governo). O sistema precisa continuar emitindo,
consultando e cancelando normalmente — só mudando o destino da transmissão.

**Boa notícia:** o código já gera o DPS no **layout nacional** (campos
`infDPS` do padrão nacional). O acoplamento com a Betha está restrito a:
envelope/namespace SOAP, URL de destino e autenticação. A leitura (distribuição
ADN, SEFIN, Portal Nacional) **já é nacional** e funciona.

---

## 2. Arquitetura atual — o que é nacional vs. o que é Betha

| Componente | Arquivo / Função | Situação | Comentário |
|---|---|---|---|
| Geração do DPS (`infDPS`) | `gerar_dps_xml_nfse` (`services/nfse_betha.py:1348`, `gerar_dps_xml:1087`) | **Já nacional** | Campos nacionais; só o `xmlns` é Betha. |
| Transmissão do DPS | `enviar_dps` (`:137`) | **Betha** | POST em `BETHA_NFSE_URL`, envelope `<RecepcionarDpsEnvio>`, namespace `http://www.betha.com.br/e-nota-dps`. |
| Consulta de status | `consultar_status` (`:200`) | **Betha** | SOAP `<ConsultarStatusDpsEnvio>` em `BETHA_NFSE_URL`. |
| Cancelamento (evento) | `cancelar_nfse_assinado` (`:890`) | **Betha** | `<RecepcionarEventoCancelamentoEnvio>` em `BETHA_NFSE_URL`. |
| Cancelamento (fallback) | `cancelar_dps` (`:836`) | **Betha** | `<CancelarDpsEnvio>`. |
| Distribuição DF-e (ADN) | `listar_nfse_adn` (`:336`), `_varrer_dfe_adn` (`:511`) | **Nacional** | `ADN_DFE_URL = https://adn.nfse.gov.br/...` — já usado pela busca ADN. |
| Consulta SEFIN | `consultar_situacao_nfse` (`:843`), `_obter_xml_sefin` (`:587`) | **Nacional** | `ADN_NFSE_URL = https://sefin.nfse.gov.br/...` (frequente 403). |
| Portal Nacional por chave | `_obter_xml_portal_por_chave` (`:615`), `PORTAL_NFSE_URL` | **Nacional** | `https://nfse.gov.br/{chave}/xml` (mTLS). |
| Obtenção de XML nacional | `obter_xml_nacional_por_chave` (`:716`) | **Nacional** | Usa Portal/ADN/SEFIN — já implementado. |
| Geração do DANFSe/PDF | `nfse_pdf.gerar_danfse_pdf`, `_salvar_xml_nacional_e_danfse` | **Nacional-ready** | Gera a partir do XML nacional (`infNFSe`); não depende da Betha. |
| URL de PDF da Betha | `obter_danfse_url` (`:304`), `consultar_nfse_rest` (`:287`) | **Betha** | REST Fly e-Nota / `recoverpdfservlet`. Torna-se irrelevante (há fallback nacional). |

**Conclusão:** a parte de "escrita" (emissão + cancelamento via Betha) é o que
precisa ganhar um caminho nacional. A parte de "leitura" já está pronta.

---

## 3. O que muda em 01/09/2026 (Dourados manteve o prazo; prorrogação federal não aplicada)

- O destino da **emissão** e do **cancelamento** deixa de ser `BETHA_NFSE_URL`
  e passa a ser o webservice do **Ambiente Nacional**.
- O **namespace** do XML/SOAP deixa de ser `http://www.betha.com.br/e-nota-dps`
  e passa a ser o namespace oficial do Ambiente Nacional.
- A **autenticação** deixa de ser HTTP Basic (`BETHA_USUARIO`/`BETHA_SENHA`,
  `_get_session` em `:108`) e passa a ser **mTLS com o certificado A1/A3 do
  emissor** (o mesmo `.pfx` já usado — veja `load_cert_from_empresa`/cert_store).
- O conteúdo do `<infDPS>` (serviço, valores, prestador, tomador) **permanece
  idêntico** — é o mesmo padrão nacional.

---

## 4. Análise de impacto por funcionalidade

### 4.1 Emissão (`enviar_dps` + `gerar_dps_xml_nfse`)
- O `infDPS` é reaproveitável. Só trocar `xmlns="http://www.betha.com.br/e-nota-dps"`
  por `xmlns="http://www.nfse.gov.br/..."` (confirmar no manual oficial).
- Trocar a URL de `BETHA_NFSE_URL` para a do Ambiente Nacional.
- Trocar `<RecepcionarDpsEnvio>` pelo nome de operação nacional
  (`RecepcionarDps` — a confirmar).
- `verAplic` (`fly_WS_1.1.0`, `:1300`/`:1472`) provavelmente precisa mudar para
  o identificador do seu sistema no Ambiente Nacional.
- Numeração: o sistema **já controla** o `nDPS` (campo `numero` da NFSe,
  `:1525`/`:1388`); o Ambiente Nacional devolve o protocolo e o `nNFSe`. Sem
  mudança de lógica.

### 4.2 Consulta de status (`consultar_status`)
- Mesmo padrão: trocar URL + namespace + operação (`ConsultarStatusDps`).
- A extração de `<situacao><codigo>` (corrigida em 22/08) continua válida.

### 4.3 Cancelamento (`cancelar_nfse_assinado` / `cancelar_dps`)
- `cancelar_nfse_assinado` já monta `<RecepcionarEventoCancelamentoEnvio>` no
  formato nacional (`:925`) — só trocar namespace/endpoint. Esse é o caminho
  preferido no nacional.
- `cancelar_dps` (ABRASF `CancelarDpsEnvio`) é específico e provavelmente
  **não existirá** no nacional da mesma forma — manter só como fallback Betha.

### 4.4 Leitura / Distribuição / PDF
- **Sem alteração.** ADN, SEFIN, Portal Nacional e geração de DANFSe já são
  nacionais e foram validados na prática (busca ADN trouxe cancelamentos e
  notas faltantes).

---

## 5. Plano de ação (checklist técnico) — STATUS

- [x] **2. Parâmetro `NFSE_EMISSAO`** criado em `.env` (`betha` | `nacional`).
      Implementado em `services/nfse_betha.py` (constante + helper `nfse_emissao_nacional()`).
- [x] **3. Caminho nacional de emissão** implementado (reaproveitando a geração de DPS):
      - constantes `NACIONAL_NFSE_URL`, `NACIONAL_SOAP_NS`, `NACIONAL_OP_*`,
        `NACIONAL_VER_APPLIC`, `NACIONAL_DPS_VERSAO`, `NACIONAL_TOKEN`.
      - `_get_session_nacional()` (mTLS, sem Basic Auth; token opcional).
      - `enviar_dps_nacional`, `consultar_status_nacional`, `cancelar_nfse_nacional`.
      - `enviar_dps` / `consultar_status` / `cancelar_nfse_assinado` roteiam para o
        nacional quando `NFSE_EMISSAO=nacional`.
      - `gerar_dps_xml` / `gerar_dps_xml_nfse` aceitam `xmlns`/`ver_aplic` (nacional).
      - `emitir_rascunho` / `emitir_completa` marcam `nfse.origem='nacional'` no sucesso.
- [x] **4. Roteamento por origem** — notas antigas (`origem='betha'`/`'adn'`) continuam
      no caminho Betha; novas emissões nacionais usam `origem='nacional'`.
- [x] **1. Especificação técnica obtida** (via swagger dos docs nacionais):
      - **O Ambiente Nacional é REST/JSON, NÃO SOAP**, autenticado por **mTLS**
        (confirmado — SEFIN e ADN exigem certificado cliente).
      - **SEFIN = EMISSÃO/CANCELAMENTO** (`sefin.nfse.gov.br/SefinNacional`):
        - `POST /nfse` — recebe a DPS e **gera a NFS-e de forma síncrona**; body
          `{"dpsXmlGZipB64": "<gzip base64 do XML DPS>"}`; a resposta já traz
          `chaveAcesso` + `nfseXmlGZipB64` (a NFS-e).
        - `POST /nfse/{chaveAcesso}/eventos` — cancelamento (Pedido Registro Evento),
          body `{"pedidoRegistroEventoXmlGZipB64": "<gzip base64 do evento>"}`.
        - `GET /nfse/{chaveAcesso}` — consulta da NFS-e; `GET /dps/{id}` — chave a partir do DPS.
      - **ADN = CONSULTA/DISTRIBUIÇÃO** (`adn.nfse.gov.br`): `/contribuintes/DFe/{NSU}`,
        `/contribuintes/NFSe/{chave}/Eventos`, `/danfse/{chave}`. A leitura já usava ADN.
      - `NACIONAL_NFSE_URL` = base do **SEFIN**: `https://sefin.nfse.gov.br/SefinNacional`
        (produção) ou `https://sefin.producaorestrita.nfse.gov.br/SefinNacional` (restrita);
        o código acrescenta `/nfse` e `/nfse/{chave}/eventos`.
- [x] **1b. Namespace do XML nacional confirmado:** `http://www.sped.fazenda.gov.br/nfse`
      (informado pelo usuário; é o `xmlns` do `<DPS>` e do evento de cancelamento).
- [ ] **1c. Validar em teste** (restrita): `NACIONAL_OP_CANCELA` (raiz do evento de
      cancelamento — hoje `RecepcionarEventoCancelamentoEnvio`) e `NACIONAL_VER_APPLIC`
      (identificador do sistema no Ambiente Nacional, ainda a confirmar com o município).
- [x] **Configuração via UI (Config. NFSe):** adicionados campos no modelo `Empresa`
      (`nfse_emissao_ambiente`, `nfse_url_producao`, `nfse_url_homologacao`,
      `nfse_namespace`, `nfse_ver_aplic`), migração Alembic (`c3d4e5f6a7b8`),
      formulário `configuracoes.py` e aba "Config. NFSe" do template. O certificado
      usado é o A1 já cadastrado no sistema (Config. NFSe) — em modo nacional o
      serviço carrega o certificado da empresa automaticamente.
- [ ] **5. Testar** em `sefin.producaorestrita.nfse.gov.br/SefinNacional` (do ambiente que
      alcança o host) com uma nota real de teste, validando recepção, obtenção da NFS-e e
      cancelamento.
- [ ] **6. Trocar `NFSE_EMISSAO=nacional`** no `.env` após validar (ou automatizar por data).
      Mantenha `betha` até o teste em homologação passar.

> ⚠️ **Nada muda em produção até `NFSE_EMISSAO=nacional`** — o padrão continua `betha`,
> então o sistema segue emitindo via Betha hoje. O caminho nacional só ativa quando o
> município efetivamente desligar o portal Betha e os valores oficiais estiverem preenchidos.

---

## 6. Decisões de design recomendadas

### 6.1 Manter Betha para notas antigas (período de transição)
Notas emitidas até 31/08/2026 (via Betha) continuarão a existir e poderão
precisar de **consulta/cancelamento** depois de 01/09. Por isso **NÃO remova**
o caminho Betha. Use o campo `origem` (`models_nfe.py:126`, `NFSe.origem`) para
roteirar:
- `origem='betha'` → operações vão para `BETHA_NFSE_URL`.
- `origem='nacional'` (nova) → operações vão para o webservice nacional.
- `origem='adn'` (importadas pela busca ADN) → já existe; manter.

Novas emissões após 01/09 recebem `origem='nacional'`.

### 6.2 Não quebrar a busca ADN / leitura
A leitura nacional já funciona; manter como está. O único risco é o SEFIN
(`sefin.nfse.gov.br`) frequentemente retornar 403 — já contornado pelo fallback
DF-e implementado em 22/08 (`_verificar_cancelamento_adn`).

---

## 7. Riscos e pontos em aberto

- **Endpoint/namespace oficiais:** endpoint (SEFIN `POST /SefinNacional/nfse`) e modelo
  de auth (mTLS) **já mapeados**. Namespace do XML DPS/evento **confirmado**:
  `http://www.sped.fazenda.gov.br/nfse`. Falta validar em teste o elemento-raiz do
  cancelamento (`NACIONAL_OP_CANCELA`) e o `NACIONAL_VER_APPLIC`.
- **Autenticação:** confirmado mTLS com o certificado do emitente (sem token
  adicional aparente; `NACIONAL_TOKEN` previsto caso necessário).
- **Validação de schema:** pequenas diferenças de campos/versão podem gerar
  rejeição (erros tipo E0xx). Ciclo de testes em `producaorestrita` é
  obrigatório antes de virar produção.
- **Numeração:** confirmar se o Ambiente Nacional aceita o `nDPS` controlado
  pelo sistema ou se exige faixa própria.
- **Compatibilidade do município:** a migração depende do município estar
  habilitado no Ambiente Nacional. O adiamento já foi confirmado em nível
  federal (CGSN nº 191/2026 → 01/11), mas convém confirmar com Dourados/MS a
  data exata de desligamento do portal Betha do município. Manter
  `NFSE_EMISSAO=betha` até a virada.

---

## 8. Cronograma sugerido

| Quando | Ação |
|---|---|
| 22/08/2026 | Levantar manual oficial; desenhar arquitetura (doc criado). |
| 01/09/2026 | **Prazo Dourados**: município desliga portal Betha. Caminho nacional já implementado (parametrizado), porém inativo (`NFSE_EMISSAO=betha`). |
| Assim que o município fornecer os valores oficiais | Preencher `NACIONAL_*` no `.env` e testar em `producaorestrita.nfse.gov.br`. |
| Quando o município desligar o Betha (ou por determinação) | Trocar `.env` para `NFSE_EMISSAO=nacional`; monitorar primeiras emissões. |
| Pós-virada | Manter Betha só para consulta/cancelamento de notas antigas (`origem='betha'`/`'adn'`). |

---

## 9. Referências no código

- Emissão/Betha: `services/nfse_betha.py` — `enviar_dps:137`, `consultar_status:200`,
  `cancelar_nfse_assinado:890`, `cancelar_dps:836`, `gerar_dps_xml_nfse:1348`.
- Leitura nacional (já pronta): `listar_nfse_adn:336`, `consultar_situacao_nfse:843`,
  `obter_xml_nacional_por_chave:716`, `_verificar_cancelamento_adn` (21/08).
- Modelo: `models_nfe.py` — `NFSe.origem:126`.
- Constantes: `services/nfse_betha.py:30-39` (`BETHA_NFSE_URL`, `ADN_NFSE_URL`,
  `ADN_DFE_URL`, `PORTAL_NFSE_URL`).

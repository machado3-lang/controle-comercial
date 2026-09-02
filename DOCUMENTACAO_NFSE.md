# Documentação NFSe — Recebidas: Vincular Fornecedor e Gerar Conta a Pagar

Documentação focada no módulo de **NFSe recebidas** (somos o tomador/prestador de
serviço contratado) e na geração de **Contas a Pagar** a partir delas, espelhando o
que já existe para NFe recebidas. O foco aqui é a NFSe; a emissão de NFSe (Betha/ADN)
está em `NFSe.md`.

---

## 1. Visão Geral

Muitas NFSe recebidas (prestadores de serviço) não trazem, no padrão, a data de
vencimento nem duplicatas — diferente da NFe (Modelo 55), cujo XML possui o bloco
`<cobr><dup>` com vencimento. Para permitir o controle de despesas/fornecedores,
foi adicionado o mesmo fluxo da NFe, com dois ajustes:

1. **Vincular o prestador (fornecedor)** à NFSe recebida pelo CNPJ do emitente.
2. **Gerar Conta(s) a Pagar** a partir da NFSe, com o **vencimento informado
   manualmente** no popup (já que, via de regra, não vem no XML).

Quando o XML da NFSe **possui** fatura/duplicata/vencimento, o sistema extrai
automaticamente e pré-preenche as parcelas (best-effort, tolerante a layouts
diferentes).

| Aspecto | NFe recebida | NFSe recebida |
|---------|--------------|---------------|
| Vencimento no XML | Sim (`<cobr><dup>`) | Geralmente **não** |
| Geração de Conta a Pagar | Automática (duplicatas) + manual | **Manual** (popup) + extração quando houver |
| Vinculação do fornecedor | Por CNPJ do emitente | Por CNPJ do emitente |

---

## 2. Modelo de Dados (`models_nfe.py`)

### Tabela `nfse_recebida` (classe `NFSeRecebida`)

Campos relevantes para este fluxo:

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | int | PK |
| `chave_acesso` | str(100) | Chave de acesso (única) |
| `numero` | str(50) | Número da NFSe |
| `data_emissao` | DateTime | Data de emissão (usada como base p/ vencimento) |
| `valor_total` | Numeric(12,2) | Valor total da nota |
| `xml_text` | Text | XML completo da nota (fonte p/ extração de fatura) |
| `emitente_nome` | str(200) | Razão social do prestador |
| `emitente_cnpj` | str(20) | CNPJ do prestador |
| `fornecedor_id` | FK → fornecedores.id | **Prestador vinculado** (pode vir da importação ADN ou do vínculo manual) |
| `origem` | str(20) | `adn`, etc. |
| `status` / `cancelada` | str / bool | Situação da nota |

Relacionamento: `fornecedor` (`Fornecedor.back_populates="nfse_recebidas"`).

A `ContaPagar` gerada referencia o `fornecedor_id` e usa `numero_documento = nfse.numero`.
A detecção de duplicidade usa `descricao LIKE 'NFSe {numero} - {emitente}%'` + fornecedor
+ status pendente/vencido.

---

## 3. Rotas (`routers/nfse.py`)

| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/nfse/recebidas` | Lista NFSe recebidas (tomador). Aceita `?vincular={id}` para auto-vincular após cadastrar fornecedor |
| POST | `/nfse/recebidas/{id}/vincular-fornecedor` | Vincula o prestador (busca `Fornecedor` pelo `emitente_cnpj`; se não existir e o CNPJ for válido, redireciona ao cadastro pré-preenchido) |
| GET | `/nfse/recebidas/{id}/parcelas-info` | JSON com fornecedor, valor, emissão, flag de conta já existente e (quando houver) as parcelas/vencimentos extraídos do XML |
| POST | `/nfse/recebidas/{id}/gerar-conta` | Gera a(s) Conta(s) a Pagar via `gerar_contas_pagar_parcelas` |

### Funções auxiliares (em `routers/nfse.py`)

- `_resolver_fornecedor_nfse_recebida(db, rec, criar=False)` — resolve o `Fornecedor`
  pelo `rec.fornecedor_id` (se já vinculado) ou pelo `emitente_cnpj` **normalizado**
  (apenas dígitos). A normalização evita que um CNPJ já cadastrado (mas com formatação
  diferente da NFSe) passe despercebido e force um cadastro duplicado.
- `_emitente_valido_nfse_recebida(db, rec)` — True se o emitente tem CNPJ válido e
  não é a própria empresa.
- `_url_cadastro_fornecedor_nfse(rec)` — monta `/fornecedores/novo?nome=...&cpf_cnpj=...&tipo_pessoa=...&next=/nfse/recebidas?vincular={id}`
  (o formulário de fornecedor já lê esses query params e pré-preenche os campos).

### Fluxo de vínculo (`?vincular=`)

Após salvar o fornecedor a partir do cadastro pré-preenchido, o `next` volta para
`/nfse/recebidas?vincular={id}`. A listagem detecta o parâmetro, resolve o fornecedor
(acabou de ser criado) e grava `rec.fornecedor_id`, exibindo a badge "Fornec. vinculado".

---

## 4. Serviço de Extração de Fatura (`services/nfse_service.py`)

### `extrair_fatura_nfse(xml: str) -> list`

Varredura **namespace-agnóstica** (usa `xml.etree.ElementTree`, ignora prefixos) em
busca de blocos de fatura/parcela e das respectivas datas de vencimento e valores.

- Blocos considerados: `<fat>`, `<fatura>`, `<dup>`, `<duplicata>`, `<parcela>`, `<cobr>`.
- Também detecta uma tag de vencimento "solta" no documento (fallback de nível raiz).
- Para cada parcela, captura:
  - **número**: tag contendo `num|fat|dup|parcela`
  - **vencimento**: tag contendo `venc` (formatos `YYYY-MM-DD`, `DD/MM/YYYY`, `YYYYMMDD`)
  - **valor**: tag contendo `val`, ou padrões `vDup`/`vFat`/`vParcela` (trata vírgula decimal)
- Ordena por vencimento e remove duplicatas idênticas.
- Retorna `[]` quando não há informação de vencimento → o popup abre em modo manual.

Exemplo de retorno:

```python
[
  {"numero": "1", "vencimento": date(2026, 9, 10), "valor": Decimal("250.50")},
  {"numero": "2", "vencimento": date(2026, 10, 10), "valor": Decimal("100.00")},
]
```

> Como o layout da NFSe varia entre municípios (ABRASF, Ginfes, Betha, Padrão
> Nacional, etc.), a extração é *best-effort*. Se um município usar nomes de tag
> diferentes, basta estender as listas de padrões em `extrair_fatura_nfse`.

---

## 5. Frontend (`templates/nfse/recebidas.html`)

Cada linha da listagem de recebidas ganhou dois botões:

- **Vincular Fornecedor** (`link`): POST para `vincular-fornecedor`.
- **Gerar Conta a Pagar** (`banknote`): abre o modal de parcelas.

### Modal "Gerar Conta a Pagar"

Comportamento (idêntico ao da NFe, adaptado):

1. `fetch('/nfse/recebidas/{id}/parcelas-info')` carrega fornecedor, valor e emissão.
2. Se o **fornecedor não está cadastrado**: mostra aviso e botão "Cadastrar Fornecedor"
   (pré-preenche o CNPJ/nome no cadastro). Não gera conta até o vínculo.
3. Se já existe conta a pagar pendente para esta NFSe: avisa e pede confirmação.
4. **Com fatura/vencimento no XML**: pré-preenche as parcelas (nº, vencimento, valor)
   lidas do XML e esconde o divisor manual.
5. **Sem vencimento no XML** (caso comum): abre com uma parcela única, vencimento
   pré-preenchido com a **data de emissão** (editável) e permite dividir em N parcelas
   informando 1º vencimento e intervalo em dias. O usuário informa o vencimento manualmente.
6. Valida a soma das parcelas contra o valor total da NFSe antes de enviar.

A submissão vai para `POST /nfse/recebidas/{id}/gerar-conta`, que cria as
`ContaPagar` via `services.parcelamento.gerar_contas_pagar_parcelas`.

---

## 6. Regras de Negócio

- **Vínculo**: prioriza `fornecedor_id` já existente; senão busca `Fornecedor.cpf_cnpj == emitente_cnpj`. Emitente sem CNPJ ou igual à própria empresa não pode ser vinculado.
- **Duplicidade**: não gera conta de novo se já houver `ContaPagar` pendente/vencida com a mesma descrição+fornecedor (a menos que o usuário confirme).
- **Valor total**: usado como base quando não há parcelas informadas/extraídas (conta única com vencimento = emissão).
- **Vencimento manual**: quando o XML não traz a data, o campo fica em branco (pré-preenchido com a emissão) para preenchimento do usuário — exatamente o comportamento solicitado.

---

## 7. Como estender (novo layout de município)

Se uma NFSe trouxer o vencimento em tags diferentes das reconhecidas, ajuste:

- `blocos_tags` em `extrair_fatura_nfse` (adicionar o nome do bloco);
- os critérios de detação de `numero`, `vencimento` (`'venc' in k`) e `valor`
  (`'val' in k` ou prefixos `vd/vf/vp`).

---

## 8. Manutenção de CEP / IBGE do Tomador (emissão)

A emissão de NFSe (Ambiente Nacional SEFIN) **rejeita com "CEP do cliente não é
válido"** quando o CEP não bate com o município. O ponto que falta na prática é o
**`codigo_ibge`** do tomador (cliente/fornecedor/empresa): quando ele está vazio, o
sistema cai no fallback do município da própria empresa (Dourados) e o CEP passa a
não bater. O formato do CEP em si (com ou sem máscara) não é o gatilho — o código de
emissão normaliza para 8 dígitos.

### Preencher em massa (`scripts/corrigir_ceps.py`)

Corrige CEP (máscara `00000-000`) e preenche `codigo_ibge` em `clientes`,
`fornecedores`, `empresa` e `transportadoras`:

```powershell
# Da máquina local (precisa de internet p/ ViaCEP + IBGE), apontando p/ o banco do Railway:
python scripts/corrigir_ceps.py --apply --enriquecer-ibge --db "postgresql://USUARIO:SENHA@HOST:PORTA/railway?sslmode=require"
```

- Tenta primeiro o **ViaCEP** pelo CEP; se não achar (CEP geral / logradouro novo),
  usa **cidade+estado** cruzando com a base de municípios do IBGE.
- Idempotente: rodar de novo não duplica nem sobrescreve o que já está certo.
- Dry-run sem `--apply` mostra o que seria alterado.

### Autocomplete de CEP no cadastro (ViaCEP)

O backend no Railway **não tem saída de rede** (egress bloqueado), então as rotas
`/api/consultas/cep` e `/api/consultas/cnpj` falham. Os formulários de cliente e
fornecedor (`templates/clientes/form.html`, `templates/fornecedores/form.html`)
chamam o **ViaCEP direto do navegador** (`https://viacep.com.br/ws/{cep}/json/`), que
preenche endereço e `codigo_ibge` ao sair do campo de CEP.

> Atenção: qualquer lógica de enriquecer dados externos (ViaCEP, CNPJ, IBGE) que
> rode **no servidor** vai falhar no Railway por falta de internet. Faça no browser
> ou em script executado localmente.

### Gotcha da API do IBGE

No endpoint completo `GET /api/v1/localidades/municipios`, o `uf` **não** vem no
nível superior do município — ele está aninhado em
`microrregiao.mesorregiao.UF.sigla` (ou `regiao-imediata.regiao-intermediaria.UF.sigla`).
Quem baixar a base para cruzar nome→IBGE precisa extrair a UF desse caminho.

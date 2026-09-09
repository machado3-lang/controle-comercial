# Documentação — NFSe de Consolidação: geração, 500 e regerar rascunhos

Este documento cobre as correções feitas no fluxo de **emissão de NFe/NFSe a partir de
consolidações** (`routers/nfse.py::emitir_consolidacao_nfse`), no salvamento do rascunho
da NFSe (`editar_nfse_salvar`) e nos helpers de explosão de itens (`services/nfe_notaas.py`).

Commits relacionados (branch `master`):

- `a6eb4a3` — NFSe de consolidação: itens de kit, `valor_total` consistente, cliente e desconto
- `ffa59fb` — Corrige 500 ao emitir/salvar NFSe de consolidação (imports + try/except no salvar)
- `933235c` — Detecta rascunhos existentes (NFe/NFSe), opções manter/regerar e correção de `Decimal * float`

---

## 1. Problema: valor alto / desconto negativo / sem cliente na NFSe

### Sintoma
Ao emitir o rascunho da NFSe de uma consolidação, ela era gerada (a) sem o nome do
cliente, (b) com um valor alto "aleatório" e (c) com um desconto negativo inexistente
(exibido em `templates/nfse/editar.html`).

### Causa raiz
Em `services/nfe_notaas.py::_explodir_kit`, quando um **kit** continha um **serviço** como
insumo, o serviço era anexado a `itens_nfse` como o objeto `Produto` cru
(`itens_nfse.append(insumo)`), sem `total`/`produto_id`. Isso fazia a geração da NFSe
salvar itens zerados/ausentes → o cabeçalho (`valor_total`) divergia da soma dos itens
→ "desconto negativo" e valor inconsistente.

### Correção (`a6eb4a3`)
- `_explodir_kit` (nfe_notaas.py ~495): serviço insumo do kit agora vira um item
  normalizado (dict com `produto_id`, `descricao`, `quantidade`, `preco_unitario`,
  `total`, `produto`), igual às folhas de produto.
- `emitir_consolidacao_nfse` (nfse.py): o loop de geração da NFSe **normaliza** itens
  (objeto `PedidoConsolidadoItem` OU dict do kit) e calcula `valor_total` a partir dos
  **mesmos itens salvos** (`sum(s["total"] for s in servicos_norm)`), garantindo
  cabeçalho == soma dos itens (fim do desconto negativo). A validação de LC116 também
  passou a tratar dicts.
- Fallback de cliente: se `consolidacao.cliente` estiver vazio, deriva o cliente do
  primeiro pré-pedido (`consolidacao.pedidos[0]`), evitando NFSe sem cliente.
- `templates/nfse/editar.html`: `desconto_atual` limitado a `max(0, …)`, nunca negativo.

---

## 2. Problema: Erro 500 ao emitir/salvar

### Causa raiz
A rota `emitir_consolidacao_nfse` referenciava dois símbolos **não importados**, o que
disparava `NameError` sempre que a consolidação tinha **produtos** (caminho da NFe):

- `_limpar_doc` (validação de IE do cliente) — usado na linha ~974.
- `NFe` / `NFeItem` (criação do rascunho da NFe) — usados na criação do `NFe(...)`.

Como o `except` da rota capturava o erro e redirecionava, o usuário via a página de
emissão voltar com erro — e qualquer erro de banco no **salvar** virava 500 genérico
(não havia `try/except` nas operações de `db`).

### Correção (`ffa59fb`)
- Importados `_limpar_doc`, `NFe`, `NFeItem` em `routers/nfse.py`.
- `editar_nfse_salvar` (salvar rascunho): operações de banco envolvidas em `try/except`
  que faz `db.rollback()` e retorna mensagem legível (`"Erro ao salvar rascunho: …"`)
  em vez de 500.

---

## 3. Melhoria: detectar rascunhos existentes (manter / regerar)

Solicitado pelo usuário: ao reemitir uma consolidação, o sistema deve identificar se já
existe NFe/NFSe e perguntar se quer **manter** o existente ou **regenerar**.

### Regra de status (definida em `_RASCUNHO = {"rascunho", "erro"}`)
- **Rascunho/erro** → regenerável.
- **Autorizada / pendente / em_processamento / cancelada** → **trancada**: nunca é
  regenerada; é sempre mantida. (Quando o rascunho é transmitido, o status vira
  `autorizada` e aí não se recria.)

### Comportamento na tela (`templates/nfe/emissao_consolidacao.html`)
A rota GET (`pagina_emitir_consolidacao`) passa `nfe_existente` e `nfse_existente`.
A tela mostra cards de status e, conforme o caso:

- **Ambas autorizadas** → mensagem "já foram transmitidas. Não é possível regerar."
  (sem botão de geração).
- **Existe algum rascunho** → aviso + dois botões:
  - **"Manter existentes e gerar faltantes"** (`acao=manter`)
  - **"Regenerar rascunhos existentes"** (`acao=regerar`)
- **Nada existe** → botão único "Salvar Rascunhos" (`acao=manter`).

### Lógica na rota POST (`emitir_consolidacao_nfse`)
```python
gerar_nfe  = bool(itens_nfe)  and (not nfe_autorizada)  and (acao_regerar if nfe_rascunho  else True)
gerar_nfse = bool(itens_nfse) and (not nfse_autorizada) and (acao_regerar if nfse_rascunho else True)
```
- `acao=manter`: gera apenas o que está **faltando**; mantém rascunhos existentes.
  (Ex.: NFe existe, só NFSe → gera só a NFSe.)
- `acao=regerar`: **apaga somente** os rascunhos existentes (nunca os autorizados) e recria.
- Se nada for gerado (tudo já existe) → redireciona com a mensagem
  "NFe/NFSe da consolidação já existem; nenhum rascunho novo foi gerado."

> Antes desta melhorha a rota bloqueava com `raise 400` se já houvesse NFSe, e **nunca**
> checava a NFe — o que criava NFe duplicada ao reemitir. Agora ambas são verificadas.

---

## 4. Correção de borda: `Decimal * float` ao reemitir

### Causa
Em `services/nfe_notaas.py` e nas rotas de emissão, os totais eram calculados com
`preco * quantidade` onde `preco` vem do banco como `Decimal` (coluna `Numeric`) e
`quantidade` como `float`. Após o primeiro `commit` (que expira os atributos via
`expire_on_commit`), o `preco` é recarregado como `Decimal` → `TypeError:
unsupported operand type(s) for *: 'float' and 'decimal.Decimal'`.

### Correção (`933235c`)
Toda multiplicação de valores passou a usar `Decimal(str(valor))`:
- `nfe_notaas.py`: soma/escala de folhas do kit e `total` do serviço insumo do kit.
- `routers/nfse.py`: `total_nfe` e `total` do `NFeItem` (rota de consolidação).
- `routers/nfe.py`: soma do `total_nfe` e `total` do `NFeItem` (pedido / OS /
  consolidação).

---

## 5. Como testar

1. Consolidação finalizada com **produtos + serviços + kit (com insumo serviço)**:
   - Emitir → NFe + NFSe criados; `valor_total` da NFSe == soma dos itens (sem desconto
     negativo); cliente preenchido.
2. Reabrir "Emitir NFe/NFSe" da mesma consolidação:
   - Deve mostrar cards de status e os botões **Manter / Regenerar**.
   - Clicar **Manter** → não duplica (conta de NFe/NFSe continua 1/1).
   - Clicar **Regenerar** → apaga rascunhos e recria com novos IDs.
3. Transmitir a NFSe (status `autorizada`) e reabrir a emissão:
   - NFSe autorizada aparece como "Autorizada/emitida"; não há botão de regerar para ela;
     reemitir "Manter" não a recria.
4. Salvar o rascunho da NFSe (`/nfse/{id}/editar`): qualquer erro de banco aparece como
   mensagem legível, não 500.

---

## 6. Arquivos alterados

- `routers/nfse.py` — `pagina_emitir_consolidacao` (GET), `emitir_consolidacao_nfse`
  (POST: detecção de rascunhos, flags `gerar_nfe`/`gerar_nfse`, deleção de rascunhos,
  normalização de itens, `total_nfe`/`total` do NFeItem), `editar_nfse_salvar`
  (try/except), imports `_limpar_doc`/`NFe`/`NFeItem`.
- `routers/nfe.py` — `total` do `NFeItem` (pedido/OS/consolidação) em `Decimal`.
- `services/nfe_notaas.py` — `_explodir_kit` (item serviço normalizado; soma/escala em
  `Decimal`); import de `Decimal`.
- `templates/nfe/emissao_consolidacao.html` — cards de status + botões Manter/Regenerar.
- `templates/nfse/editar.html` — `desconto_atual` não negativo.

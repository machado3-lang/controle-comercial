# Resolução — Emissão de NFSe no Ambiente Nacional (SEFIN)

Documentação da sessão de debug que tornou a emissão de NFSe via **Ambiente Nacional (SEFIN)**
operacional no Railway, após a descontinuação do emissor Betha/Dourados em 01/09.

---

## 1. Sintoma inicial

Ao tentar emitir NFSe no "ambiente nacional", o SEFIN retornava erro de validação XSD (`[E001]`):

```
cvc-complex-type.3.2.2: O atributo 'Id' não pode aparecer no elemento 'infDPS'.
cvc-complex-type.4: O atributo 'id' deve aparecer no elemento 'infDPS'.
cvc-complex-type.2.4.d: Conteúdo inválido encontrado ao iniciar com o elemento 'pAliq'.
Nenhum elemento filho é esperado neste ponto.
```

Hipóteses descartadas durante a investigação:
- **Fuso horário** (`dhEmi` 4h adiantado): não causa erro de XSD; é bug separado (ver seção 4).
- **Alíquota / Simples Nacional / `pAliq`**: em localhost a mesma nota autorizou **com** a alíquota
  preenchida, então o `pAliq` não era o vilão para este cliente.
- **Namespace/tipo de dado do `Id`**: validei o DPS contra o schema oficial do repo
  (`notas/DPS_v1.01.xsd`) e um DPS correto (atributo `Id` maiúsculo, sem namespace, `pAliq` após
  `tpRetISSQN`) **valida OK**.

---

## 2. Causa raiz real #1 — modo de emissão errado (Betha em vez de SEFIN)

O log mostrava que o Railway emitia para **Betha/Dourados**, não para o SEFIN:

```
Enviando DPS para Betha (tpAmb=1)...
URL: https://nota-eletronica.betha.cloud/dps/ws
<DPS xmlns="http://www.betha.com.br/e-nota-dps" ...>
```

Motivo: a variável de ambiente **`NFSE_EMISSAO` não existia no Railway**, então o código caía no
default `'betha'` (`services/nfse_betha.py`):

```python
NFSE_EMISSAO = os.getenv('NFSE_EMISSAO', 'betha').strip().lower()
```

Como Betha/Dourados foi descontinuado em 01/09, o validador da Betha rejeitava o DPS com os
mesmos erros de XSD.

**Correção:** adicionar no Railway:
```
NFSE_EMISSAO=nacional
```

---

## 3. Causa raiz real #2 — `dhEmi` posterior ao processamento (E0008)

Após ajustar para nacional, o SEFIN passou a responder:

```
E0008: A data de emissão da DPS não pode ser posterior à data do seu processamento.
```

O container do Railway roda em **UTC**. O rascunho grava `data_emissao` com `datetime.now()`
(valor UTC), mas o sistema trata datas salvas como locais. Resultado: o `dhEmi` acabava **4 horas
no futuro** e o buffer de 3 min não cobria 4h.

**Correções:**
1. Variável de ambiente no Railway: `TZ=America/Cuiaba` (Dourados = UTC‑4). Faz `datetime.now()`
   retornar o horário local.
2. No código, margem de segurança de 3 min no `dhEmi` (commit `72aea49`), em ambos os geradores:
   `services/nfse_betha.py` → `gerar_dps_xml` e `gerar_dps_xml_nfse`:
   ```python
   data_emissao = (datetime.fromtimestamp(time.time(), tz=timezone.utc)
                   + timedelta(hours=offset_fuso) - timedelta(minutes=3)).replace(tzinfo=None)
   ```
3. (Opcional, correção de representação) `fuso_horario = -4` em **Config. NFSe / Config. NFe**
   (é a mesma coluna `Empresa.fuso_horario` que o NFSe lê).

---

## 4. Causa raiz real #3 — `origem` sobrescrito com `'nacional'`

NFSe gerada a partir de **assinatura** aparecia como **Avulsa** no lugar de **Assinatura**.
Ao investigar, o `origem` no banco estava como `'nacional'`.

Em `services/nfse_betha.py` a emissão nacional fazia:
```python
if getattr(nfse, 'origem', None) != 'nacional':
    nfse.origem = 'nacional'
    db.commit()
```
Isso apagava o vínculo real (`assinatura`, `pedido`, etc.). Nada no sistema lê `origem == 'nacional'`
(a geração do DANFSe nacional usa o *namespace do XML*, não o `origem`), então a atribuição só
prejudicava.

**Correção (commit `20c6734`):** removidas as duas atribuições `nfse.origem = 'nacional'`.

> Detalhe de visualização: o template `templates/nfse/lista.html` compara `n.origem` contra strings
> **minúsculas** (`"assinatura"`, `"avulsa"`, ...). Qualquer valor não reconhecido (ex.: `'nacional'`
> ou `'Assinatura'` com maiúscula) cai no `{% else %}` e exibe **"Avulsa"**. Por isso a edição manual
> com `"Assinatura"` (maiúsculo) não refletia — o valor certo no banco é `'assinatura'` (minúsculo).

---

## 5. Alterações em código (commits)

| Commit | Arquivo(s) | O que fez |
|--------|-----------|-----------|
| `d840d78` | `services/nfse_betha.py`, `requirements.txt`, `requirements_nfse.txt` | Adicionada `_normalizar_id_sem_namespace()` (garante `Id` do `infDPS` sem namespace, defensiva) + log do tag `infDPS` assinado; fixadas versões `lxml==6.1.1`, `signxml==5.0.1`, `cryptography==48.0.0`. |
| `72aea49` | `services/nfse_betha.py` | Margem de 3 min no `dhEmi` (evita E0008) em `gerar_dps_xml` e `gerar_dps_xml_nfse`. |
| `20c6734` | `services/nfse_betha.py` | Remove `nfse.origem = 'nacional'` na emissão SEFIN (mantém vínculo assinatura/pedido). |

> Observação: em `d840d78` houve um ajuste de indentação (a helper havia fechado prematuramente a
> classe `BethaNfseService`); foi corrigido antes do commit — o `import` confirma que
> `_assinar_xml` continua método da classe e a helper é module-level.

---

## 6. Variáveis de ambiente necessárias no Railway

| Var | Valor | Por quê |
|-----|-------|---------|
| `NFSE_EMISSAO` | `nacional` | Sem isso, cai no default `betha` (descontinuado). |
| `TZ` | `America/Cuiaba` | Container roda UTC; sem isso, `dhEmi` e datas ficam 4h tortas. |
| `CERT_MASTER_KEY` / `CERT_PASSWORD` | (já existentes) | Certificado A1 para assinar a DPS. |
| `NACIONAL_NFSE_URL` / `NACIONAL_DPS_NS` / `NACIONAL_VER_APPLIC` | defaults OK | `sefin.nfse.gov.br`, `http://www.sped.fazenda.gov.br/nfse`, `fly_WS_1.1.0`. |

No **Config. NFSe** (banco da empresa): `nfse_namespace` = `http://www.sped.fazenda.gov.br/nfse`,
URL de produção do SEFIN, ambiente (produção/homologação → `tpAmb`) e **certificado A1** cadastrados.
`fuso_horario = -4` em **Config. NFe** (compartilha `Empresa.fuso_horario`).

---

## 7. Numeração

`_proximo_numero` (`routers/nfse.py`) incrementa `empresa.ultimo_numero_nfse` ao criar a NFSe.
A tentativa falha da #2 já deixou o contador em **2**, então a próxima emissão é a **#3** (sem
conflito com a #2 já autorizada na SEFIN).

---

## 8. Corrigir um registro já emitido que ficou com `origem` errado

Se uma NFSe já autorizada aparecer como "Avulsa" por ter `origem='nacional'`, corrija no banco
**usando minúsculo**:

```sql
UPDATE nfse SET origem='assinatura' WHERE id=<id_da_nfse> AND assinatura_id IS NOT NULL;
```

O `assinatura_id` já está linkado; só o rótulo de origem estava errado.

---

## 9. Fluxo final validado

1. Railway com `NFSE_EMISSAO=nacional` + `TZ=America/Cuiaba`.
2. Gerar rascunho a partir de assinatura (origem=`assinatura`, `assinatura_id` preenchido).
3. Emitir → SEFIN autoriza (E0008 não ocorre porque `dhEmi` está em horário local e com margem).
4. NFSe #3 autorizada com sucesso e exibida corretamente como **Assinatura**.

---

## 10. Lições / pontos de atenção

- O erro de XSD `Id`/`id` + `pAliq` era, neste caso, consequência de estar batendo no validador
  **Betha** (modo errado), não um problema de schema nacional.
- Sempre checar o **modo de emissão** (`NFSE_EMISSAO`) antes de debugar o XML.
- Containers cloud rodam em UTC: usar `TZ` ou converter explicitamente todas as datas.
- O campo `origem` é para marcar a **origem do documento** (assinatura/pedido/os/...), não o canal
  de emissão — não sobrescrevê-lo com o modo (`'nacional'`/`'betha'`).
- Ao editar `origem` manualmente, usar o valor exato em minúsculas esperado pelo template.

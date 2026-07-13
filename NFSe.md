# NFSe - Nota Fiscal de Serviço Eletrônica

## Status: ⏸️ DESACTIVADO TEMPORARIAMENTE

Devido a instabilidades no ambiente Betha (prefeitura Dourados-MS) e à migração obrigatória para o **Ambiente Nacional DPS** (iniciando 01/09/2026 para empresas optantes pelo Simples Nacional), a funcionalidade está desativada até nova definição.

## Contexto

- **Cidade**: Dourados-MS
- **CNPJ**: 13.133.714/0001-10
- **Regime**: Simples Nacional
- **Provedor**: Betha Sistemas (até 01/09/2026)
- **Futuro**: SEFAZ Nacional - Padrão DPS

## Implementação Atual

### Estrutura de Arquivos

| Arquivo | Função |
|---------|--------|
| `models_nfe.py` | Modelos `NFSe` e `NFSeItem` |
| `routers/nfse.py` | Rotas: listar, pedidos-serviço, emissão, detalhe |
| `services/nfse_betha.py` | Serviço: geração XML DPS, envio SOAP, consulta status |
| `templates/nfse/*.html` | Templates: lista, emissão, detalhe |

### Tabela `nfse`

```sql
CREATE TABLE nfse (
    id INTEGER PRIMARY KEY,
    pedido_id INTEGER REFERENCES pedidos_venda(id),
    numero VARCHAR(20),
    codigo_verificacao VARCHAR(20),
    data_emissao TIMESTAMP,
    status VARCHAR(20) DEFAULT 'pendente',
    xml_path VARCHAR(500),
    pdf_path VARCHAR(500),
    mensagem_retorno TEXT,
    valor_total FLOAT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

### Endpoints Implementados

```
GET  /nfse/                 - Lista todas as NFSe
GET  /nfse/pedidos-servico   - Lista pedidos sem NFSe vinculada
GET  /nfse/emitir/{pedido_id} - Página de emissão
POST /nfse/emitir/{pedido_id} - Envia NFSe para Betha
GET  /nfse/detalhe/{nfse_id}  - Detalhes da NFSe
```

### Credenciais (.env)

```bash
# Portal Fly Notas
BETHA_USUARIO=50087320134
BETHA_SENHA=Multi123com

# Certificado Digital A1
CERT_PATH=./certs/certificado.pfx
CERT_PASSWORD=8853

# Identificadores
MUNICIPIO_CODIGO=5003702  # Dourados-MS
BETHA_CNPJ=13133714000110

# Endpoint DPS Nacional
BETHA_NFSE_DPS_URL=https://nota-eletronica.betha.cloud/dps/ws
```

## Especificações Técnicas

### Formato DPS (Documentos PS)

- **Schema**: `http://www.betha.com.br/e-nota-dps`
- **Versão**: 1.01
- **ID DPS**: `DPS + cMun(7) + série(1) + CNPJ(14) + 0000 + série(1) + nDPS(15)` = 45 chars

### Serviços LC116

| Código | Descrição |
|--------|-----------|
| 010101 | Serviços de internet |
| ... | Ver manual Betha |

### Estrutura XML Gerada

```xml
<DPS xmlns="http://www.betha.com.br/e-nota-dps" versao="1.01">
   <infDPS id="[ID_DPS]">
      <tpAmb>1</tpAmb>
      <dhEmi>2024-XX-XXTXX:XX:XX</dhEmi>
      <serie>1</serie>
      <nDPS>NNNNNNNNNNNNNNN</nDPS>
      <dCompet>2024-XX-XX</dCompet>
      <prest>
         <CNPJ>[CNPJ_PRESTADOR]</CNPJ>
         <regTrib>
            <opSimpNac>1</opSimpNac>
            <regEspTrib>0</regEspTrib>
         </regTrib>
      </prest>
      <toma>
         <CNPJ/CPF>[CPF_CNPJ_TOMADOR]</CNPJ/CPF>
         <xNome>[NOME_TOMADOR]</xNome>
      </toma>
      <!-- Serviços e valores -->
   </infDPS>
</DPS>
```

## Mudanças Previstas (01/09/2026)

### Ambiente Nacional - Simples Nacional

- **Novo endpoint**: a definir pela SEFAZ Nacional
- **Nova versão DPS**: 2.0
- **LC116 obrigatório**: códigos de serviços validados
- **IBGE**: validação de municípios

## Verificação de Funcionamento

### Testar Conexão Betha

```bash
curl -X POST https://nota-eletronica.betha.cloud/dps/ws \
  -H "Content-Type: text/xml; charset=utf-8" \
  --cert ./certs/certificado.pfx:password \
  -u usuario:senha \
  -d @teste_dps.xml
```

### Logs

Verificar console do FastAPI:
```
INFO: Enviando DPS para Betha (tpAmb=1)...
INFO: Protocolo recebido: XXXXXXXX
```

## Reativação

1. Verificar novo endpoint SEFAZ Nacional
2. Atualizar `services/nfse_betha.py`
3. Validar códigos LC116
4. Testes em ambiente de homologação
5. Reativar rotas e templates

---

**Última atualização**: Julho/2026
**Responsável**: Equipe Controle Comercial
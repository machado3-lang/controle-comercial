# Documentação de Backup e Restore

Este documento descreve o estado real do backup do sistema "Controle de Serviços",
suas limitações conhecidas e o inventário dos artefatos de backup encontrados no projeto.

## 1. Como funciona hoje

### 1.1 Backup/Restore via interface web (principal)
- Módulo: `services/backup.py`
  - `generate_backup()` — lê 21 tabelas em ordem (`TABLES_IN_ORDER`) e gera um JSON.
  - `restore_backup(backup_dict)` — restaura com `upsert`, normalização de enums e
    savepoints por linha (PostgreSQL e SQLite).
- Endpoints em `routers/configuracoes.py`:
  - `GET /configuracoes/backup` — gera e baixa `backup_AAAAMMDD_HHMMSS.json`.
  - `POST /configuracoes/restore` — recebe um `.json` e restaura.
- Acesso: apenas administradores (`verificar_admin`).

### 1.2 Backup manual de NFSe
- Script: `scripts/backup_nfse.py` — exporta apenas `nfse` e `nfse_itens` para
  `backups/nfse_backup_AAAAMMDD_HHMMSS.json`. Execução manual (`python scripts/backup_nfse.py`).

### 1.3 Backup bruto do banco (Railway)
- Arquivo: `backup-rlw/backup_railway.sql` — dump PostgreSQL cru de uma instância
  Railway. Gerado manualmente, fora do código.

## 2. O que está incluído no backup principal
`marcas_produto, categorias_produto, cfop_natureza, usuarios, clientes, fornecedores,
contas_pagar, contas_receber, produtos, produto_variacoes, produto_composicao,
assinaturas, assinaturas_historico, pedidos_venda, pedidos_venda_itens, ordens_servico,
nfe, nfe_itens, nfse, nfse_itens, empresa`

## 3. Limitações conhecidas
1. **Sem automação nativa no banco.** Há agora um scheduler em `services/backup_scheduler.py`
   (desligado por padrão; ative via `/configuracoes/backup-config`). Ele grava em disco e
   aplica retenção. Não há still snapshot em nível de DB/transactional.
2. **Restore "sobrepor" é "merge".** O modo `sobrepor` (padrão) não remove dados ausentes
   no backup. Para recuperação real, use o modo **`limpar`** (veja seção 6).
3. **Não há checagem de schema.** O restore assume que o schema do banco bate com o JSON.
   Deriva colunas da primeira linha (`rows[0]._fields`); tabelas vazias viram `[]`.
4. **Sem transação global no restore.** O commit é por tabela; uma falha deixa o banco
   parcialmente restaurado. Não há dry-run.
5. **Múltiplos mecanismos fragmentados** (web JSON, script NFSe, dump SQL) sem padrão único.

## 4. Inventário de artefatos de backup no projeto
| Arquivo | Tipo | Status |
|---|---|---|
| `services/backup.py` | Código (export/import full DB) | Em uso |
| `routers/configuracoes.py` | Endpoints web | Em uso |
| `scripts/backup_nfse.py` | Script NFSe | Manual, legado |
| `backups/nfse_backup_*.json` | Dados NFSe | Manual, contém PII |
| `backup-rlw/backup_railway.sql` | Dump PostgreSQL | Manual, fora do código |
| `backup.json` (raiz) | Export full obsoleto (03/06) | Obsoleto, contém PII |
| `routers/contas.py.backup2` | Cópia de código (não dado) | Lixo, deve ser removido |

> Atenção: `backup.json`, `backups/nfse_backup_*.json` e `backup_railway.sql` contêm
> dados sensíveis (CPF/CNPJ, hashes de senha). Não devem ser commitados no git.

## 5. Backup automático (agendamento)
Implementado em `services/backup_scheduler.py`, com scheduler iniciado no
`lifespan` da aplicação (`app/core/lifespan.py`).

- Configuração persiste em `backup_config.json` (gitignored):
  - `enabled` (bool, padrão `false`)
  - `interval_hours` (1–8760, padrão `24`)
  - `retention` (nº de backups automáticos mantidos, padrão `7`)
  - `directory` (padrão `backups`)
- O scheduler roda em loop asyncio: quando `enabled`, gera o backup e grava em
  `backups/auto_backup_AAAAMMDD_HHMMSS.json`, aplicando retenção (remove os
  mais antigos além de `retention`). Um primeiro backup é disparado ~30s após
  subir, se habilitado.
- Em testes (`ENVIRONMENT=testing`) o scheduler NÃO é iniciado.

### Endpoints (admin)
| Método | Rota | Função |
|---|---|---|
| `GET` | `/configuracoes/backup-config` | lê config do agendamento |
| `POST` | `/configuracoes/backup-config` | salva config (forms: `enabled`, `interval_hours`, `retention`) |
| `POST` | `/configuracoes/backup-salvar` | backup manual em disco (`backups/manual_backup_*.json`) |
| `GET` | `/configuracoes/backups` | lista arquivos de backup em disco |
| `GET` | `/configuracoes/backup-arquivo?nome=` | baixa um arquivo de backup |
| `POST` | `/configuracoes/backup-arquivo-excluir` | apaga um arquivo de backup |

### Interface
Na aba **Backup** da tela de Configurações há controles para: ativar/salvar o
agendamento (intervalo e retenção), disparar um backup manual em disco, listar
os backups armazenados (com botões de baixar/excluir) e escolher o **modo**
(`sobrepor`/`limpar`) no restore.

## 6. Restore: modos "sobrepor" e "limpar"
O endpoint `POST /configuracoes/restore` aceita o parâmetro `modo` (form):

- **`sobrepor`** (padrão): `upsert` por `id`. Linhas ausentes no backup permanecem
  no banco (comportamento de "mesclagem"). Não remove dados.
- **`limpar`**: antes de restaurar, **apaga** (`DELETE`) todas as linhas das tabelas
  presentes no backup. Em PostgreSQL, como `DISABLE TRIGGER ALL` não contorna
  FKs sem superusuário e o schema tem FKs cíclicas (ex.: `pedidos_venda` ↔
  `assinaturas`), **todas as FKs das tabelas são removidas** antes do restore e
  **recriadas ao final** (operação permitida ao dono do banco). Em SQLite o FK
  não é enforcement por padrão. Garante um restore fiel ao ponto do backup para
  as tabelas contidas nele. Tabelas não presentes no backup não são tocadas.

> Recomendação: use `limpar` apenas quando quiser voltar a um estado anterior
> conhecido. `sobrepor` é mais seguro para "complementar" dados.

## 7. Recomendações (pendentes)
- Adicionar UI (aba "Backup" em `configuracoes.html`) para ligar/desligar o
  agendamento, disparar backup manual e gerenciar arquivos.
- Validar schema antes do restore e adicionar dry-run.
- Para PostgreSQL em produção, `limpar` depende de ordem de FK; se houver
  referências cíclicas, pode ser necessário desabilitar FK via superusuário.

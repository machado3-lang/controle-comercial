# Documentação de Backup e Restore

Este documento descreve o estado real do backup do sistema "Controle de Serviços",
suas limitações conhecidas e o inventário dos artefatos de backup encontrados no projeto.

## 1. Como funciona hoje

### 1.1 Backup/Restore via interface web (principal)
- Módulo: `services/backup.py`
  - `generate_backup()` — descobre **todas** as tabelas do schema (exceto
    `alembic_version`) e gera um JSON. As tabelas conhecidas saem na ordem
    histórica; tabelas novas/desconhecidas entram automaticamente no final, com
    aviso em `warnings`. Grava `version: 2`, `created_at`, `schema_version`
    (revisão do Alembic), `backend`, `table_order` e `tables`.
  - Valores `Decimal` são gravados como **string** (ex.: `"273.61"`), sem perda
    de precisão; datas/datetimes em ISO 8601.
  - `validate_backup(backup_dict)` — confere cobertura de tabelas e colunas
    contra o banco configurado, contagem de linhas, `schema_version` e devolve
    `ok`, listas de divergências e `avisos`. **Não grava nada.**
  - `restore_backup(backup_dict, modo, engine_=None)` — percorre as tabelas do
    próprio backup (inclui tabelas novas), relata tabelas inexistentes no
    destino, filtra colunas desconhecidas, normaliza enums, sincroniza
    sequences e recria as FKs. `engine_` permite restaurar em outro banco
    (usado na verificação com banco descartável).
- Endpoints em `routers/configuracoes.py`:
  - `GET /configuracoes/backup` — gera e baixa `backup_AAAAMMDD_HHMMSS.json`.
  - `POST /configuracoes/restore` — recebe um `.json` e restaura.
- Acesso: apenas administradores (`verificar_admin`).

### 1.1.1 Verificação de que o backup restaura 100%
- Script: `scripts/verificar_backup.py`.

```bash
# Gera um backup do banco configurado, valida e testa o restore
python scripts/verificar_backup.py

# Verifica um backup já baixado (ex.: do Railway)
python scripts/verificar_backup.py --arquivo backups/backup_20261112_073000.json

# Só valida schema/cobertura, sem criar banco descartável
python scripts/verificar_backup.py --arquivo backup.json --sem-restaurar

# Valida no banco descartável e, se passar, restaura no banco local
python scripts/verificar_backup.py --arquivo backup.json --restaurar-local
```

O script **não altera o banco de origem** (a menos que `--restaurar-local` seja
usado). Ele cria um banco PostgreSQL descartável (`verif_backup_<timestamp>`),
aplica o schema do banco de origem via `pg_dump --schema-only`, restaura o JSON
com `modo="limpar"` e compara **linha a linha** por checksum, além de conferir
contagem de linhas e sequences.

`--restaurar-local` usa `modo="limpar"` (apaga os dados atuais) e pede
confirmação digitando `SIM` (use `--sim` para automação). O fluxo recomendado
para trazer o Railway para o banco local é: baixar o JSON, rodar com
`--restaurar-local` e conferir o resultado. Sem os binários do PostgreSQL, cai
para o schema dos models SQLAlchemy (tabelas legadas ficam de fora e são
avisadas). Use `--pg-bin` para apontar o diretório dos binários, se necessário.

Resultado esperado: `RESULTADO: OK — backup completo e restauração fiel`.


### 1.2 Backup manual de NFSe
- Script: `scripts/backup_nfse.py` — exporta apenas `nfse` e `nfse_itens` para
  `backups/nfse_backup_AAAAMMDD_HHMMSS.json`. Execução manual (`python scripts/backup_nfse.py`).

### 1.3 Backup bruto do banco (Railway)
- Arquivo: `backup-rlw/backup_railway.sql` — dump PostgreSQL cru de uma instância
  Railway. Gerado manualmente, fora do código.

## 2. O que está incluído no backup principal
Todas as tabelas de dados do schema `public`, exceto `alembic_version` (controle
de migração). No banco local em 12/11/2026: **38 tabelas / 7.236 linhas**.

A lista histórica (`TABLES_IN_ORDER`) define a ordem de restauração e contém 38
nomes, incluindo `servicos` e `servicos_insumos` (tabelas legadas sem model
SQLAlchemy). Se o banco tiver tabelas que não estão nessa lista, elas são
descobertas e incluídas automaticamente (com aviso no campo `warnings`), de modo
que nenhum dado fica de fora.

## 2.1 Formato do arquivo (version 2)
```json
{
  "version": 2,
  "created_at": "2026-11-12T07:50:47",
  "schema_version": "c4d5e6f7a8b9",
  "backend": "postgresql",
  "table_order": ["marcas_produto", "..."],
  "warnings": [],
  "tables": {
    "clientes": [{"id": 1, "nome": "...", "...": "..."}]
  }
}
```
Backups antigos (`version: 1`, com floats e sem `schema_version`/`table_order`)
continuam aceitos no restore e na validação.

## 3. Limitações conhecidas
1. **Sem snapshot transacional.** Há um scheduler em `services/backup_scheduler.py`
   (desligado por padrão; ative via `/configuracoes/backup-config`). Ele grava em
   disco e aplica retenção. O backup lê tabela por tabela, sem `REPEATABLE READ`
   global: uma escrita concorrente durante a geração pode aparecer em uma tabela
   e não em outra.
2. **Restore "sobrepor" é "merge".** O modo `sobrepor` (padrão) não remove dados
   ausentes no backup. Para recuperação real, use o modo **`limpar`** (seção 6).
3. **O backup JSON é lógico, não um dump físico.** Não carrega tipos/índices/
   constraints: o schema precisa existir no destino e estar na mesma revisão do
   Alembic (`schema_version`). Divergências são avisadas por `validate_backup` e
   `restore_backup`, não bloqueadas. Para fidelidade física total, use
   `pg_dump`/`pg_restore`.
4. **Sem transação global no restore.** O commit é por tabela; uma falha deixa o
   banco parcialmente restaurado. O relatório (`stats`) mostra importados/erros
   por tabela e tabelas ignoradas. Use `scripts/verificar_backup.py` antes.
5. **Sequences**: são sincronizadas apenas para tabelas com coluna `id` e
   sequence própria (PostgreSQL), após cada tabela restaurada.
6. **Múltiplos mecanismos fragmentados** (web JSON, script NFSe, dump SQL) sem
   padrão único.

## 4. Inventário de artefatos de backup no projeto
| Arquivo | Tipo | Status |
|---|---|---|
| `services/backup.py` | Código (export/import full DB) | Em uso |
| `routers/configuracoes.py` | Endpoints web | Em uso |
| `scripts/backup_nfse.py` | Script NFSe | Manual, legado |
| `scripts/verificar_backup.py` | Verificação de cobertura + restore descartável | Em uso |
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
- Rodar `python scripts/verificar_backup.py` após cada mudança de schema e após
  baixar um backup do Railway, antes de restaurar no banco local.
- Guardar também um dump físico periódico (`pg_dump -Fc`) para recuperação de
  schema/índices/constraints; o JSON não substitui esse dump.
- Criptografar/limitar acesso aos arquivos de backup (contêm CPF/CNPJ e hashes).
- Evoluir o restore para uma transação única (ou staging + swap) quando o volume
  justificar, eliminando o estado parcial em caso de falha.

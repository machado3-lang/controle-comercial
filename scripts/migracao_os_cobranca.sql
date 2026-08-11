-- ============================================================================
-- Migração: fluxo de Ordens de Serviço + rastro OS->NFSe + cobrança
-- Aplicar UMA VEZ em cada ambiente (PostgreSQL).
-- Execute via: psql -U postgres -d controledb -f scripts/migracao_os_cobranca.sql
-- Ou cole no DBeaver / pgAdmin.
-- ============================================================================

-- 1) Rótulo CONCLUIDA ausente no enum nativo statusos (causava 500 ao concluir OS).
--    O SQLAlchemy grava o NOME do enum (maiúsculas); sem esse rótulo o UPDATE falhava.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
        WHERE t.typname = 'statusos' AND e.enumlabel = 'CONCLUIDA'
    ) THEN
        ALTER TYPE statusos ADD VALUE 'CONCLUIDA';
    END IF;
END$$;

-- 2) Coluna os_id na NFSe para rastrear a OS de origem (antes só existia origem='os').
ALTER TABLE nfse ADD COLUMN IF NOT EXISTS os_id INTEGER REFERENCES ordens_servico(id);
CREATE INDEX IF NOT EXISTS ix_nfse_os_id ON nfse(os_id);

-- 3) (Opcional) Preencher os_id retroativamente em NFSe de OS cujo número de OS
--    esteja no texto de observação. Ajuste o padrão conforme o seu formato real.
-- UPDATE nfse SET os_id = sub.os_id
-- FROM (SELECT id, (regexp_match(observacoes, 'OS #(\d+)'))[1]::int AS os_id
--       FROM nfse WHERE origem = 'os' AND os_id IS NULL) sub
-- WHERE nfse.id = sub.id;

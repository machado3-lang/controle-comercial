-- ============================================================================
-- Migração: fluxo de Ordens de Serviço + rastro OS->NFSe + cobrança
-- Aplicar UMA VEZ em cada ambiente (PostgreSQL).
-- Execute via: psql -U postgres -d controledb -f scripts/migracao_os_cobranca.sql
-- Ou cole no DBeaver / pgAdmin.
-- ============================================================================

-- 1) Rótulo 'concluida' (minúsculo) ausente no enum nativo statusos.
--    O SQLAlchemy grava o VALOR do enum (StatusOS.CONCLUIDA.value = 'concluida',
--    minúsculo). Sem esse rótulo, o UPDATE/INSERT de uma OS concluída falha com
--    InvalidTextRepresentation (500) e a UI ficava com o spinner girando.
--    O auto-migration (app/core/lifespan.py -> _add_missing_enum_values) já
--    adiciona esse valor em startup; este script cobre quem prefere SQL explícito.
--    (Não usar 'CONCLUIDA' maiúsculo: o modelo nunca grava esse rótulo.)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
        WHERE t.typname = 'statusos' AND e.enumlabel = 'concluida'
    ) THEN
        ALTER TYPE statusos ADD VALUE 'concluida';
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

"""Converte atestado_tecnico de Boolean para tri-state String.

Valores: "emitido" | "pendente" | "nao_aplica".

Equipamentos vendidos que nao sao REPs (relogio de ponto) nao precisam de
atestado tecnico; o terceiro estado permite distingui-los de quem esta
"pendente" (deveria ter atestado, mas ainda nao foi emitido).

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def _col_type(bind, table, column):
    from sqlalchemy import inspect

    for c in inspect(bind).get_columns(table):
        if c["name"] == column:
            return c["type"]
    return None


def upgrade():
    bind = op.get_bind()
    tipo = _col_type(bind, "relogios_ponto", "atestado_tecnico")
    if tipo is not None and isinstance(tipo, sa.Boolean):
        op.execute(
            "ALTER TABLE relogios_ponto ALTER COLUMN atestado_tecnico TYPE varchar(20) "
            "USING (CASE WHEN atestado_tecnico THEN 'emitido' ELSE 'pendente' END)"
        )
    # Normaliza valores invalidos/ausentes e garante restricoes
    op.execute(
        "UPDATE relogios_ponto SET atestado_tecnico = 'pendente' "
        "WHERE atestado_tecnico IS NULL OR atestado_tecnico NOT IN ('emitido','pendente','nao_aplica')"
    )
    op.execute("ALTER TABLE relogios_ponto ALTER COLUMN atestado_tecnico SET DEFAULT 'pendente'")
    op.execute("ALTER TABLE relogios_ponto ALTER COLUMN atestado_tecnico SET NOT NULL")


def downgrade():
    bind = op.get_bind()
    tipo = _col_type(bind, "relogios_ponto", "atestado_tecnico")
    if tipo is not None and not isinstance(tipo, sa.Boolean):
        op.execute(
            "ALTER TABLE relogios_ponto ALTER COLUMN atestado_tecnico TYPE boolean "
            "USING (atestado_tecnico = 'emitido')"
        )

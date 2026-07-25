"""Adiciona flag travar_cobranca na assinatura.

Revision ID: a1b2c3d4e5f6
Revises: 2fd056b77553

Idempotente: como o app tambem cria colunas faltantes no startup
(_add_missing_columns), esta migracao verifica a existencia da coluna
antes de criar/remover, evitando erro de coluna duplicada.
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "2fd056b77553"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    if not _column_exists("assinaturas", "travar_cobranca"):
        op.add_column(
            "assinaturas",
            sa.Column("travar_cobranca", sa.Boolean(), nullable=True, server_default=sa.false()),
        )


def downgrade():
    if _column_exists("assinaturas", "travar_cobranca"):
        op.drop_column("assinaturas", "travar_cobranca")

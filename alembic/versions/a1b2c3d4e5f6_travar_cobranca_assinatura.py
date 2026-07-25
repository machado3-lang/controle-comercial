"""Adiciona flag travar_cobranca na assinatura.

Revision ID: a1b2c3d4e5f6
Revises: 2fd056b77553
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "2fd056b77553"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "assinaturas",
        sa.Column("travar_cobranca", sa.Boolean(), nullable=True, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("assinaturas", "travar_cobranca")

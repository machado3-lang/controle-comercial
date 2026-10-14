"""Adiciona o campo email_arquivo na empresa para copia oculta (BCC) dos envios.

O campo recebe uma copia oculta de todos os e-mails enviados pelo sistema
(NFE, NFSe, boletos, notificacoes e recibos), funcionando como "arquivo"
dos enviados. Pode ser o mesmo endereco do remetente ou um dedicado.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
"""

from alembic import op
import sqlalchemy as sa


revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("empresa", sa.Column("email_arquivo", sa.String(500), nullable=True))


def downgrade():
    op.drop_column("empresa", "email_arquivo")

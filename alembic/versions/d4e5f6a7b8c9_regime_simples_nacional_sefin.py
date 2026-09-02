"""Campos de regime de tributacao do Simples Nacional na empresa (SEFIN).

Adiciona a 'empresa' os campos usados no DPS/Evento nacional:
op_simp_nac, reg_esp_trib, reg_ap_trib_sn, p_tot_trib_sn.

Idempotente: verifica a existencia da coluna antes de criar.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None

_COLS = [
    ("op_simp_nac", sa.Column("op_simp_nac", sa.Integer(), nullable=False, server_default="1")),
    ("reg_esp_trib", sa.Column("reg_esp_trib", sa.Integer(), nullable=False, server_default="0")),
    ("reg_ap_trib_sn", sa.Column("reg_ap_trib_sn", sa.Integer(), nullable=True)),
    ("p_tot_trib_sn", sa.Column("p_tot_trib_sn", sa.Float(), nullable=True)),
]


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    for name, col in _COLS:
        if not _column_exists("empresa", name):
            op.add_column("empresa", col)


def downgrade():
    for name, _ in _COLS:
        if _column_exists("empresa", name):
            op.drop_column("empresa", name)

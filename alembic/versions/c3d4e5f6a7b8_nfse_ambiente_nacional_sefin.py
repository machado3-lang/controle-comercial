"""Configuracao de emissao NFS-e no Ambiente Nacional (SEFIN).

Adiciona as colunas de configuracao do Ambiente Nacional (SEFIN) a 'empresa':
nfse_emissao_ambiente, nfse_url_producao, nfse_url_homologacao,
nfse_namespace, nfse_ver_aplic.

Idempotente: verifica a existencia da coluna antes de criar, evitando erro
de coluna duplicada (o app tambem cria colunas faltantes no startup).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""

from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

_COLS = [
    ("nfse_emissao_ambiente", sa.Column("nfse_emissao_ambiente", sa.String(20), nullable=False, server_default="producao")),
    ("nfse_url_producao", sa.Column("nfse_url_producao", sa.String(500), nullable=True)),
    ("nfse_url_homologacao", sa.Column("nfse_url_homologacao", sa.String(500), nullable=True)),
    ("nfse_namespace", sa.Column("nfse_namespace", sa.String(200), nullable=True)),
    ("nfse_ver_aplic", sa.Column("nfse_ver_aplic", sa.String(100), nullable=True)),
]


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    for name, col in _COLS:
        if not _column_exists("empresa", name):
            op.add_column("empresa", col)
    # garante o default do namespace caso a coluna já existisse vazia
    op.execute(
        "UPDATE empresa SET nfse_namespace = 'http://www.sped.fazenda.gov.br/nfse' "
        "WHERE nfse_namespace IS NULL OR nfse_namespace = ''"
    )


def downgrade():
    for name, _ in _COLS:
        if _column_exists("empresa", name):
            op.drop_column("empresa", name)

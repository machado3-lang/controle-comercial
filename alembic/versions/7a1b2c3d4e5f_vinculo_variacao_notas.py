"""vinculo_variacao_notas

Adiciona variacao_id em nfe_itens e nfse_itens para vincular a venda
direta nas notas a variacao do produto (baixa de estoque por variacao).

Idempotente: o app ja cria colunas faltantes no startup (_add_missing_columns),
entao esta migracao verifica a existencia antes de criar/remover.

Revision ID: 7a1b2c3d4e5f
Revises: a1b2c3d4e5f6
Create Date: 2026-07-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7a1b2c3d4e5f"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _column_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def _fk_exists(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    fks = [fk["name"] for fk in insp.get_foreign_keys(table)]
    return name in fks


def _index_exists(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    idxs = [idx["name"] for idx in insp.get_indexes(table)]
    return name in idxs


def upgrade() -> None:
    if not _column_exists("nfe_itens", "variacao_id"):
        op.add_column("nfe_itens", sa.Column("variacao_id", sa.Integer(), nullable=True))
    if not _fk_exists("nfe_itens", "fk_nfe_itens_variacao_id"):
        op.create_foreign_key(
            "fk_nfe_itens_variacao_id",
            "nfe_itens", "variacao_id",
            "produto_variacoes", "id",
            ondelete="SET NULL",
        )
    if not _index_exists("nfe_itens", "ix_nfe_itens_variacao_id"):
        op.create_index("ix_nfe_itens_variacao_id", "nfe_itens", ["variacao_id"])

    if not _column_exists("nfse_itens", "variacao_id"):
        op.add_column("nfse_itens", sa.Column("variacao_id", sa.Integer(), nullable=True))
    if not _fk_exists("nfse_itens", "fk_nfse_itens_variacao_id"):
        op.create_foreign_key(
            "fk_nfse_itens_variacao_id",
            "nfse_itens", "variacao_id",
            "produto_variacoes", "id",
            ondelete="SET NULL",
        )
    if not _index_exists("nfse_itens", "ix_nfse_itens_variacao_id"):
        op.create_index("ix_nfse_itens_variacao_id", "nfse_itens", ["variacao_id"])


def downgrade() -> None:
    if _index_exists("nfse_itens", "ix_nfse_itens_variacao_id"):
        op.drop_index("ix_nfse_itens_variacao_id", table_name="nfse_itens")
    if _fk_exists("nfse_itens", "fk_nfse_itens_variacao_id"):
        op.drop_constraint("fk_nfse_itens_variacao_id", "nfse_itens", type_="foreignkey")
    if _column_exists("nfse_itens", "variacao_id"):
        op.drop_column("nfse_itens", "variacao_id")

    if _index_exists("nfe_itens", "ix_nfe_itens_variacao_id"):
        op.drop_index("ix_nfe_itens_variacao_id", table_name="nfe_itens")
    if _fk_exists("nfe_itens", "fk_nfe_itens_variacao_id"):
        op.drop_constraint("fk_nfe_itens_variacao_id", "nfe_itens", type_="foreignkey")
    if _column_exists("nfe_itens", "variacao_id"):
        op.drop_column("nfe_itens", "variacao_id")

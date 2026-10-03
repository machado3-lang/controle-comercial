"""desconto_pedido_venda

Campos de desconto do resumo do pedido (% e R$) em `pedidos_venda` e
`pedidos_consolidados` (a consolidacao herda a soma dos descontos dos
pedidos de origem).
O app tambem cria colunas faltantes no startup (_add_missing_columns),
entao a migracao e idempotente.

Revision ID: b7c8d9e0f1a2
Revises: a2b3c4d5e6f7
Create Date: 2026-10-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _column_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return False
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


_TABELAS = ("pedidos_venda", "pedidos_consolidados")


def upgrade() -> None:
    for tabela in _TABELAS:
        if not _column_exists(tabela, "desconto_percentual"):
            op.add_column(
                tabela,
                sa.Column("desconto_percentual", sa.Numeric(precision=5, scale=2), nullable=True, server_default="0"),
            )
        if not _column_exists(tabela, "valor_desconto"):
            op.add_column(
                tabela,
                sa.Column("valor_desconto", sa.Numeric(precision=12, scale=2), nullable=True, server_default="0"),
            )


def downgrade() -> None:
    for tabela in _TABELAS:
        if _column_exists(tabela, "valor_desconto"):
            op.drop_column(tabela, "valor_desconto")
        if _column_exists(tabela, "desconto_percentual"):
            op.drop_column(tabela, "desconto_percentual")

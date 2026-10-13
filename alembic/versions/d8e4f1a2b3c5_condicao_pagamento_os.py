"""condicao_pagamento_os

A OS passa a guardar a condicao de pagamento acordada com o cliente
(forma, parcelas, intervalo e 1o vencimento), escolhida na tela da OS e
herdada pelas notas:

- `ordens_servico.forma_pagamento`: vai para o rascunho de NFSe da OS, que
  antes nascia sem condicao nenhuma e caia no fallback literal "NFSe" da
  geracao de cobranca.
- `num_parcelas`, `intervalo_dias`, `primeiro_vencimento`: usados pela
  cobranca gerada na tela da OS, que antes fixava 1x30 com vencimento na
  data de saida.

O app tambem cria colunas faltantes no startup (_add_missing_columns), entao a
migracao e idempotente.

Revision ID: d8e4f1a2b3c5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-13 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d8e4f1a2b3c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


_COLUNAS = (
    ("forma_pagamento", sa.String(20), None),
    ("num_parcelas", sa.Integer(), "1"),
    ("intervalo_dias", sa.Integer(), "30"),
    ("primeiro_vencimento", sa.Date(), None),
)


def _column_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    for coluna, tipo, default in _COLUNAS:
        if _column_exists("ordens_servico", coluna):
            continue
        op.add_column(
            "ordens_servico",
            sa.Column(coluna, tipo, nullable=True, server_default=default),
        )


def downgrade() -> None:
    for coluna, _tipo, _default in _COLUNAS:
        if _column_exists("ordens_servico", coluna):
            op.drop_column("ordens_servico", coluna)
"""nfe_data_saida_timestamp

`nfe.data_saida` foi criada como DATE na baseline, mas o modelo declara
DateTime. O PostgreSQL truncava `data_saida=now` para 00:00 e o XML saia com
`dhSaiEnt` anterior ao `dhEmi` do mesmo dia, gerando a Rejeicao 506 da SEFAZ.

Esta migracao converte a coluna para TIMESTAMP sem perder a data ja gravada.
O startup (`_fix_datetime_columns`) repete a conversao de forma idempotente,
entao bancos criados por create_all tambem se corrigem.

Revision ID: c4d5e6f7a8b9
Revises: e1b7c9d4a2f6
Create Date: 2026-11-10 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "e1b7c9d4a2f6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _tipo_coluna(table: str, column: str):
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return None
    cols = {c["name"]: c for c in insp.get_columns(table)}
    col = cols.get(column)
    return str(col["type"]).upper() if col else None


def upgrade() -> None:
    # SQLite (testes) nao precisa: o create_all ja cria a coluna como DateTime.
    if op.get_bind().dialect.name != "postgresql":
        return
    tipo = _tipo_coluna("nfe", "data_saida")
    if tipo is None or "TIMESTAMP" in tipo or "DATETIME" in tipo:
        return
    op.alter_column(
        "nfe",
        "data_saida",
        existing_type=sa.Date(),
        type_=sa.DateTime(),
        postgresql_using="data_saida::timestamp",
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    tipo = _tipo_coluna("nfe", "data_saida")
    if tipo is None or "TIMESTAMP" not in tipo:
        return
    op.alter_column(
        "nfe",
        "data_saida",
        existing_type=sa.DateTime(),
        type_=sa.Date(),
        postgresql_using="data_saida::date",
    )

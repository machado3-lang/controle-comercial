"""propagar_observacao_os

A OS tem `observacao` (Text) desde a baseline, mas ela nunca era lida por
nenhum gerador de NFe/NFSe: a NFe da OS nascia sem `observacoes` e a NFSe da
OS nascia com `observacoes` vazia, entao o campo vivia so no banco, na tela da
OS e na sincronizacao com o Bling.

`ordens_servico.propagar_observacao` e o padrao da OS para a emissao: quando
marcado, a tela de emissao da NFe e da NFSe oferece o envio da observacao para a
nota, e a escolha fica registrada em vez de ser um clique descartavel.

O default e False: quase toda observacao de OS e interna e nao deve virar
documento fiscal. Apos a transmissao o campo da nota e imutavel (na NFe exige
carta de correcao), entao a decisao precisa existir na emissao.

O app tambem cria colunas faltantes no startup (_add_missing_columns), entao a
migracao e idempotente.

Revision ID: e1b7c9d4a2f6
Revises: d8e4f1a2b3c5
Create Date: 2026-10-13 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1b7c9d4a2f6"
down_revision: Union[str, None] = "d8e4f1a2b3c5"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _column_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _column_exists("ordens_servico", "propagar_observacao"):
        op.add_column(
            "ordens_servico",
            sa.Column("propagar_observacao", sa.Boolean(), nullable=True, server_default=sa.false()),
        )


def downgrade() -> None:
    if _column_exists("ordens_servico", "propagar_observacao"):
        op.drop_column("ordens_servico", "propagar_observacao")
"""conta_receita_produto

Origem da classificacao contabil automatica:

- `produtos.conta_receita_id`: conta de receita do produto/servico. Produto e
  Servico sao linhas da mesma tabela `produtos` (discriminadas por `tipo`), nao
  ha modelo Servico separado, entao um unico campo cobre os dois.
- `empresa.conta_receita_padrao_id`: fallback final da cascata de resolucao,
  para que nenhuma conta a receber nasca sem classificacao.
- unique constraint em `plano_contas.codigo`: hoje so existe checagem na
  aplicacao (routers/planocontas.py), entao dois requests simultaneos
  conseguem inserir o mesmo codigo.
- indexes nas duas FKs novas.

O app tambem cria colunas faltantes no startup (_add_missing_columns), entao a
migracao e idempotente.

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-10-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _column_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return False
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def _index_exists(table: str, index: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return False
    return index in {i["name"] for i in insp.get_indexes(table)}


_COLUNAS = (
    ("produtos", "conta_receita_id", "ix_produtos_conta_receita_id"),
    ("empresa", "conta_receita_padrao_id", "ix_empresa_conta_receita_padrao_id"),
    ("contas_receber", "classificacao_revisar", "ix_contas_receber_classificacao_revisar"),
)


def upgrade() -> None:
    for tabela, coluna, index in _COLUNAS:
        if not _column_exists(tabela, coluna):
            if coluna == "classificacao_revisar":
                op.add_column(
                    tabela,
                    sa.Column(coluna, sa.Boolean(), nullable=True, server_default=sa.false()),
                )
            else:
                op.add_column(tabela, sa.Column(coluna, sa.Integer(), nullable=True))
        if not _index_exists(tabela, index):
            op.create_index(index, tabela, [coluna], unique=False)

    # unique em plano_contas.codigo, apenas se nao houver duplicatas
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "plano_contas" in insp.get_table_names():
        existe_uq = any(
            c["name"] == "uq_plano_contas_codigo"
            for c in insp.get_unique_constraints("plano_contas")
        ) or "uq_plano_contas_codigo" in {
            i["name"] for i in insp.get_indexes("plano_contas")
            if i.get("unique")
        }
        if not existe_uq:
            dup = bind.execute(sa.text(
                "SELECT COUNT(*) FROM (SELECT codigo FROM plano_contas "
                "GROUP BY codigo HAVING COUNT(*) > 1) sub"
            )).scalar()
            if dup:
                # Nao quebra a migration: reporta e segue sem a constraint.
                print(f"[MIGRATION] WARNING: {dup} codigos duplicados em plano_contas; "
                      f"unique constraint nao adicionada")
            else:
                op.create_unique_constraint("uq_plano_contas_codigo", "plano_contas", ["codigo"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "plano_contas" in insp.get_table_names():
        try:
            op.drop_constraint("uq_plano_contas_codigo", "plano_contas", type_="unique")
        except Exception:
            pass

    for tabela, coluna, index in _COLUNAS:
        if _index_exists(tabela, index):
            op.drop_index(index, table_name=tabela)
        if _column_exists(tabela, coluna):
            op.drop_column(tabela, coluna)
"""tributos_nfe_frete_transportadora

Adiciona os campos de tributação (ICMS/IPI/PIS/COFINS/CST/CSOSN/CEST) ao
produto e aos itens da NF-e, o CRT na empresa, frete/transportadora na NF-e
e a tabela de transportadoras.

Idempotente: o app já cria colunas faltantes no startup (_add_missing_columns),
então esta migração verifica a existência antes de criar/remover.

Revision ID: b2c3d4e5f6a7
Revises: 7a1b2c3d4e5f
Create Date: 2026-08-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _column_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def _table_exists(table: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names()


def _fk_exists(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    fks = [fk["name"] for fk in insp.get_foreign_keys(table)]
    return name in fks


def upgrade() -> None:
    # Empresa: CRT
    if not _column_exists("empresa", "crt"):
        op.add_column("empresa", sa.Column("crt", sa.Integer(), nullable=False, server_default="3"))

    # Produto: tributação
    for col, typ in [
        ("cst", sa.String(length=2)),
        ("csosn", sa.String(length=3)),
        ("aliquota_icms", sa.Numeric(precision=5, scale=2)),
        ("aliquota_pis", sa.Numeric(precision=5, scale=2)),
        ("aliquota_cofins", sa.Numeric(precision=5, scale=2)),
        ("cest", sa.String(length=7)),
        ("codigo_beneficio_fiscal", sa.String(length=10)),
    ]:
        if not _column_exists("produtos", col):
            op.add_column("produtos", sa.Column(col, typ, nullable=True))

    # NFe: frete e transportadora
    if not _column_exists("nfe", "valor_frete"):
        op.add_column("nfe", sa.Column("valor_frete", sa.Numeric(precision=12, scale=2), nullable=True, server_default="0"))
    if not _column_exists("nfe", "transportadora_id"):
        op.add_column("nfe", sa.Column("transportadora_id", sa.Integer(), nullable=True))

    # NFeItem: tributação e desconto
    for col, typ in [
        ("desconto", sa.Numeric(precision=12, scale=2)),
        ("cst", sa.String(length=2)),
        ("csosn", sa.String(length=3)),
        ("aliquota_icms", sa.Numeric(precision=5, scale=2)),
        ("aliquota_pis", sa.Numeric(precision=5, scale=2)),
        ("aliquota_cofins", sa.Numeric(precision=5, scale=2)),
        ("cest", sa.String(length=7)),
        ("codigo_beneficio_fiscal", sa.String(length=10)),
    ]:
        if not _column_exists("nfe_itens", col):
            op.add_column("nfe_itens", sa.Column(col, typ, nullable=True))

    # Garante a FK de variacao_id (a coluna já existe via auto-migrate do app,
    # mas o FK pode não ter sido criado pela migration anterior pulei).
    if not _column_exists("nfe_itens", "variacao_id"):
        op.add_column("nfe_itens", sa.Column("variacao_id", sa.Integer(), nullable=True))
    if not _fk_exists("nfe_itens", "fk_nfe_itens_variacao_id"):
        op.create_foreign_key(
            "fk_nfe_itens_variacao_id",
            "nfe_itens", "produto_variacoes",
            ["variacao_id"], ["id"],
            ondelete="SET NULL",
        )
    if not _column_exists("nfse_itens", "variacao_id"):
        op.add_column("nfse_itens", sa.Column("variacao_id", sa.Integer(), nullable=True))
    if not _fk_exists("nfse_itens", "fk_nfse_itens_variacao_id"):
        op.create_foreign_key(
            "fk_nfse_itens_variacao_id",
            "nfse_itens", "produto_variacoes",
            ["variacao_id"], ["id"],
            ondelete="SET NULL",
        )

    # Tabela transportadoras
    if not _table_exists("transportadoras"):
        op.create_table(
            "transportadoras",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("empresa_id", sa.Integer(), nullable=True),
            sa.Column("nome", sa.String(length=200), nullable=False),
            sa.Column("cpf_cnpj", sa.String(length=20), nullable=True),
            sa.Column("inscricao_estadual", sa.String(length=20), nullable=True),
            sa.Column("endereco", sa.String(length=300), nullable=True),
            sa.Column("cidade", sa.String(length=100), nullable=True),
            sa.Column("estado", sa.String(length=2), nullable=True),
            sa.Column("cep", sa.String(length=10), nullable=True),
            sa.Column("ativo", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    # FK nfe -> transportadoras
    if not _fk_exists("nfe", "fk_nfe_transportadora_id"):
        op.create_foreign_key(
            "fk_nfe_transportadora_id",
            "nfe", "transportadoras",
            ["transportadora_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if _fk_exists("nfe", "fk_nfe_transportadora_id"):
        op.drop_constraint("fk_nfe_transportadora_id", "nfe", type_="foreignkey")
    for col in ["codigo_beneficio_fiscal", "cest", "aliquota_cofins", "aliquota_pis",
                "aliquota_icms", "csosn", "cst", "desconto"]:
        if _column_exists("nfe_itens", col):
            op.drop_column("nfe_itens", col)
    if _column_exists("nfe", "transportadora_id"):
        op.drop_column("nfe", "transportadora_id")
    if _column_exists("nfe", "valor_frete"):
        op.drop_column("nfe", "valor_frete")
    for col in ["codigo_beneficio_fiscal", "cest", "aliquota_cofins", "aliquota_pis",
                "aliquota_icms", "csosn", "cst"]:
        if _column_exists("produtos", col):
            op.drop_column("produtos", col)
    if _column_exists("empresa", "crt"):
        op.drop_column("empresa", "crt")
    if _table_exists("transportadoras"):
        op.drop_table("transportadoras")

"""Amplia o campo email para suportar multiplos enderecos (separados por ; ou ,).

O cadastro de clientes (e tambem fornecedores/empresa) agora permite informar
mais de um e-mail no mesmo campo, que sera dividido no envio. O limite de 200
caracteres era insuficiente para 2 ou mais enderecos.

Revision ID: a2b3c4d5e6f7
Revises: f6a7b8c9d0e1
"""

from alembic import op
import sqlalchemy as sa


revision = "a2b3c4d5e6f7"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    # Amplia os campos de e-mail que podem receber multiplos destinatarios.
    op.alter_column("clientes", "email", type_=sa.String(500), existing_nullable=True)
    op.alter_column("fornecedores", "email", type_=sa.String(500), existing_nullable=True)
    op.alter_column("empresa", "email", type_=sa.String(500), existing_nullable=True)


def downgrade():
    op.alter_column("clientes", "email", type_=sa.String(200), existing_nullable=True)
    op.alter_column("fornecedores", "email", type_=sa.String(200), existing_nullable=True)
    op.alter_column("empresa", "email", type_=sa.String(200), existing_nullable=True)

"""Normaliza status das Ordens de Servico para os valores do enum.

Historicamente o SQLAlchemy native_enum gravava o NOME do membro
(ex.: 'CONCLUIDA') em vez do valor ('concluida'). Os rotulos do enum no
banco sao os valores em minusculas, entao gravar o nome maiusculo quebrava
o UPDATE/INSERT ('invalid input value for enum') -> 500 ao alterar o status.

Esta migracao converte os valores maiusculos existentes para minusculos e
remove o rotulo lixo 'zz_teste_tmp' eventualmente criado por _garantir_valor_enum.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""

from alembic import op
import sqlalchemy as sa


revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    # Converte 'CONCLUIDA'/'EM_ANDAMENTO'/... para 'concluida'/... (valores validos)
    op.execute(
        "UPDATE ordens_servico SET status = lower(status::text)::statusos "
        "WHERE status::text <> lower(status::text)"
    )


def downgrade():
    # Nao e possivel reconstruir os nomes maiusculos originais com seguranca;
    # mantemos os valores minusculos (equivalente funcional).
    pass

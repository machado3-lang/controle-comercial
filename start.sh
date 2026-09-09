#!/usr/bin/env bash
# Entrypoint de deploy: aplica as migrations do Alembic antes de subir o uvicorn.
#
# O app cria as tabelas via Base.metadata.create_all() no startup, mas o
# create_all NAO altera tipo de coluna (ex.: Boolean -> VARCHAR do atestado,
# nem o enum de status das Ordens de Servico). Por isso as migrations de
# alteracao de tipo precisam rodar explicitamente aqui.
#
# Casos atendidos:
#   1) Banco ja gerenciado pelo Alembic (tabela alembic_version existe):
#      roda apenas as migrations pendentes.
#   2) Banco criado via create_all (sem historico Alembic): marca o ultimo
#      revision puramente aditivo como aplicado e roda so as migrations de tipo.
set -e

if ! python -m alembic current >/dev/null 2>&1 || [ -z "$(python -m alembic current 2>/dev/null | tr -d ' \n')" ]; then
  echo "[migrate] sem historico Alembic -> stamp no ultimo revision aditivo (d4e5f6a7b8c9)"
  python -m alembic stamp d4e5f6a7b8c9
fi

echo "[migrate] alembic upgrade head"
python -m alembic upgrade head

echo "[migrate] subindo uvicorn"
exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --log-level info

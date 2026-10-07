"""Inventario do estado atual: o que o motor contabil vai encontrar."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa

from database import SessionLocal
from models import PlanoDeContas

db = SessionLocal()

print("=== contas_receber PAGO: valor vs valor_total ===")
print("PAGO com valor_total NULL:",
      db.execute(sa.text("SELECT COUNT(*) FROM contas_receber WHERE status='PAGO' AND valor_total IS NULL")).scalar())
print("PAGO total              :",
      db.execute(sa.text("SELECT COUNT(*) FROM contas_receber WHERE status='PAGO'")).scalar())
print("soma valor              :",
      db.execute(sa.text("SELECT COALESCE(SUM(valor),0) FROM contas_receber WHERE status='PAGO'")).scalar())
print("soma valor_total        :",
      db.execute(sa.text("SELECT COALESCE(SUM(valor_total),0) FROM contas_receber WHERE status='PAGO'")).scalar())
print("diferenca (juros-desc)  :",
      db.execute(sa.text(
          "SELECT COALESCE(SUM(valor_total),0)-COALESCE(SUM(valor),0) "
          "FROM contas_receber WHERE status='PAGO'")).scalar())

print()
print("=== tipos de conta usados ===")
print(db.execute(sa.text("SELECT tipo, COUNT(*) FROM plano_contas GROUP BY tipo")).all())

print()
print("=== cobertura da classificacao (contas a receber) ===")
total = db.execute(sa.text("SELECT COUNT(*) FROM contas_receber")).scalar()
com = db.execute(sa.text("SELECT COUNT(*) FROM contas_receber WHERE plano_conta_id IS NOT NULL")).scalar()
revisar = db.execute(sa.text("SELECT COUNT(*) FROM contas_receber WHERE classificacao_revisar IS TRUE")).scalar()
print(f"total={total}  classificadas={com}  para_revisar={revisar}")

print()
print("=== cobertura (produtos) ===")
tp = db.execute(sa.text("SELECT COUNT(*) FROM produtos")).scalar()
pc = db.execute(sa.text("SELECT COUNT(*) FROM produtos WHERE conta_receita_id IS NOT NULL")).scalar()
print(f"total={tp}  com_conta_receita={pc}")

print()
print("=== existe alguma tabela contabil hoje? ===")
tabelas = {t for (t,) in db.execute(sa.text(
    "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).all()}
esperadas = ("lancamentos", "partidas", "periodos", "contas_correntes",
             "saldos", "centro_custo", "razao")
print("tabelas contabeis encontradas:",
      [t for t in tabelas if t in esperadas] or "NENHUMA")
print("total de tabelas no banco:", len(tabelas))

print()
print("=== enum de status (o motor vai precisar de estorno/conciliado) ===")
from models import StatusConta
print([e.name for e in StatusConta])

db.close()
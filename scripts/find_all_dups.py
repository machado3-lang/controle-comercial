"""
Encontra TODAS as duplicatas NFSe por numero E por codigo_verificacao.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import models
from database import get_db
from sqlalchemy import text

db = next(get_db())

print("=== DUPLICATAS POR NUMERO ===")
rows = db.execute(text("""
    SELECT numero, COUNT(*), 
           STRING_AGG(origem, ', ' ORDER BY origem),
           STRING_AGG(CAST(id AS TEXT), ', ' ORDER BY origem),
           STRING_AGG(COALESCE(chave_acesso, 'NULL'), ', ' ORDER BY origem)
    FROM nfse 
    WHERE numero IS NOT NULL 
    GROUP BY numero 
    HAVING COUNT(*) > 1
    ORDER BY numero
""")).all()

if rows:
    for r in rows:
        print(f"  Numero {r[0]}: {r[1]}x origens=[{r[2]}] ids=[{r[3]}] chaves=[{r[4]}]")
else:
    print("  Nenhuma duplicata por numero")

print(f"\n=== DUPLICATAS POR CODIGO_VERIFICACAO ===")
rows = db.execute(text("""
    SELECT codigo_verificacao, COUNT(*), 
           STRING_AGG(origem, ', ' ORDER BY origem),
           STRING_AGG(CAST(id AS TEXT), ', ' ORDER BY origem)
    FROM nfse 
    WHERE codigo_verificacao IS NOT NULL 
    GROUP BY codigo_verificacao 
    HAVING COUNT(*) > 1
    ORDER BY COUNT(*) DESC
""")).all()

if rows:
    for r in rows:
        print(f"  CV {str(r[0])[:20]}: {r[1]}x origens=[{r[2]}] ids=[{r[3]}]")
else:
    print("  Nenhuma duplicata por codigo_verificacao")

print(f"\n=== TOTAIS POR ORIGEM ===")
rows = db.execute(text("SELECT origem, COUNT(*) FROM nfse GROUP BY origem ORDER BY origem")).all()
for r in rows:
    print(f"  {r[0]}: {r[1]}")

db.close()

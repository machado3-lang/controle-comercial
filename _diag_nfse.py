from database import engine
from sqlalchemy import text

with engine.connect() as c:
    print("=== DISTINCT status em nfse ===")
    for r in c.execute(text("SELECT status, count(*) FROM nfse GROUP BY status ORDER BY status")).fetchall():
        print(r)
    print("=== contagem por origem ===")
    for r in c.execute(text("SELECT origem, count(*) FROM nfse GROUP BY origem ORDER BY origem")).fetchall():
        print(r)
    print("=== amostra nfse (id, numero, status, origem, xml_text len, pdf_path) ===")
    for r in c.execute(text("SELECT id, numero, status, origem, char_length(xml_text), pdf_path FROM nfse ORDER BY id DESC LIMIT 8")).fetchall():
        print(r)
    print("=== sera que tem NFe na tabela nfse? (colunas) ===")
    cols = [col["name"] for col in c.execute(text("SELECT * FROM nfse LIMIT 0")).cursor.description]
    print(cols)

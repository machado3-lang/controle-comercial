"""
Gera backup JSON das tabelas nfse e nfse_itens antes da correcao.
"""
import sys, os, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models
from database import get_db
from sqlalchemy import text

db = next(get_db())

backup = {
    'timestamp': datetime.now().isoformat(),
    'nfse': [],
    'nfse_itens': [],
}

rows = db.execute(text("SELECT * FROM nfse ORDER BY id")).all()
for r in rows:
    backup['nfse'].append(dict(r._mapping))

rows = db.execute(text("SELECT * FROM nfse_itens ORDER BY id")).all()
for r in rows:
    backup['nfse_itens'].append(dict(r._mapping))

db.close()

path = f"backups/nfse_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
os.makedirs('backups', exist_ok=True)

def serialize(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    return str(obj)

from decimal import Decimal
from datetime import date

with open(path, 'w', encoding='utf-8') as f:
    json.dump(backup, f, ensure_ascii=False, indent=2, default=serialize)

size = os.path.getsize(path)
print(f"Backup salvo: {path} ({size/1024:.1f} KB)")
print(f"  NFSe: {len(backup['nfse'])} registros")
print(f"  NFSeItens: {len(backup['nfse_itens'])} registros")

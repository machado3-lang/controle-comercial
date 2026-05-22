import httpx, json
from database import SessionLocal
from models import Empresa

db = SessionLocal()
emp = db.query(Empresa).first()
token = emp.bling_token if emp else None
db.close()

headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

contract_id = 14881050041
contact_id = 16567125190

# Try different formats and locations for descISSTotalNota
tests = [
    ("descISS=S as string", {"contato": {"id": contact_id}, "descISSTotalNota": "S", "descricao": "test", "data": "2024-01-01", "numero": "test1", "valor": 100, "situacao": 1, "cobranca": {"dataBase": "2024-01-01", "contato": {"id": contact_id}, "vencimento": {"tipo": 1, "dia": 10, "periodicidade": 1}}}),
    ("descISS=N as string", {"contato": {"id": contact_id}, "descISSTotalNota": "N", "descricao": "test", "data": "2024-01-01", "numero": "test2", "valor": 100, "situacao": 1, "cobranca": {"dataBase": "2024-01-01", "contato": {"id": contact_id}, "vencimento": {"tipo": 1, "dia": 10, "periodicidade": 1}}}),
    ("descISS int 1", {"contato": {"id": contact_id}, "descISSTotalNota": 1, "descricao": "test", "data": "2024-01-01", "numero": "test3", "valor": 100, "situacao": 1, "cobranca": {"dataBase": "2024-01-01", "contato": {"id": contact_id}, "vencimento": {"tipo": 1, "dia": 10, "periodicidade": 1}}}),
    ("descISS int 0", {"contato": {"id": contact_id}, "descISSTotalNota": 0, "descricao": "test", "data": "2024-01-01", "numero": "test4", "valor": 100, "situacao": 1, "cobranca": {"dataBase": "2024-01-01", "contato": {"id": contact_id}, "vencimento": {"tipo": 1, "dia": 10, "periodicidade": 1}}}),
    ("descISS inside notaFiscal", {"contato": {"id": contact_id}, "notaFiscal": {"descISSTotalNota": False, "iss": {"descontar": False}}, "descricao": "test", "data": "2024-01-01", "numero": "test5", "valor": 100, "situacao": 1, "cobranca": {"dataBase": "2024-01-01", "contato": {"id": contact_id}, "vencimento": {"tipo": 1, "dia": 10, "periodicidade": 1}}}),
    ("no descISS, only notaFiscal.iss.descontar", {"contato": {"id": contact_id}, "notaFiscal": {"iss": {"descontar": False}}, "descricao": "test", "data": "2024-01-01", "numero": "test6", "valor": 100, "situacao": 1, "cobranca": {"dataBase": "2024-01-01", "contato": {"id": contact_id}, "vencimento": {"tipo": 1, "dia": 10, "periodicidade": 1}}}),
    ("descontarISS camelCase", {"contato": {"id": contact_id}, "descontarISS": False, "descricao": "test", "data": "2024-01-01", "numero": "test7", "valor": 100, "situacao": 1, "cobranca": {"dataBase": "2024-01-01", "contato": {"id": contact_id}, "vencimento": {"tipo": 1, "dia": 10, "periodicidade": 1}}}),
    ("descontaISS", {"contato": {"id": contact_id}, "descontaISS": False, "descricao": "test", "data": "2024-01-01", "numero": "test8", "valor": 100, "situacao": 1, "cobranca": {"dataBase": "2024-01-01", "contato": {"id": contact_id}, "vencimento": {"tipo": 1, "dia": 10, "periodicidade": 1}}}),
]

for name, body in tests:
    r = httpx.put(f"https://api.bling.com.br/Api/v3/contratos/{contract_id}", headers=headers, json=body, timeout=30)
    err = r.json().get("error", {})
    fields = err.get("fields", [])
    print(f"=== {name} ===")
    print(f"Status: {r.status_code}")
    for f in fields:
        code = f.get("code","")
        msg = f.get("msg","")
        elem = f.get("element","")
        print(f"  [{code}] {msg} | elem={elem}")
    if r.status_code in (200, 204):
        print("  SUCCESS!")
    print()

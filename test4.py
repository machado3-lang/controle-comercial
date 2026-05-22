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

# Test minimal body that MUST work if API is consistent
payloads = [
    {"contato": {"id": contact_id}, "numero": "PAULO SERG", "descricao": "test", "data": "2024-01-01", "valor": 100, "situacao": 1, "descISSTotalNota": False},
    {"cliente": {"id": contact_id}, "descISSTotalNota": False, "numero": "test2", "descricao": "test", "data": "2024-01-01", "valor": 100},
]

for i, body in enumerate(payloads):
    r = httpx.put(f"https://api.bling.com.br/Api/v3/contratos/{contract_id}", headers=headers, json=body, timeout=30)
    err = r.json().get("error", {})
    fields = err.get("fields", [])
    print(f"Test {i+1}:")
    print(f"  body: {json.dumps(body, ensure_ascii=False)}")
    print(f"  status: {r.status_code}")
    for f in fields:
        print(f"  [{f.get('code','')}] {f.get('msg','')} | elem={f.get('element','')} ns={f.get('namespace','')}")
    if r.status_code in (200, 204):
        print("  SUCCESS!")
    print()

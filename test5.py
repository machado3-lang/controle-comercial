import httpx, json
from database import SessionLocal
from models import Empresa

db = SessionLocal()
emp = db.query(Empresa).first()
token = emp.bling_token if emp else None
db.close()

headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

contract_id = 14881050041

# Get full contract
r = httpx.get(f"https://api.bling.com.br/Api/v3/contratos/{contract_id}", headers=headers, timeout=30)
original = r.json()["data"]
original.pop("id", None)

# Test 1: original + descISSTotalNota (control)
t1 = {**original, "descISSTotalNota": False}

# Test 2: original + descISSTotalNota + change numero
t2 = {**t1, "numero": "3"}

# Test 3: original + descISSTotalNota + change data
t3 = {**t1, "data": "2018-04-26"}

# Test 4: original + descISSTotalNota + change cobranca
t4 = {**t1, "cobranca": {
    "dataBase": "2018-04-26",
    "vencimento": {"tipo": 1, "dia": 26, "periodicidade": 5}
}}

tests = [
    ("control (orig+descISS)", t1),
    ("change numero to 3", t2),
    ("change data", t3),
    ("change cobranca", t4),
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

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

base = {
    "contato": {"id": contact_id},
    "descricao": "test",
    "data": "2024-01-01",
    "numero": "test_final",
    "valor": 100,
    "situacao": 1,
    "cobranca": {
        "dataBase": "2024-01-01",
        "contato": {"id": contact_id},
        "vencimento": {"tipo": 1, "dia": 10, "periodicidade": 1}
    }
}

# Last attempt: try POST to create a new contract
print("=== Trying POST to create new contract ===")
r = httpx.post("https://api.bling.com.br/Api/v3/contratos", headers=headers, json=base, timeout=30)
print(f"Status: {r.status_code}")
body = r.json()
fields = body.get("error", {}).get("fields", [])
for f in fields:
    print(f"  [{f.get('code','')}] {f.get('msg','')} | elem={f.get('element','')}")
if r.status_code in (200, 201):
    print("  CREATED! ID:", body.get("data", {}).get("id"))

# Check the Bling changelog for contract API changes
print("\n=== Checking alternative contract endpoints ===")
# Try with Accept-version header
for ver in ["v310", "v300", "v3"]:
    h = {**headers, "Accept": f"application/json; version={ver}"}
    payload = {**base, "descISSTotalNota": False}
    r = httpx.put(f"https://api.bling.com.br/Api/v3/contratos/{contract_id}", headers=h, json=payload, timeout=30)
    fields = r.json().get("error", {}).get("fields", [])
    iss_error = any(f.get("element") == "descISSTotalNota" for f in fields)
    print(f"  version={ver}: status={r.status_code}, descISS error={iss_error}")

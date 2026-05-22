import httpx, json
from database import SessionLocal
from models import Empresa

db = SessionLocal()
emp = db.query(Empresa).first()
token = emp.bling_token if emp else None
db.close()

headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

# Try different endpoint paths
contract_id = 14881050041
contact_id = 16567125190
base = {
    "contato": {"id": contact_id},
    "descricao": "Teste",
    "data": "2024-01-01",
    "numero": "T123",
    "valor": 100,
    "situacao": 1,
    "descISSTotalNota": False,
    "cobranca": {"dataBase": "2024-01-01", "contato": {"id": contact_id}, "vencimento": {"tipo": 1, "dia": 10, "periodicidade": 1}}
}

paths = [
    (f"v3/contratos/{contract_id}", "PUT"),
    (f"v3/contrato/{contract_id}", "PUT"),
    ("v3/contratos", "POST"),
    ("v3/contrato", "POST"),
]

for path, method in paths:
    url = f"https://api.bling.com.br/Api/{path}"
    r = httpx.request(method, url, headers=headers, json=base, timeout=30)
    body = r.json()
    fields = body.get("error", {}).get("fields", [])
    desc_err = any(f.get("element") == "descISSTotalNota" for f in fields)
    print(f"[{method}] {path}: status={r.status_code}, descISS error={desc_err}")
    if r.status_code in (200, 201, 204):
        print(f"  SUCCESS! Response: {json.dumps(body, ensure_ascii=False)[:200]}")
        break

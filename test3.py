import httpx, json
from database import SessionLocal
from models import Empresa

db = SessionLocal()
emp = db.query(Empresa).first()
token = emp.bling_token if emp else None
db.close()

headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}

r = httpx.get("https://api.bling.com.br/Api/v3/contratos/14881050041", headers=headers, timeout=30)
original = r.json()["data"]
original.pop("id", None)

tests = [
    ("descISS inside notaFiscal", {**original, "notaFiscal": {**original.get("notaFiscal", {}), "descISSTotalNota": False}}),
    ("descISS as string N", {**original, "descISSTotalNota": "N"}),
    ("descISS as int 0", {**original, "descISSTotalNota": 0}),
    ("both root and inside", {**original, "descISSTotalNota": False, "notaFiscal": {**original.get("notaFiscal", {}), "descISSTotalNota": False}}),
    ("without ISS field entirely", {**dict(original), **({"notaFiscal": {k:v for k,v in original.get("notaFiscal", {}).items() if k != "iss"}})}),
]

for name, payload in tests:
    r2 = httpx.put("https://api.bling.com.br/Api/v3/contratos/14881050041", headers=headers, json=payload, timeout=30)
    print(f"=== {name} ===")
    print(f"Status: {r2.status_code}")
    body = r2.json()
    if r2.status_code in (200, 204):
        print("SUCCESS!")
    else:
        fields = body.get("error", {}).get("fields", [])
        for f in fields:
            code = f.get("code", "")
            msg = f.get("msg", "")
            ns = f.get("namespace", "")
            elem = f.get("element", "")
            print(f"  [{code}] {msg} | elem={elem} ns={ns}")
    print()

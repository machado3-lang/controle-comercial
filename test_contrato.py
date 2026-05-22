import httpx, json
from database import SessionLocal
from models import Empresa

db = SessionLocal()
emp = db.query(Empresa).first()
token = emp.bling_token if emp else None
db.close()

if not token:
    print("No token"); exit()

url = "https://api.bling.com.br/Api/v3/contratos/14881050041"
headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}

tests = [
    ("cliente as bare ID", {"cliente": 16567125190, "descISSTotalNota": False, "valor": 670.0, "diaVencimento": 26}),
    ("cliente.id as string", {"cliente": {"id": "16567125190"}, "descISSTotalNota": False, "valor": 670.0}),
    ("contato same", {"contato": {"id": 16567125190}, "descISSTotalNota": False, "valor": 670.0}),
    ("only id field", {"cliente": {"id": 16567125190}, "descISSTotalNota": False, "valor": 670.0, "diaVencimento": 26, "descricao": "test", "data": "2024-01-01"}),
    ("with notaFiscal.iss.descontar", {"cliente": {"id": 16567125190}, "descISSTotalNota": False, "notaFiscal": {"iss": {"descontar": False}}, "valor": 670.0, "diaVencimento": 26}),
    ("POST new minimal", {"descricao": "Teste criacao", "data": "2024-01-01", "numero": "99999", "valor": 100.0, "situacao": 1, "cliente": {"id": 16567125190}, "descISSTotalNota": False, "cobranca": {"dataBase": "2024-01-01", "vencimento": {"tipo": 1, "dia": 10, "periodicidade": 1}}}),
]

for name, data in tests:
    method = "POST" if name.startswith("POST") else "PUT"
    r = httpx.request(method, url if method == "PUT" else "https://api.bling.com.br/Api/v3/contratos", headers=headers, json=data, timeout=30)
    print(f"=== {name} ===")
    print(f"Status: {r.status_code}")
    body = r.json()
    fields = body.get("error", {}).get("fields", [])
    for f in fields:
        print(f"  [{f.get('code','')}] {f.get('msg','')} | element={f.get('element','')} ns={f.get('namespace','')}")
    if r.status_code in (200, 201, 204):
        print("SUCCESS!")
        print(json.dumps(body, indent=2, ensure_ascii=False)[:500])
        break
    print()

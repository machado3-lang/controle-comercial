import httpx, json
from database import SessionLocal
from models import Empresa

db = SessionLocal()
emp = db.query(Empresa).first()
token = emp.bling_token if emp else None
api_key = None  # We don't have the v2 API key
db.close()

headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

# Try v2 endpoints with the v3 OAuth token
v2_urls = [
    ("v2 contratos list", "https://bling.com.br/Api/v2/contratos/json/"),
    ("v2 contratos get", "https://bling.com.br/Api/v2/contratos/14881050041/json/"),
    ("v2 with token as apikey", "https://bling.com.br/Api/v2/contratos/json/?apikey=" + token),
]

for name, url in v2_urls:
    try:
        r = httpx.get(url, headers=headers, timeout=30)
        print(f"=== {name} ===")
        print(f"Status: {r.status_code}")
        print(f"Body: {r.text[:500]}")
        print()
    except Exception as e:
        print(f"=== {name} ===")
        print(f"Error: {e}")
        print()

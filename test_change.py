import httpx, json
from database import SessionLocal
from models import Empresa

db = SessionLocal()
emp = db.query(Empresa).first()
token = emp.bling_token if emp else None
db.close()

headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

contract_id = 14881050041

# Get current state
r = httpx.get(f"https://api.bling.com.br/Api/v3/contratos/{contract_id}", headers=headers, timeout=30)
original = r.json()["data"]
print(f"Original descricao: {original['descricao']}")
print(f"Original observacoes: {original['observacoes']}")

# Modify observacoes and try PUT
original.pop("id", None)
original["observacoes"] = "TESTE UPDATE " + str(hash("test"))
print(f"\nPUT with new observacoes: {original['observacoes']}")

r2 = httpx.put(f"https://api.bling.com.br/Api/v3/contratos/{contract_id}", headers=headers, json=original, timeout=30)
print(f"PUT status: {r2.status_code}")
print(f"PUT response: {r2.text[:300]}")

# GET again to check if changed
r3 = httpx.get(f"https://api.bling.com.br/Api/v3/contratos/{contract_id}", headers=headers, timeout=30)
new_data = r3.json()["data"]
print(f"\nNew observacoes: {new_data['observacoes']}")
print(f"Changed: {original['observacoes'] == new_data['observacoes']}")

# If it didn't change, try without descISSTotalNota field
if original['observacoes'] != new_data['observacoes']:
    print("\nSUCCESS! Contract updated despite descISSTotalNota error!")

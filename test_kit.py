from fastapi.testclient import TestClient
from main import app
import json
import time

client = TestClient(app)

# Testar POST para criar um kit com código único
form_data = {
    'tipo': 'kit',
    'nome': 'Kit Teste ' + str(int(time.time())),
    'codigo': 'KIT-TEST-' + str(int(time.time())),
    'insumos': '[{"insumo_id": 1, "quantidade": 2}]'
}
response = client.post('/produtos/novo', data=form_data, follow_redirects=False)
print(f'Status: {response.status_code}')
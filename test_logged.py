from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# Login first
r = client.post('/auth/login', data={'email': 'admin@controle.com', 'senha': 'admin123'}, follow_redirects=True)
print(f'Login: {r.status_code}')

# Get produtos
r = client.get('/produtos')
print(f'Produtos: {r.status_code}')
print(f'Has Produtos: {"Produtos" in r.text}')
print(f'Has Foto header: {"Foto" in r.text}')
print(f'Has Marca header: {"Marca" in r.text}')
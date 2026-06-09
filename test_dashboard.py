from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# Login
r = client.post('/auth/login', data={'email': 'admin@controle.com', 'senha': 'admin123'}, follow_redirects=False)
print(f'Login status: {r.status_code}')

# Get dashboard
r = client.get('/')
print(f'Dashboard status: {r.status_code}')
print(f'Dashboard contains ControliZ: {"ControliZ" in r.text}')
print(f'Error in page: {"Erro no servidor" in r.text}')
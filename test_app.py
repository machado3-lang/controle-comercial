from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# Test GET login
r = client.get('/auth/login')
print(f'GET /auth/login: {r.status_code}')

# Test POST login
r = client.post('/auth/login', data={'email': 'admin@controle.com', 'senha': 'admin123'}, follow_redirects=False)
print(f'POST /auth/login: {r.status_code}')
print(f'Headers: {[k for k in r.headers.keys() if "set-cookie" in k.lower() or "location" in k.lower()]}')
if 'set-cookie' in r.headers or 'Set-Cookie' in r.headers:
    print(f'Cookies: {r.cookies}')

# Follow redirect
if r.status_code == 303:
    follow_r = client.get('/', cookies=r.cookies)
    print(f'GET / after login: {follow_r.status_code}')
else:
    print(f'Response body: {r.text[:300]}')
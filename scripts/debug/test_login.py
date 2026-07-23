from main import app
from starlette.testclient import TestClient
client = TestClient(app)

passwords = ['admin123', 'admin', '123456', 'controliz', 'Controliz', 'Controle123', 'controle', 'admin1234', 'Admin123', '12345678']
for pwd in passwords:
    response = client.post('/auth/login', data={'email': 'admin@controle.com', 'password': pwd}, follow_redirects=False)
    print(f'Password: {pwd} -> {response.status_code} {response.headers.get("location", "")}')
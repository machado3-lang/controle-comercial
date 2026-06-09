import urllib.request
import urllib.parse

data = urllib.parse.urlencode({'email': 'admin@controle.com', 'senha': 'admin123'}).encode()
req = urllib.request.Request('http://localhost:8000/auth/login', data=data, method='POST')
try:
    r = urllib.request.urlopen(req)
    print(f'Status: {r.status}')
except urllib.error.HTTPError as e:
    print(f'Error: {e.code}')
    print(f'Location: {e.headers.get("Location", "none")}')
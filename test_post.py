import urllib.request
import urllib.parse

data = urllib.parse.urlencode({'email': 'admin@controle.com', 'senha': 'admin123'}).encode()
req = urllib.request.Request('http://127.0.0.1:8000/auth/login', data=data, method='POST')
try:
    r = urllib.request.urlopen(req)
    print(f'Status: {r.status}')
except urllib.error.HTTPError as e:
    print(f'Status: {e.code}')
    print(f'Headers: {dict((k, v) for k, v in e.headers.items() if k.lower() in ["location", "set-cookie"])}')
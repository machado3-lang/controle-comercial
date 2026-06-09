import urllib.request
import urllib.parse

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = urllib.request.build_opener(NoRedirect)
data = urllib.parse.urlencode({'email': 'admin@controle.com', 'senha': 'admin123'}).encode()
req = urllib.request.Request('http://127.0.0.1:8000/auth/login', data=data, method='POST')
try:
    r = opener.open(req)
    print(f'Status: {r.status}')
except urllib.error.HTTPError as e:
    print(f'Status: {e.code}')
    loc = e.headers.get('Location', 'none')
    cookie = e.headers.get('Set-Cookie', 'none')
    print(f'Location: {loc}')
    print(f'Cookie: {cookie[:100] if len(cookie) > 100 else cookie}')
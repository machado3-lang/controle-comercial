import urllib.request
import urllib.parse
import http.cookiejar

cookie_jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

# Get page first
r1 = opener.open('http://localhost:8000/auth/login')
print(f'GET /auth/login: {r1.status}')

# Post login
data = urllib.parse.urlencode({'email': 'admin@controle.com', 'senha': 'admin123'}).encode()
try:
    r2 = opener.open(urllib.request.Request('http://localhost:8000/auth/login', data=data, method='POST'))
    print(f'POST /auth/login: {r2.status}')
except urllib.error.HTTPError as e:
    print(f'POST error: {e.code}')
    # Follow redirect manually
    loc = e.headers.get('Location')
    if loc:
        r3 = opener.open(loc)
        print(f'GET {loc}: {r3.status}')
        cookies = list(cookie_jar)
        print(f'Cookies: {[c.name for c in cookies]}')
import subprocess
import time
import sys

# Start server in background
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"])
print(f"Server started with PID: {proc.pid}")
time.sleep(3)

import requests
session = requests.Session()
r = session.post('http://localhost:8001/auth/login', data={'email': 'admin@admin.com', 'senha': 'admin123'}, allow_redirects=True)
print('Login:', r.status_code, 'URL:', r.url)

r = session.get('http://localhost:8001/nfe/emitir/consolidacao/2')
print('NFe:', r.status_code, 'URL:', r.url, 'Len:', len(r.text))
if 'Consolida' in r.text:
    print('SUCCESS - NFe page!')
elif 'Login' in r.text:
    print('Redirected to login')
else:
    print('Page:', r.text[:200])

proc.terminate()
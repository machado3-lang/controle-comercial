import subprocess
import json
r = subprocess.run(['curl', '-X', 'POST', 'http://127.0.0.1:8000/auth/login', '-d', 'email=admin@controle.com&senha=admin123', '-c', '-', '-s', '-w', '%{http_code}'], capture_output=True, text=True)
print(f'stdout: {r.stdout[:500]}')
print(f'stderr: {r.stderr[:500]}')
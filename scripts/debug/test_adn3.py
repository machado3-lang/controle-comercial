from dotenv import load_dotenv
import logging
logging.basicConfig(level=logging.INFO)
load_dotenv()

import requests, os, tempfile
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption

cert_path = os.getenv('CERT_PATH', './certs/certificado.pfx')
cert_password = os.getenv('CERT_PASSWORD')

with open(cert_path, 'rb') as f:
    pfx_data = f.read()
private_key, cert, _ = pkcs12.load_key_and_certificates(
    pfx_data,
    password=cert_password.encode() if cert_password else None
)
combined = private_key.private_bytes(
    encoding=Encoding.PEM,
    format=PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=NoEncryption()
) + cert.public_bytes(Encoding.PEM)
tmp_path = os.path.join(tempfile.gettempdir(), 'cert_combined.pem')
with open(tmp_path, 'wb') as f:
    f.write(combined)

session = requests.Session()
session.cert = tmp_path
session.verify = False

# Test 1: contribuintes/dfe/distribuicao
url = "https://adn.nfse.gov.br/contribuintes/dfe/distribuicao?ultNSU=0"
print(f"\n=== GET {url} ===")
r = session.get(url, timeout=15)
print(f"HTTP {r.status_code}")
print(f"Body: {r.text[:1000]}")

# Test 2: Try POST
print(f"\n=== POST {url} ===")
r = session.post(url, timeout=15)
print(f"HTTP {r.status_code}")
print(f"Body: {r.text[:1000]}")

# Test 3: Try SEFIN endpoints (maybe distribution is in SEFIN)
sefin_urls = [
    "https://sefin.nfse.gov.br/SefinNacional/dfe?ultNSU=0",
    "https://sefin.nfse.gov.br/SefinNacional/nfse",
]
for u in sefin_urls:
    print(f"\n=== GET {u} ===")
    r = session.get(u, timeout=15)
    print(f"HTTP {r.status_code}")
    print(f"Body: {r.text[:500]}")

from dotenv import load_dotenv
from services.nfse_betha import BethaNfseService
import logging
logging.basicConfig(level=logging.INFO)
load_dotenv()
s = BethaNfseService()

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

urls = [
    "https://adn.nfse.gov.br/dfe?ultNSU=0",
    "https://adn.nfse.gov.br/contribuintes/dfe?ultNSU=0",
    "https://adn.nfse.gov.br/api/dfe?ultNSU=0",
    "https://adn.nfse.gov.br/dfe/distribuicao?ultNSU=0",
    "https://adn.nfse.gov.br/contribuintes/dfe/distribuicao?ultNSU=0",
]

for url in urls:
    try:
        r = requests.get(url, cert=tmp_path, verify=False, timeout=15)
        print(f"GET {url} => HTTP {r.status_code} ({len(r.text)} bytes)")
        if r.status_code == 200:
            print(f"  JSON: {r.text[:500]}")
    except Exception as e:
        print(f"GET {url} => ERROR: {e}")

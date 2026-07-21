from dotenv import load_dotenv
load_dotenv()
import requests, os, tempfile, time
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

time.sleep(2)  # avoid 429

urls = [
    "https://adn.nfse.gov.br/contribuintes/dfe/distribuicao?NSU=0",
    "https://adn.nfse.gov.br/contribuintes/dfe/distribuicao/0",
    "https://adn.nfse.gov.br/contribuintes/dfe?NSU=0",
    "https://adn.nfse.gov.br/contribuintes/dfe/0",
]
for url in urls:
    try:
        r = session.get(url, timeout=15)
        print(f"GET {url} => HTTP {r.status_code}")
        if r.text:
            print(f"  Body: {r.text[:600]}")
        time.sleep(1)
    except Exception as e:
        print(f"GET {url} => ERROR: {e}")

from dotenv import load_dotenv
load_dotenv()
import base64, gzip, re, logging, requests, os, tempfile
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
logging.basicConfig(level=logging.INFO)

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

# Get first page
import time
time.sleep(1)
r = session.get("https://adn.nfse.gov.br/contribuintes/dfe/800", timeout=30)
data = r.json()
lote = data.get('LoteDFe', [])
print(f"Total docs: {len(lote)}")
if lote:
    item = lote[0]
    xml_b64 = item['ArquivoXml']
    xml = gzip.decompress(base64.b64decode(xml_b64)).decode('utf-8')
    print(f"XML length: {len(xml)} chars")
    print(f"\nFIRST 2000 CHARS:\n{xml[:2000]}")
    # Search for tomador, prestador fields
    print(f"\n--- TOMADOR/PRESTADOR fields ---")
    for m in re.finditer(r'<[^>]*([Tt]omador|[Pp]restador|[Tt]oma)[^>]*>.*?</[^>]*\1[^>]*>', xml, re.DOTALL):
        print(f"\nMatch: {m.group()[:500]}")

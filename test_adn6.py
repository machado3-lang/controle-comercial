from dotenv import load_dotenv
load_dotenv()
import requests, os, tempfile, json, base64, re
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

# Check fields available and date extraction
url = "https://adn.nfse.gov.br/contribuintes/dfe/0"
r = session.get(url, timeout=30)
data = r.json()
print("Top-level keys:", list(data.keys()))
lote = data.get('LoteDFe', [])
if lote:
    item = lote[0]
    print(f"Item keys: {list(item.keys())}")
    print(f"Full item: {json.dumps(item, indent=2)[:500]}")

    # Extract date from XML
    xml_b64 = item['ArquivoXml']
    xml = base64.b64decode(xml_b64).decode('utf-8')
    print(f"\nXML excerpt (dates):")
    for m in re.finditer(r'(dhEmi|dEmi|dCompet|dhCont|dtEmi)[^>]*>[^<]+', xml):
        print(f"  {m.group()}")
    for m in re.finditer(r'(valorTotal|vServ|vDescCondicionado|vBC)[^>]*>([^<]+)', xml):
        print(f"  {m.group()}")
    for m in re.finditer(r'(tomadorNome|tomadorCnpj|tomadorCpf|prestadorNome|prestadorCnpj)[^>]*>([^<]+)', xml):
        print(f"  {m.group()}")

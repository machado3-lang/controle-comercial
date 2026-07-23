from dotenv import load_dotenv
load_dotenv()
import requests, os, tempfile, json, base64, gzip
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

# Test with ultNSU=0
url = "https://adn.nfse.gov.br/contribuintes/dfe/0"
print(f"GET {url}")
r = session.get(url, timeout=30)
print(f"HTTP {r.status_code}")
data = r.json()
print(f"StatusProcessamento: {data.get('StatusProcessamento')}")
lote = data.get('LoteDFe', [])
print(f"Total no lote: {len(lote)}")
if lote:
    ult_nsu = data.get('ultNSU')
    print(f"ultNSU: {ult_nsu}")
    for item in lote[:5]:
        print(f"  NSU={item['NSU']} Chave={item['ChaveAcesso'][:15]}... Tipo={item.get('TipoDocumento')}")
        xml_b64 = item.get('ArquivoXml', '')
        if xml_b64:
            try:
                xml_bytes = base64.b64decode(xml_b64)
                # Try gzip decompress
                try:
                    xml = gzip.decompress(xml_bytes).decode('utf-8')
                except:
                    xml = xml_bytes.decode('utf-8')
                print(f"  XML({len(xml)} chars): {xml[:300]}")
            except Exception as e:
                print(f"  Erro decodificar XML: {e}")

# Now test with the ultNSU from first response
if lote and ult_nsu and ult_nsu != 0:
    import time
    time.sleep(1)
    url2 = f"https://adn.nfse.gov.br/contribuintes/dfe/{ult_nsu}"
    print(f"\nGET {url2}")
    r2 = session.get(url2, timeout=30)
    print(f"HTTP {r2.status_code}")
    data2 = r2.json()
    lote2 = data2.get('LoteDFe', [])
    print(f"Total no lote 2: {len(lote2)}")

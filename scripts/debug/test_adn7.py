from dotenv import load_dotenv
load_dotenv()
import requests, os, tempfile, json, base64, gzip, re
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

url = "https://adn.nfse.gov.br/contribuintes/dfe/0"
r = session.get(url, timeout=30)
data = r.json()
lote = data.get('LoteDFe', [])
print(f"Total: {len(lote)}")
print(f"DataHoraProcessamento: {data.get('DataHoraProcessamento')}")

for item in lote[:3]:
    print(f"\n--- NSU={item['NSU']} ---")
    print(f"  ChaveAcesso: {item['ChaveAcesso']}")
    print(f"  TipoDocumento: {item['TipoDocumento']}")
    print(f"  DataHoraGeracao: {item.get('DataHoraGeracao')}")
    xml_b64 = item['ArquivoXml']
    xml = gzip.decompress(base64.b64decode(xml_b64)).decode('utf-8')
    
    # Extract key fields
    for field in ['dEmi', 'dhEmi', 'dCompet', 'vServ', 'vDescCondicionado', 'vBC', 'vISS', 'vServ', 'vTotalServicos', 'xNomeTomador', 'CNPJTomador', 'CPFTomador', 'CPNTomador', 'xNomePrestador', 'CNPJPrestador', 'nNFSe', 'serieNFSe', 'cStat']:
        m = re.search(rf'<[^:>]*:?{field}[^>]*>([^<]+)</', xml)
        if m:
            print(f"  {field}: {m.group(1)}")

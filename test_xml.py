from dotenv import load_dotenv
load_dotenv()
from services.nfse_betha import BethaNfseService, ADN_DFE_URL
import base64, gzip, re, logging
logging.basicConfig(level=logging.INFO)

s = BethaNfseService()
r = s.listar_nfse_adn('2026-07-01', '2026-07-11', max_paginas=2)

for n in r[:3]:
    print(f"\n=== NFSe #{n['numero']} ===")
    print(f"  chave={n['chaveAcesso']}")
    print(f"  tomador_nome={n['tomador_nome']}")
    print(f"  tomador_cnpj={n['tomador_cnpj']}")
    xml = n['xml']
    # Find ALL tag names in the XML (first 100 unique)
    tags = set(re.findall(r'<(\w+)[>\s]', xml))
    print(f"  Tags nas primeiras 2000 chars: {sorted(tags)}")
    # Search for tomador-related content
    for m in re.finditer(r'(?i)(tomador|prestador|Nome|Cnpj|Cpf)[^>]*>[^<]*<', xml[:5000]):
        tag_full = re.search(r'<([^>]+)>[^<]*<', m.group())
        if tag_full:
            val = re.search(r'>([^<]*)<', m.group())
            print(f"  MATCH: <{tag_full.group(1)}> = {val.group(1) if val else ''}")

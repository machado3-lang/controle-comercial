from dotenv import load_dotenv
from services.nfse_betha import BethaNfseService
import logging
logging.basicConfig(level=logging.INFO)
load_dotenv()
s = BethaNfseService()
r = s.listar_nfse_adn('2026-07-01', '2026-07-11', max_paginas=3)
print(f"Total: {len(r)}")
for n in r[:3]:
    print(f"  NSU={n['NSU']} chave={str(n['chaveAcesso'])[:20] if n['chaveAcesso'] else '-'}... valor={n['valor']}")

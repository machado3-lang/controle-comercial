from dotenv import load_dotenv
load_dotenv()
from services.nfse_betha import BethaNfseService
import logging
logging.basicConfig(level=logging.INFO)

s = BethaNfseService()
r = s.listar_nfse_adn('2023-09-01', '2024-12-31', max_paginas=2)
print(f"\n=== TOTAL: {len(r)} NFS-e no período ===")
for n in r[:5]:
    print(f"  NSU={n['NSU']} NFSe#{n['numero']} R${n['valor']} {n['dhEmi'][:10]} tomador={n['tomador_nome']}")

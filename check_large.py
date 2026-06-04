import json

with open('backup.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Verificar valores grandes em clientes
for row in data.get('clientes', []):
    for k, v in row.items():
        if isinstance(v, int) and v > 2147483647:
            print(f"Cliente id={row.get('id')} campo {k}={v} MUITO GRANDE")
            break
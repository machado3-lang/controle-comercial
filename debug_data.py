import json

with open('backup.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Verificar clientes
for r in data.get('clientes', []):
    codigo = r.get('codigo')
    if codigo in ('', None):
        print("Cliente sem codigo:", r.get('id'), r.get('nome'))
        break

# Verificar empresa
emp = data.get('empresa', [{}])[0]
for k, v in emp.items():
    if isinstance(v, str) and len(v) > 300:
        print(f"Empresa campo {k}: {len(v)} chars")
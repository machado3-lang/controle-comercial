import re, os

router_dir = 'C:/Controle de Serviços/routers'
found = False

for fname in sorted(os.listdir(router_dir)):
    if not fname.endswith('.py'):
        continue
    fpath = os.path.join(router_dir, fname)
    with open(fpath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines, 1):
        # Find session assignments
        m = re.match(r'.*request\.session\["[^\]]*"\]\s*=\s*(.+)', line)
        if not m:
            continue
        val = m.group(1).strip().split('#')[0].strip()
        # Skip simple types
        if re.match(r'^["\']|True|False|None|\d+', val):
            continue
        # Check if value comes from DB attribute (potential Decimal)
        if re.search(r'\.(preco|total|quantidade|valor|saldo|desconto|preco_unitario|preco_custo|preco_adicional|quantidade_padrao|aliquota|base_calculo|icms)', val):
            if 'float(' not in val and 'int(' not in val and 'str(' not in val:
                print(f'WARNING: {fname}:{i} - {val.strip()}')
                found = True

if not found:
    print('No Decimal session values found - all properly converted')

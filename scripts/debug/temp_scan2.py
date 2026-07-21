import glob, re
templates = set()
for f in glob.glob("routers/*.py"):
    try:
        with open(f, errors="ignore") as fh:
            content = fh.read()
        for m in re.findall(r'TemplateResponse\("([^"]+)"', content):
            templates.add(m)
        for m in re.findall(r"TemplateResponse\('([^']+)'", content):
            templates.add(m)
    except Exception as e:
        print(f"ERRO {f}: {e}")
print("\n".join(sorted(templates)))
print("---")
print(f"Total: {len(templates)}")

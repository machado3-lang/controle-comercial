import glob, re
templates=set()
for f in glob.glob("routers/*.py"):
    content = open(f, encoding="utf-8", errors="ignore").read()
    for m in re.findall('TemplateResponse\("([^"]+)"', content):
        templates.add(m)
    for m in re.findall("TemplateResponse\('([^']+)'", content):
        templates.add(m)
print('\n'.join(sorted(templates)))
print("---")
print(f"Total: {len(templates)}")

import urllib.request
html = urllib.request.urlopen("http://127.0.0.1:3000/produtos/997/editar").read().decode('utf-8')

# Verificar se a seção está visível (sem 'hidden')
section_start = html.find('insumos-section')
if section_start > 0:
    section_snippet = html[section_start:section_start+200]
    if 'hidden' in section_snippet:
        print("ERRO: Seção insumos está HIDDEN")
    else:
        print("OK: Seção insumos está VISÍVEL")
else:
    print("ERRO: Seção insumos não encontrada")

# Verificar se os itens aparecem
if "Cartão Teste" in html:
    print("OK: Cartão Teste encontrado")
else:
    print("ERRO: Cartão Teste NÃO encontrado")
    
if "Cordão Teste" in html:
    print("OK: Cordão Teste encontrado")
else:
    print("ERRO: Cordão Teste NÃO encontrado")
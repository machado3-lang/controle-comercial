with open('templates/base.html', 'r', encoding='utf-8') as f: 
    content = f.read() 
with open('templates/base.html', 'w', encoding='utf-8') as f: 
    f.write(content) 
print('OK') 

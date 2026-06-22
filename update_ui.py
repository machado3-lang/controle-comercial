with open('templates/base.html', 'r', encoding='utf-8') as f: 
    content = f.read() 
content = content.replace('bg-slate-900/60 backdrop-blur-md', 'bg-slate-950/40 backdrop-blur-md') 
content = content.replace('border-slate-800/80', 'border-white/10') 
content = content.replace('container-fluid px-4', 'w-full max-w-7xl mx-auto container-fluid px-4') 
with open('templates/base.html', 'w', encoding='utf-8') as f: 
    f.write(content) 
print('OK') 

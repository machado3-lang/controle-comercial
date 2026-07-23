import re
import os

templates_dir = 'templates'
updated = 0

for root, dirs, files in os.walk(templates_dir):
    for file in files:
        if file.endswith('.html'):
            filepath = os.path.join(root, file)
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Remove duplicate csrf_token lines first
            content = re.sub(r'(<input type="hidden" name="csrf_token" value="{{ csrf_token\(\) }}">)\s*\n\s*\1+', r'\1', content)
            
            # Find forms without csrf_token
            forms = re.findall(r'(<form method="post"[^>]*>)', content)
            
            for form in forms:
                if 'csrf_token' not in form:
                    # Add hidden input after the opening form tag
                    new_form = form + '\n    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">'
                    content = content.replace(form, new_form)
                    updated += 1

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)

print(f'Updated {updated} forms')
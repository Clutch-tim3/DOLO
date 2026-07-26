import os, re
files = ['index.html', 'sort.html', 'system.html', 'accuracy.html', 'vault.html', 'calendar.html', 'workspace.html']
old_url = r'https://fonts\.googleapis\.com/css2[^\"]*'
new_url = 'https://fonts.googleapis.com/css2?family=Cinzel:wght@400;500;600;700;800&family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Bebas+Neue&family=DM+Serif+Display:ital@0;1&family=Inter:wght@400;500&display=swap'

for f in files:
    path = os.path.join('static', f)
    with open(path, 'r', encoding='utf-8') as fh:
        content = fh.read()
    
    if re.search(old_url, content):
        content = re.sub(old_url, new_url, content)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(content)
        print(f'Updated fonts in {f}')
    else:
        print(f'No match found in {f}')

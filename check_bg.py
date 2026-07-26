import sys
with open('static/workspace.html', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if 'style="' in line and 'background' in line and 'rgba(' in line:
            print(f'{i+1}: {line.strip()[:100]}')

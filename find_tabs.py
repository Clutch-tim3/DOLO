with open('static/workspace.html', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if 'id=\"tab-' in line:
            print(f'{i+1}: {line.strip()}')

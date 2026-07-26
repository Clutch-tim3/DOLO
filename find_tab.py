with open('static/workspace.html', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if 'id="tab-quotation"' in line:
            print(f'Found at line {i+1}')

import glob, re

for f in glob.glob('static/*.html'):
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    keys = re.findall(r'localStorage\.(?:getItem|setItem)\([\'"]([^\'"]+)[\'"]', content)
    print(f, set(keys))

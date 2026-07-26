import re

with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

# 1. Remove the color: var(--text-primary) block from fix_ui.py
regex1 = r'html\[data-theme="light"\] \.corner-btn,\nbody\.light-theme \.corner-btn \{\n    color: var\(--text-primary\) !important;\n\}'
css = re.sub(regex1, '', css, flags=re.DOTALL)

# 2. Remove the background: transparent hover block from fix_ui.py
regex2 = r'html\[data-theme="light"\] \.corner-btn:hover,\nbody\.light-theme \.corner-btn:hover \{\n    background: transparent !important;\n\}'
css = re.sub(regex2, '', css, flags=re.DOTALL)

# 3. Add the complete Ink/Cream button colors for Clive light mode
clive_btn_css = """
html[data-theme="light"] .corner-btn,
body.light-theme .corner-btn,
html[data-theme="light"] .submit-btn,
body.light-theme .submit-btn {
    background: var(--text-primary) !important; /* Ink */
    color: var(--bg-color) !important; /* Cream */
    border: none !important;
}
html[data-theme="light"] .corner-btn:hover,
body.light-theme .corner-btn:hover,
html[data-theme="light"] .submit-btn:hover,
body.light-theme .submit-btn:hover {
    background: var(--text-primary) !important; /* Keep Ink on hover */
    color: #8c6d46 !important; /* Orange-ish text on hover */
}
"""

css = css.replace('/* Theme Toggle Button */', clive_btn_css + '\n/* Theme Toggle Button */')

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

print('Applied Ink/Cream button colors for Clive light mode.')

import re

with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

# I will find all selectors targeting `.corner-btn` or `.submit-btn` under `html[data-theme="light"]` or `body.light-theme`
# and remove their blocks.

regex = r'html\[data-theme="light"\] \.corner-btn[^{]*?\{[^}]*?\}'
css = re.sub(regex, '', css, flags=re.DOTALL)

regex2 = r'body\.light-theme \.corner-btn[^{]*?\{[^}]*?\}'
css = re.sub(regex2, '', css, flags=re.DOTALL)

regex3 = r'html\[data-theme="light"\] \.submit-btn[^{]*?\{[^}]*?\}'
css = re.sub(regex3, '', css, flags=re.DOTALL)

regex4 = r'body\.light-theme \.submit-btn[^{]*?\{[^}]*?\}'
css = re.sub(regex4, '', css, flags=re.DOTALL)

# Just to be safe, any blocks that still have multiple selectors (like corner-btn AND submit-btn)
# were handled by the first regex if it started with it. But let's check for any lingering blocks.
# Let's remove the specific block I added in fix_btn.py manually if it's still there.

block_to_remove = r'''
html\[data-theme=\"light\"\] \.corner-btn,
body\.light-theme \.corner-btn,
html\[data-theme=\"light\"\] \.submit-btn,
body\.light-theme \.submit-btn \{
    background: var\(--text-primary\) !important; \/\* Ink \*\/
    color: var\(--bg-color\) !important; \/\* Cream \*\/
    border: none !important;
\}
html\[data-theme=\"light\"\] \.corner-btn:hover,
body\.light-theme \.corner-btn:hover,
html\[data-theme=\"light\"\] \.submit-btn:hover,
body\.light-theme \.submit-btn:hover \{
    background: var\(--text-primary\) !important; \/\* Keep Ink on hover \*\/
    color: #8c6d46 !important; \/\* Orange-ish text on hover \*\/
\}
'''

css = re.sub(block_to_remove.strip(), '', css, flags=re.DOTALL)

# Let's also remove any other weird overrides we added.
# Like the one from fix_ui.py
block_to_remove_2 = r'''html\[data-theme=\"light\"\] \.corner-btn,
body\.light-theme \.corner-btn \{
    color: var\(--text-primary\) !important;
\}
html\[data-theme=\"light\"\] \.corner-btn \.bracket::before,
html\[data-theme=\"light\"\] \.corner-btn \.bracket::after,
body\.light-theme \.corner-btn \.bracket::before,
body\.light-theme \.corner-btn \.bracket::after \{
    border-color: #8c6d46 !important; \/\* Original light mode gold/orange \*\/
\}
html\[data-theme=\"light\"\] \.corner-btn:hover,
body\.light-theme \.corner-btn:hover \{
    background: transparent !important;
\}'''

css = re.sub(block_to_remove_2, '', css, flags=re.DOTALL)


with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

print('Cleaned up corner-btn overrides.')

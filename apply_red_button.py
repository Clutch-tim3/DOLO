import os, re

css_rule = """
/* ── CLIVE LIGHT MODE SIGNATURE BUTTON ANIMATION (#C8331F) ── */
html[data-theme="light"] .corner-btn,
body.light-theme .corner-btn {
    color: var(--text-primary, #141210) !important;
}
html[data-theme="light"] .corner-btn .bracket-tr,
body.light-theme .corner-btn .bracket-tr {
    border-top-color: #C8331F !important;
    border-right-color: #C8331F !important;
}
html[data-theme="light"] .corner-btn .bracket-bl,
body.light-theme .corner-btn .bracket-bl {
    border-bottom-color: #C8331F !important;
    border-left-color: #C8331F !important;
}
html[data-theme="light"] .corner-btn:hover,
body.light-theme .corner-btn:hover {
    color: #ffffff !important;
}
html[data-theme="light"] .corner-btn:hover .bracket-tr,
body.light-theme .corner-btn:hover .bracket-tr,
html[data-theme="light"] .corner-btn:hover .bracket-bl,
body.light-theme .corner-btn:hover .bracket-bl {
    background-color: #C8331F !important;
}
"""

with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

if 'CLIVE LIGHT MODE SIGNATURE BUTTON ANIMATION' not in css:
    css += '\n' + css_rule
else:
    # replace existing block if already present
    css = re.sub(r'/\* ── CLIVE LIGHT MODE SIGNATURE BUTTON ANIMATION.*?\#C8331F !important;\n\}', css_rule.strip(), css, flags=re.DOTALL)

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

print('Updated static/style.css successfully.')

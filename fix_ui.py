import os, re

with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

# 1. Fix `#ffffff` blocks
# Replace `#ffffff` with `var(--bg-color)` in specific areas that were aggressive overrides
css = css.replace('background-color: #ffffff !important;', 'background-color: var(--bg-color) !important;')
css = css.replace('background: #ffffff !important;', 'background: var(--bg-color) !important;')
css = css.replace('--input-bg: #ffffff;', '--input-bg: var(--bg-color);')

# 2. Fix Signature Button Animation
# I previously added:
# html[data-theme="light"] .corner-btn ... { background: var(--text-primary) !important; color: var(--bg-color) !important; border: none !important; }
# html[data-theme="light"] .corner-btn:hover { background: var(--accent-gold) !important; }
# html[data-theme="light"] .corner-btn .bracket { display: none !important; }
# I'll rip that out and replace it with something that keeps the animation but adapts to light mode colors.

btn_regex = r'html\[data-theme="light"\] \.corner-btn.*?\/\* Remove brackets in Clive mode \*\/\n\}'
btn_replacement = """html[data-theme="light"] .corner-btn,
body.light-theme .corner-btn {
    color: var(--text-primary) !important;
}
html[data-theme="light"] .corner-btn .bracket::before,
html[data-theme="light"] .corner-btn .bracket::after,
body.light-theme .corner-btn .bracket::before,
body.light-theme .corner-btn .bracket::after {
    border-color: var(--accent-gold) !important; /* Red brackets in light mode */
}
html[data-theme="light"] .corner-btn:hover,
body.light-theme .corner-btn:hover {
    background: transparent !important;
}
"""

if re.search(btn_regex, css, re.DOTALL):
    css = re.sub(btn_regex, btn_replacement, css, flags=re.DOTALL)
else:
    print("WARNING: Could not find corner-btn overrides via regex.")

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

print("Updates applied to style.css")

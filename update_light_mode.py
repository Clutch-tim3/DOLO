import os, re

with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

# The new variables for light mode
new_light_vars = """[data-theme="light"] {
    --bg-color: #F2EAD8;
    --text-primary: #141210;
    --text-white: #141210;
    --text-muted: #6B6862;
    --accent-gold: #C8331F;
    --border-faint: #DCD5C5;
    --card-bg: #F2EAD8;
    --sidebar-bg: #F2EAD8;
    --topbar-bg: rgba(242, 234, 216, 0.95);
    --input-bg: #ffffff;
    --input-border: #DCD5C5;
    --font-heading: 'Bebas Neue', sans-serif;
    --font-subheading: 'DM Serif Display', serif;
    --font-body: 'Inter', sans-serif;
    --font-mono: 'JetBrains Mono', monospace;
}"""

# We replace the [data-theme="light"] block (lines 21-33)
css = re.sub(r'\[data-theme="light"\].*?\}', new_light_vars, css, flags=re.DOTALL)

# The new general light mode overrides (replacing the block 35-145)
new_light_overrides = """/* ── CLIVE LIGHT MODE OVERRIDES ── */
html[data-theme="light"],
html[data-theme="light"] body,
body.light-theme,
body.light-theme .workspace-main,
html[data-theme="light"] .workspace-main {
    background-color: var(--bg-color) !important;
    background: var(--bg-color) !important;
    color: var(--text-primary) !important;
    font-family: var(--font-body) !important;
}

html[data-theme="light"] h1,
html[data-theme="light"] h2,
html[data-theme="light"] h3,
html[data-theme="light"] .logo,
html[data-theme="light"] .brand-logo,
html[data-theme="light"] .block-title,
html[data-theme="light"] .hero-title,
html[data-theme="light"] .tab-title-display,
body.light-theme h1, body.light-theme h2, body.light-theme h3 {
    font-family: var(--font-heading) !important;
    color: var(--text-primary) !important;
    text-transform: uppercase !important;
    letter-spacing: 0.5px !important;
}

html[data-theme="light"] .category-label,
body.light-theme .category-label,
html[data-theme="light"] .brand-badge,
body.light-theme .brand-badge {
    font-family: var(--font-mono) !important;
    text-transform: uppercase !important;
    letter-spacing: 1.5px !important;
}

html[data-theme="light"] .sidebar,
body.light-theme .sidebar {
    background-color: var(--bg-color) !important;
    background: var(--bg-color) !important;
    border-right: 0.5px solid var(--border-faint) !important;
}

html[data-theme="light"] .top-bar,
html[data-theme="light"] .top-nav,
body.light-theme .top-bar,
body.light-theme .top-nav {
    background-color: var(--topbar-bg) !important;
    background: var(--topbar-bg) !important;
    border-bottom: 2px solid var(--text-primary) !important; /* Masthead border rule */
}

html[data-theme="light"] .guided-text,
body.light-theme .guided-text,
html[data-theme="light"] .disclaimer,
body.light-theme .disclaimer {
    font-family: var(--font-subheading) !important;
    font-style: italic !important;
    font-weight: 400 !important;
    font-size: 14px !important;
}

html[data-theme="light"] .ws-card,
html[data-theme="light"] .profile-card,
html[data-theme="light"] .doc-block,
body.light-theme .ws-card,
body.light-theme .profile-card,
body.light-theme .doc-block {
    background-color: var(--bg-color) !important;
    background: var(--bg-color) !important;
    border: 0.5px solid var(--border-faint) !important;
    box-shadow: none !important;
    border-radius: 6px !important;
}

html[data-theme="light"] .form-input,
html[data-theme="light"] .form-select,
body.light-theme .form-input,
body.light-theme .form-select {
    background-color: #ffffff !important;
    background: #ffffff !important;
    border: 0.5px solid var(--border-faint) !important;
    border-radius: 6px !important;
    color: var(--text-primary) !important;
}

html[data-theme="light"] .file-drop-area,
body.light-theme .file-drop-area {
    background-color: #ffffff !important;
    background: #ffffff !important;
    border: 0.5px solid var(--border-faint) !important;
}

html[data-theme="light"] .corner-btn,
body.light-theme .corner-btn,
html[data-theme="light"] .primary-btn {
    background: var(--text-primary) !important;
    color: var(--bg-color) !important;
    border-radius: 6px !important;
    font-family: var(--font-body) !important;
    font-weight: 500 !important;
    border: none !important;
}
html[data-theme="light"] .corner-btn:hover,
body.light-theme .corner-btn:hover {
    background: var(--accent-gold) !important; /* Red hover */
}
html[data-theme="light"] .corner-btn .bracket {
    display: none !important; /* Remove brackets in Clive mode */
}

html[data-theme="light"] .nav-item,
body.light-theme .nav-item {
    color: var(--text-muted) !important;
}

html[data-theme="light"] .nav-item:hover,
body.light-theme .nav-item:hover {
    background-color: rgba(200, 51, 31, 0.04) !important;
    color: var(--text-primary) !important;
}

html[data-theme="light"] .nav-item.active,
body.light-theme .nav-item.active {
    background-color: rgba(200, 51, 31, 0.04) !important;
    color: var(--text-primary) !important;
    border-left: 2px solid var(--accent-gold) !important;
}
"""

css = re.sub(r'/\* ── BULLETPROOF LIGHT MODE OVERRIDES ── \*/.*?/\* Theme Toggle Button \*/', new_light_overrides + '\n/* Theme Toggle Button */', css, flags=re.DOTALL)

# Now we replace the aggressive overrides at the bottom
new_aggressive = """/* ── AGGRESSIVE LIGHT MODE REFINEMENTS FOR CONSOLE & SYSTEM PAGES (CLIVE) ── */

html[data-theme="light"] .stat-cell,
html[data-theme="light"] .model-card,
body.light-theme .stat-cell,
body.light-theme .model-card {
    background-color: var(--bg-color) !important;
    background: var(--bg-color) !important;
    border: 0.5px solid var(--border-faint) !important;
    box-shadow: none !important;
    border-radius: 6px !important;
}

html[data-theme="light"] .feature-item,
body.light-theme .feature-item {
    border-bottom: 0.5px solid var(--border-faint) !important;
}

html[data-theme="light"] select,
html[data-theme="light"] option,
body.light-theme select,
body.light-theme option {
    background: #ffffff !important;
    color: var(--text-primary) !important;
    border: 0.5px solid var(--border-faint) !important;
}

html[data-theme="light"] .bulk-sort-panel,
body.light-theme .bulk-sort-panel {
    background-color: var(--bg-color) !important;
    background: var(--bg-color) !important;
    border-right: 0.5px solid var(--border-faint) !important;
}

html[data-theme="light"] .specs-table th,
html[data-theme="light"] .specs-table td,
body.light-theme .specs-table th,
body.light-theme .specs-table td {
    border-bottom: 0.5px solid var(--border-faint) !important;
}

/* Force light backgrounds for inline styled dark divs */
html[data-theme="light"] div[style*="background: rgba(15,15,20,0.6)"],
html[data-theme="light"] div[style*="background: rgba(0,0,0,0.3)"],
html[data-theme="light"] div[style*="background: rgba(0,0,0,0.2)"],
html[data-theme="light"] div[style*="background: #0a0a0a"],
html[data-theme="light"] div[style*="background: rgba(255,255,255,0.05)"],
html[data-theme="light"] div[style*="background: rgba(0,0,0,0.8)"],
body.light-theme div[style*="background: rgba(15,15,20,0.6)"],
body.light-theme div[style*="background: rgba(0,0,0,0.3)"],
body.light-theme div[style*="background: rgba(0,0,0,0.2)"],
body.light-theme div[style*="background: #0a0a0a"],
body.light-theme div[style*="background: rgba(255,255,255,0.05)"],
body.light-theme div[style*="background: rgba(0,0,0,0.8)"] {
    background: var(--bg-color) !important;
    background-color: var(--bg-color) !important;
    border: 0.5px solid var(--border-faint) !important;
    border-radius: 6px !important;
}

html[data-theme="light"] .ds-card,
body.light-theme .ds-card {
    background-color: var(--bg-color) !important;
    background: var(--bg-color) !important;
    border: 0.5px solid var(--border-faint) !important;
    border-radius: 6px !important;
}

/* Force dark text for inline styled gray/white text */
html[data-theme="light"] span[style*="color: #aaa"],
html[data-theme="light"] span[style*="color: #ccc"],
html[data-theme="light"] span[style*="color: #999"],
html[data-theme="light"] div[style*="color: #aaa"],
html[data-theme="light"] div[style*="color: #ccc"],
html[data-theme="light"] div[style*="color: #999"],
html[data-theme="light"] p[style*="color: #aaa"],
html[data-theme="light"] p[style*="color: #ccc"],
html[data-theme="light"] p[style*="color: #999"],
body.light-theme span[style*="color: #aaa"],
body.light-theme span[style*="color: #ccc"],
body.light-theme span[style*="color: #999"],
body.light-theme div[style*="color: #aaa"],
body.light-theme div[style*="color: #ccc"],
body.light-theme div[style*="color: #999"],
body.light-theme p[style*="color: #aaa"],
body.light-theme p[style*="color: #ccc"],
body.light-theme p[style*="color: #999"] {
    color: var(--text-muted) !important;
}

html[data-theme="light"] div[style*="color: #fff"],
html[data-theme="light"] span[style*="color: #fff"],
html[data-theme="light"] div[style*="color:#fff"],
html[data-theme="light"] span[style*="color:#fff"],
body.light-theme div[style*="color: #fff"],
body.light-theme span[style*="color: #fff"],
body.light-theme div[style*="color:#fff"],
body.light-theme span[style*="color:#fff"] {
    color: var(--text-primary) !important;
}

/* Hardcoded Table Rows in sort logic */
html[data-theme="light"] .specs-table tbody tr,
body.light-theme .specs-table tbody tr {
    background: var(--bg-color) !important;
    border-bottom: 0.5px solid var(--border-faint) !important;
}

/* Hardcoded Batch Sorting Panel File list */
html[data-theme="light"] #batch_files_list > div,
body.light-theme #batch_files_list > div {
    background: var(--bg-color) !important;
    border: 0.5px solid var(--border-faint) !important;
    color: var(--text-primary) !important;
}
"""

css = re.sub(r'/\* ── AGGRESSIVE LIGHT MODE REFINEMENTS FOR CONSOLE & SYSTEM PAGES ── \*/.*', new_aggressive, css, flags=re.DOTALL)

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(css)
print("Updated style.css with Clive Light Mode")

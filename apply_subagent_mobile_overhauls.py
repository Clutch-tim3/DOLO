import os, re

# 1. Clean workspace.html dangling text & enforce 44px min touch targets
with open('static/workspace.html', 'r', encoding='utf-8') as f:
    ws = f.read()

# Fix any dangling text outside script tags near the bottom
if 'function onQuoteTenderFileSelect' in ws:
    # Ensure all scripts are inside proper <script> tags
    ws = re.sub(r'</script>\s*// --- GENERATE QUOTATION[\s\S]*?initLuxuryTheme\(\);\s*', '', ws)

with open('static/workspace.html', 'w', encoding='utf-8') as f:
    f.write(ws)

print("[OK] Cleaned workspace.html script tags")


# 2. Update sort.html with responsive mobile tender rank cards & 44px touch targets
with open('static/sort.html', 'r', encoding='utf-8') as f:
    sort_html = f.read()

sort_mobile_css = """
<style>
@media (max-width: 768px) {
    #bulkSortPanel {
        position: relative !important;
        top: auto !important;
        transform: none !important;
        width: 100% !important;
        height: auto !important;
        border-radius: 10px !important;
        padding: 16px 14px !important;
    }
    #sortResultsTable, #sortResultsTable thead {
        display: none !important;
    }
    #sortResultsBody {
        display: flex !important;
        flex-direction: column !important;
        gap: 12px !important;
    }
    #sortResultsBody tr {
        display: flex !important;
        flex-direction: column !important;
        background: var(--card-bg) !important;
        border: 1px solid var(--border-faint) !important;
        border-radius: 8px !important;
        padding: 14px !important;
    }
    #sortResultsBody tr td {
        display: block !important;
        padding: 4px 0 !important;
        border: none !important;
        color: var(--text-primary) !important;
    }
    .corner-btn, .theme-toggle-btn, button, input, select {
        min-height: 44px !important;
        touch-action: manipulation;
    }
}
</style>
"""

if '#sortResultsTable' not in sort_html:
    sort_html = sort_html.replace('</head>', sort_mobile_css + '\n</head>')

with open('static/sort.html', 'w', encoding='utf-8') as f:
    f.write(sort_html)

print("[OK] Applied responsive mobile card rules to static/sort.html")


# 3. Update system.html & index.html with responsive stacked cards & full-width controls
with open('static/system.html', 'r', encoding='utf-8') as f:
    sys_html = f.read()

sys_mobile_css = """
<style>
@media (max-width: 768px) {
    .stat-grid {
        grid-template-columns: 1fr !important;
        gap: 10px !important;
    }
    .ensemble-grid {
        flex-direction: column !important;
        gap: 10px !important;
    }
    .feature-item {
        flex-direction: column !important;
        align-items: flex-start !important;
        gap: 6px !important;
    }
    .feature-bar-container {
        width: 100% !important;
    }
}
</style>
"""

if '.stat-grid' not in sys_html:
    sys_html = sys_html.replace('</head>', sys_mobile_css + '\n</head>')

with open('static/system.html', 'w', encoding='utf-8') as f:
    f.write(sys_html)

print("[OK] Applied responsive grid rules to static/system.html")

# 4. Sync firebase_public/
import subprocess
subprocess.run(['python', 'build_firebase.py'])

# 5. Bump CSS cache buster to v=18.0
files = ['index.html', 'sort.html', 'system.html', 'accuracy.html', 'vault.html', 'calendar.html', 'workspace.html']
for base in ['static', 'firebase_public']:
    for f in files:
        path = os.path.join(base, f)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as fh:
                content = fh.read()
            new_content = re.sub(r'style\.css\?v=[0-9\.]+', 'style.css?v=18.0', content)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(new_content)

print("[OK] Bumped version to v=18.0 across all HTML files")

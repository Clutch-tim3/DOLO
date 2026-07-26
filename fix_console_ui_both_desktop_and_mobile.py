import os, re

# 1. Clean up style.css so Desktop Console is 100% restored & Mobile is isolated
with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

# Remove any un-scoped desktop padding-top: 88px on .workspace-main
css = re.sub(r'(\.workspace-main[^{]*?\{[^}]*?padding-top:\s*88px\s*!important;[^}]*?\})', '', css)

# Explicit Desktop Console Layout rules (min-width: 769px)
desktop_console_rules = """

/* ── DESKTOP CONSOLE PRISTINE WORKSPACE SYSTEM (min-width: 769px) ── */
@media (min-width: 769px) {
    html, body {
        height: 100vh !important;
        overflow: hidden !important;
    }
    body {
        display: flex !important;
        flex-direction: row !important;
    }
    .sidebar {
        width: 260px !important;
        min-width: 260px !important;
        height: 100vh !important;
        display: flex !important;
        flex-direction: column !important;
        justify-content: space-between !important;
        z-index: 100 !important;
    }
    .workspace-main {
        flex: 1 !important;
        height: 100vh !important;
        display: flex !important;
        flex-direction: column !important;
        overflow-y: auto !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
        padding-left: 0 !important;
        padding-right: 0 !important;
    }
    .top-bar {
        display: flex !important;
        padding: 16px 32px !important;
        justify-content: space-between !important;
        align-items: center !important;
        width: 100% !important;
        box-sizing: border-box !important;
    }
    .guided-banner {
        margin: 24px 32px 0 32px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
    }
    .content-section {
        padding: 28px 32px !important;
    }
    .mobile-app-header, .mobile-bottom-bar {
        display: none !important;
    }
}

/* ── NATIVE MOBILE CONSOLE SYSTEM (max-width: 768px) ── */
@media (max-width: 768px) {
    body {
        display: block !important;
        height: auto !important;
        min-height: 100vh !important;
        overflow-y: auto !important;
    }
    .sidebar, .top-bar {
        display: none !important;
    }
    .mobile-app-header {
        display: flex !important;
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        width: 100% !important;
        height: 56px !important;
        z-index: 2000 !important;
        align-items: center !important;
        justify-content: space-between !important;
        padding: 0 16px !important;
        box-sizing: border-box !important;
    }
    .mobile-bottom-bar {
        display: flex !important;
        position: fixed !important;
        bottom: 0 !important;
        left: 0 !important;
        width: 100% !important;
        height: 64px !important;
        z-index: 2000 !important;
        align-items: center !important;
        justify-content: space-around !important;
        box-sizing: border-box !important;
    }
    .workspace-main {
        padding-top: 68px !important;
        padding-bottom: 80px !important;
        padding-left: 14px !important;
        padding-right: 14px !important;
        width: 100% !important;
        height: auto !important;
        min-height: 100vh !important;
        box-sizing: border-box !important;
    }
    .guided-banner {
        margin: 12px 0 0 0 !important;
        flex-direction: column !important;
        padding: 14px !important;
    }
    .content-section {
        padding: 16px 0 !important;
    }
}
"""

if 'DESKTOP CONSOLE PRISTINE WORKSPACE SYSTEM' not in css:
    css = css + '\n' + desktop_console_rules

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

print("[OK] Fixed Desktop & Mobile Console isolation in static/style.css")


# 2. Update workspace.html layout tags
with open('static/workspace.html', 'r', encoding='utf-8') as f:
    ws = f.read()

# Make sure top-nav or mobile-app-header are cleanly positioned
if '<header class="mobile-app-header">' not in ws:
    header_html = """
    <!-- NATIVE MOBILE APP HEADER -->
    <header class="mobile-app-header">
        <div class="mobile-app-brand">
            <span>DONINGTON VALE</span>
            <span class="brand-badge">PRO</span>
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
            <span class="pulse-dot"></span>
            <button type="button" class="theme-toggle-btn" onclick="toggleLuxuryTheme()" style="padding: 4px 10px; font-size: 9px; min-height: 32px;">
                <svg id="themeIconMobile" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
                <span id="themeLabelMobile">THEME</span>
            </button>
        </div>
    </header>
"""
    ws = ws.replace('<body>', '<body>\n' + header_html)

with open('static/workspace.html', 'w', encoding='utf-8') as f:
    f.write(ws)

print("[OK] Verified workspace.html structure")

# 3. Sync firebase_public/
import subprocess
subprocess.run(['python', 'build_firebase.py'])

# 4. Bump CSS version to v=19.0
files = ['index.html', 'sort.html', 'system.html', 'accuracy.html', 'vault.html', 'calendar.html', 'workspace.html']
for base in ['static', 'firebase_public']:
    for f in files:
        path = os.path.join(base, f)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as fh:
                content = fh.read()
            new_content = re.sub(r'style\.css\?v=[0-9\.]+', 'style.css?v=19.0', content)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(new_content)

print("[OK] Bumped version to v=19.0 across static and firebase_public")

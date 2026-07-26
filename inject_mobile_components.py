import os

pages = {
    'workspace.html': 'workspace',
    'sort.html': 'sort',
    'vault.html': 'vault',
    'calendar.html': 'calendar',
    'system.html': 'system',
    'accuracy.html': 'system',
    'index.html': 'workspace'
}

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

bottom_bar_template = """
    <!-- NATIVE MOBILE BOTTOM NAVIGATION BAR -->
    <nav class="mobile-bottom-bar">
        <a href="/workspace" class="mobile-nav-tab {active_workspace}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
            <span>Console</span>
        </a>
        <a href="/sort" class="mobile-nav-tab {active_sort}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
            <span>Sort</span>
        </a>
        <a href="/vault" class="mobile-nav-tab {active_vault}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>
            <span>Vault</span>
        </a>
        <a href="/calendar" class="mobile-nav-tab {active_calendar}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
            <span>Calendar</span>
        </a>
        <a href="/system" class="mobile-nav-tab {active_system}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
            <span>System</span>
        </a>
    </nav>
"""

for page, active_tab in pages.items():
    path = os.path.join('static', page)
    if not os.path.exists(path):
        continue
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()

    # Avoid duplicate injection
    if 'mobile-app-header' in html:
        continue

    # Build active tab mapping
    bottom_bar = bottom_bar_template.format(
        active_workspace='active' if active_tab == 'workspace' else '',
        active_sort='active' if active_tab == 'sort' else '',
        active_vault='active' if active_tab == 'vault' else '',
        active_calendar='active' if active_tab == 'calendar' else '',
        active_system='active' if active_tab == 'system' else ''
    )

    # Inject header right after <body...> tag
    idx_body = html.find('<body')
    if idx_body != -1:
        idx_body_end = html.find('>', idx_body)
        if idx_body_end != -1:
            html = html[:idx_body_end+1] + '\n' + header_html + html[idx_body_end+1:]

    # Inject bottom bar right before </body> tag
    idx_body_close = html.rfind('</body>')
    if idx_body_close != -1:
        html = html[:idx_body_close] + '\n' + bottom_bar + '\n' + html[idx_body_close:]

    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'Injected mobile header and bottom bar into {page}')

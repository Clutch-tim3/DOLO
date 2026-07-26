import os, re

mobile_css = """
/* ── DEDICATED NATIVE MOBILE APPLICATION UI SYSTEM ── */

@media (max-width: 768px) {
    /* Hide desktop sidebar and topbar on mobile */
    .sidebar {
        display: none !important;
    }
    .top-bar {
        display: none !important;
    }

    /* Fixed Mobile App Header */
    .mobile-app-header {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        width: 100% !important;
        height: 56px !important;
        z-index: 2000 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
        padding: 0 16px !important;
        background: var(--topbar-bg, #0a0a0a) !important;
        backdrop-filter: blur(16px) !important;
        -webkit-backdrop-filter: blur(16px) !important;
        border-bottom: 1px solid var(--border-faint) !important;
        box-sizing: border-box !important;
    }
    
    .mobile-app-brand {
        font-family: 'Cinzel', serif !important;
        font-size: 12px !important;
        font-weight: 700 !important;
        color: var(--text-white, #ffffff) !important;
        letter-spacing: 0.18em !important;
        display: flex !important;
        align-items: center !important;
        gap: 6px !important;
    }
    
    /* Fixed Mobile Bottom Navigation Tab Bar */
    .mobile-bottom-bar {
        position: fixed !important;
        bottom: 0 !important;
        left: 0 !important;
        width: 100% !important;
        height: 64px !important;
        z-index: 2000 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: space-around !important;
        background: var(--topbar-bg, #0a0a0a) !important;
        backdrop-filter: blur(16px) !important;
        -webkit-backdrop-filter: blur(16px) !important;
        border-top: 1px solid var(--border-faint) !important;
        box-sizing: border-box !important;
        padding: 4px 6px !important;
    }
    
    .mobile-nav-tab {
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 3px !important;
        flex: 1 !important;
        text-decoration: none !important;
        color: var(--text-muted, #888888) !important;
        font-family: 'Plus Jakarta Sans', 'Inter', sans-serif !important;
        font-size: 9px !important;
        font-weight: 600 !important;
        letter-spacing: 0.5px !important;
        text-transform: uppercase !important;
        padding: 6px 0 !important;
        border-radius: 8px !important;
        transition: all 0.2s ease !important;
    }
    
    .mobile-nav-tab svg {
        width: 18px !important;
        height: 18px !important;
    }
    
    .mobile-nav-tab.active {
        color: var(--accent-gold, #c5a880) !important;
    }
    
    html[data-theme="light"] .mobile-nav-tab.active,
    body.light-theme .mobile-nav-tab.active {
        color: #C8331F !important;
    }

    /* Mobile Main Scrollable Container */
    .workspace-main, .hero-section, .sort-container, .system-container, .vault-container, .calendar-container {
        padding-top: 68px !important;
        padding-bottom: 80px !important;
        padding-left: 16px !important;
        padding-right: 16px !important;
        box-sizing: border-box !important;
        width: 100% !important;
        height: auto !important;
        min-height: 100vh !important;
        overflow-y: auto !important;
    }
    
    /* Native Mobile Modal Bottom Sheet Drawer */
    .prediction-panel {
        top: auto !important;
        bottom: 0 !important;
        right: 0 !important;
        left: 0 !important;
        width: 100% !important;
        max-width: 100% !important;
        height: 85vh !important;
        max-height: 85vh !important;
        border-radius: 20px 20px 0 0 !important;
        border-top: 1px solid var(--border-faint) !important;
        transform: translateY(100%) !important;
        padding: 20px 16px 40px 16px !important;
        box-sizing: border-box !important;
    }
    
    .prediction-panel.active, .prediction-panel.open {
        transform: translateY(0) !important;
    }
}

/* Hide Mobile Components on Desktop */
@media (min-width: 769px) {
    .mobile-app-header, .mobile-bottom-bar {
        display: none !important;
    }
}
"""

with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

if 'DEDICATED NATIVE MOBILE APPLICATION UI SYSTEM' not in css:
    css += '\n' + mobile_css

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

print('Appended mobile UI CSS rules to static/style.css')

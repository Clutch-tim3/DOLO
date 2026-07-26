import os, re

# Master Mobile UI CSS Enhancements
master_mobile_css = """

/* ── MASTER NATIVE MOBILE UI SYSTEM (MAX-EFFORT RESPONSIVE OVERHAUL) ── */

@media (max-width: 768px) {
    /* Touch Target & Hit Area Enforcement (Min 44px) */
    button, input, select, textarea, .corner-btn, .theme-toggle-btn, .mobile-nav-tab, .nav-item {
        min-height: 44px !important;
        touch-action: manipulation;
    }

    /* Safe Area Inset Padding for Mobile Notch & Home Indicator */
    .workspace-main, .hero-section, .sort-container, .system-container, .vault-container, .calendar-container, .page-container, main {
        padding-top: calc(84px + env(safe-area-inset-top, 0px)) !important;
        padding-bottom: calc(88px + env(safe-area-inset-bottom, 0px)) !important;
        padding-left: 14px !important;
        padding-right: 14px !important;
        box-sizing: border-box !important;
        width: 100% !important;
    }

    /* Fixed Mobile Header with Safe Area */
    .mobile-app-header {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        width: 100% !important;
        height: calc(56px + env(safe-area-inset-top, 0px)) !important;
        padding-top: env(safe-area-inset-top, 0px) !important;
        padding-left: 16px !important;
        padding-right: 16px !important;
        background: rgba(8, 8, 11, 0.92) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        z-index: 2000 !important;
        border-bottom: 1px solid var(--border-faint) !important;
    }

    /* Fixed Mobile Bottom Bar with Safe Area */
    .mobile-bottom-bar {
        position: fixed !important;
        bottom: 0 !important;
        left: 0 !important;
        width: 100% !important;
        height: calc(64px + env(safe-area-inset-bottom, 0px)) !important;
        padding-bottom: env(safe-area-inset-bottom, 0px) !important;
        background: rgba(8, 8, 11, 0.95) !important;
        backdrop-filter: blur(16px) !important;
        -webkit-backdrop-filter: blur(16px) !important;
        z-index: 2000 !important;
        border-top: 1px solid var(--border-faint) !important;
        display: flex !important;
        justify-content: space-around !important;
        align-items: center !important;
    }

    /* Single Column Stacked Mobile Cards */
    .ws-grid, .archive-grid, .ensemble-grid, .feature-list, .stat-grid {
        grid-template-columns: 1fr !important;
        display: flex !important;
        flex-direction: column !important;
        gap: 12px !important;
    }

    /* High-Density Compact Cards */
    .ws-card, .profile-card, .doc-block, .model-card, .stat-cell, #bulkSortPanel, .prediction-panel {
        padding: 14px 14px !important;
        border-radius: 8px !important;
        margin-bottom: 12px !important;
    }

    /* Compact Mobile Titles & Typography */
    h1, .hero-title { font-size: 26px !important; line-height: 1.2 !important; }
    h2, .section-title { font-size: 18px !important; line-height: 1.3 !important; }
    h3, .card-title, .block-title { font-size: 14px !important; }
    p, .hero-subtitle, .column-desc { font-size: 13px !important; line-height: 1.45 !important; }

    /* Light Theme Mobile Adjustments */
    html[data-theme="light"] .mobile-app-header,
    html[data-theme="light"] .mobile-bottom-bar,
    body.light-theme .mobile-app-header,
    body.light-theme .mobile-bottom-bar {
        background: rgba(232, 224, 206, 0.96) !important;
        border-color: #DCD5C5 !important;
    }
}

@media (max-width: 480px) {
    /* Extra Small Screen Specific Rules */
    .hero-title { font-size: 22px !important; }
    .corner-btn { padding: 10px 14px !important; font-size: 11px !important; }
    .mobile-nav-tab span { font-size: 9px !important; }
}
"""

with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

if 'MASTER NATIVE MOBILE UI SYSTEM' not in css:
    css = css + master_mobile_css
    with open('static/style.css', 'w', encoding='utf-8') as f:
        f.write(css)

print("[OK] Injected Master Native Mobile UI CSS into static/style.css")

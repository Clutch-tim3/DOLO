import os

responsive_css = """
/* ── UNIVERSAL RESPONSIVE DESIGN SYSTEM ── */

/* 1. Tablet & Mid-Size Displays (max-width: 1024px) */
@media (max-width: 1024px) {
    .sidebar {
        width: 220px !important;
    }
    .workspace-main {
        padding: 20px !important;
    }
    .hero-title {
        font-size: 44px !important;
    }
    .ws-grid, .features-grid, .system-grid, .sort-grid {
        grid-template-columns: repeat(2, 1fr) !important;
        gap: 16px !important;
    }
    .prediction-panel {
        max-width: 480px !important;
    }
}

/* 2. Mobile Displays (max-width: 768px) */
@media (max-width: 768px) {
    /* Stacked layout for workspace & body */
    body {
        flex-direction: column !important;
        height: auto !important;
        min-height: 100vh !important;
        overflow-y: auto !important;
    }
    .sidebar {
        width: 100% !important;
        height: auto !important;
        border-right: none !important;
        border-bottom: 1px solid var(--border-faint) !important;
        position: relative !important;
    }
    .sidebar-brand {
        padding: 16px 20px !important;
    }
    .nav-list {
        flex-direction: row !important;
        overflow-x: auto !important;
        padding: 8px 12px !important;
        white-space: nowrap !important;
        -webkit-overflow-scrolling: touch !important;
    }
    .nav-item {
        padding: 8px 14px !important;
        font-size: 12px !important;
        flex-shrink: 0 !important;
    }
    .workspace-main {
        width: 100% !important;
        height: auto !important;
        overflow-y: visible !important;
        padding: 16px !important;
        box-sizing: border-box !important;
    }
    .nav-header {
        padding: 12px 16px !important;
        flex-wrap: wrap !important;
        gap: 12px !important;
    }
    .nav-right {
        width: 100% !important;
        justify-content: space-between !important;
    }
    
    /* Single-column grids */
    .ws-grid, .features-grid, .system-grid, .sort-grid, .stats-row, .hero-grid, .pricing-grid {
        grid-template-columns: 1fr !important;
        gap: 16px !important;
    }
    
    /* Responsive typography */
    h1, .hero-title {
        font-size: 32px !important;
        letter-spacing: 1px !important;
    }
    h2, .block-title {
        font-size: 22px !important;
    }
    h3 {
        font-size: 18px !important;
    }
    
    /* Responsive tables */
    .table-container, .specs-table-wrapper, #batch_files_list {
        overflow-x: auto !important;
        -webkit-overflow-scrolling: touch !important;
        width: 100% !important;
    }
    .specs-table {
        min-width: 600px !important;
    }
    
    /* Touch-friendly buttons & tap targets */
    .corner-btn, .theme-toggle-btn, .submit-btn, .primary-btn {
        min-height: 44px !important;
        padding: 12px 20px !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        box-sizing: border-box !important;
    }
    
    /* Prediction panel modal on mobile */
    .prediction-panel {
        max-width: 100% !important;
        width: 100% !important;
        padding: 24px 16px !important;
        box-sizing: border-box !important;
    }
}

/* 3. Small Mobile Displays (max-width: 480px) */
@media (max-width: 480px) {
    .brand-logo {
        font-size: 11px !important;
    }
    .hero-title {
        font-size: 26px !important;
    }
    .corner-btn {
        padding: 10px 14px !important;
        font-size: 10px !important;
    }
}
"""

with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

if 'UNIVERSAL RESPONSIVE DESIGN SYSTEM' not in css:
    css += '\n' + responsive_css

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

print('Added universal responsive CSS breakpoints to static/style.css')

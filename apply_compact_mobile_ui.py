import os, re

compact_mobile_css = """
/* ── ULTRA-COMPACT MOBILE APP COMPONENT REFINEMENT ── */
@media (max-width: 768px) {
    /* Compact Cards & Container Spacing */
    .ws-card, .profile-card, .doc-block, .stat-cell, .model-card, .ds-card, .block-card, .card {
        padding: 12px 14px !important;
        margin-bottom: 10px !important;
        border-radius: 6px !important;
    }
    
    /* Compact Grids */
    .ws-grid, .features-grid, .system-grid, .sort-grid, .stats-row, .hero-grid, .pricing-grid, .archive-grid {
        gap: 10px !important;
        margin-top: 10px !important;
    }
    
    /* Compact Typography for Headers */
    .block-title, .card-title, h2, h3 {
        font-size: 14px !important;
        margin-bottom: 4px !important;
        line-height: 1.3 !important;
    }
    
    .category-label {
        font-size: 8px !important;
        margin-bottom: 4px !important;
        letter-spacing: 1.5px !important;
    }
    
    /* Compact Metrics & Stat Numbers */
    .stat-val, .metric-big, .score-number, .stat-value {
        font-size: 22px !important;
        line-height: 1.2 !important;
    }
    
    .stat-label, .metric-label, .meta-text {
        font-size: 11px !important;
    }
    
    /* Compact Drop Area for File Uploads */
    .file-drop-area {
        padding: 16px 12px !important;
        min-height: 90px !important;
    }
    
    .file-drop-area svg {
        width: 24px !important;
        height: 24px !important;
        margin-bottom: 6px !important;
    }
    
    /* Compact Inputs & Form Controls */
    .form-input, .form-select, input[type="text"], select {
        padding: 8px 10px !important;
        font-size: 12px !important;
        min-height: 36px !important;
        margin-bottom: 8px !important;
    }
    
    /* Compact Buttons */
    .corner-btn, .theme-toggle-btn, .submit-btn, .primary-btn, .action-btn {
        padding: 8px 14px !important;
        min-height: 36px !important;
        font-size: 10px !important;
        letter-spacing: 1px !important;
    }
    
    /* Compact Tables */
    .specs-table th, .specs-table td {
        padding: 8px 10px !important;
        font-size: 11px !important;
    }
    
    #batch_files_list > div {
        padding: 8px 10px !important;
        font-size: 11px !important;
        margin-bottom: 6px !important;
    }
    
    /* Compact Container Padding */
    .workspace-main, .hero-section, .sort-container, .system-container, .vault-container, .calendar-container {
        padding-top: 62px !important;
        padding-bottom: 72px !important;
        padding-left: 12px !important;
        padding-right: 12px !important;
    }
}
"""

with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

if 'ULTRA-COMPACT MOBILE APP COMPONENT REFINEMENT' not in css:
    css += '\n' + compact_mobile_css

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

print('Appended ultra-compact mobile CSS refinements to static/style.css')

import os, re

# 1. Update style.css with complete light theme overrides for ALL pages
with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

light_theme_css_expansion = """
/* ── COMPREHENSIVE LIGHT THEME OVERRIDES (CLIVE SYSTEM) ── */
html[data-theme="light"],
html[data-theme="light"] body,
body.light-theme {
    background-color: #F2EAD8 !important;
    color: #141210 !important;
}

html[data-theme="light"] .page-container,
html[data-theme="light"] .hero-section,
html[data-theme="light"] .workspace-main,
html[data-theme="light"] .sort-container,
html[data-theme="light"] .system-container,
html[data-theme="light"] .vault-container,
html[data-theme="light"] .calendar-container,
html[data-theme="light"] #bulkSortPanel,
html[data-theme="light"] .prediction-panel,
html[data-theme="light"] .panel-container,
html[data-theme="light"] .ws-card,
html[data-theme="light"] .profile-card,
html[data-theme="light"] .doc-block,
html[data-theme="light"] .model-card,
html[data-theme="light"] .stat-cell,
html[data-theme="light"] .day-cell,
html[data-theme="light"] .upcoming-item,
body.light-theme .page-container,
body.light-theme .hero-section,
body.light-theme .workspace-main,
body.light-theme .sort-container,
body.light-theme .system-container,
body.light-theme .vault-container,
body.light-theme .calendar-container,
body.light-theme #bulkSortPanel,
body.light-theme .prediction-panel,
body.light-theme .panel-container,
body.light-theme .ws-card,
body.light-theme .profile-card,
body.light-theme .doc-block,
body.light-theme .model-card,
body.light-theme .stat-cell,
body.light-theme .day-cell,
body.light-theme .upcoming-item {
    background-color: #F2EAD8 !important;
    background: #F2EAD8 !important;
    color: #141210 !important;
    border-color: #DCD5C5 !important;
}

html[data-theme="light"] h1, html[data-theme="light"] h2, html[data-theme="light"] h3, html[data-theme="light"] h4,
html[data-theme="light"] .hero-title, html[data-theme="light"] .section-title, html[data-theme="light"] .block-title, html[data-theme="light"] .card-title,
body.light-theme h1, body.light-theme h2, body.light-theme h3, body.light-theme h4,
body.light-theme .hero-title, body.light-theme .section-title, body.light-theme .block-title, body.light-theme .card-title {
    color: #141210 !important;
}

html[data-theme="light"] p, html[data-theme="light"] span, html[data-theme="light"] div, html[data-theme="light"] label,
body.light-theme p, body.light-theme span, body.light-theme div, body.light-theme label {
    color: #383632;
}

html[data-theme="light"] .category-label, html[data-theme="light"] .brand-badge, html[data-theme="light"] .card-tag,
body.light-theme .category-label, body.light-theme .brand-badge, body.light-theme .card-tag {
    color: #C8331F !important;
}

html[data-theme="light"] .mobile-app-header,
html[data-theme="light"] .mobile-bottom-bar,
html[data-theme="light"] .top-nav,
html[data-theme="light"] .top-bar,
body.light-theme .mobile-app-header,
body.light-theme .mobile-bottom-bar,
body.light-theme .top-nav,
body.light-theme .top-bar {
    background-color: #E8E0CE !important;
    background: #E8E0CE !important;
    border-color: #DCD5C5 !important;
    color: #141210 !important;
}

html[data-theme="light"] .theme-toggle-btn,
body.light-theme .theme-toggle-btn {
    background: #141210 !important;
    color: #F2EAD8 !important;
    border: 1px solid #141210 !important;
}
"""

if 'CLIVE LIGHT MODE OVERRIDES' in css:
    css = css + '\n' + light_theme_css_expansion
else:
    css = css + '\n' + light_theme_css_expansion

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

print("[OK] Expanded comprehensive Light Theme rules in static/style.css")


# 2. Universal Luxury Theme JS Script Template
theme_js_template = """
    <script>
        function initLuxuryTheme() {
            const saved = localStorage.getItem('luxury-theme') || 'dark';
            const html = document.documentElement;
            const body = document.body;
            if (saved === 'light') {
                html.setAttribute('data-theme', 'light');
                html.classList.add('light-theme');
                body.classList.add('light-theme');
                updateThemeUI('light');
            } else {
                html.removeAttribute('data-theme');
                html.classList.remove('light-theme');
                body.classList.remove('light-theme');
                updateThemeUI('dark');
            }
        }
        function toggleLuxuryTheme() {
            const html = document.documentElement;
            const body = document.body;
            const current = html.getAttribute('data-theme');
            if (current === 'light') {
                html.removeAttribute('data-theme');
                html.classList.remove('light-theme');
                body.classList.remove('light-theme');
                localStorage.setItem('luxury-theme', 'dark');
                updateThemeUI('dark');
            } else {
                html.setAttribute('data-theme', 'light');
                html.classList.add('light-theme');
                body.classList.add('light-theme');
                localStorage.setItem('luxury-theme', 'light');
                updateThemeUI('light');
            }
        }
        function updateThemeUI(mode) {
            const label = document.getElementById('themeLabel');
            const icon = document.getElementById('themeIcon');
            const labelM = document.getElementById('themeLabelMobile');
            const iconM = document.getElementById('themeIconMobile');
            if (label && icon) {
                label.textContent = mode === 'light' ? 'DARK MODE' : 'LIGHT MODE';
                icon.innerHTML = mode === 'light' ? '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>' : '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>';
            }
            if (labelM && iconM) {
                labelM.textContent = mode === 'light' ? 'DARK' : 'LIGHT';
                iconM.innerHTML = mode === 'light' ? '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>' : '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>';
            }
        }
        document.addEventListener('DOMContentLoaded', initLuxuryTheme);
    </script>
"""

# Fix theme script in workspace.html & add generateQuotationPDF
with open('static/workspace.html', 'r', encoding='utf-8') as f:
    ws_content = f.read()

# Replace broken theme script in workspace.html
ws_script_idx = ws_content.find('// --- LUXURY THEME SWITCHER')
if ws_script_idx != -1:
    end_script = ws_content.find('</script>', ws_script_idx)
    ws_content = ws_content[:ws_script_idx] + ws_content[end_script:]

quotation_js = """
        // --- GENERATE QUOTATION PDF FUNCTIONALITY ---
        function onQuoteTenderFileSelect(input) {
            const label = document.getElementById('quoteTenderFileLabel');
            if (input.files && input.files.length > 0) {
                label.textContent = `Selected File: ${input.files[0].name}`;
                label.style.display = "block";
            }
        }

        async function generateQuotationPDF() {
            const fileInput = document.getElementById('quoteTenderFile');
            const companySelect = document.getElementById('quoteCompanySelect');
            const downloadArea = document.getElementById('quoteDownloadArea');
            const downloadLink = document.getElementById('quoteDownloadLink');
            
            const supplier_name = companySelect ? companySelect.value : "DONINGTON VALE";
            
            showToast("⚙️ Generating Quotation", "Extracting tender items and calculating PPPFA 80/20 scores...");

            const formData = new FormData();
            formData.append("supplier_name", supplier_name);
            formData.append("evaluation_system", "80/20");
            if (fileInput && fileInput.files.length > 0) {
                formData.append("tender_file", fileInput.files[0]);
            }

            try {
                const res = await fetch("/api/generate-quotation", {
                    method: "POST",
                    body: formData
                });
                if (res.ok) {
                    const blob = await res.blob();
                    const url = window.URL.createObjectURL(blob);
                    downloadLink.href = url;
                    downloadLink.download = `${supplier_name.replace(/\\s+/g, '_')}_Quotation.pdf`;
                    downloadArea.style.display = "block";
                    showToast("✅ Quotation Ready", "Official PDF quotation generated successfully.");
                } else {
                    showToast("❌ Generation Error", "Failed to generate quotation PDF.");
                }
            } catch(e) {
                console.error(e);
                showToast("❌ Network Error", e.message);
            }
        }
"""

# Insert quotation JS before </body> in workspace.html
ws_content = ws_content.replace('</body>', quotation_js + '\n' + theme_js_template + '\n</body>')
with open('static/workspace.html', 'w', encoding='utf-8') as f:
    f.write(ws_content)

print("[OK] Fixed workspace.html theme JS SyntaxError and added working generateQuotationPDF()")

# Check and update all other HTML files for clean theme JS
html_files = ['index.html', 'sort.html', 'system.html', 'accuracy.html', 'vault.html', 'calendar.html']
for hf in html_files:
    path = os.path.join('static', hf)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            c = f.read()
        
        # Remove old theme scripts if present
        c = re.sub(r'<script>\s*function initLuxuryTheme\(\)[\s\S]*?</script>', '', c)
        
        # Insert clean theme_js_template before </body>
        c = c.replace('</body>', theme_js_template + '\n</body>')
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(c)
        print(f"[OK] Standardized theme toggle JS in static/{hf}")

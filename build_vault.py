html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Donington Vale — Compliance Vault</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Outfit:wght@200;300;400;600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="static/style.css">
    <style>
        .page-container { padding-top: 80px; max-width: 1200px; margin: 0 auto; }
        .filter-bar { display: flex; gap: 8px; margin-bottom: 24px; }
        .filter-btn { background: transparent; border: 1px solid rgba(197,168,128,0.2); color: #fff; padding: 6px 12px; font-size: 11px; cursor: pointer; border-radius: 2px; }
        .filter-btn.active { border-color: var(--accent-gold); color: var(--accent-gold); }
        .grid-container { display: grid; grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap: 16px; margin-bottom: 60px; }
        .doc-list { list-style: none; padding: 0; margin: 16px 0 0 0; font-size: 11px; }
        .doc-item { margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
        .doc-tag { font-weight: 600; text-transform: uppercase; font-size: 10px; padding: 2px 6px; border-radius: 2px; }
        .tag-valid { color: #999; }
        .tag-expiring { color: var(--accent-gold); border: 1px solid var(--accent-gold); }
        .tag-expired { color: #e05c5c; border: 1px solid #e05c5c; }
        .tag-missing { color: #e05c5c; }
        
        .card-compliant { border-top: 2px solid rgba(255,255,255,0.1); }
        .card-attention { border-top: 2px solid var(--accent-gold); }
        .card-non_compliant { border-top: 2px solid #e05c5c; }
        
        #summaryBanner { font-size: 12px; color: var(--accent-gold); margin-bottom: 24px; letter-spacing: 0.05em; font-weight: 500; text-transform: uppercase; }
    </style>
</head>
<body>
    <nav class="top-nav">
        <div class="nav-left">
            <a href="/" class="back-link">
                <svg class="back-arrow" width="16" height="12" viewBox="0 0 16 12" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M6 1L1 6M1 6L6 11M1 6H15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                <span>BACK TO MAIN</span>
            </a>
        </div>
        <div class="nav-center">
            <span class="logo">DONINGTON VALE</span>
        </div>
        <div class="nav-right" style="display:flex; justify-content:flex-end;">
            <a href="/system" class="back-link" style="flex-direction:row-reverse; margin-right: 16px;">
                <span>SYSTEM</span>
            </a>
            <a href="/calendar" class="back-link" style="flex-direction:row-reverse; margin-right: 16px;">
                <span>CALENDAR</span>
            </a>
            <a href="/vault" class="back-link" style="flex-direction:row-reverse; margin-right: 16px;">
                <span style="color:var(--accent-gold)">COMPLIANCE</span>
            </a>
            <a href="/accuracy" class="back-link" style="flex-direction:row-reverse; margin-right: 16px;">
                <span>ACCURACY</span>
            </a>
            <a href="/sort" class="back-link" style="flex-direction:row-reverse;">
                <span>SORT TENDERS</span>
                <svg class="back-arrow" width="16" height="12" viewBox="0 0 16 12" fill="none" xmlns="http://www.w3.org/2000/svg" style="transform: rotate(180deg); margin-right:0; margin-left:8px;">
                    <path d="M6 1L1 6M1 6L6 11M1 6H15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </a>
        </div>
    </nav>

    <main class="page-container">
        <div style="margin-bottom: 32px;">
            <span class="category-label">REGULATORY STANDING</span>
            <h1 class="hero-title" style="font-size: 36px; margin-top: 12px; margin-bottom: 16px; text-align: left;">COMPLIANCE VAULT.</h1>
            <div id="summaryBanner"></div>
        </div>
        
        <div class="filter-bar">
            <button class="filter-btn active" data-filter="all">ALL</button>
            <button class="filter-btn" data-filter="expiring">EXPIRING SOON</button>
            <button class="filter-btn" data-filter="expired">EXPIRED / MISSING</button>
            <button class="filter-btn" data-filter="compliant">COMPLIANT</button>
        </div>

        <div class="profile-card" id="emptyState" style="border-style: dashed; display: flex; align-items: center; justify-content: center; min-height: 120px; margin-bottom: 60px;">
            <div style="text-align: center;">
                <span class="card-tag">NO COMPANIES IN ARCHIVE</span>
                <h3 class="card-title" style="font-size:14px; margin-top:8px;">COMPLIANCE TRACKING BEGINS ONCE COMPANIES ARE ADDED TO THE LEDGER</h3>
            </div>
        </div>

        <div class="grid-container" id="vaultGrid" style="display:none;"></div>
    </main>

    <script>
        const docNames = {
            'tax_clearance': 'Tax Clearance',
            'bbbee_certificate': 'B-BBEE Certificate',
            'cidb_grading': 'CIDB Grading',
            'csd_report': 'CSD Report',
            'cipc_registration': 'CIPC Registration'
        };
        
        function renderTag(doc) {
            if(doc.status === 'valid') {
                return `<span class="doc-tag tag-valid">Valid ${doc.expiry_date ? 'until ' + doc.expiry_date : ''}</span>`;
            } else if(doc.status === 'expiring_soon') {
                return `<span class="doc-tag tag-expiring">Expires in ${doc.days_until_expiry} days</span>`;
            } else if(doc.status === 'expired') {
                return `<span class="doc-tag tag-expired">EXPIRED ${Math.abs(doc.days_until_expiry)} days ago</span>`;
            } else {
                return `<span class="doc-tag tag-missing">Not on file</span>`;
            }
        }
        
        let allCompanies = [];
        
        async function loadVault() {
            try {
                const res = await fetch('/api/compliance-status');
                allCompanies = await res.json();
                renderGrid('all');
            } catch(e) { console.error(e); }
        }
        
        function renderGrid(filter) {
            const grid = document.getElementById('vaultGrid');
            const empty = document.getElementById('emptyState');
            const summary = document.getElementById('summaryBanner');
            grid.innerHTML = '';
            
            if(allCompanies.length === 0) {
                empty.style.display = 'flex';
                grid.style.display = 'none';
                summary.textContent = '';
                return;
            }
            
            empty.style.display = 'none';
            grid.style.display = 'grid';
            
            let issues = 0;
            let expiredCount = 0;
            let expiringCount = 0;
            
            const filtered = allCompanies.filter(c => {
                if(c.overall_status === 'non_compliant') expiredCount++;
                if(c.overall_status === 'attention_needed') expiringCount++;
                if(c.overall_status !== 'compliant') issues++;
                
                if(filter === 'all') return true;
                if(filter === 'compliant' && c.overall_status === 'compliant') return true;
                if(filter === 'expiring' && c.overall_status === 'attention_needed') return true;
                if(filter === 'expired' && c.overall_status === 'non_compliant') return true;
                return false;
            });
            
            summary.textContent = issues > 0 ? `${issues} COMPANIES REQUIRE ATTENTION — ${expiringCount} EXPIRING SOON, ${expiredCount} EXPIRED OR MISSING` : 'ALL COMPANIES COMPLIANT';
            
            filtered.forEach(c => {
                const card = document.createElement('div');
                card.className = `profile-card card-${c.overall_status}`;
                
                const docsHtml = c.documents.map(d => `
                    <li class="doc-item">
                        <span style="color:#fff;">${docNames[d.type]}</span>
                        ${renderTag(d)}
                    </li>
                `).join('');
                
                card.innerHTML = `
                    <span class="card-tag">${c.company_id}</span>
                    <h3 class="card-title">${c.company_name}</h3>
                    <ul class="doc-list">${docsHtml}</ul>
                `;
                grid.appendChild(card);
            });
        }
        
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                renderGrid(e.target.dataset.filter);
            });
        });
        
        window.addEventListener('DOMContentLoaded', loadVault);
    </script>
</body>
</html>
"""
with open('static/vault.html', 'w') as f:
    f.write(html)


# Now modify index.html to add Expiry Date to the Archive upload form
import re
with open('static/index.html', 'r') as f:
    idx_content = f.read()

upload_form = r'''<div class="form-group" style="position: relative;">
                    <label for="company_file">Upload Document \(CIPC / CSD\)</label>
                    <div style="display: flex; gap: 12px; align-items: center;">
                        <input type="file" id="company_file" name="file" accept=".pdf" multiple style="flex: 1;" required>
                        <button type="submit" class="corner-btn" style="padding: 8px 16px; font-size: 11px; white-space: nowrap;">
                            <span>ADD FILE</span>
                            <span class="bracket bracket-tr"></span>
                            <span class="bracket bracket-bl"></span>
                        </button>
                    </div>
                </div>'''

new_upload_form = r'''<div class="form-group" style="position: relative;">
                    <label for="company_file">Upload Document (CIPC / CSD)</label>
                    <div style="display: flex; gap: 12px; align-items: center; margin-bottom: 8px;">
                        <input type="file" id="company_file" name="file" accept=".pdf" multiple style="flex: 1;" required>
                    </div>
                    <div style="display: flex; gap: 12px; align-items: flex-end;">
                        <div style="flex: 1;">
                            <label style="font-size:11px; color:#999; margin-bottom:4px; display:block;">Expiry Date (Optional)</label>
                            <input type="date" name="expiry_date" style="width:100%; background:rgba(0,0,0,0.5); border:1px solid #333; color:#fff; padding:7px;">
                        </div>
                        <button type="submit" class="corner-btn" style="padding: 8px 16px; font-size: 11px; white-space: nowrap;">
                            <span>ADD FILE</span>
                            <span class="bracket bracket-tr"></span>
                            <span class="bracket bracket-bl"></span>
                        </button>
                    </div>
                </div>'''

idx_content = re.sub(upload_form, new_upload_form, idx_content)

with open('static/index.html', 'w') as f:
    f.write(idx_content)

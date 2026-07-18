html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Donington Vale — System Configuration</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Outfit:wght@200;300;400;600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="static/style.css">
    <style>
        .page-container { padding-top: 80px; max-width: 1200px; margin: 0 auto; padding-bottom: 60px; }
        .section-title { font-family: 'Outfit', sans-serif; font-size: 20px; color: var(--accent-gold); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 24px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 8px; }
        
        .ensemble-grid { display: flex; gap: 16px; margin-bottom: 48px; }
        .model-card { flex: 1; padding: 24px; background: rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.05); }
        .model-name { font-family: 'Outfit', sans-serif; font-size: 24px; color: #fff; margin-bottom: 16px; }
        .weight-bar-bg { width: 100%; height: 4px; background: rgba(255,255,255,0.1); margin-top: 8px; position: relative; }
        .weight-bar-fill { height: 100%; background: var(--accent-gold); position: absolute; left: 0; top: 0; }
        
        .feature-list { display: flex; flex-direction: column; gap: 12px; margin-bottom: 48px; max-width: 800px; }
        .feature-item { display: flex; align-items: center; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .feature-label { font-size: 14px; color: #fff; }
        .feature-internal { font-size: 11px; color: #666; font-family: monospace; margin-left: 8px; }
        .feature-bar-container { width: 200px; display: flex; align-items: center; gap: 12px; }
        .feature-bar-bg { flex: 1; height: 4px; background: rgba(255,255,255,0.1); position: relative; }
        .feature-bar-fill { height: 100%; background: var(--accent-gold); position: absolute; left: 0; top: 0; }
        .feature-val { font-size: 12px; color: var(--accent-gold); font-family: monospace; min-width: 40px; text-align: right; }
        
        .data-tag { display: inline-block; padding: 4px 12px; border: 1px solid rgba(255,255,255,0.1); color: #999; font-size: 11px; text-transform: uppercase; margin-right: 8px; margin-bottom: 8px; }
        .disclaimer { font-size: 11px; color: #666; margin-top: 60px; text-align: center; }
        
        .stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.05); margin-bottom: 48px; }
        .stat-cell { background: #0a0a0a; padding: 20px; }
        .stat-label { font-size: 11px; color: #999; text-transform: uppercase; margin-bottom: 8px; }
        .stat-val { font-family: 'Outfit', sans-serif; font-size: 20px; color: #fff; }
        .stat-val.gold { color: var(--accent-gold); }
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
                <span style="color:var(--accent-gold)">SYSTEM</span>
            </a>
            <a href="/calendar" class="back-link" style="flex-direction:row-reverse; margin-right: 16px;">
                <span>CALENDAR</span>
            </a>
            <a href="/vault" class="back-link" style="flex-direction:row-reverse; margin-right: 16px;">
                <span>COMPLIANCE</span>
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
        <div style="margin-bottom: 40px;">
            <span class="category-label">SYSTEM TRANSPARENCY</span>
            <h1 class="hero-title" style="font-size: 36px; margin-top: 12px; margin-bottom: 24px; text-align: left;">MODEL & CONFIGURATION.</h1>
        </div>

        <div class="section-title">MODEL OVERVIEW</div>
        <div class="stat-grid">
            <div class="stat-cell"><div class="stat-label">Model Version</div><div class="stat-val" id="model_version">--</div></div>
            <div class="stat-cell"><div class="stat-label">Last Trained</div><div class="stat-val" id="last_trained_at">--</div></div>
            <div class="stat-cell"><div class="stat-label">Test AUC</div><div class="stat-val gold" id="test_auc">--</div></div>
            <div class="stat-cell"><div class="stat-label">Current Threshold</div><div class="stat-val gold" id="current_threshold">--</div></div>
            <div class="stat-cell"><div class="stat-label">Threshold Precision</div><div class="stat-val" id="threshold_precision">--</div></div>
            <div class="stat-cell"><div class="stat-label">Threshold Recall</div><div class="stat-val" id="threshold_recall">--</div></div>
            <div class="stat-cell"><div class="stat-label">Calibration Method</div><div class="stat-val" id="calibration_method">--</div></div>
            <div class="stat-cell"><div class="stat-label">Total Predictions</div><div class="stat-val" id="total_predictions_made">--</div></div>
            <div class="stat-cell"><div class="stat-label">Archived Companies</div><div class="stat-val" id="total_companies_archived">--</div></div>
        </div>
        
        <div class="section-title">ENSEMBLE COMPOSITION</div>
        <div class="ensemble-grid" id="ensembleContainer"></div>
        
        <div class="section-title">TOP CONTRIBUTING FACTORS</div>
        <div class="feature-list" id="featuresContainer"></div>
        
        <div class="section-title">DATA FOUNDATION</div>
        <div id="dataSourcesContainer" style="margin-bottom: 48px;"></div>
        
        <div class="disclaimer">
            This system provides decision support based on historical procurement patterns. It does not guarantee tender outcomes.
        </div>
    </main>

    <script>
        async function loadSystem() {
            try {
                const res = await fetch('/api/system-status');
                const data = await res.json();
                
                document.getElementById('model_version').textContent = data.model_version;
                document.getElementById('last_trained_at').textContent = data.last_trained_at.split('T')[0];
                document.getElementById('test_auc').textContent = data.test_auc.toFixed(4);
                document.getElementById('current_threshold').textContent = (data.current_threshold * 100).toFixed(2) + '%';
                document.getElementById('threshold_precision').textContent = (data.threshold_precision * 100).toFixed(1) + '%';
                document.getElementById('threshold_recall').textContent = (data.threshold_recall * 100).toFixed(1) + '%';
                document.getElementById('calibration_method').textContent = data.calibration_method;
                document.getElementById('total_predictions_made').textContent = data.total_predictions_made;
                document.getElementById('total_companies_archived').textContent = data.total_companies_archived;
                
                const ensemble = document.getElementById('ensembleContainer');
                ensemble.innerHTML = '';
                data.ensemble_models.forEach(m => {
                    ensemble.innerHTML += `
                        <div class="model-card">
                            <div class="model-name">${m.name}</div>
                            <div style="font-size:12px; color:#999; margin-bottom:8px;">Indiv. AUC: ${m.individual_auc.toFixed(4)}</div>
                            <div style="font-size:11px; color:#fff;">Weight: ${(m.weight*100).toFixed(0)}%</div>
                            <div class="weight-bar-bg">
                                <div class="weight-bar-fill" style="width:${m.weight*100}%"></div>
                            </div>
                        </div>
                    `;
                });
                
                const features = document.getElementById('featuresContainer');
                features.innerHTML = '';
                
                // Find max importance to scale bars
                const maxImp = Math.max(...data.top_features.map(f => f.importance));
                
                data.top_features.forEach(f => {
                    const widthPct = (f.importance / maxImp) * 100;
                    features.innerHTML += `
                        <div class="feature-item">
                            <div>
                                <span class="feature-label">${f.plain_language_label}</span>
                                <span class="feature-internal">[${f.name}]</span>
                            </div>
                            <div class="feature-bar-container">
                                <div class="feature-bar-bg">
                                    <div class="feature-bar-fill" style="width:${widthPct}%"></div>
                                </div>
                                <div class="feature-val">${f.importance.toFixed(2)}</div>
                            </div>
                        </div>
                    `;
                });
                
                const sources = document.getElementById('dataSourcesContainer');
                sources.innerHTML = '';
                data.data_sources.forEach(s => {
                    sources.innerHTML += `<span class="data-tag">${s}</span>`;
                });
                
            } catch(e) { console.error(e); }
        }
        
        window.addEventListener('DOMContentLoaded', loadSystem);
    </script>
</body>
</html>
"""

with open('static/system.html', 'w') as f:
    f.write(html)

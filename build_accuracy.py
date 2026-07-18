html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Donington Vale — Accuracy Tracker</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Outfit:wght@200;300;400;600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="static/style.css">
    <style>
        .page-container { padding-top: 80px; max-width: 1200px; margin: 0 auto; }
        .stat-block { flex: 1; padding: 24px; border: 1px solid rgba(255,255,255,0.05); background: rgba(255,255,255,0.02); text-align: center; }
        .stat-val { font-family: 'Outfit', sans-serif; font-size: 32px; color: #fff; margin-top: 8px; }
        .stat-val.gold { color: var(--accent-gold); }
        .trend-container { height: 120px; display: flex; align-items: flex-end; justify-content: space-between; gap: 8px; padding-top: 24px; }
        .trend-bar-wrapper { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; }
        .trend-bar { width: 100%; max-width: 40px; background: var(--accent-gold); transition: height 0.5s ease; border-radius: 2px 2px 0 0; }
        .trend-label { font-size: 11px; color: #666; margin-top: 8px; }
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
                <span>COMPLIANCE</span>
            </a>
            <a href="/accuracy" class="back-link" style="flex-direction:row-reverse; margin-right: 16px;">
                <span style="color:var(--accent-gold)">ACCURACY</span>
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
            <span class="category-label">PERFORMANCE VALIDATION</span>
            <h1 class="hero-title" style="font-size: 36px; margin-top: 12px; margin-bottom: 24px; text-align: left;">TRACK RECORD.</h1>
        </div>

        <div style="display: flex; gap: 16px; margin-bottom: 40px;" id="statsRow">
            <div class="stat-block"><div class="category-label">OVERALL ACCURACY</div><div class="stat-val gold" id="acc_val">--%</div></div>
            <div class="stat-block"><div class="category-label">TOTAL TRACKED</div><div class="stat-val" id="total_val">--</div></div>
            <div class="stat-block"><div class="category-label">WON / LOST / PENDING</div><div class="stat-val" style="font-size: 24px; padding-top:4px;" id="wlp_val">-- / -- / --</div></div>
            <div class="stat-block"><div class="category-label">PRECISION (PURSUE CALLS)</div><div class="stat-val" id="prec_val">--%</div></div>
        </div>

        <div style="margin-bottom: 48px; max-width: 600px;">
            <span class="category-label">6-MONTH TREND</span>
            <div class="trend-container" id="trendContainer"></div>
        </div>

        <section class="results-section" style="padding-bottom:60px;">
            <h2 class="column-title" style="margin-bottom:24px;">TRACKED OUTCOMES</h2>
            
            <div class="profile-card" id="emptyState" style="border-style: dashed; display: flex; align-items: center; justify-content: center; min-height: 120px;">
                <div style="text-align: center;">
                    <span class="card-tag">NO OUTCOMES TRACKED YET</span>
                    <h3 class="card-title" style="font-size:14px; margin-top:8px;">MARK A TENDER RESULT FROM SINGLE PREDICTIONS OR BATCH SORT TO BEGIN</h3>
                </div>
            </div>

            <div class="specs-table-wrapper" style="width:100%; display:none;" id="outcomesTableWrapper">
                <table class="specs-table" style="width:100%; text-align:left;">
                    <thead>
                        <tr>
                            <th class="spec-label">Date Tracked</th>
                            <th class="spec-label">Tender / Filename</th>
                            <th class="spec-label">Supplier</th>
                            <th class="spec-label">Recommendation</th>
                            <th class="spec-label">Predicted Prob.</th>
                            <th class="spec-label">Actual Outcome</th>
                            <th class="spec-label">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="outcomesBody"></tbody>
                </table>
            </div>
        </section>
    </main>

    <!-- Inline Update Form Template -->
    <template id="updateFormTemplate">
        <tr class="expanded-row">
            <td colspan="7" style="border-bottom: 1px solid rgba(255,255,255,0.05); padding: 16px;">
                <form class="updateOutcomeForm" style="display:flex; gap:16px; align-items:flex-end;">
                    <input type="hidden" name="prediction_id">
                    <div style="flex:1;">
                        <label style="font-size:11px; color:#999; display:block; margin-bottom:4px;">Outcome</label>
                        <select name="actual_outcome" style="width:100%; background:rgba(0,0,0,0.5); color:#fff; border:1px solid #333; padding:8px;">
                            <option value="won">Won</option>
                            <option value="lost">Lost</option>
                            <option value="withdrawn">Withdrawn</option>
                            <option value="pending">Pending</option>
                        </select>
                    </div>
                    <div style="flex:1;">
                        <label style="font-size:11px; color:#999; display:block; margin-bottom:4px;">Date (Optional)</label>
                        <input type="date" name="outcome_date" style="width:100%; background:rgba(0,0,0,0.5); color:#fff; border:1px solid #333; padding:7px;">
                    </div>
                    <div style="flex:2;">
                        <label style="font-size:11px; color:#999; display:block; margin-bottom:4px;">Notes</label>
                        <input type="text" name="notes" placeholder="e.g. Lost on price" style="width:100%; background:rgba(0,0,0,0.5); color:#fff; border:1px solid #333; padding:8px;">
                    </div>
                    <div>
                        <button type="submit" class="corner-btn" style="padding:8px 16px; font-size:11px;">
                            <span>SAVE</span>
                            <span class="bracket bracket-tr"></span><span class="bracket bracket-bl"></span>
                        </button>
                    </div>
                </form>
            </td>
        </tr>
    </template>

    <script>
        async function loadAccuracy() {
            try {
                const statRes = await fetch('/api/accuracy-stats');
                const stats = await statRes.json();
                
                document.getElementById('acc_val').textContent = stats.total_tracked > 0 ? stats.accuracy_pct.toFixed(1) + '%' : '--%';
                document.getElementById('total_val').textContent = stats.total_tracked;
                document.getElementById('wlp_val').textContent = `${stats.won} / ${stats.lost} / ${stats.pending}`;
                document.getElementById('prec_val').textContent = stats.total_tracked > 0 ? stats.precision_actual.toFixed(1) + '%' : '--%';
                
                const trendContainer = document.getElementById('trendContainer');
                trendContainer.innerHTML = '';
                stats.accuracy_trend.forEach(t => {
                    const w = document.createElement('div');
                    w.className = 'trend-bar-wrapper';
                    w.innerHTML = `
                        <div class="trend-bar" style="height: ${t.accuracy_pct}%"></div>
                        <div class="trend-label">${t.month}</div>
                    `;
                    trendContainer.appendChild(w);
                });

                const outRes = await fetch('/api/tracked-outcomes');
                const outcomes = await outRes.json();
                
                if(outcomes.length > 0) {
                    document.getElementById('emptyState').style.display = 'none';
                    document.getElementById('outcomesTableWrapper').style.display = 'block';
                    
                    const tbody = document.getElementById('outcomesBody');
                    tbody.innerHTML = '';
                    const template = document.getElementById('updateFormTemplate');
                    
                    outcomes.forEach(o => {
                        const tr = document.createElement('tr');
                        tr.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
                        
                        let outColor = '#999';
                        let outStyle = '';
                        if(o.actual_outcome === 'won') { outColor = 'var(--accent-gold)'; }
                        else if(o.actual_outcome === 'lost') { outColor = '#666'; }
                        else if(o.actual_outcome === 'withdrawn') { outColor = '#e05c5c'; }
                        else if(o.actual_outcome === 'pending') { outColor = 'var(--accent-gold)'; outStyle = 'border:1px dashed var(--accent-gold); padding:2px 6px; background:transparent;'; }
                        
                        const dateStr = o.created_at ? o.created_at.split('T')[0] : '--';
                        const prob = o.sa_adjusted_probability ? (o.sa_adjusted_probability*100).toFixed(1)+'%' : (o.predicted_probability ? (o.predicted_probability*100).toFixed(1)+'%' : '--');
                        
                        tr.innerHTML = `
                            <td style="color:#999">${dateStr}</td>
                            <td style="color:#fff">${o.filename || o.tender_identifier}</td>
                            <td style="color:#fff">${o.supplier_name}</td>
                            <td style="color:${o.recommendation==='PURSUE'?'var(--accent-gold)':'#999'}">${o.recommendation || '--'}</td>
                            <td>${prob}</td>
                            <td><span style="color:${outColor}; ${outStyle} font-weight:600; text-transform:uppercase; font-size:11px;">${o.actual_outcome}</span></td>
                            <td>
                                <button class="corner-btn update-btn" style="padding:4px 8px; font-size:10px;">
                                    <span>UPDATE</span>
                                    <span class="bracket bracket-tr" style="width:4px; height:4px;"></span>
                                    <span class="bracket bracket-bl" style="width:4px; height:4px;"></span>
                                </button>
                            </td>
                        `;
                        
                        tr.querySelector('.update-btn').addEventListener('click', () => {
                            const next = tr.nextElementSibling;
                            if(next && next.classList.contains('expanded-row')) {
                                next.remove();
                                return;
                            }
                            const formRow = template.content.cloneNode(true);
                            const form = formRow.querySelector('form');
                            form.querySelector('[name="prediction_id"]').value = o.prediction_id;
                            form.querySelector('[name="actual_outcome"]').value = o.actual_outcome;
                            form.querySelector('[name="outcome_date"]').value = o.outcome_date || '';
                            form.querySelector('[name="notes"]').value = o.notes || '';
                            
                            form.addEventListener('submit', async (e) => {
                                e.preventDefault();
                                const fd = new FormData(form);
                                await fetch('/api/track-outcome', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify(Object.fromEntries(fd.entries()))
                                });
                                loadAccuracy();
                            });
                            tr.after(formRow);
                        });
                        
                        tbody.appendChild(tr);
                    });
                }
            } catch(e) {
                console.error(e);
            }
        }
        
        window.addEventListener('DOMContentLoaded', loadAccuracy);
    </script>
</body>
</html>
"""

with open('static/accuracy.html', 'w') as f:
    f.write(html)

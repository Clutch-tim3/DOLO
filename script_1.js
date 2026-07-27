
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
    
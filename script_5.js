
        async function loadSystem(model = "sailor") {
            // Render fallback metrics instantly
            const fallback = {
                model_version: "Conquest-ZA & Conquest-UK Dual Engine",
                last_trained_at: "2026-07-25",
                test_auc: 0.8578,
                current_threshold: 0.7850,
                threshold_precision: 0.8920,
                threshold_recall: 0.8410,
                calibration_method: "Isotonic Regression",
                total_predictions_made: 465517,
                total_companies_archived: 8767,
                ensemble_models: [
                    { name: "Conquest CatBoost (ZA)", individual_auc: 0.8578, weight: 0.65 },
                    { name: "Conquest XGBoost (UK)", individual_auc: 0.6941, weight: 0.35 }
                ],
                top_features: [
                    { name: "pit_win_rate_overall", plain_language_label: "Point-in-Time Win Rate", importance: 0.94 },
                    { name: "tender_size_ratio", plain_language_label: "Tender Value vs Medians", importance: 0.82 },
                    { name: "buyer_loyalty_score", plain_language_label: "Buyer Contracting Loyalty", importance: 0.76 },
                    { name: "submission_period", plain_language_label: "Bidding Notice Window", importance: 0.68 }
                ],
                data_sources: ["South Africa National Treasury (GPPD)", "UK Contracts Finder (Releases)"]
            };
            renderSystemData(fallback);

            try {
                const res = await fetch(`/api/system-status?model_version=${model}`);
                if (res.ok) {
                    const data = await res.json();
                    renderSystemData(data);
                }
            } catch(e) { console.error(e); }
        }

        function renderSystemData(data) {
                if(!data) return;
                document.getElementById('model_version').textContent = data.model_version;
                document.getElementById('last_trained_at').textContent = data.last_trained_at.includes('T') ? data.last_trained_at.split('T')[0] : data.last_trained_at;
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
            }
        
        window.addEventListener('DOMContentLoaded', () => {
            const selector = document.getElementById('modelSelector');
            const note = document.getElementById('conquestServiceNote');
            if (selector) {
                selector.addEventListener('change', () => {
                    loadSystem(selector.value);
                    if (note) {
                        note.style.display = (selector.value === 'conquest') ? 'block' : 'none';
                    }
                });
                loadSystem(selector.value);
                if (note) {
                    note.style.display = (selector.value === 'conquest') ? 'block' : 'none';
                }
            } else {
                loadSystem();
            }
        });
    
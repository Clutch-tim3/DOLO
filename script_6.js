
        let currentTargetBlock = null;

        function switchTab(tabId, element) {
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            if (element) element.classList.add('active');
            document.querySelectorAll('.content-section').forEach(sec => sec.classList.remove('active'));
            const target = document.getElementById('tab-' + tabId);
            if (target) target.classList.add('active');
            const titles = {'evaluation': 'Single Tender Evaluation', 'sort': 'Batch Tender Sorting & Ranking', 'archive': 'Smart Compliance Archive & Vault', 'calendar': 'Calendar & Submission Deadlines', 'quotation': 'Automated Quotation Generator', 'system': 'System Status & Configuration', 'agent': 'Company-Aware Assistant'};
            const c = document.getElementById('topNavCenter');
            if (c && titles[tabId]) c.textContent = titles[tabId];
        }

        // Agent Wiring
        async function loadAgentData() {
            try {
                const subRes = await fetch('/api/subscription-status');
                if (subRes.ok) {
                    const sub = await subRes.json();
                    const quotaCount = document.getElementById('agentQuotaCount');
                    const quotaPlan = document.getElementById('agentQuotaPlan');
                    if(quotaCount) quotaCount.textContent = sub.quotes_today + ' / ' + sub.quotes_limit;
                    if(quotaPlan) quotaPlan.textContent = sub.plan;
                }

                const compRes = await fetch('/api/company-profile');
                if (compRes.ok) {
                    const comp = await compRes.json();
                    
                    const locked = document.getElementById('agentLockedState');
                    const active = document.getElementById('agentActiveState');
                    if (comp.tier === 'starter') {
                        if(locked) locked.style.display = 'block';
                        if(active) active.style.display = 'none';
                        return;
                    } else {
                        if(locked) locked.style.display = 'none';
                        if(active) active.style.display = 'grid';
                    }

                    const cName = document.getElementById('sidebarCompanyName');
                    const cMeta = document.getElementById('sidebarCompanyMeta');
                    const cTier = document.getElementById('sidebarCompanyTier');
                    if(cName) cName.textContent = comp.name;
                    if(cMeta) cMeta.textContent = comp.registration + ' \u00B7 ' + comp.location;
                    if(cTier) cTier.textContent = comp.tier === 'pro' ? 'Pro Plan' : 'Starter';

                    const sWins = document.getElementById('statTotalWins');
                    const sWinRate = document.getElementById('statWinRate');
                    const sBbbee = document.getElementById('statBbbee');
                    const sIncumb = document.getElementById('statIncumbent');
                    if(sWins) sWins.textContent = comp.stats.pit_total_wins;
                    if(sWinRate) sWinRate.textContent = comp.stats.pit_win_rate_overall;
                    if(sBbbee) sBbbee.textContent = comp.stats.bbbee_level;
                    if(sIncumb) sIncumb.textContent = comp.stats.pit_is_incumbent;
                }

                const vaultRes = await fetch('/api/compliance-status');
                if (vaultRes.ok) {
                    const vault = await vaultRes.json();
                    let vaultHtml = '';
                    if(vault.documents && vault.documents.length > 0) {
                        vault.documents.forEach(doc => {
                            let statusClass = doc.status === 'Valid' ? 'ok' : 'warn';
                            vaultHtml += <div class="doc-block">
                                <span class="doc-name"> + doc.name + </span>
                                <span class="doc-status  + statusClass + "> + doc.status + </span>
                            </div>;
                        });
                    } else {
                        vaultHtml = '<div style="font-size:11px; color:#666;">No documents uploaded.</div>';
                    }
                    const vDocs = document.getElementById('sidebarVaultDocs');
                    if(vDocs) vDocs.innerHTML = vaultHtml;
                }
            } catch (e) {
                console.error('Error loading agent data', e);
            }
        }

        async function sendAgentMessage() {
            const input = document.getElementById('agentInput');
            if(!input) return;
            const msg = input.value.trim();
            if (!msg) return;

            const log = document.getElementById('agentMessageLog');
            if(!log) return;
            log.innerHTML += 
                <div class="msg-row user">
                  <span class="msg-label">You</span>
                  <div class="msg-bubble"> + msg + </div>
                </div>
            ;
            input.value = '';
            log.scrollTop = log.scrollHeight;

            if (msg === "What's Due This Week") {
                try {
                    const calRes = await fetch('/api/calendar-events');
                    const calData = await calRes.json();
                    const evtCount = calData.events ? calData.events.length : 0;
                    log.innerHTML += 
                        <div class="msg-row agent">
                          <span class="msg-label agent-label">Agent</span>
                          <div class="msg-bubble">You have  + evtCount +  events due this week. Check the calendar tab for full details.</div>
                        </div>
                    ;
                    log.scrollTop = log.scrollHeight;
                } catch(e) {}
                return;
            }

            try {
                const res = await fetch('/api/agent-chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: msg, action: msg === 'Generate Quote' ? 'generate_quote' : 'chat', tender_file_path: '' })
                });
                
                if (!res.ok) {
                    const err = await res.json();
                    log.innerHTML += 
                        <div class="msg-row agent">
                          <span class="msg-label agent-label">Agent</span>
                          <div class="msg-bubble" style="color:#e05c5c"> + (err.detail || err.error || 'Connection error') + </div>
                        </div>
                    ;
                    log.scrollTop = log.scrollHeight;
                    return;
                }

                const data = await res.json();
                
                let flagHtml = '';
                if (data.status === 'MANUAL_REVIEW_REQUIRED' || data.status === 'LOW_CONFIDENCE') {
                    flagHtml = '<div class="flag-chip">' + data.status.replace(/_/g, ' ') + '</div>';
                }
                
                let disclaimerHtml = '';
                if (data.is_guidance) {
                    disclaimerHtml = '<p class="disclaimer-line">General guidance based on document analysis — not a compliance audit. Verify against the original tender before submission.</p>';
                }

                log.innerHTML += 
                    <div class="msg-row agent">
                      <span class="msg-label agent-label">Agent</span>
                      <div class="msg-bubble"> + data.response + </div>
                       + flagHtml + 
                       + disclaimerHtml + 
                    </div>
                ;
                log.scrollTop = log.scrollHeight;
                
                if (msg === 'Generate Quote') {
                    loadAgentData();
                }
            } catch (e) {
                console.error(e);
            }
        }

        const originalSwitchTabAgent = switchTab;
        switchTab = function(tabId, element) {
            originalSwitchTabAgent(tabId, element);
            if (tabId === 'agent') {
                loadAgentData();
            }
        };

        function triggerBlockUpload(blockType) {
            currentTargetBlock = blockType;
            document.getElementById('archiveFileInput').click();
        }

        async function handleArchiveFileUpload(event) {
            const file = event.target.files[0];
            if (!file) return;

            const formData = new FormData();
            formData.append('file', file);
            formData.append('target_block', currentTargetBlock || 'CSD_CERT');

            try {
                const res = await fetch('/api/archive/upload-document', { method: 'POST', body: formData });
                const data = await res.json();

                if (data.status === 'success') {
                    if (data.auto_sorted) {
                        showToast('âš ï¸ Auto-Sorted Document', `You uploaded into ${data.intended_block_label}, but this document is a ${data.detected_label}. We automatically sorted it into ${data.detected_label} to prevent disqualification!`);
                    } else {
                        showToast('âœ“ Document Validated', `Saved directly into ${data.detected_label}.`);
                    }

                    // Highlight block
                    const block = document.getElementById('block-' + data.actual_block);
                    if (block) {
                        block.classList.add('uploaded');
                    }
                }
            } catch(e) {
                console.error(e);
            }
        }

        function showToast(title, message) {
            const toast = document.getElementById('toastNotification');
            document.getElementById('toastTitle').textContent = title;
            document.getElementById('toastMessage').textContent = message;
            toast.style.display = 'block';
            setTimeout(() => { toast.style.display = 'none'; }, 6000);
        }

        let quoteSelectedTenderFile = null;

        function onQuoteTenderFileSelect(input) {
            quoteSelectedTenderFile = input.files[0];
            if (quoteSelectedTenderFile) {
                const lbl = document.getElementById('quoteTenderFileLabel');
                lbl.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#c5a880" stroke-width="1.8" style="vertical-align: middle; margin-right: 6px;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg> Selected Tender: ' + quoteSelectedTenderFile.name;
                lbl.style.display = 'block';
            }
        }

        async function generateQuotationPDF() {
            if (!quoteSelectedTenderFile) {
                showToast('âš ï¸ Missing Tender PDF', 'Please upload a Tender PDF file to generate the quotation.');
                return;
            }

            const company = document.getElementById('quoteCompanySelect').value;
            const area = document.getElementById('quoteDownloadArea');
            area.style.display = 'none';

            const formData = new FormData();
            formData.append('tender_file', quoteSelectedTenderFile);
            formData.append('supplier_name', company);

            try {
                showToast('âš™ï¸ Processing PDF', 'Parsing tender requirements & generating official quotation...');
                const res = await fetch('/api/generate-quotation', {
                    method: 'POST',
                    body: formData
                });

                const data = await res.json();
                if (data.pdf_url) {
                    const link = document.getElementById('quoteDownloadLink');
                    link.href = data.pdf_url;
                    area.style.display = 'block';
                    showToast('âœ“ Quotation Ready', 'Your official PDF quotation is ready to download.');
                }
            } catch(e) {
                console.error(e);
                showToast('âŒ Generation Failed', 'An error occurred while generating the PDF.');
            }
        }

        function onModelChange(val) {
            document.getElementById('activeModelLabel').textContent = val === 'conquest' ? 'Conquest CatBoost (0.8578 AUC)' : 'Sailor (0.8187 AUC)';
        }

        // --- SINGLE EVALUATION HANDLERS ---
        let wsSelectedTenderFile = null;
        let wsSelectedBidFile = null;

        function onWsTenderFileSelect(input) {
            wsSelectedTenderFile = input.files[0];
            if (wsSelectedTenderFile) {
                const lbl = document.getElementById('wsTenderFileLabel');
                lbl.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#c5a880" stroke-width="1.8" style="vertical-align: middle; margin-right: 6px;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg> Selected Tender: ' + wsSelectedTenderFile.name;
                lbl.style.display = 'block';
            }
        }

        function onWsBidFileSelect(input) {
            wsSelectedBidFile = input.files[0];
            if (wsSelectedBidFile) {
                const lbl = document.getElementById('wsBidFileLabel');
                lbl.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#c5a880" stroke-width="1.8" style="vertical-align: middle; margin-right: 6px;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg> Selected Bid: ' + wsSelectedBidFile.name;
                lbl.style.display = 'block';
            }
        }

        async function computeWsEvaluation() {
            if (!wsSelectedTenderFile) {
                showToast('âš ï¸ Missing File', 'Please select a Tender PDF file to compute estimates.');
                return;
            }

            const resPanel = document.getElementById('wsEvalResultPanel');
            resPanel.innerHTML = '<div style="text-align:center; padding:60px 0; color:var(--accent-gold);"><div style="font-size:32px; margin-bottom:12px;">âš™ï¸</div><div>Computing probability & PPPFA score via Conquest Engine...</div></div>';

            const formData = new FormData();
            formData.append('tender_file', wsSelectedTenderFile);
            if (wsSelectedBidFile) formData.append('bid_file', wsSelectedBidFile);

            const supName = document.getElementById('wsSupplierName').value;
            if (supName) formData.append('supplier_name', supName);

            try {
                const res = await fetch('/api/predict', { method: 'POST', body: formData });
                const data = await res.json();

                const prob = ((data.sa_adjusted_probability || data.base_probability || 0) * 100).toFixed(1);
                const rec = data.recommendation || 'BID';
                const isBid = rec.toUpperCase().includes('BID') || rec.toUpperCase().includes('PURSUE');

                resPanel.innerHTML = `
                    <div style="text-align: center; margin-bottom: 24px;">
                        <div style="font-family: 'Outfit', sans-serif; font-size: 56px; font-weight: 300; color: #fff; line-height: 1;">${prob}%</div>
                        <div style="font-size: 10px; letter-spacing: 0.15em; color: #888; text-transform: uppercase; margin-top: 6px;">SA-Adjusted Win Probability</div>
                        <div style="display: inline-block; padding: 6px 20px; border-radius: 20px; font-size: 12px; font-weight: 700; letter-spacing: 0.1em; margin-top: 12px; background: ${isBid ? 'rgba(76,175,80,0.15)' : 'rgba(224,92,92,0.15)'}; color: ${isBid ? '#4CAF50' : '#e05c5c'}; border: 1px solid ${isBid ? 'rgba(76,175,80,0.3)' : 'rgba(224,92,92,0.3)'};">${rec.toUpperCase()}</div>
                    </div>

                    <div style="display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 13px;">
                        <span style="color: #888;">Detected Bidding Supplier</span>
                        <span style="font-family: monospace; color: #fff;">${data.supplier_name || 'Matched'}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 13px;">
                        <span style="color: #888;">Tender Identifier</span>
                        <span style="font-family: monospace; color: #fff;">${data.tender_identifier || 'Parsed'}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 13px;">
                        <span style="color: #888;">B-BBEE Preference Level</span>
                        <span style="font-family: monospace; color: #fff;">Level ${data.bbbee_level || '1'}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 13px;">
                        <span style="color: #888;">Base Machine Learning Probability</span>
                        <span style="font-family: monospace; color: #fff;">${((data.base_probability || 0)*100).toFixed(1)}%</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 10px 0; font-size: 13px;">
                        <span style="color: #888;">Inference Threshold</span>
                        <span style="font-family: monospace; color: var(--accent-gold);">41.9%</span>
                    </div>
                `;
            } catch(e) {
                resPanel.innerHTML = '<div style="color: #e05c5c; text-align: center; padding: 40px;">Error calculating prediction. Check app server logs.</div>';
            }
        }

        // --- BATCH SORTING & RANKED PIPELINE HANDLERS ---
        let wsBatchFiles = [];
        let wsRankedTendersList = [];

        function onWsBatchSortFilesSelect(input) {
            wsBatchFiles = Array.from(input.files);
            const listEl = document.getElementById('wsBatchFileList');
            listEl.innerHTML = wsBatchFiles.map((f, i) => `<div style="padding: 6px 10px; background: var(--card-bg); margin-bottom: 4px; border-radius: 4px; display: flex; align-items: center; gap: 8px; border: 1px solid var(--border-faint);"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#c5a880" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg> ${f.name}</div>`).join('');
            document.getElementById('wsBatchSortBtn').disabled = wsBatchFiles.length === 0;
        }

        async function runWsBatchSort() {
            if (wsBatchFiles.length === 0) return;

            showToast('âš™ï¸ Scoring Batch', `Evaluating ${wsBatchFiles.length} tender documents via Conquest Engine...`);

            for (let file of wsBatchFiles) {
                const formData = new FormData();
                formData.append('tender_file', file);
                try {
                    const res = await fetch('/api/predict', { method: 'POST', body: formData });
                    const data = await res.json();
                    wsRankedTendersList.unshift({
                        tender_id: data.tender_identifier || 'Tender_' + Math.random().toString(36).substr(2, 6),
                        title: file.name,
                        supplier: data.supplier_name || 'DONINGTON VALE',
                        prob: data.sa_adjusted_probability || data.win_probability || data.base_probability || 0.65,
                        rec: data.recommendation || 'PURSUE',
                        framework: '80/20'
                    });
                } catch(e) {
                    console.error(e);
                }
            }

            renderWsRankedTable();
            showToast('âœ“ Batch Analysis Complete', `Ranked ${wsBatchFiles.length} newly uploaded tenders.`);
        }

        async function loadWsRankedPipeline() {
            wsRankedTendersList = [];
            try {
                const res = await fetch('/api/tracked-outcomes');
                const data = await res.json();
                if (Array.isArray(data)) {
                    data.forEach(item => {
                        wsRankedTendersList.push({
                            tender_id: item.tender_identifier || item.prediction_id || 'Tender',
                            title: item.filename || item.tender_identifier || 'Tender Submission',
                            supplier: item.supplier_name || 'DONINGTON VALE',
                            prob: item.sa_adjusted_probability || item.predicted_probability || 0.0,
                            rec: item.recommendation || 'PASS',
                            framework: (item.sa_analysis && item.sa_analysis.evaluation_system) || '80/20'
                        });
                    });
                }
            } catch(e) {
                console.error("Error loading tracked outcomes:", e);
            }

            // Update stats
            const total = wsRankedTendersList.length;
            const pursueCount = wsRankedTendersList.filter(t => t.rec.toUpperCase() === 'PURSUE').length;
            const avgProb = total > 0 ? (wsRankedTendersList.reduce((acc, t) => acc + t.prob, 0) / total * 100).toFixed(1) + '%' : '0.0%';

            const badge = document.getElementById('wsTenderCountBadge');
            if (badge) badge.textContent = `${total} DATASET TENDERS`;

            const statsBoxes = document.querySelectorAll('#tab-sort .ws-card div div div');
            if (statsBoxes.length >= 3) {
                statsBoxes[0].querySelector('div:nth-child(2)').textContent = pursueCount;
                statsBoxes[1].querySelector('div:nth-child(2)').textContent = avgProb;
            }

            renderWsRankedTable();
        }

        function renderWsRankedTable() {
            wsRankedTendersList.sort((a, b) => b.prob - a.prob);
            filterWsTenderTable();
        }

        function filterWsTenderTable() {
            const query = (document.getElementById('wsTenderSearchInput')?.value || '').toLowerCase();
            const recFilter = document.getElementById('wsFilterRec')?.value || 'ALL';
            const tbody = document.getElementById('wsBatchSortBody');
            if (!tbody) return;

            const filtered = wsRankedTendersList.filter(t => {
                const matchesQuery = !query || t.title.toLowerCase().includes(query) || t.tender_id.toLowerCase().includes(query) || t.supplier.toLowerCase().includes(query);
                const matchesRec = recFilter === 'ALL' || t.rec.toUpperCase() === recFilter.toUpperCase();
                return matchesQuery && matchesRec;
            });

            if (filtered.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="6" style="padding: 40px; text-align: center; color: var(--text-muted); font-family: 'Cormorant Garamond', serif; font-size: 16px;">
                            No opportunities found. Upload tender documents or run evaluations to populate your pipeline.
                        </td>
                    </tr>
                `;
            } else {
                tbody.innerHTML = filtered.map((item, idx) => {
                    const pPct = (item.prob * 100).toFixed(1);
                    const isPursue = item.rec.toUpperCase().includes('PURSUE') || item.rec.toUpperCase().includes('BID');
                    return `
                        <tr style="border-bottom: 1px solid var(--border-faint);">
                            <td style="padding: 12px; font-family: 'JetBrains Mono', monospace; font-weight: 700; color: var(--accent-gold);">#${idx + 1}</td>
                            <td style="padding: 12px;">
                                <div style="font-weight: 600; color: var(--text-white);">${item.title}</div>
                                <div style="font-size: 11px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;">Ref: ${item.tender_id}</div>
                            </td>
                            <td style="padding: 12px; color: var(--text-primary); font-family: 'JetBrains Mono', monospace;">${item.supplier}</td>
                            <td style="padding: 12px;">
                                <span style="padding: 4px 10px; border-radius: 12px; font-size: 10px; font-weight: 700; letter-spacing: 0.1em; background: ${isPursue ? 'rgba(76,175,80,0.15)' : 'rgba(224,92,92,0.15)'}; color: ${isPursue ? '#4CAF50' : '#e05c5c'}; border: 1px solid ${isPursue ? 'rgba(76,175,80,0.3)' : 'rgba(224,92,92,0.3)'};">${item.rec.toUpperCase()}</span>
                            </td>
                            <td style="padding: 12px; text-align: right; font-family: 'JetBrains Mono', monospace; font-size: 15px; font-weight: 700; color: ${item.prob >= 0.419 ? '#4CAF50' : '#e05c5c'};">${pPct}%</td>
                            <td style="padding: 12px; text-align: right; font-family: 'JetBrains Mono', monospace; color: var(--text-muted);">${item.framework}</td>
                        </tr>
                    `;
                }).join('');
            }

            const countLbl = document.getElementById('wsTableDisplayCount');
            if (countLbl) countLbl.textContent = `Showing ${filtered.length} of ${wsRankedTendersList.length} ranked tenders`;
        }

        // --- FULL INTERACTIVE CALENDAR HANDLERS ---
        const wsMonthNames = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"];
        let wsCurrentDate = new Date();
        let wsCalendarEvents = [];

        async function loadWsCalendar() {
            try {
                const res = await fetch('/api/calendar-events');
                wsCalendarEvents = await res.json();
            } catch(e) {
                // Fallback calendar events
                wsCalendarEvents = [
                    { date: '2026-07-28', tender_identifier: 'Tender 1d59478c', filename: 'ICT Equipment Supply', event_type: 'closing' },
                    { date: '2026-08-04', tender_identifier: 'Tender 88492015', filename: 'Server Rack Infrastructure', event_type: 'briefing' },
                    { date: '2026-08-15', tender_identifier: 'Tender 77391024', filename: 'Network Switch Deployment', event_type: 'award' }
                ];
            }
            renderWsCalendar();
            renderWsUpcoming();
        }

        function changeWsMonth(delta) {
            wsCurrentDate.setMonth(wsCurrentDate.getMonth() + delta);
            renderWsCalendar();
        }

        function renderWsCalendar() {
            const y = wsCurrentDate.getFullYear();
            const m = wsCurrentDate.getMonth();
            const monthLbl = document.getElementById('wsMonthDisplay');
            if (monthLbl) monthLbl.textContent = `${wsMonthNames[m]} ${y}`;

            const firstDay = new Date(y, m, 1).getDay();
            const daysInMonth = new Date(y, m + 1, 0).getDate();
            const body = document.getElementById('wsCalendarBody');
            if (!body) return;

            let html = '';
            for (let i = 0; i < firstDay; i++) {
                html += `<div style="min-height: 90px; padding: 8px; border-right: 1px solid var(--border-faint); border-bottom: 1px solid var(--border-faint); opacity: 0.25;"></div>`;
            }

            const today = new Date();

            for (let i = 1; i <= daysInMonth; i++) {
                const isToday = i === today.getDate() && m === today.getMonth() && y === today.getFullYear();
                const dayEvents = wsCalendarEvents.filter(e => {
                    const d = new Date(e.date);
                    return d.getDate() === i && d.getMonth() === m && d.getFullYear() === y;
                });

                let dots = dayEvents.map(ev => {
                    const bg = ev.event_type === 'closing' ? '#e05c5c' : (ev.event_type === 'briefing' ? 'var(--accent-gold)' : '#52c27c');
                    return `<div style="width: 6px; height: 6px; border-radius: 50%; background-color: ${bg};" title="${ev.tender_identifier}: ${ev.filename}"></div>`;
                }).join('');

                html += `
                    <div style="min-height: 90px; padding: 8px; border-right: 1px solid var(--border-faint); border-bottom: 1px solid var(--border-faint); cursor: pointer; transition: background 0.2s;" onmouseover="this.style.background='rgba(197,168,128,0.06)'" onmouseout="this.style.background='transparent'" onclick="showToast('ðŸ“… Date Info', 'Day ${i}: ${dayEvents.length} scheduled milestones.')">
                        <div style="font-size: 12px; font-weight: ${isToday ? '700' : '400'}; color: ${isToday ? 'var(--accent-gold)' : 'var(--text-white)'}; margin-bottom: 6px;">${i}</div>
                        <div style="display: flex; flex-wrap: wrap; gap: 4px;">${dots}</div>
                    </div>
                `;
            }

            const totalCells = firstDay + daysInMonth;
            const remaining = Math.ceil(totalCells / 7) * 7 - totalCells;
            for (let i = 0; i < remaining; i++) {
                html += `<div style="min-height: 90px; padding: 8px; border-right: 1px solid var(--border-faint); border-bottom: 1px solid var(--border-faint); opacity: 0.25;"></div>`;
            }

            body.innerHTML = html;
        }

        function renderWsUpcoming() {
            const list = document.getElementById('wsUpcomingList');
            if (!list) return;

            if (wsCalendarEvents.length === 0) {
                document.getElementById('wsEmptySide').style.display = 'block';
                list.innerHTML = '';
                return;
            }

            list.innerHTML = wsCalendarEvents.slice(0, 8).map(e => `
                <li style="padding: 12px 0; border-bottom: 1px solid var(--border-faint); font-size: 12px;">
                    <div style="color: var(--accent-gold); font-family: 'JetBrains Mono', monospace; font-weight: 600; margin-bottom: 2px;">${e.date}</div>
                    <div style="color: var(--text-white); font-weight: 500;">${e.tender_identifier} - ${e.filename}</div>
                    <div style="color: var(--text-muted); font-size: 10px; text-transform: uppercase; margin-top: 2px;">${e.event_type}</div>
                </li>
            `).join('');
        }

        // Initialize Pipeline and Calendar on load
        loadWsRankedPipeline();
        loadWsCalendar();

        // --- LUXURY THEME SWITCHER (DARK / LIGHT) ---
        let currentCompanyId = "pro_corp"; // Mocking authentication. Change to "starter_corp" to see locked state.

        async function checkSubscriptionStatus() {
            try {
                const res = await fetch('/api/subscription-status', {
                    headers: { 'X-Company-ID': currentCompanyId }
                });
                const data = await res.json();
                
                const lockedState = document.getElementById('agentLockedState');
                const activeState = document.getElementById('agentActiveState');
                const quotaText = document.getElementById('agentQuotaText');
                
                if (data.features.agent_enabled) {
                    lockedState.style.display = 'none';
                    activeState.style.display = 'flex';
                    
                    if (data.features.quotes_per_day > 0) {
                        const used = data.usage.quotes_used_today;
                        const limit = data.features.quotes_per_day;
                        quotaText.textContent = `${used} of ${limit} quotes used today`;
                        if (used >= limit) {
                            quotaText.style.color = '#e05c5c';
                        }
                    }
                } else {
                    lockedState.style.display = 'block';
                    activeState.style.display = 'none';
                }
            } catch (err) {
                console.error("Subscription check failed", err);
            }
        }

        function fillAgentPrompt(text) {
            document.getElementById('agentInput').value = text;
        }

        

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
                if (mode === 'light') {
                    label.textContent = 'DARK MODE';
                    icon.innerHTML = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>';
                } else {
                    label.textContent = 'LIGHT MODE';
                    icon.innerHTML = '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>';
                }
            }
            
            if (labelM) labelM.textContent = mode === 'light' ? 'DARK MODE' : 'LIGHT MODE';
            if (iconM) iconM.innerHTML = mode === 'light' ? '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>' : '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>';
        }
        initLuxuryTheme();
    
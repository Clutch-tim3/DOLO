import sys

with open("static/index.html", "r") as f:
    content = f.read()

# Append script to the end of the file before </body>
script_injection = """
<script>
// --- BATCH SORT LOGIC ---
const openSortBtn = document.getElementById('openSortBtn');
const closeSortBtn = document.getElementById('closeSortBtn');
const bulkSortPanel = document.getElementById('bulkSortPanel');
const sortResultsSection = document.getElementById('sortResultsSection');
const bulkSortForm = document.getElementById('bulkSortForm');
const batchTenderFiles = document.getElementById('batch_tender_files');
const clearBatchFilesBtn = document.getElementById('clear_batch_files');
const batchFilesList = document.getElementById('batch_files_list');
const batchProgressContainer = document.getElementById('batchProgressContainer');
const batchProgressBar = document.getElementById('batchProgressBar');
const batchProgressText = document.getElementById('batchProgressText');
const runBatchBtn = document.getElementById('runBatchBtn');
const sortResultsBody = document.getElementById('sortResultsBody');
const sortResultsTableWrapper = document.getElementById('sortResultsTableWrapper');
const emptyBatchState = document.getElementById('emptyBatchState');
const batchSummaryText = document.getElementById('batchSummaryText');
const exportCsvBtn = document.getElementById('exportCsvBtn');

let currentBatchResults = [];
let currentSortColumn = 'win_prob';
let currentSortAsc = false;

if(openSortBtn) {
    openSortBtn.addEventListener('click', (e) => {
        e.preventDefault();
        bulkSortPanel.classList.add('active');
        sortResultsSection.style.display = 'block';
    });
}
if(closeSortBtn) {
    closeSortBtn.addEventListener('click', () => {
        bulkSortPanel.classList.remove('active');
    });
}

batchTenderFiles.addEventListener('change', () => {
    if (batchTenderFiles.files.length > 0) {
        clearBatchFilesBtn.style.display = 'block';
        batchFilesList.innerHTML = `<strong>${batchTenderFiles.files.length}</strong> tender documents selected`;
    } else {
        clearBatchFilesBtn.style.display = 'none';
        batchFilesList.innerHTML = '';
    }
});

clearBatchFilesBtn.addEventListener('click', () => {
    batchTenderFiles.value = '';
    clearBatchFilesBtn.style.display = 'none';
    batchFilesList.innerHTML = '';
});

// Intercept fetchArchive to populate the batch supplier select
const originalFetchArchive = fetchArchive;
fetchArchive = async function() {
    await originalFetchArchive();
    const select = document.getElementById('batch_supplier_select');
    if(!select) return;
    
    // Clear except first option
    while (select.options.length > 1) {
        select.remove(1);
    }
    
    const cards = document.querySelectorAll('.company-card');
    if (cards.length > 0) {
        select.style.display = 'block';
        // The API response is not directly accessible here easily without global var, 
        // but we can extract names from the DOM or just fetch again
        fetch("/api/companies").then(r => r.json()).then(data => {
            data.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.company_name;
                opt.textContent = c.company_name;
                select.appendChild(opt);
            });
        });
    }
};

document.getElementById('batch_supplier_select').addEventListener('change', (e) => {
    const textInput = document.getElementById('batch_supplier_name');
    if(e.target.value) {
        textInput.value = e.target.value;
        textInput.style.display = 'none';
    } else {
        textInput.value = '';
        textInput.style.display = 'block';
    }
});

bulkSortForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if(batchTenderFiles.files.length === 0) {
        alert("Please select at least one file.");
        return;
    }
    
    runBatchBtn.style.opacity = '0.5';
    runBatchBtn.style.pointerEvents = 'none';
    batchProgressContainer.style.display = 'block';
    batchProgressText.textContent = `Initializing batch job...`;
    batchProgressBar.style.width = '0%';
    
    const formData = new FormData();
    for(let i=0; i<batchTenderFiles.files.length; i++){
        formData.append("files", batchTenderFiles.files[i]);
    }
    formData.append("supplier_name", document.getElementById('batch_supplier_name').value);
    
    try {
        const res = await fetch("/api/batch-sort", {
            method: "POST",
            body: formData
        });
        const data = await res.json();
        
        if(!res.ok) throw new Error(data.detail || "Upload failed");
        
        pollBatchStatus(data.job_id);
    } catch(err) {
        alert(err.message);
        runBatchBtn.style.opacity = '1';
        runBatchBtn.style.pointerEvents = 'auto';
        batchProgressContainer.style.display = 'none';
    }
});

async function pollBatchStatus(job_id) {
    try {
        const res = await fetch(`/api/batch-status/${job_id}`);
        const data = await res.json();
        
        if(data.total > 0) {
            const pct = (data.processed / data.total) * 100;
            batchProgressBar.style.width = `${pct}%`;
            batchProgressText.textContent = `Processing ${data.processed} of ${data.total} tenders...`;
        }
        
        if(data.status === "complete") {
            currentBatchResults = data.results;
            renderBatchTable();
            runBatchBtn.style.opacity = '1';
            runBatchBtn.style.pointerEvents = 'auto';
            setTimeout(() => { batchProgressContainer.style.display = 'none'; }, 2000);
            
            // Auto-close drawer
            bulkSortPanel.classList.remove('active');
            
            // Show summary
            let pursue = 0, pass = 0, disq = 0;
            currentBatchResults.forEach(r => {
                if(r.recommendation === 'PURSUE') pursue++;
                else if(r.recommendation === 'DISQUALIFIED') disq++;
                else pass++;
            });
            batchSummaryText.textContent = `${data.total} tenders analyzed — ${pursue} PURSUE, ${pass} PASS, ${disq} DISQUALIFIED`;
            batchSummaryText.style.display = 'block';
            emptyBatchState.style.display = 'none';
            sortResultsTableWrapper.style.display = 'block';
            exportCsvBtn.style.display = 'block';
            
        } else {
            setTimeout(() => pollBatchStatus(job_id), 1500);
        }
    } catch(err) {
        console.error("Polling error", err);
        setTimeout(() => pollBatchStatus(job_id), 3000);
    }
}

function sortBatchTable(column) {
    if(currentSortColumn === column) {
        currentSortAsc = !currentSortAsc;
    } else {
        currentSortColumn = column;
        currentSortAsc = false;
    }
    
    const headers = document.querySelectorAll('#sortResultsTable th');
    headers.forEach(h => h.innerHTML = h.innerHTML.replace(' ▾', '').replace(' ▴', ''));
    const targetTh = Array.from(headers).find(h => h.getAttribute('onclick').includes(column));
    if(targetTh) targetTh.innerHTML += currentSortAsc ? ' ▴' : ' ▾';
    
    renderBatchTable();
}

function renderBatchTable() {
    sortResultsBody.innerHTML = '';
    
    // Sort logic
    let sorted = [...currentBatchResults];
    sorted.sort((a, b) => {
        // Disqualified always at bottom
        if (a.disqualified && !b.disqualified) return 1;
        if (!a.disqualified && b.disqualified) return -1;
        
        let valA, valB;
        if (currentSortColumn === 'win_prob') {
            valA = a.win_probability || 0;
            valB = b.win_probability || 0;
        } else if (currentSortColumn === 'filename') {
            valA = a.filename;
            valB = b.filename;
        } else if (currentSortColumn === 'recommendation') {
            valA = a.recommendation;
            valB = b.recommendation;
        } else if (currentSortColumn === 'rank') {
            // Rank relies on default sorting (win prob desc)
            valA = a.win_probability || 0;
            valB = b.win_probability || 0;
        }
        
        if (valA < valB) return currentSortAsc ? -1 : 1;
        if (valA > valB) return currentSortAsc ? 1 : -1;
        return 0;
    });
    
    sorted.forEach((r, idx) => {
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
        
        // Colors
        let recColor = '#999';
        if(r.recommendation === 'PURSUE') recColor = 'var(--accent-gold)';
        if(r.recommendation === 'DISQUALIFIED') recColor = '#e05c5c';
        
        let rankColor = (idx < 3 && r.recommendation !== 'DISQUALIFIED' && !currentSortAsc && currentSortColumn === 'win_prob') ? 'var(--accent-gold)' : '#fff';
        
        let prob = r.win_probability ? (r.win_probability*100).toFixed(1)+'%' : '—';
        let saProb = r.sa_adjusted_probability ? (r.sa_adjusted_probability*100).toFixed(1)+'%' : '—';
        
        if(r.processing_error) {
            recColor = '#e05c5c';
            r.recommendation = 'ERROR';
        }

        tr.innerHTML = `
            <td style="color:${rankColor}; padding: 12px 0;">#${idx+1}</td>
            <td style="color:#fff;">${r.filename}<br><small style="color:#666;">${r.tender_identifier || 'Unknown ID'}</small></td>
            <td style="color:${recColor}; font-weight:600;">${r.recommendation}</td>
            <td>${prob}</td>
            <td>${saProb}</td>
            <td>${r.competitive_position || '—'}</td>
            <td>${r.preferential_framework || '—'}</td>
        `;
        
        tr.addEventListener('click', () => {
            const nextRow = tr.nextElementSibling;
            if(nextRow && nextRow.classList.contains('expanded-row')) {
                nextRow.remove();
                tr.style.backgroundColor = 'transparent';
            } else {
                tr.style.backgroundColor = 'rgba(255,255,255,0.02)';
                const exp = document.createElement('tr');
                exp.className = 'expanded-row';
                let expContent = '';
                
                if(r.disqualified) {
                    expContent = `<div style="padding: 16px; color: #e05c5c;">
                        <strong>HARD FAILURES:</strong><br>
                        ${r.hard_failures.map(f => `✗ ${f}`).join('<br>')}
                    </div>`;
                } else if(r.processing_error) {
                    expContent = `<div style="padding: 16px; color: #e05c5c;">
                        <strong>PROCESSING ERROR:</strong><br>${r.processing_error}
                    </div>`;
                } else {
                    expContent = `<div style="padding: 16px; color: var(--accent-gold);">
                        Detailed metrics parsing succeeded. (In full version, this embeds the entire results-section table for this specific tender).
                    </div>`;
                }
                
                exp.innerHTML = `<td colspan="7" style="border-bottom: 1px solid rgba(255,255,255,0.05);">${expContent}</td>`;
                tr.after(exp);
            }
        });
        
        sortResultsBody.appendChild(tr);
    });
}

exportCsvBtn.addEventListener('click', () => {
    let csvContent = "data:text/csv;charset=utf-8,Filename,Tender_ID,Recommendation,Win_Probability,SA_Probability,Framework\\n";
    currentBatchResults.forEach(r => {
        let row = [
            r.filename,
            r.tender_identifier || '',
            r.recommendation,
            r.win_probability || '',
            r.sa_adjusted_probability || '',
            r.preferential_framework || ''
        ].join(",");
        csvContent += row + "\\n";
    });
    
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", "batch_results.csv");
    document.body.appendChild(link);
    link.click();
    link.remove();
});
</script>
</body>"""

content = content.replace("</body>", script_injection)

with open("static/index.html", "w") as f:
    f.write(content)

import sys

with open('static/index.html', 'r') as f:
    content = f.read()

# Add a "MARK OUTCOME" dropdown to the results section header in index.html
res_head = """<div class="two-column-layout">
                <!-- Left Column: Capabilities -->
                <div class="column capabilities-column">"""

new_res_head = """<div style="display:flex; justify-content: flex-end; margin-bottom:16px;" id="markOutcomeContainer">
                <div style="position:relative;">
                    <button type="button" class="corner-btn" id="markOutcomeBtn" style="padding:8px 16px; font-size:11px;">
                        <span>MARK OUTCOME ▾</span>
                        <span class="bracket bracket-tr"></span><span class="bracket bracket-bl"></span>
                    </button>
                    <div id="markOutcomeDropdown" style="display:none; position:absolute; right:0; top:100%; margin-top:8px; background:rgba(0,0,0,0.9); border:1px solid rgba(197,168,128,0.3); padding:16px; z-index:100; min-width:250px;">
                        <form id="markOutcomeForm">
                            <input type="hidden" id="outcome_prediction_id" name="prediction_id">
                            <label style="font-size:11px; color:#999; display:block; margin-bottom:4px;">Actual Outcome</label>
                            <select name="actual_outcome" style="width:100%; background:rgba(0,0,0,0.5); color:#fff; border:1px solid #333; padding:8px; margin-bottom:12px;">
                                <option value="won">Won</option>
                                <option value="lost">Lost</option>
                                <option value="withdrawn">Withdrawn</option>
                            </select>
                            <label style="font-size:11px; color:#999; display:block; margin-bottom:4px;">Date (Optional)</label>
                            <input type="date" name="outcome_date" style="width:100%; background:rgba(0,0,0,0.5); color:#fff; border:1px solid #333; padding:7px; margin-bottom:12px;">
                            <label style="font-size:11px; color:#999; display:block; margin-bottom:4px;">Notes (Optional)</label>
                            <input type="text" name="notes" style="width:100%; background:rgba(0,0,0,0.5); color:#fff; border:1px solid #333; padding:8px; margin-bottom:16px;">
                            <button type="submit" class="corner-btn" style="width:100%; padding:8px; font-size:11px;">
                                <span>SAVE OUTCOME</span>
                                <span class="bracket bracket-tr"></span><span class="bracket bracket-bl"></span>
                            </button>
                        </form>
                    </div>
                </div>
            </div>
            
            <div class="two-column-layout">
                <!-- Left Column: Capabilities -->
                <div class="column capabilities-column">"""

content = content.replace(res_head, new_res_head)

# Also need to add the JS to show/hide the dropdown and submit
js_add = """
        // Mark outcome dropdown logic
        const markOutcomeBtn = document.getElementById('markOutcomeBtn');
        const markOutcomeDropdown = document.getElementById('markOutcomeDropdown');
        const markOutcomeForm = document.getElementById('markOutcomeForm');
        
        if(markOutcomeBtn) {
            markOutcomeBtn.addEventListener('click', () => {
                markOutcomeDropdown.style.display = markOutcomeDropdown.style.display === 'none' ? 'block' : 'none';
            });
            
            markOutcomeForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                const fd = new FormData(markOutcomeForm);
                try {
                    await fetch('/api/track-outcome', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(Object.fromEntries(fd.entries()))
                    });
                    markOutcomeDropdown.style.display = 'none';
                    markOutcomeBtn.querySelector('span').textContent = 'SAVED ✓';
                    setTimeout(() => markOutcomeBtn.querySelector('span').textContent = 'MARK OUTCOME ▾', 3000);
                } catch(err) {
                    alert('Error saving outcome');
                }
            });
        }
"""

content = content.replace("</script>", js_add + "\n</script>")

# Update the form submission to store prediction_id
submit_logic = """const data = await res.json();
        
        if(!res.ok) throw new Error(data.detail || "Prediction failed");"""

new_submit_logic = """const data = await res.json();
        
        if(!res.ok) throw new Error(data.detail || "Prediction failed");
        
        if(document.getElementById('outcome_prediction_id')) {
            document.getElementById('outcome_prediction_id').value = data.prediction_id;
        }"""
        
content = content.replace(submit_logic, new_submit_logic)

with open('static/index.html', 'w') as f:
    f.write(content)


# 3. Patch sort.html
with open('static/sort.html', 'r') as f:
    sort_content = f.read()

# I need to add an action button in sort.html JS for each row
sort_row_js = """<td style="color:${rankColor}; padding: 12px 0;">#${idx+1}</td>
            <td style="color:#fff;">${r.filename}<br><small style="color:#666;">${r.tender_identifier || 'Unknown ID'}</small></td>
            <td style="color:${recColor}; font-weight:600;">${r.recommendation}</td>
            <td>${prob}</td>
            <td>${saProb}</td>
            <td>${r.competitive_position || '—'}</td>
            <td>${r.preferential_framework || '—'}</td>
        `;"""

new_sort_row_js = """<td style="color:${rankColor}; padding: 12px 0;">#${idx+1}</td>
            <td style="color:#fff;">${r.filename}<br><small style="color:#666;">${r.tender_identifier || 'Unknown ID'}</small></td>
            <td style="color:${recColor}; font-weight:600;">${r.recommendation}</td>
            <td>${prob}</td>
            <td>${saProb}</td>
            <td>${r.competitive_position || '—'}</td>
            <td>${r.preferential_framework || '—'}</td>
            <td>
                ${!r.processing_error ? `<button class="corner-btn mark-outcome-row-btn" data-id="${r.prediction_id}" style="padding:4px 8px; font-size:10px;">
                    <span>MARK</span><span class="bracket bracket-tr" style="width:4px; height:4px;"></span><span class="bracket bracket-bl" style="width:4px; height:4px;"></span>
                </button>` : ''}
            </td>
        `;"""
sort_content = sort_content.replace(sort_row_js, new_sort_row_js)

# Add column header
th_old = '<th class="spec-label">Framework</th>'
th_new = '<th class="spec-label">Framework</th>\n<th class="spec-label">Actions</th>'
sort_content = sort_content.replace(th_old, th_new)

# Add event listener for mark outcome in row
event_js = """sortResultsBody.appendChild(tr);"""
new_event_js = """
        const markBtn = tr.querySelector('.mark-outcome-row-btn');
        if(markBtn) {
            markBtn.addEventListener('click', (ev) => {
                ev.stopPropagation();
                const actual = prompt("Enter outcome for this tender (won/lost/withdrawn):");
                if (actual && ['won','lost','withdrawn'].includes(actual.toLowerCase())) {
                    fetch('/api/track-outcome', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            prediction_id: markBtn.dataset.id,
                            actual_outcome: actual.toLowerCase()
                        })
                    }).then(() => markBtn.querySelector('span').textContent = '✓');
                }
            });
        }
        sortResultsBody.appendChild(tr);"""
sort_content = sort_content.replace(event_js, new_event_js)

with open('static/sort.html', 'w') as f:
    f.write(sort_content)

import sys
import re

with open('static/sort.html', 'r') as f:
    content = f.read()

# Fix the duplicate <th>
content = content.replace('<th class="spec-label">Actions</th>\n<th class="spec-label">Actions</th>', '<th class="spec-label">Actions</th>')

# Fix the duplicate <td> block inside renderBatchTable
target_td = """            <td>
                ${!r.processing_error ? `<button class="corner-btn mark-outcome-row-btn" data-id="${r.prediction_id}" style="padding:4px 8px; font-size:10px;">
                    <span>MARK</span><span class="bracket bracket-tr" style="width:4px; height:4px;"></span><span class="bracket bracket-bl" style="width:4px; height:4px;"></span>
                </button>` : ''}
            </td>
            <td>
                ${!r.processing_error ? `<button class="corner-btn mark-outcome-row-btn" data-id="${r.prediction_id}" style="padding:4px 8px; font-size:10px;">
                    <span>MARK</span><span class="bracket bracket-tr" style="width:4px; height:4px;"></span><span class="bracket bracket-bl" style="width:4px; height:4px;"></span>
                </button>` : ''}
            </td>"""
clean_td = """            <td>
                ${!r.processing_error ? `<button class="corner-btn mark-outcome-row-btn" data-id="${r.prediction_id}" style="padding:4px 8px; font-size:10px;">
                    <span>MARK</span><span class="bracket bracket-tr" style="width:4px; height:4px;"></span><span class="bracket bracket-bl" style="width:4px; height:4px;"></span>
                </button>` : ''}
            </td>"""
content = content.replace(target_td, clean_td)

# Fix the duplicate JS listener
target_js = """        const markBtn = tr.querySelector('.mark-outcome-row-btn');
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
        }"""
clean_js = """        const markBtn = tr.querySelector('.mark-outcome-row-btn');
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
        }"""
content = content.replace(target_js, clean_js)

# Fix the broken script tag at the end
broken_script = """</script>
        
        // Remove drawer sliding behavior that was copied from index
        if(openSortBtn) openSortBtn.remove();
        if(closeSortBtn) closeSortBtn.remove();
        
        // Initial setup for empty state visibility
        document.getElementById('sortResultsTableWrapper').style.display = 'none';
        
        // Initialize companies dropdown manually
        window.addEventListener('DOMContentLoaded', () => {
            const select = document.getElementById('batch_supplier_select');
            if(select) {
                fetch("/api/companies").then(r => r.json()).then(data => {
                    if (data.length > 0) {
                        select.style.display = 'block';
                        data.forEach(c => {
                            const opt = document.createElement('option');
                            opt.value = c.company_name;
                            opt.textContent = c.company_name;
                            select.appendChild(opt);
                        });
                    }
                }).catch(err => console.error(err));
            }
        });
    </script>"""
clean_script = """
        // Remove drawer sliding behavior that was copied from index
        if(openSortBtn) openSortBtn.remove();
        if(closeSortBtn) closeSortBtn.remove();
        
        // Initial setup for empty state visibility
        document.getElementById('sortResultsTableWrapper').style.display = 'none';
        
        // Initialize companies dropdown manually
        window.addEventListener('DOMContentLoaded', () => {
            const select = document.getElementById('batch_supplier_select');
            if(select) {
                fetch("/api/companies").then(r => r.json()).then(data => {
                    if (data.length > 0) {
                        select.style.display = 'block';
                        data.forEach(c => {
                            const opt = document.createElement('option');
                            opt.value = c.company_name;
                            opt.textContent = c.company_name;
                            select.appendChild(opt);
                        });
                    }
                }).catch(err => console.error(err));
            }
        });
    </script>"""
content = content.replace(broken_script, clean_script)

with open('static/sort.html', 'w') as f:
    f.write(content)

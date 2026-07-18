import re

with open('static/sort.html', 'r') as f:
    content = f.read()

# 1. Update the HTML for the file input group
html_old = """<div class="form-group" style="position: relative;">
                        <label for="batch_tender_files">Upload Tender Documents (PDF/DOCX)</label>
                        <div style="display: flex; gap: 12px; align-items: center;">
                            <input type="file" id="batch_tender_files" name="files" accept=".pdf,.docx" multiple style="flex: 1;" required>
                            <button type="button" id="clear_batch_files" style="background: transparent; border: none; color: var(--accent-gold); font-size: 16px; font-weight: bold; cursor: pointer; display: none; padding: 4px 8px;">&times; CLEAR</button>
                        </div>
                        <div id="batch_files_list" style="margin-top:8px; font-size:12px; color:#999;"></div>
                    </div>"""

html_new = """<div class="form-group" style="position: relative;">
                        <label>Upload Tender Documents (PDF/DOCX)</label>
                        
                        <div style="display: flex; gap: 12px; align-items: center; margin-bottom: 8px;">
                            <button type="button" id="addMoreFilesBtn" class="corner-btn" style="padding: 8px 16px; font-size: 11px;">
                                <span>+ ADD FILES</span>
                                <span class="bracket bracket-tr"></span>
                                <span class="bracket bracket-bl"></span>
                            </button>
                            <input type="file" id="batch_tender_files" accept=".pdf,.docx" multiple style="display: none;">
                            <button type="button" id="clear_batch_files" style="background: transparent; border: none; color: var(--accent-gold); font-size: 12px; font-weight: bold; cursor: pointer; display: none; padding: 4px 8px;">[CLEAR ALL]</button>
                        </div>
                        
                        <!-- Accumulated file list -->
                        <div id="batch_files_list" style="margin-top:12px; font-size:12px; color:#ccc; display:flex; flex-direction:column; gap:4px; max-height: 150px; overflow-y: auto;"></div>
                    </div>"""

content = content.replace(html_old, html_new)

# 2. Update the JS logic
js_old = """batchTenderFiles.addEventListener('change', () => {
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
});"""

js_new = """let accumulatedFiles = [];
const addMoreFilesBtn = document.getElementById('addMoreFilesBtn');

addMoreFilesBtn.addEventListener('click', () => {
    batchTenderFiles.click();
});

function renderFileList() {
    batchFilesList.innerHTML = '';
    if (accumulatedFiles.length > 0) {
        clearBatchFilesBtn.style.display = 'block';
        accumulatedFiles.forEach((file, index) => {
            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.justifyContent = 'space-between';
            row.style.padding = '4px 8px';
            row.style.background = 'rgba(255,255,255,0.05)';
            row.style.borderRadius = '2px';
            
            const nameSpan = document.createElement('span');
            nameSpan.textContent = file.name;
            
            const removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.innerHTML = '&times;';
            removeBtn.style.background = 'transparent';
            removeBtn.style.border = 'none';
            removeBtn.style.color = '#e05c5c';
            removeBtn.style.cursor = 'pointer';
            removeBtn.style.fontSize = '14px';
            removeBtn.addEventListener('click', () => {
                accumulatedFiles.splice(index, 1);
                renderFileList();
            });
            
            row.appendChild(nameSpan);
            row.appendChild(removeBtn);
            batchFilesList.appendChild(row);
        });
    } else {
        clearBatchFilesBtn.style.display = 'none';
        batchFilesList.innerHTML = 'No files selected.';
    }
}

batchTenderFiles.addEventListener('change', () => {
    if (batchTenderFiles.files.length > 0) {
        // Append new files to our accumulated list
        for (let i = 0; i < batchTenderFiles.files.length; i++) {
            accumulatedFiles.push(batchTenderFiles.files[i]);
        }
        // Reset the input so the same file can be selected again if needed
        batchTenderFiles.value = '';
        renderFileList();
    }
});

clearBatchFilesBtn.addEventListener('click', () => {
    accumulatedFiles = [];
    batchTenderFiles.value = '';
    renderFileList();
});
"""
content = content.replace(js_old, js_new)

# Modify the form submit to use accumulatedFiles
submit_old = """const formData = new FormData();
    for(let i=0; i<batchTenderFiles.files.length; i++){
        formData.append("files", batchTenderFiles.files[i]);
    }"""
submit_new = """const formData = new FormData();
    for(let i=0; i<accumulatedFiles.length; i++){
        formData.append("files", accumulatedFiles[i]);
    }"""
content = content.replace(submit_old, submit_new)

# Change the validation alert
alert_old = "if(batchTenderFiles.files.length === 0)"
alert_new = "if(accumulatedFiles.length === 0)"
content = content.replace(alert_old, alert_new)

with open('static/sort.html', 'w') as f:
    f.write(content)

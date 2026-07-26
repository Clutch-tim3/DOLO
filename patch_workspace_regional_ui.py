import glob

for path in ['static/workspace.html', 'firebase_public/workspace.html']:
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
    
    old_status = '<div class="model-status-indicator"><span class="pulse-dot"></span> <span>MODEL ONLINE (CONQUEST V1)</span></div>'
    new_status = '<div class="model-status-indicator" style="flex-direction:column; align-items:flex-start; gap:4px;"><div style="display:flex; align-items:center; gap:6px;"><span class="pulse-dot"></span> <span style="font-weight:700; color:var(--text-white);">ENGINES ACTIVE</span></div><div style="font-size:9px; color:var(--text-muted);">🇿🇦 Conquest-ZA: <b style="color:#52c27c;">0.8578 AUC</b> (PPPFA)</div><div style="font-size:9px; color:var(--text-muted);">🇬🇧 Conquest-UK: <b style="color:#c5a880;">0.6941 AUC</b> (MEAT)</div></div>'
    
    if 'Conquest-ZA' not in html:
        html = html.replace(old_status, new_status)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f'Updated regional engine badge in {path}')

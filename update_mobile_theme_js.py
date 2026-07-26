import glob

for path in glob.glob('static/*.html'):
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
    
    if 'themeLabelMobile' in html and 'updateThemeUI' in html:
        # Check if mobile sync code is already inside updateThemeUI
        if 'themeLabelMobile' not in html.split('function updateThemeUI')[1]:
            old_func = """const label = document.getElementById('themeLabel');
            const icon = document.getElementById('themeIcon');"""
            
            new_func = """const label = document.getElementById('themeLabel');
            const icon = document.getElementById('themeIcon');
            const labelM = document.getElementById('themeLabelMobile');
            const iconM = document.getElementById('themeIconMobile');"""
            
            html = html.replace("const label = document.getElementById('themeLabel');", new_func)
            
            # Sync labelM and iconM
            sync_code = """if (labelM) labelM.textContent = mode === 'light' ? 'DARK MODE' : 'LIGHT MODE';
            if (iconM) iconM.innerHTML = mode === 'light' ? '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>' : '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>';"""
            
            html = html.replace("initLuxuryTheme();", sync_code + "\ninitLuxuryTheme();")
            
            with open(path, 'w', encoding='utf-8') as f:
                f.write(html)
            print(f'Synced mobile theme toggle JS in {path}')

import re
import sys

def replace_tab_content(workspace_html, tab_id, replacement_file):
    with open(replacement_file, 'r', encoding='utf-8') as f:
        replacement_content = f.read()

    # Find the opening section tag
    pattern_start = re.compile(rf'(<section\s+id="{tab_id}"[^>]*>)')
    match_start = pattern_start.search(workspace_html)
    if not match_start:
        print(f"Could not find <section id='{tab_id}'> in workspace.html")
        return workspace_html
    
    start_idx = match_start.end()
    
    # Find the closing </section> tag
    # Assuming no nested <section> tags within the tab content
    end_idx = workspace_html.find('</section>', start_idx)
    if end_idx == -1:
        print(f"Could not find closing </section> for {tab_id}")
        return workspace_html
    
    # Replace content
    print(f"Successfully injected {replacement_file} into {tab_id}")
    return workspace_html[:start_idx] + '\n' + replacement_content + '\n' + workspace_html[end_idx:]

def main():
    try:
        with open('static/workspace.html', 'r', encoding='utf-8') as f:
            html = f.read()
    except FileNotFoundError:
        print("workspace.html not found!")
        return

    tabs_to_replace = {
        'tab-calendar': 'temp_tab_calendar.html',
        'tab-system': 'temp_tab_system.html',
        'tab-sort': 'temp_tab_sort.html',
        'tab-archive': 'temp_tab_archive.html',
        'tab-evaluation': 'temp_tab_evaluation.html'
    }

    for tab_id, fname in tabs_to_replace.items():
        html = replace_tab_content(html, tab_id, fname)

    with open('static/workspace.html', 'w', encoding='utf-8') as f:
        f.write(html)
        
    print("All tabs stitched successfully.")

if __name__ == '__main__':
    main()

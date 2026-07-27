"""
Sync standalone page designs into workspace.html console tabs.
Extracts <main> + page logic <script> from each standalone page
and replaces the corresponding <section> content in workspace.html.
"""
import re

PROJECT = r"C:\Users\Thabang\.gemini\antigravity\scratch\DOLO\static"

def read_file(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.readlines()

def write_file(path, lines):
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

def extract_content(filepath, main_start, main_end, script_start, script_end):
    """Extract lines from main_start to script_end (1-indexed, inclusive)."""
    lines = read_file(filepath)
    # Convert to 0-indexed
    return lines[main_start-1:script_end]

# ===== READ WORKSPACE =====
ws_path = f"{PROJECT}\\workspace.html"
ws_lines = read_file(ws_path)

print(f"Workspace has {len(ws_lines)} lines")

# ===== SORT: standalone lines 86-618, workspace section lines 617-1152 =====
sort_path = f"{PROJECT}\\sort.html"
sort_lines = read_file(sort_path)
# Extract lines 86 (main start) to 618 (script end) from sort.html
sort_content = sort_lines[85:618]  # 0-indexed: 85 to 617 inclusive
print(f"Sort content extracted: {len(sort_content)} lines")

# Find #tab-sort section in workspace (line 617) to </section> before #tab-archive (line 1152)
# We need to find the exact boundaries
sort_section_start = None
sort_section_end = None
for i, line in enumerate(ws_lines):
    if 'id="tab-sort"' in line:
        sort_section_start = i
    if sort_section_start is not None and i > sort_section_start:
        if 'id="tab-archive"' in line:
            # Go back to find </section> before this
            for j in range(i-1, sort_section_start, -1):
                if '</section>' in ws_lines[j]:
                    sort_section_end = j + 1  # inclusive
                    break
            break

print(f"Sort section in workspace: lines {sort_section_start+1} to {sort_section_end}")

# ===== CALENDAR: standalone lines 97-238, workspace section lines 1279-1423 =====
cal_path = f"{PROJECT}\\calendar.html"
cal_lines = read_file(cal_path)
# Extract lines 97 (main start) to 238 (script end)
cal_content = cal_lines[96:238]  # 0-indexed
print(f"Calendar content extracted: {len(cal_content)} lines")

cal_section_start = None
cal_section_end = None
for i, line in enumerate(ws_lines):
    if 'id="tab-calendar"' in line:
        cal_section_start = i
    if cal_section_start is not None and i > cal_section_start:
        if 'id="tab-quotation"' in line:
            for j in range(i-1, cal_section_start, -1):
                if '</section>' in ws_lines[j]:
                    cal_section_end = j + 1
                    break
            break

print(f"Calendar section in workspace: lines {cal_section_start+1} to {cal_section_end}")

# ===== SYSTEM: standalone lines 99-261, workspace section lines 1467-1632 =====
sys_path = f"{PROJECT}\\system.html"
sys_lines = read_file(sys_path)
# Extract lines 99 (main start) to 261 (script end)
sys_content = sys_lines[98:261]  # 0-indexed
print(f"System content extracted: {len(sys_content)} lines")

sys_section_start = None
sys_section_end = None
for i, line in enumerate(ws_lines):
    if 'id="tab-system"' in line:
        sys_section_start = i
    if sys_section_start is not None and i > sys_section_start:
        if 'id="tab-agent"' in line:
            for j in range(i-1, sys_section_start, -1):
                if '</section>' in ws_lines[j]:
                    sys_section_end = j + 1
                    break
            break

print(f"System section in workspace: lines {sys_section_start+1} to {sys_section_end}")

# ===== REBUILD WORKSPACE =====
# We need to replace sections from bottom to top to preserve line numbers

# Sort replacements by start position (descending) so we replace from bottom first
replacements = [
    ("system", sys_section_start, sys_section_end, sys_content),
    ("calendar", cal_section_start, cal_section_end, cal_content),
    ("sort", sort_section_start, sort_section_end, sort_content),
]

# Sort descending by start position
replacements.sort(key=lambda x: x[1], reverse=True)

for name, start, end, content in replacements:
    if start is None or end is None:
        print(f"WARNING: Could not find {name} section boundaries!")
        continue
    
    # Build new section
    section_id = f"tab-{name}"
    new_section = [f'        <section id="{section_id}" class="content-section">\n']
    new_section.extend(content)
    new_section.append('\n</section>\n\n')
    
    print(f"Replacing {name}: workspace lines {start+1}-{end} ({end-start} lines) with {len(new_section)} lines")
    ws_lines[start:end] = new_section

# ===== FIX AGENT TAB =====
# Remove hardcoded style="display: none;" from tab-agent
for i, line in enumerate(ws_lines):
    if 'id="tab-agent"' in line and 'style="display: none;"' in line:
        ws_lines[i] = line.replace(' style="display: none;"', '')
        print(f"Fixed agent tab display:none on line {i+1}")
        break

# ===== WRITE RESULT =====
write_file(ws_path, ws_lines)
print(f"\nWorkspace updated! New line count: {len(ws_lines)}")

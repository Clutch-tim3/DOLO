import re

with open('static/index.html', 'r') as f:
    content = f.read()

# 1. Extract batch sort HTML blocks
batch_sort_panel_match = re.search(r'<!-- Batch Sort Drawer.*?</section>', content, re.DOTALL)
batch_sort_panel_html = batch_sort_panel_match.group(0) if batch_sort_panel_match else ""

sort_results_match = re.search(r'<!-- Sort Results Section.*?</section>', content, re.DOTALL)
sort_results_html = sort_results_match.group(0) if sort_results_match else ""

# Extract JS block
js_match = re.search(r'// --- BATCH SORT LOGIC ---.*?</script>', content, re.DOTALL)
js_logic = js_match.group(0) if js_match else ""

# 2. Build static/sort.html
sort_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Donington Vale — Batch Analysis</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Outfit:wght@200;300;400;600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="static/style.css">
    <style>
        .page-container {
            padding-top: 80px;
        }
        .hero-section {
            margin-bottom: 40px;
        }
        /* Override panel styles so they appear inline rather than absolute/fixed slide-overs */
        #bulkSortPanel {
            position: relative;
            top: auto;
            right: auto;
            width: 100%;
            height: auto;
            transform: none;
            display: block; /* Always visible */
            background: rgba(0,0,0,0.3);
            border: 1px solid rgba(197, 168, 128, 0.1);
            margin-bottom: 40px;
        }
        #bulkSortPanel .panel-container {
            padding: 40px;
        }
        #bulkSortPanel .close-btn {
            display: none; /* No close button since it's a page */
        }
        #sortResultsSection {
            display: block !important; /* Always visible block but empty state shows by default */
        }
    </style>
</head>
<body>
    <nav class="top-nav">
        <div class="nav-left">
            <a href="/" class="back-link">
                <svg class="back-arrow" width="16" height="12" viewBox="0 0 16 12" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M6 1L1 6M1 6L6 11M1 6H15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                <span>BACK TO MAIN</span>
            </a>
        </div>
        <div class="nav-center">
            <span class="logo">DONINGTON VALE</span>
        </div>
        <div class="nav-right"></div>
    </nav>

    <main class="page-container">
        <section class="hero-section">
            <div class="hero-content" style="max-width: 800px; margin: 0 auto; text-align: center;">
                <span class="category-label">BATCH PROCUREMENT PIPELINE</span>
                <h1 class="hero-title" style="font-size: 48px; margin-top: 12px; margin-bottom: 24px;">SORT TENDERS.</h1>
                <p class="hero-subtitle" style="font-size: 16px; margin: 0 auto 32px auto; max-width: 600px;">
                    Upload multiple tender documents at once. Our engine will read each document, run the hard eligibility screening, and process it through the ML ensemble to rank your best opportunities.
                </p>
            </div>
        </section>

        """ + batch_sort_panel_html.replace('id="closeSortBtn"', 'id="closeSortBtn" style="display:none;"') + """
        
        """ + sort_results_html + """
    </main>
    <script>
        // Fake fetchArchive for standalone page since it's used in JS
        async function fetchArchive() { return []; }
        
        """ + js_logic + """
        
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
    </script>
</body>
</html>
"""

with open('static/sort.html', 'w') as f:
    f.write(sort_html)

# 3. Modify index.html to remove the extracted bits and update the SORT TENDERS link
if batch_sort_panel_match:
    content = content.replace(batch_sort_panel_match.group(0), "")
if sort_results_match:
    content = content.replace(sort_results_match.group(0), "")
if js_match:
    content = content.replace(js_match.group(0), "</script>")

# Change the SORT TENDERS link from an ID that opens a drawer to a real link
link_old = '<a href="#" class="back-link" id="openSortBtn"'
link_new = '<a href="/sort" class="back-link"'
content = content.replace(link_old, link_new)

with open('static/index.html', 'w') as f:
    f.write(content)

import sys

with open("static/index.html", "r") as f:
    content = f.read()

# 1. Add SORT TENDERS to nav-right
nav_right_old = """<div class="nav-right">
            <!-- Balancer spacing -->
        </div>"""
nav_right_new = """<div class="nav-right" style="display:flex; justify-content:flex-end;">
            <a href="#" class="back-link" id="openSortBtn" style="flex-direction:row-reverse;">
                <span>SORT TENDERS</span>
                <svg class="back-arrow" width="16" height="12" viewBox="0 0 16 12" fill="none" xmlns="http://www.w3.org/2000/svg" style="transform: rotate(180deg); margin-right:0; margin-left:8px;">
                    <path d="M6 1L1 6M1 6L6 11M1 6H15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </a>
        </div>"""
content = content.replace(nav_right_old, nav_right_new)

# 2. Add bulkSortPanel drawer after bidPanel
bid_panel_end_idx = content.find("</section>", content.find("id=\"bidPanel\"")) + len("</section>")
bulk_sort_html = """

        <!-- Batch Sort Drawer (Glassmorphic Slide-over) -->
        <section class="prediction-panel" id="bulkSortPanel">
            <div class="panel-container">
                <div class="panel-header">
                    <span class="category-label">BATCH TENDER ANALYSIS</span>
                    <h2 class="section-title">SORT TENDERS BY PROBABILITY</h2>
                    <button class="close-btn" id="closeSortBtn">CLOSE</button>
                </div>
                
                <p class="column-desc" style="margin-bottom: 24px;">
                    Upload multiple tender documents at once. Each will be automatically matched against your archive and ranked by estimated win probability.
                </p>
                
                <form id="bulkSortForm" class="predict-form" enctype="multipart/form-data">
                    <div class="form-group" style="position: relative;">
                        <label for="batch_tender_files">Upload Tender Documents (PDF/DOCX)</label>
                        <div style="display: flex; gap: 12px; align-items: center;">
                            <input type="file" id="batch_tender_files" name="files" accept=".pdf,.docx" multiple style="flex: 1;" required>
                            <button type="button" id="clear_batch_files" style="background: transparent; border: none; color: var(--accent-gold); font-size: 16px; font-weight: bold; cursor: pointer; display: none; padding: 4px 8px;">&times; CLEAR</button>
                        </div>
                        <div id="batch_files_list" style="margin-top:8px; font-size:12px; color:#999;"></div>
                    </div>
                    
                    <div class="form-group">
                        <label for="batch_supplier_name">Assess as (Supplier Name)</label>
                        <select id="batch_supplier_select" style="width:100%; margin-bottom:8px; background:rgba(0,0,0,0.5); color:#fff; border:1px solid #333; padding:8px; display:none;">
                            <option value="">-- Manual Entry --</option>
                        </select>
                        <input type="text" id="batch_supplier_name" name="supplier_name" placeholder="Enter supplier name">
                        <small style="color:#666; font-size: 11px; margin-top: 4px;">This is the single supplier profile the batch will be evaluated against.</small>
                    </div>

                    <button type="submit" class="submit-btn corner-btn" id="runBatchBtn">
                        <span>RUN BATCH ANALYSIS</span>
                        <span class="bracket bracket-tr"></span>
                        <span class="bracket bracket-bl"></span>
                    </button>
                </form>
                
                <div id="batchProgressContainer" style="margin-top: 24px; display: none;">
                    <div style="font-size: 13px; color: var(--accent-gold); margin-bottom: 8px;" id="batchProgressText">Processing 0 of 0 tenders...</div>
                    <div style="width: 100%; height: 2px; background: rgba(255,255,255,0.1); position: relative;">
                        <div id="batchProgressBar" style="height: 100%; width: 0%; background: var(--accent-gold); transition: width 0.3s ease;"></div>
                    </div>
                </div>
            </div>
        </section>
"""
content = content[:bid_panel_end_idx] + bulk_sort_html + content[bid_panel_end_idx:]

# 3. Add sort-results-section
grid_section_idx = content.find("<!-- Floating Card Grid Section -->")
sort_results_html = """
        <!-- Sort Results Section -->
        <section class="results-section" id="sortResultsSection" style="display: none; margin-bottom: 40px;">
            <div style="display:flex; justify-content: space-between; align-items:flex-end; margin-bottom:24px;">
                <div>
                    <span class="category-label">BATCH RESULTS</span>
                    <h2 class="column-title" style="margin-bottom:0;">TENDERS RANKED BY PROBABILITY.</h2>
                </div>
                <div>
                    <button class="corner-btn" id="exportCsvBtn" style="padding: 12px 24px; font-size: 11px; display:none;">
                        <span>EXPORT RANKING</span>
                        <span class="bracket bracket-tr"></span>
                        <span class="bracket bracket-bl"></span>
                    </button>
                </div>
            </div>
            
            <p class="category-label" id="batchSummaryText" style="color:#999; margin-bottom:16px; display:none;">0 tenders analyzed</p>
            
            <div class="profile-card" id="emptyBatchState" style="border-style: dashed; display: flex; align-items: center; justify-content: center; min-height: 120px;">
                <div style="text-align: center;">
                    <span class="card-tag">NO BATCH RESULTS YET</span>
                    <h3 class="card-title" style="font-size:14px;">UPLOAD TENDER DOCUMENTS TO BEGIN</h3>
                </div>
            </div>

            <div class="specs-table-wrapper" style="width:100%; display:none;" id="sortResultsTableWrapper">
                <table class="specs-table" id="sortResultsTable" style="width:100%; text-align:left;">
                    <thead>
                        <tr>
                            <th class="spec-label" style="cursor:pointer; width:60px;" onclick="sortBatchTable('rank')">Rank ▾</th>
                            <th class="spec-label" style="cursor:pointer;" onclick="sortBatchTable('filename')">Tender / Filename</th>
                            <th class="spec-label" style="cursor:pointer;" onclick="sortBatchTable('recommendation')">Recommendation</th>
                            <th class="spec-label" style="cursor:pointer;" onclick="sortBatchTable('win_prob')">Win Probability ▾</th>
                            <th class="spec-label">SA-Adjusted</th>
                            <th class="spec-label">Position</th>
                            <th class="spec-label">Framework</th>
                        </tr>
                    </thead>
                    <tbody id="sortResultsBody">
                    </tbody>
                </table>
            </div>
        </section>

"""
content = content[:grid_section_idx] + sort_results_html + content[grid_section_idx:]

with open("static/index.html", "w") as f:
    f.write(content)

import sys

with open('static/sort.html', 'r') as f:
    content = f.read()

# Update the table headers
old_header = """<th class="spec-label">Predictive Outcome</th>
<th class="spec-label">Actions</th>"""
new_header = """<th class="spec-label">Predictive Outcome</th>
<th class="spec-label">Completeness</th>
<th class="spec-label">Actions</th>"""
content = content.replace(old_header, new_header)

# Update the table row injection
old_row = """            <td>
                <div class="result-number ${saAdj >= 50 ? 'success' : (saAdj >= 20 ? 'warning' : 'danger')}">
                    ${r.competitive_position || 'Weak'}
                </div>
            </td>
            <td>
                ${!r.processing_error ? `<button class="corner-btn mark-outcome-row-btn" data-id="${r.prediction_id}" style="padding:4px 8px; font-size:10px;">"""
new_row = """            <td>
                <div class="result-number ${saAdj >= 50 ? 'success' : (saAdj >= 20 ? 'warning' : 'danger')}">
                    ${r.competitive_position || 'Weak'}
                </div>
            </td>
            <td>
                <div class="result-number" style="font-size: 14px;">
                    ${r.extraction_completeness ? (r.extraction_completeness * 100).toFixed(0) + '%' : 'N/A'}
                </div>
            </td>
            <td>
                ${!r.processing_error ? `<button class="corner-btn mark-outcome-row-btn" data-id="${r.prediction_id}" style="padding:4px 8px; font-size:10px;">"""
content = content.replace(old_row, new_row)

# Update CSV export logic
old_csv_head = """const headers = ["Filename", "Tender ID", "Win Prob (%)", "SA Prob (%)", "Framework", "Position", "Disqualified", "Error", "Actual Outcome"];"""
new_csv_head = """const headers = ["Filename", "Tender ID", "Win Prob (%)", "SA Prob (%)", "Framework", "Position", "Completeness (%)", "Disqualified", "Error", "Actual Outcome"];"""
content = content.replace(old_csv_head, new_csv_head)

old_csv_row = """    r.sa_adjusted_probability ? (r.sa_adjusted_probability * 100).toFixed(1) : "",
    r.preferential_framework || "",
    r.competitive_position || "",
    r.disqualified ? "YES" : "NO",
    r.processing_error || "",
    r.actual_outcome || "Pending"
]"""
new_csv_row = """    r.sa_adjusted_probability ? (r.sa_adjusted_probability * 100).toFixed(1) : "",
    r.preferential_framework || "",
    r.competitive_position || "",
    r.extraction_completeness ? (r.extraction_completeness * 100).toFixed(0) : "",
    r.disqualified ? "YES" : "NO",
    r.processing_error || "",
    r.actual_outcome || "Pending"
]"""
content = content.replace(old_csv_row, new_csv_row)

with open('static/sort.html', 'w') as f:
    f.write(content)

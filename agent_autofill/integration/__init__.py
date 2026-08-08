"""
Integration layer for Agent Autofill.

Three concerns live here, and nothing else:

  * `tender_assessment` — runs the EXISTING eligibility gate and the EXISTING
    single-prediction pipeline against the same document the autofill ran on,
    so the user gets "should you bid" and "here is your draft" together.
  * `review_gate`      — the export gate. A filled document cannot be exported
    as "reviewed" while any flagged field is unacknowledged.
  * `export_metadata`  — writes the review state INTO the exported file, so a
    half-reviewed draft forwarded by email is still recognisable as one.

Nothing here fills anything. `fill_engine/` owns that and is not touched.
"""

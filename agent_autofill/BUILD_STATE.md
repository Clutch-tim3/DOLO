# Agent Autofill — build state

Last updated 4 August 2026. Commits `272eb15`, `15a85d1`. **202 tests pass.**

Read this before continuing. It records what is done, what is not, and the
things that were discovered the hard way and would otherwise be rediscovered.

## The principle

**Drafts only.** Nothing this system produces is submission-ready without a
human confirming every auto-filled field. No signature is ever applied. No
price is ever written — pricing routes through the existing quotation gate.
If a change would relax this, stop and raise it rather than loosening it.

## Done

| Component | Where | State |
|---|---|---|
| Extraction | `extraction/` | AcroForm, pdfplumber coordinate blanks, DOCX tables, **PDF tables**, **legacy .doc reader**, rapidfuzz aliases |
| Profile | `agent/memory/` | Additive migration; writes require `confirmed=True` and return a diff; signature images refused even when confirmed |
| Fill engine | `fill_engine/` | safe/never split, document_filler (copy-on-write, gold shading, `[ ! ]` markers), review_summary HTML |

## Not done

1. **Subagent 5** — classification gate (Haiku, ≥0.7) and tier limits.
   Tier config lives in `agent/subscription.py`, *not* a `tier_config.py`.
   The quota check must run **before any Claude call**, not after.
2. **Subagent 6** — trigger the existing eligibility/prediction pipeline on the
   same document; export gate that blocks "reviewed" while any flag is open and
   records review state in export metadata.
3. **Subagent 7** — adversarial verification, including two-company isolation
   and an independent attempt at a *different* signature-label trick.
4. **Subagent 1** — Drive/Dropbox providers. Code only; **cannot be verified
   here** (OAuth consent needs credentials). Mark UNVERIFIED.
5. `static/company_profile.html` has **no route** — it 404s until someone adds
   `@app.get("/company-profile")` and mounts `agent_autofill/questionnaire_api.py`.
   The wizard has never been rendered in a browser.

## Corrections to the original spec — verified, do not revert

- **`drive.readonly` cannot be folder-scoped.** It grants "View and download
  all your Drive files". Use **`drive.file` + Google Picker** for the
  minimal-consent model. Checked against Google's docs.
- **Drive channel expiry is 86,400s (1 day)**, default 1 hour — not ~7 days. A
  *daily* renewal cron leaves gaps where webhooks silently stop. Renew every
  6–12 hours with overlap.
- **The Alfred Duma document is not a form pack.** All three copies are the
  same 1-page, 81-word tender *summary*. It has no MBD forms. Use it for the
  **eligibility / DISQUALIFIED** proof only. For extraction and fill, use
  `data/archive/temp_tender_BID_DOCUMENT_06FY27_.pdf` (145pp) and
  `tests/fixtures/sa_forms/`.
- **The current Treasury form is titled "BIDDER'S DISCLOSURE"**, not
  "Declaration of Interest" — that is the older MBD 4 wording. Keying on the old
  title misses every revised form.

## Traps found against real forms

- **Per-label matching is not enough.** SBD 4's declaration table is a grid of
  innocuous cells (`Full Name`, `Identity Number`, `Name of organ of state`).
  They survived only because no alias maps "Full Name" — adding that obvious
  alias would have auto-populated a state-employee declaration. Hence
  `classify_document_context()`; declaration context blocks the whole form.
- **The same label appears in the buyer's block and the bidder's block.** MBD 1
  has `CONTACT PERSON` / `TELEPHONE NUMBER` / `E-MAIL ADDRESS` under both
  "ENQUIRIES MAY BE DIRECTED TO" (municipality staff) and "SUPPLIER
  INFORMATION" (us). Section headings are tracked and carried into the fill
  decision; the buyer's block is refused.
- **Two table shapes**, both required: `label | blank` rows (MBD 1) and
  column-headed entry grids (SBD 4). Missing the second meant 43 cells were
  neither filled nor marked.
- **Format by magic bytes, never by extension.** A `.doc` renamed to `.docx` is
  still OLE2. Users do this. Legacy files are **read-only** — there is no
  pure-Python writer for binary `.doc`.
- Over-blocking is real: `CAPACITY UNDER WHICH THIS BID IS SIGNED` (contains
  "SIGNED") and `TOTAL NUMBER OF ITEMS OFFERED` (greedy `\btotal\b`) were both
  wrongly blocked. Narrow exemptions exist; they only clear SIGNATURE and
  PRICING, never DECLARATION.

## Fixtures

- `tests/fixtures/sa_forms/` — 22 real SBD/MBD forms. 15 are true `.docx`;
  7 are OLE2 despite `.docx` names (read via `antiword`, which is installed).
- `tests/fixtures/sa_forms_generated/mbd1_supplier_info.docx` — built from the
  real MBD 1 labels, because MBD 1 itself cannot be written to.
- Benchmarks: MBD 1 supplier block fills **14/18** with signature, date, price
  and method statement blocked. SBD 4 fills **0/43**, every cell marked.

## Weaknesses the authors flagged about their own work

- `confirmed=True` is a speed bump, not authorisation — nothing records *who*
  confirmed or what they saw. An audit column holding the confirmed diff is the
  real fix.
- `bbbee_level` is declared `INTEGER` but holds `'Level 1 Contributor'`.
- Directors get a Luhn check only; no CIPC cross-check, which is what actually
  invalidates an SBD 4.

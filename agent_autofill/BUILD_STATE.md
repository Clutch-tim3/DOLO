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

## The Autofill Vault (packs) — 13 August 2026

The manual-upload stopgap, shipped ahead of the cloud-monitoring version that
is blocked on Drive's scope model (HANDOFF.md §6). A **pack** is a group of
uploaded documents reviewed and exported as one thing.

`integration/pack_store.py` + `pack_api.py`, mounted in `app.py`. Eleven routes
under `/api/autofill-packs`, all `require_principal`, all listed in
`tests/test_auth.py`'s PROTECTED table. 46 tests in
`tests/test_autofill_packs.py`.

**It is a wrapper, not a second pipeline.** Every per-file decision belongs to
`run_autofill_batch`; every review decision belongs to `review_gate`. The pack
adds three things and nothing else: a file group, an aggregation, and a
pack-level status *derived* from the per-document reviews on every read. There
is no request that sets a pack to `reviewed` — a test asserts that.

Things worth knowing before changing it:

- **Status is a column, not a dict.** `BATCH_JOBS` in app.py is process-local
  and a Cloud Functions restart loses it. A pack whose spinner never clears is
  worse than one that failed, so the worker writes its terminal status in a
  `finally` and a pack is never left in `processing`.
- **The tier check at submit is not a second gate**, it is
  `subscription.check_autofill_quota` — the same free COUNT `run_autofill` runs
  as its own first step, called early so Starter gets a 403 from submit rather
  than a 200 followed by an error. Proven with a call counter: the whole Starter
  flow makes **0** Anthropic requests, including when the endpoint gate is
  bypassed and the worker is driven directly.
- **`flag_kind()` maps a stored reason back through `BLOCK_MESSAGES`** rather
  than re-examining the label. A second classifier would drift from the first
  the moment either changed, and the one that decided is the one that should be
  reported. Two block messages are built at the block site (an unreadable label,
  a counterparty section) and fall through to a plain `blocked`.
- **A pack of several documents exports as a zip**, because the endpoint
  promises one `download_url` and returning the first of six would silently lose
  five. The zip is not itself an export `/api/generated` can verify, so every
  member is checked with `verify_export` before it goes in.
- **Known cosmetic gap:** a document that filled nothing (SBD 4 fills 0 of 43)
  reports `values_confirmed=True` before anyone confirms anything — that is
  `_values_unconfirmed` correctly returning empty for a review with no values.
  The gate is unaffected; the pack's `values.confirmed` counter can look
  partially satisfied on a declaration-only pack.

Benchmarks on a real 3-document pack (MBD 1 supplier block + REVISED SBD 4
Annexure A + alfred_duma.pdf): 49 flags aggregated — 43
`declaration_of_interest`, 1 `signature`, 1 `signing_date`, 1 `pricing`,
1 `narrative`, 2 `no_data` — 12 values written, all from MBD 1. `SIGNATURE OF
BIDDER` is flagged and does not appear among the written values. The PDF is
`analysis_only` (no PDF writer) and is what the eligibility gate runs on.

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

## The two export-gate bypasses — fixed 8 August 2026

Both were demonstrated, recorded in `c5eb068`, and are now closed. The fix is
`integration/stamp_signing.py`: HMAC-SHA256 with `AUTOFILL_STAMP_SECRET`.

- **Forged database rows.** Signing the *stamp* alone would not have helped —
  the export path would read the forged rows, believe them, and sign the result
  itself. So each acknowledgement carries its own MAC over
  `(review_id, item_key, acknowledged_at, note)`. `export_reviewed` re-verifies
  every one before the status UPDATE. Before: 11 forged rows produced a genuine
  REVIEWED file. After: refused, `tamper_detected: True`, review stays DRAFT.
- **Fabricated `stamp_docx()` call.** A REVIEWED stamp now requires a signature
  the caller cannot compute. Before: `flags_open=0` produced a REVIEWED file
  while the DB said DRAFT with 11 open. After: `ReviewStateError`.

**A THIRD bypass, found by attacking that fix — also closed.** The stamp MAC
tied the stamp to the *record*, and nothing tied it to the document's
*content*. It structurally could not: the MAC is written into the file, so it
can only cover the digest of the draft as it stood beforehand, which is
unrecoverable once stamping has rewritten the file. Consequences, both proven:
the body of a genuine export could be rewritten and still verify, and the whole
stamp could be lifted onto an unrelated document and still verify. Fixed by
hashing the file *after* stamping and signing that digest separately
(`export_payload`, `final_sha256`, `export_mac`). Editing the file breaks the
digest; editing the stored digest to match breaks its MAC.

**Residual, pinned in `tests/test_agent_autofill_stamp_binding.py`:**
`stamp_docx()` checks a MAC is *present*, not valid — it has the file, not the
record. `mac="x"` still yields a document whose banner reads REVIEWED. It fails
`verify_export(path, company_id, review_id)`, which is the authority.

Editing an acknowledgement after a genuine export invalidates that export too.
That is intended: the file and the record must keep agreeing.

## Check 8 — the question, answered: it WAS possible. Now closed

"Could this system ever mark a document submission-ready without a human
confirming every auto-filled field?" Yes, and it took no attack at all.

The gate required acknowledgement only of the fields it could **not** fill. The
values it **did** write were never confirmed by anyone. On a form where
everything filled cleanly — no signature, price, declaration or narrative — a
REVIEWED export needed zero human involvement, and the stamp read "All 0
flagged field(s) were acknowledged by a person". On MBD 1 a user acknowledged 4
flags and received a document asserting review of all 18, including 14 values
they had never seen. **The gate reviewed the gaps, not the fills.**

Closed with a bulk confirmation: `filled_values()` lists every value written,
`confirm_filled_values()` records the exact set the person was shown and MACs
it. `export_reviewed` refuses while any filled value is unconfirmed. A partial
set is refused rather than intersected. Editing a value after confirmation
invalidates it. Both steps are exposed as tools — `autofill_show_filled_values`
and `autofill_confirm_filled_values` — because a gate nothing can reach is a
dead end, which is how the quotation gate first shipped.

Refusal order is deliberate: forged acknowledgements (a security signal) before
open flags before unconfirmed values.

**`verify_export` still has no caller outside tests.** It is documented as the
authority on whether an export is genuine, and nothing in the app, the tool
registry or the UI calls it. Until something does, the residual below is live
rather than theoretical.

## Weaknesses the authors flagged about their own work

- `confirmed=True` is a speed bump, not authorisation — nothing records *who*
  confirmed or what they saw. An audit column holding the confirmed diff is the
  real fix.
- `bbbee_level` is declared `INTEGER` but holds `'Level 1 Contributor'`.
- Directors get a Luhn check only; no CIPC cross-check, which is what actually
  invalidates an SBD 4.

> Continuing this work after a context reset? See
> `agent_autofill/NEXT_PROMPTS.md` for ready-to-paste prompts covering the two
> open export-gate bypasses, the adversarial verification pass, deployment
> blockers, and known gaps.

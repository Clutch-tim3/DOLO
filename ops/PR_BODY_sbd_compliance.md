Three commits: things reported against real packs, `SBD_COMPLIANCE.md`, a
production 503, and `Comprehensive_Tender_Document_Training_Guide.pdf`.

**1503 tests pass.** All three deployed and verified live: health all-true,
`claude-sonnet-5` resolving against the real API, memory at 2Gi, and
`/api/autofill/providers/status` returning 401 instead of the 500 it had been
returning on every request.

| commit | |
|---|---|
| `cf7b13f` | Ask when unsure, catch what disqualifies a bid, stop drawing on the form |
| `157cdba` | Stop holding every page of a pack in memory |
| `6f4d6bc` | "N/A" beats a blank, and the pack's own forms beat the current template |

## The red marks came off the form

> "i dont like the exclamation marks that it puts on fields it cant answer it
> ends up staying on the hard copy so no red exclamation marks remove that
> feature."

The SBD 4 page shows why: nine `[ ! ]` stamped down the Identity Number column
of a table that is **correctly empty** — no director is employed by the state,
so there is nothing to declare. Printed and handed to an organ of state, that
is a statutory form that looks defaced.

Removed from the PDF and .docx paths. Every refusal is still recorded, still
listed in the review, still blocks an export where it did. Verified on the
145-page pack: **269 refusals recorded, 0 marks on the page.**

## Being unsure is now a reason to ask

> "the address line was not filled i keep telling you the agent needs to ask me
> if its not certain, postal address or physical address"

Two things were blocking that question:

- `missing_profile_fields` only built questions from `"Nothing on file"`
  refusals. A bare `ADDRESS` is not that — both values are held; only the
  choice is missing.
- `_outstanding_rows` filters `advisory = 0`, and `ambiguous_label` is
  advisory, so the row never reached the builder anyway.

One flag was answering two questions: *must a person tick this before export?*
(no) and *is this worth asking about?* (yes). Now separated.

Questions carry a `kind` — `supply` (no value held) or `which_one` (value held,
question unknown). New tool `autofill_resolve_label` records the answer;
`update_company_profile` was wrong for it because nothing is missing from the
profile. It goes to `learned_labels`, so the question is asked **once ever**.

## Four reasons fields were left blank

On MBD 3.1, four blanks with every value already on file. Three now fill:

| blank | why it was empty |
|---|---|
| `Name of Bidder............` | the word fused to its dot leader, so the label arrived as `"Name of"` |
| `in my capacity as ______` | the SBD signature block runs its question *through* the blank; lower-case start was discarded as prose |
| `______(company name)` | the underscores are 0.72pt rectangles, not characters — nothing saw them |
| `I (full name) ______` | still blank — a personal name |

The dictionary now outranks the capitalisation heuristic, but on an **exact**
alias only. A test caught the first version letting `"representative of
(tenderer)"` through — the fuzzy matcher scores it 95 against `contact_person`
on the strength of one word.

## Claude is asked what a label means

One batched call per pack, on Sonnet 5, quoted as untrusted content because
tender labels are third-party text. It returns a **meaning, never a value**, and
any field it names goes through `is_blocked` and `SAFE_FILL_FIELDS` unchanged —
the module imports neither, and a test parses its AST to prove it. Answers are
cached, so a label costs one call in its lifetime.

On the 145-page pack: **129 "I could not tell what this field is asking for"
became 2.**

## SBD_COMPLIANCE.md

**P0-2** — The preference system is **read off the form**, not derived from
tender value against the R50m threshold. That threshold is what an organ of
state *should* pick, not what this tender *says*, and a Level 1 bidder claims
20 points under 80/20 and 10 under 90/10. The Johannesburg Water pack states
both (the buyer never deleted one), so it asks. Every pack also carries SBD
6.1's boilerplate explaining both systems, so keyword matching would have been
confidently wrong on all three.

The goals table could not be filled "from the four `owned_51pc_*` columns" as
the brief asks, because **every tender writes its own goals** — this pack wants
Historically Disadvantaged Individuals, Local Production, Locality. A row is
claimed only where a stored flag is strictly **narrower** than the goal, making
the implication airtight: 51% black-women-owned *is* 51% women-owned.

`owned_51pc_black` → HDI is **refused**. HDI is a defined term that black
ownership overlaps without being, and claiming three points on that reasoning
is a misrepresentation to an organ of state. A `False` flag asks rather than
declining — 51% white-women-owned is still women-owned.

**P0-3** — Closing dates extracted from all three real packs. With no closing
date found, nothing is compared: falling back to today would silently clear a
certificate that expired before the tender closed.

**P0-1** — The same fact written two ways across forms, a named
disqualification cause. Formatting differences are not conflicts, or the real
ones drown.

**P0-4** — A pre-export summary leading with what would disqualify the bid. On
this pack: 42 signature lines with their pages, in reading order. Counted from
the refusal **record** rather than `[ ! ]` marks, since those are gone — and the
record was always the real source. It reports and never refuses;
`export_reviewed` stays the only thing that blocks an export.

---

# `157cdba` · The production 503

> "im getting a server refused error frequently when i submit certain tender
> packs"

Every 503 in the Cloud Run log is `/api/autofill-packs/<id>/submit`, each paired
within seconds by `Memory limit of 1024 MiB exceeded with 1101 MiB used`. Cloud
Run killed the container mid-request, so the browser got a 503 with nothing to
explain it. The 19–28s latencies are Starlette background tasks: `process_pack`
runs before the ASGI call returns, so the whole fill happens inside the request.

pdfplumber caches every object it derives for a page and holds it for the life
of the PDF. Extraction never needs a page again once its blanks are collected.
On the real 145-page pack:

| | peak | retained |
|---|---|---|
| before | **573 MB** | 195 MB |
| after `flush_cache` | **64 MB** | — |

Same 353 blanks. **One of the two hogs was mine** — the goals-table scan added
in `cf7b13f` walked every page calling `find_tables()` and cost 198 MB. Memory
also raised 1024 → 2048 MiB, as headroom on top of the fix, not instead of it.

**Second bug found in the same logs:** `/api/autofill/providers/status` had been
returning **500 on every request** — `type "blob" does not exist`. Postgres has
no BLOB, and `provider_tokens.token_ciphertext BLOB NOT NULL` runs on every call
to that endpoint. `agent/db.py` translated `?`, `AUTOINCREMENT` and `DATETIME`
but not `BLOB`. Now `BYTEA`.

Neither bug was catchable by the suite as it stood: every extraction fixture is
one or two pages where the cache costs nothing, and the whole suite runs on
SQLite, which accepts `BLOB` happily.

---

# `6f4d6bc` · The training guide

## Golden rule 1 — complete every field, use "N/A"

CairoAI left them blank and `is_sentinel` read `"N/A"` as *absent*. The profile
cannot tell these apart, since both are an empty column:

| | correct answer |
|---|---|
| "we are not VAT registered" | **N/A** |
| "nobody has told CairoAI the VAT number" | **N/A would be a false statement** |

So nothing is inferred from absence. New tool `autofill_mark_not_applicable`,
called only after the user answers.

**A test stopped this widening the blocklist.** `director_names_and_id_numbers`
was declarable — a sole proprietor has no directors. But `"Name of State
institution"` maps to it, and that cell is in SBD 4's *sworn* declaration.
`is_blocked` catches that label only with document context, which `pdf_filler`
does not pass. It would have written "N/A" down a sworn table. Removed.

## Golden rule 4 — never use old forms

"SBD 4" names two documents: the pre-2022 Declaration of Interest, and the
consolidated Bidder's Disclosure that absorbed SBD 4 + 8 + 9 on 31 March 2022.
The detection trap: Part A of the *consolidated* form is still headed
"Declaration of Interest", so those words prove nothing.

Run against three real packs it found a live problem — **the Johannesburg Water
RFQ carries MBD 4 alongside MBD 8 and MBD 9**, the two it replaced.

## Also

- **PO Box as physical address** — SBD 1's named mistake; CairoAI fills that
  field on every form, so it would repeat through the whole submission.
- **B-BBEE level off the 1–8 scale** — the profile has held 9.
- **COIDA expires 31 March annually**, affidavits 12 months from the oath —
  checked even without a recorded date. No unambiguous rule returns nothing.
- **EME <R10m + 51% black = automatic Level 1** — raised in a separate
  `opportunities` list, never applied, since a claimed level must match the
  attached document.

---

## Not done

**Bid validity period** — needs the period being offered, which nothing holds.

**UN (UNGM) and World Bank (SPD)** — a third of the training guide, and a
product decision rather than a bug fix. CairoAI is South African today. The
Part 4 rules common to all three systems are implemented.

**The smoke check has not run** — it needs a login. Health returning 200 does
not prove sign-in works.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

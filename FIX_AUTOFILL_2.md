# Make a filled tender look and behave like a person filled it

From the owner's second real run: a 28-page pack that produced a filled draft
and a flag list nobody could work through. Five problems, ordered by how much
they cost the person using it.

Repo: `C:\Users\Thabang\.gemini\antigravity\scratch\DOLO`
Live: https://cairoai.web.app · 1189 tests passing
Read section 6 of `LAUNCH_PLAN.md` before starting.
Reference documents: `C:\Users\Thabang\Desktop\Tender\SBD 6.1.pdf` (filled by
hand, scanned) and `SBD4.pdf`.

---

## P0-1 · The writing is far too small

The owner's words: *"awfully small to the point where it's not visible — no
small text, that's the worst thing possible."*

Measured against his own hand-filled SBD 6.1, page 3, the claimed-points table:
**handwriting sits at the same height as the form's printed body text.** That is
what a person does — they write to the size of the form in front of them. The
printed text there is roughly 10–11pt; the handwritten `4` matches it and is
slightly heavier in stroke.

CairoAI writes at `FONT_SIZE = 8.5` in Patrick Hand, and Patrick Hand renders
about **80% the width of Helvetica at the same point size** (`"CairoAI"` is
22.7pt against 28.3pt). So the effective size on the page is around 6.8pt
against a form printed at 10–11pt — noticeably smaller than everything around
it, which is exactly why it disappears.

**Do not just raise the constant.** Derive it from the document:

- Measure the printed text size near each blank — `pdfplumber` already exposes
  per-span sizes on a text-layer PDF, and the layout extractor is already
  reading that page.
- Fill at that size, adjusted for the face's smaller glyphs (roughly ÷0.8 for
  Patrick Hand).
- Apply a **hard floor of 10pt** regardless. Below that it is not legible on a
  printed-and-rescanned form, which is how these are actually submitted.
- On a scanned form with no text layer, fall back to the OCR word heights
  already available from `ocr.OcrWord.bbox` — the form's own printed words give
  the target size.

**Done when:** a filled value is visually the same size as the label beside it,
on both a vector form and a scan. Verify by rendering a filled page to PNG and
looking at it next to the printed text — not by checking the constant changed.

If a larger value no longer fits its cell, `_fits()` correctly refuses it. Watch
the refusal rate on a real pack: a jump means the cells are genuinely tight, and
the answer is a smaller floor for that field, never weakening the fit check.

---

## P0-2 · Ask for personal details instead of flagging them

The owner: *"anything personal like name or ID number should be asked by agent,
agent should ask for clarification wherever it isn't sure of things."*

Today a field with no data becomes a line in a flag list that the user has to
find, read and acknowledge one at a time. For a director's ID number — a value
the user has and the system simply does not — that is the wrong interaction. The
system knows precisely what it needs and should ask for it.

**Done when:** after processing a pack, the agent asks for the specific missing
values in conversation — *"SBD 4 needs the ID number for Thabang Molwantwa, and
whether he is in the service of the state"* — writes them through
`update_company_profile` with `confirmed=True`, and re-fills without a second
upload. Values asked for once are never asked for again.

Two rules that do not bend:

- `is_state_employee` is a **sworn declaration** on SBD 4. It is asked, never
  inferred, and never defaulted — the schema comment says so and it is right.
- Nothing is written to the profile without `confirmed=True` meaning what it
  says: the user saw that specific value and approved it.

---

## P0-3 · The flag list is unusable at this length

From the real run, verbatim, fifteen times over:

```
Total Amount excl. VAT   BLOCKED  F01 page 1
    Pricing must come from the quotation system…    [ACKNOWLEDGE]
VAT Amount               BLOCKED  F02 page 1
    Pricing must come from the quotation system…    [ACKNOWLEDGE]
Sub-Total                BLOCKED  F05 page 7
    Pricing must come from the quotation system…    [ACKNOWLEDGE]
GRAND TOTAL              BLOCKED  F09 page 8
    Pricing must come from the quotation system…    [ACKNOWLEDGE]
…
```

Every one is the same decision for the same reason, and each demands its own
note. The per-field acknowledgement exists so nobody rubber-stamps a page of
real decisions — that is worth keeping — but fifteen identical pricing refusals
are one decision, not fifteen, and presenting them as fifteen guarantees the
user stops reading.

**Done when:** flags are grouped by reason, with the count and the pages
("Pricing — 15 fields across pages 1, 7, 8, 9"), acknowledged once per group
where the reason is identical and structural. Where a refusal is genuinely
field-specific it stays individual. **A blanket acknowledge-everything button is
still refused** — grouping by identical reason is not the same thing.

---

## P0-4 · Pre-printed content is being read as fields

The `UNMATCHED` entries are not fields at all:

```
POINTS · 80 · 20 · 100 · SPECIFIC GOALS       page 25
"of this tender" · "the tenderer)"            page 28
"Business owned by 51% or more-Women"         page 28
"ownership and share certificate where applicable"
```

On SBD 6.1 those are the **points allocation table the organ of state completes
before issuing the tender**, plus fragments of the surrounding instructions.
`80` and `20` are the preference point split. They are printed content, and the
extractor is offering them as blanks to fill.

The `(unlabelled) BLOCKED — could not read what this field is for` entries are
the same failure from the other side: a detected blank with no label.

**Done when:** a cell whose text is pre-printed content is not proposed as a
field; instruction fragments ending mid-sentence (`"the tenderer)"`) are
rejected as labels; and an unlabelled blank in a region with no readable label
is dropped rather than surfaced. The count of flags on the owner's pack should
fall substantially, and every one that remains should be something a person
actually has to do.

---

## P1-5 · It should learn from every tender

The owner: *"sometimes it even doesn't understand the field so I need this to
learn through every tender."*

Every pack currently starts from zero. The same SBD 6.1 table will be
misread the same way next month, and a label the user has already explained
once is explained again.

**Done when:** a resolution the user makes — confirming that a label means a
particular field, or that a blank is not a field — is recorded against the
normalised label and reused on the next pack. Show the user when a decision came
from a previous tender rather than from the dictionary, so a wrong lesson can be
corrected rather than silently repeated.

Keep it conservative: a learned mapping raises confidence, it does not bypass
`never_fill_fields`. Nothing learned may ever cause a signature, a price or a
sworn declaration to be filled.

---

## Constraints

**Drafts only.** No signature auto-applied, no pricing auto-inserted, every
auto-filled value confirmed by a person before export. Grouping flags and asking
for details must make the review faster, never lighter.

**Pricing stays out.** The pricing refusals in P0-3 are correct and must remain
— group them, do not remove them. Figures come from the quotation system, which
keeps its own audit trail.

**Verify by looking.** Render filled pages to PNG and inspect them. Every defect
in this file was invisible in the counts: the fill reported success, the flags
reported completeness, and the document was unusable.

**Test against the owner's real pack**, not the synthetic scan.

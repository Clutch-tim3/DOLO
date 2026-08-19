# Fix what the first real test exposed

The owner ran a real tender pack through Agent Autofill. It filled the form,
and both things wrong with it are serious: the writing is too small to read,
and every value on the page belonged to a different company.

Repo: `C:\Users\Thabang\.gemini\antigravity\scratch\DOLO`
Live: https://cairoai.web.app · 1030+ tests passing
Read section 6 of `LAUNCH_PLAN.md` before starting.

---

## P0-1 · It filled the wrong company's details

This is the one to fix first. A bid form went out carrying another company's
name, registration number and addresses.

### What actually happened

The Compliance Vault and the autofill profile are two different tables, and
nothing connects them.

```
company_archive   (what the Vault writes when documents are uploaded)
    company_name        DONINGTON VALE
    registration_number Pending
    supplier_number     Pending
    bbbee_level         9
    files               ["COR14.3.pdf", "COR15.1A.pdf", "COR14.1A.pdf"]

company_profile   (what the fill engine actually reads)
    company_name        CairoAI (Pty) Ltd
    registration_number 2020/TEST01/07
```

The owner uploaded Donington Vale's real CIPC documents to the Vault. The fill
engine read `company_profile`, which held unrelated values, and wrote those
onto the form. From the outside it looked like the system invented details;
what it did was read the wrong table with complete confidence.

His expectation is the correct one: **"everything is in the compliance vault so
it should never fill incorrect details."**

Note honestly, in the commit message, that the specific wrong values came from
placeholder data written into `enterprise_corp` during earlier testing. That
made the failure louder, but it is not the bug. The bug is that two stores of
company identity exist and the fill engine reads the one the user never edits.

### Three things to fix, in order

**1. Make the Vault the source of company identity.** The user puts documents
in the Vault; that must be what fills their forms. Either the Vault writes
through to `company_profile` on upload, or `get_company_profile` resolves the
Vault first. One store wins and the other becomes a view of it — do not leave
two tables that can disagree.

**2. The Vault extracts almost nothing.** `registration_number` is the string
`"Pending"` while COR14.3 — a CIPC registration certificate that states the
registration number on its face — sits uploaded against that record. Same for
`supplier_number`. And `bbbee_level` is `9`, which is not a level; the scale is
1–8. Whatever parses those documents is either not running or not writing back.

**3. Never fill from a value that is not real.** `"Pending"` is a placeholder,
not a registration number, and it must never reach a form. The fill engine
already refuses fields it has no data for; sentinel strings like `Pending`,
`N/A`, `TBC`, `Unknown` and empty-after-trim must be treated as absent, not as
answers.

**Done when:** documents uploaded to the Vault populate the fields those
documents contain; a form filled for a company shows that company's details;
a sentinel value is refused and appears in the review list as "left for you";
and a test fails if a form is ever filled from a company_id other than the one
that owns the pack.

---

## P0-2 · The writing is too small to read

`FONT_SIZE = 8.5` in `agent_autofill/fill_engine/pdf_filler.py`, chosen when
values were drawn in Helvetica. They are drawn in Patrick Hand now, which is
substantially smaller at the same point size:

```
"CairoAI" at 8.5pt
    Patrick Hand   22.7pt wide
    Helvetica      28.3pt wide     -> Patrick Hand is 80% the size
    to match Helvetica 8.5 visually, Patrick Hand needs ~10.6pt
```

So switching the font shrank every value by a fifth and nobody re-measured. On
a printed or re-scanned form the result is close to illegible.

**Done when:** filled values are comfortably readable at 100% zoom and after a
print-and-scan round trip. Expect roughly 10.5–12pt for Patrick Hand. The fit
check in `_fits()` already measures the real face, so raising the size will
correctly start refusing values that no longer fit — that is the system
working, but check the refusal rate does not jump on a real form. If it does,
the cells are tighter than the type and the size needs to come back down rather
than the check being weakened.

---

## P0-3 · It should look handwritten, not typeset

Right now every value sits on a perfect baseline at a uniform size in a uniform
ink. A handwriting face on a perfect grid reads as a font, not as writing.

Make a filled form look like a person completed it by hand and the page was
then scanned. Things that carry most of that effect, roughly in order:

- **Baseline jitter** — a few tenths of a point of vertical variation per
  field, and a slight horizontal offset from the cell's left inset. Nobody
  starts every entry at exactly the same x.
- **A degree of rotation** — a fraction of a degree per value, varying in sign.
  This is what breaks the typeset feel more than anything else.
- **Ink variation** — small variation in the blue-black already used, so every
  field is not the identical RGB.
- **Size variation** — a few percent between fields, as a pen and a hand
  produce.

Keep it subtle. The goal is a form that reads as hand-completed at a glance,
not a novelty effect: exaggerated rotation or wobble looks like a filter, and
this document goes to a procurement officer.

**Deterministic, not random.** Seed the jitter from the field key so the same
document always renders identically — a quotation or a draft that changes every
time it is regenerated cannot be checked, diffed, or trusted, and the export
MAC binds content that must not drift.

**The highlight stays.** The gold band behind each value is what tells a reader
which entries came from CairoAI rather than from a person, and it matters more
as the text becomes more convincingly handwritten, not less. This is the line
between "drafted for you to check" and "forged". Do not remove it, and do not
make it lighter to sell the effect.

---

## Constraints

**Drafts only.** No signature auto-applied, no pricing auto-inserted, every
auto-filled value confirmed by a person before export. Making the output look
more like handwriting must not make it easier to mistake for a signed document.

**Verify by looking.** Render the filled page to PNG and inspect it. Every
defect in this file was invisible in the counts — the wrong-company fill
reported a healthy number of fields filled, and the unreadable text reported
success. Counts have lied repeatedly on this project; rendered pages have not.

**Test with the owner's real pack**, not the synthetic scan. The synthetic one
is a rendered SBD 1 and does not exercise what a real upload does.

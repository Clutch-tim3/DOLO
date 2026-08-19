# Getting CairoAI usable end to end

Make it possible for one person to sign in, set up their company, autofill a
tender and generate a quotation — and have every number on those documents be
real. This is about the product *working*, not about it being sellable:
nothing here concerns billing, legal pages or backups.

Repo: `C:\Users\Thabang\.gemini\antigravity\scratch\DOLO`
Live: https://cairoai.web.app · test account `Test` / `Test12345678`
Baseline: 1030 tests passing.

Read section 6 of `LAUNCH_PLAN.md` first — the environment traps there will
otherwise cost a day.

---

## P0-1 · The quotation endpoint invents prices

**This is the one that matters. Do it first.**

`app.py` fabricates line-item prices in four places:

```
547   tender_val = parsed_tender.get("tender_value") or 798116.25
550   subtotal_est = float(tender_val) / 1.15
552   {"description": "Primary Supply & Delivery per … Specs",
       "qty": 1, "unit_price": round(subtotal_est * 0.75, 2)}
553   {"description": "Technical Support, Deployment & Quality Assurance",
       "qty": 1, "unit_price": round(subtotal_est * 0.25, 2)}
560   line_items = [{"description": "Professional Goods & Service Delivery",
                    "qty": 1, "unit_price": 798116.25}]
567   body.get("line_items", [{"description": "Services",
                              "qty": 1, "unit_price": 798116.25}])
```

Upload a tender with no price in it and CairoAI produces a quotation for
**R798 116,25** — a number that came from nowhere — now beautifully typeset by
the new renderer and ready to send to an organ of state. Split 75/25 into two
invented line items, it looks considered.

This is precisely the failure `agent/quotation/price_search.py` was rewritten
to remove. Read that module's docstring before touching this: it explains why
invented prices with real-looking provenance are the worst thing this system
can produce. The fix there was to return `MANUAL_REVIEW_REQUIRED` and let the
quotation show TBC. The endpoint never got the same treatment.

**Done when:** no price is ever synthesised. A tender with no extractable
pricing produces line items with `unit_price: None`, which `quote_document.py`
already renders as **TBC**, excludes from the total, and marks with
"This quotation is incomplete." A test asserts that a tender with no price
yields no number — and fails if any hardcoded figure returns.

---

## P0-2 · There is no way to set up a company from the app

`/api/company-profile` is **GET only**. There is no PUT, no PATCH, no form.
Every field the product depends on — company name, registration number, VAT
number, addresses, contact details, signatory — can only be written by running
Python against the database, which is how the test profile was populated.

This blocks both journeys. Autofill fills bid forms *from the profile*, and the
quotation renderer takes the letterhead, VAT status and signatory from it. An
empty profile means "filled 0, left 8 for you" and a quotation with no
letterhead.

There is one real path today: the agent chat can call `update_company_profile`,
which refuses to write unless `confirmed=True` — a deliberate gate, because
these values reach real bid forms. That path works and should stay.

**Done when:** a user can set up their company without leaving the app. Either
a profile form on `/company-profile` posting to a new authenticated write
route, or a guided agent flow that walks through the fields and confirms them.
The `confirmed=True` gate must survive whichever is chosen — it is what stops a
half-remembered VAT number reaching a submission.

---

## P0-3 · The agent cannot set six of the fields it needs

`agent/memory/tools_schema.py` advertises 17 of the 23 writable profile fields.
Missing:

```
standard_cell                  ← MBD 1 asks for it on its own row
standard_fax                   ← same
tax_compliance_pin             ← asked for by name on MBD 1
authorized_signatory_capacity  ← printed under the signature line
brand_colour                   ← drives the whole quotation palette
tagline                        ← the line under the wordmark
```

The model only knows what the schema tells it, so asking the agent to set a
cell number or a tax compliance PIN gets a refusal or silence — for fields the
store accepts and the SBD 1 filler needs. The first three are why a filled form
still shows blanks.

**Done when:** the description lists every entry in
`PROFILE_WRITABLE_FIELDS`, and a test fails if the two lists drift apart. Keep
the existing warnings about `directors` and `authorized_signatory_name` — those
are load-bearing.

---

## P1-4 · A logo cannot be uploaded

`logo_file_path` exists on the profile, `quote_document.py` draws it, and
nothing anywhere sets it. No upload route, no UI. The only way to get a logo
onto a quotation is to write a filesystem path into the database by hand — and
on Cloud Run that path is per-instance and vanishes.

**Done when:** a user can upload a logo, it is stored durably (Cloud Storage,
via `agent/object_store.py`, not `/tmp`), and it appears on the next generated
quotation. Validate that the upload is actually an image and cap the size.

---

## P1-5 · The Autofill Packs screen has no stylesheet

Full brief in `UI_PROMPT.md`. Summary: the JS emits 57 `ap-*` class names and
there is not one CSS rule for any of them, so the page renders on browser
defaults — that is the "white blocks", the wall of undifferentiated text, and
the broken-looking PREVIEW DRAFT link.

It does not block a test. It will shape the impression the test leaves.

---

## The walkthrough this has to support

Test it in this order, as a person would, signed in at cairoai.web.app:

1. Sign in as `Test`
2. **Set up the company** — name, registration number, VAT number (or none),
   addresses, phone, cell, email, signatory name and capacity, tax compliance
   PIN, brand colour, tagline, logo
3. **Upload a tender pack** to Tender Autofill Packs, submit it, watch the
   agent narrate
4. **Review** — flagged fields acknowledged individually, filled values ticked
5. **Preview the draft** and confirm the values landed in the right boxes
6. **Export** the reviewed draft
7. **Generate a quotation** and check every figure on it traces to something
   real

Steps 2 and 7 are the ones that do not work today.

---

## Constraints

**Drafts only.** No signature auto-applied, no pricing auto-inserted, every
auto-filled value confirmed by a person before export. Making setup easier must
not make confirmation weaker.

**Never invent a number.** P0-1 exists because that rule was broken once
already. TBC is always better than a plausible figure.

**Verify by using it.** Sign in at cairoai.web.app — not the Cloud Run URL,
which bypasses the Hosting layer where the cookie bug hid — click through the
journey, and look at the documents that come out. Counts have lied on this
project repeatedly; rendered pages have not.

**Mirror `static/` and `firebase_public/`**, and bump the `?v=` pin on any JS
you touch.

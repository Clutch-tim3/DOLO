# Read the documents in the Vault, then ask only what is left

Two failures with one cause, both found by the owner on real packs.

Repo: `C:\Users\Thabang\.gemini\antigravity\scratch\DOLO`
Live: https://cairoai.web.app
Read section 6 of `LAUNCH_PLAN.md` before starting.

---

## P0-1 · The Vault stores documents without reading them

The owner uploaded his B-BBEE certificate, his CSD registration report and his
SARS Tax Compliance PIN letter. CairoAI filed all three and extracted almost
nothing from any of them.

What the B-BBEE certificate says, verbatim:

```
100% BLACK OWNERSHIP
0% BLACK FEMALE OWNERSHIP
Black people who are youth as defined in the National Youth Commission Act
    of 1996: 100%
Black people who are persons with disabilities ...: 0%
B-BBEE LEVEL 1 CONTRIBUTOR: 135% PROCUREMENT RECOGNITION
Date of Issue 23-March-2026    Expiry Date 22-March-2027
Enterprise Number K2026250499
```

Every answer SBD 6.1 asks for in its specific-goals table is on that page. The
company profile had `bbbee_level` set to **9** — not a valid level, the scale is
1 to 8 — while the certificate proving Level 1 sat in the Vault unread.

Same story elsewhere. `company_archive.registration_number` was the literal
string `"Pending"` with a CIPC COR14.3 uploaded against the record, and that
document states the registration number on its face.

His words, and they are the correct expectation:

> "everything is in the compliance vault so it should never fill incorrect
> details"
> "in terms of B-BBEE level i submitted the Certificate all should be there"

**Done when:** uploading a document to the Vault extracts the facts it states
and writes them to the profile, so the next pack fills from them. At minimum:

| document | facts it carries |
|---|---|
| B-BBEE certificate / EME affidavit | level, black ownership %, black female %, youth %, disability %, expiry, enterprise number |
| CIPC registration (COR14.3 / CoR14.3) | registered name, registration number, registration date, type |
| CSD registration report | CSD supplier number (MAAA…), supplier type, status, tax status |
| SARS Tax Compliance PIN letter | tax reference number, TCS PIN, issue date, expiry |

Three rules that do not bend:

- **Every extracted value goes through `update_company_profile` with
  `confirmed=True` meaning what it says** — the user was shown that specific
  value and approved it. Show what was read and from which document, and let
  them correct it before it is stored. A number read wrongly off a certificate
  and written silently is worse than an empty field.
- **A document that cannot be parsed is stored anyway**, and says so. Filing is
  useful even when reading fails.
- **Expiry dates are extracted wherever present.** A B-BBEE certificate expiring
  22 March 2027 and a TCS PIN both go stale, and a bid submitted on an expired
  certificate is rejected outright.

---

## P0-2 · Fill everything first, then ask for what is genuinely left

The owner: *"the system should auto fill them then agent asks for questions in
the form of a bulleted Q and A after."*

Today the review screen presents everything at once — filled values, refusals,
and unknowns interleaved, each needing its own acknowledgement. The order he
wants is the order that respects his time: **do the work, then ask about the
gap.**

**Done when:** after a pack finishes, the agent

1. reports what it filled,
2. lists what only he can answer, as a short bulleted list of plain questions,
3. writes the answers to the profile through the confirmed path,
4. re-fills without needing another upload.

Questions are deduplicated by profile column, not by field occurrence.
`missing_fields.py` already does this — on his pack, 24 outstanding fields
mapped to 12 distinct columns, with "Designation" appearing 7 times and
"Capacity" 6, both being `authorized_signatory_capacity`. Asking 13 times for
one fact is the same failure as flagging it 13 times.

Ask only for what is genuinely unknown. A question about something already in
the Vault is the failure in P0-1 wearing a different hat.

---

## What is already done

- **Ambiguous labels resolve when they are only ambiguous in theory.** A bare
  "ADDRESS" is refused in principle because postal and physical are different
  answers — but when a company records the same string for both, there is
  nothing to disambiguate and it now fills.
  See `agent_autofill/fill_engine/ambiguity_resolver.py`.
- **Four preference-goal columns exist** on `company_profile`:
  `owned_51pc_black`, `owned_51pc_black_women`, `owned_51pc_black_youth`,
  `owned_51pc_black_disability` — populated for Donington Vale from the
  certificate, by hand, which is exactly what P0-1 should have done.
- **Refusals are categorised honestly** — a sworn declaration, a price cell and
  a genuine miss no longer share one sentence.

---

## Constraints

**Drafts only.** No signature auto-applied, no pricing auto-inserted, every
auto-filled value confirmed by a person before export. Reading documents makes
the draft better; it does not reduce what a human must approve.

**Never claim a preference point the company does not qualify for.** The goals
table decides scoring on a real bid. Every one of those four flags comes from a
document or from the user, never from inference.

**Verify by looking.** Render filled pages and inspect them. Every defect in
this file was invisible in the counts.

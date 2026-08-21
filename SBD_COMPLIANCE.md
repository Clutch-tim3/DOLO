# Stop the bid being thrown out before anyone reads it

Administrative mistakes disqualify more South African tender submissions than
weak pricing or poor technical proposals. Missing signatures, inconsistent
details across forms, and incorrect B-BBEE claims cause instant rejection —
the evaluation team never reaches the proposal.

CairoAI fills these forms. That puts it in a position to catch every one of
those failures before submission, and it currently catches none of them.

Repo: `C:\Users\Thabang\.gemini\antigravity\scratch\DOLO`
Live: https://cairoai.web.app
Read section 6 of `LAUNCH_PLAN.md` before starting.

---

## Background the code does not currently know

Standard Bidding Documents are issued by National Treasury and are mandatory
with every tender. Procurement checks them for compliance **before** evaluating
anything else.

| form | what it is |
|---|---|
| **SBD 1** | Invitation to Bid — company details, bid price, formal acceptance of conditions |
| **SBD 2** | Tax clearance declaration — which tax products you are registered for |
| **SBD 4** | Bidder's Disclosure — since 31 March 2022 this consolidates the old SBD 4 (Declaration of Interest), SBD 8 (Past SCM Practices) and SBD 9 (Independent Bid Determination) |
| **SBD 6.1** | Preference points claim, with the B-BBEE certificate or sworn affidavit attached |
| **SBD 7** | Contract form — 7.1 purchases, 7.2 services, 7.3 sales |

Not every tender needs every form; the tender pack specifies which apply.

---

## P0-1 · Check the same fact is the same on every form

**Named disqualification cause.** If the registration number on SBD 1 does not
match SBD 4, or the tax reference on SBD 2 differs from SBD 1, or director
details vary between forms, procurement reads it as carelessness or deliberate
misrepresentation. Both mean rejection.

CairoAI is uniquely placed here: it fills the whole pack, so it is the only
party that sees every form at once. A human checking this manually across six
documents is exactly where a tired person makes a mistake at 11pm.

**Done when:** after a pack is filled, every value written to more than one
form is compared, and any disagreement is raised prominently — not as one flag
among fifty. Cover at minimum: registration number, CSD supplier number, tax
reference, VAT number, company name, director names and ID numbers, B-BBEE
level.

Include values the user typed as well as values CairoAI filled. A mismatch
between a filled field and a hand-entered one is the same disqualification.

---

## P0-2 · Calculate the preference points claim

SBD 6.1 asks for a B-BBEE status level and the points claimed. The mapping is
fixed by regulation, not by judgement:

```
              80/20        90/10
Level 1         20           10
Level 2         18            9
Level 3         14            6
Level 4         12            5
Level 5          8            4
Level 6          6            3
Level 7          4            2
Level 8          2            1
Non-compliant    0            0
```

The applicable system is stated on the form by the organ of state — the
reference SBD 6.1 in this repo says *"the 80/20 preference point system"* and
prints 80 and 20 in its allocation table.

Donington Vale is **Level 1**, so under 80/20 the claim is **20 points**. That
is arithmetic from a documented fact, and leaving it blank costs points for no
reason.

**Done when:** the points claim is drafted from the stored B-BBEE level and the
system the form states, and the specific-goals table is filled from the four
`owned_51pc_*` columns. Never claim a goal the certificate does not support —
that is a misrepresentation, and the four flags exist precisely so it comes
from a document rather than an assumption.

---

## P0-3 · Refuse to export against an expired certificate

A B-BBEE certificate must be valid **at tender closing**. An expired one scores
zero, and a claim without attached proof scores zero. Same for the SARS Tax
Compliance Status PIN.

Donington Vale's certificate expires **22 March 2027**. Every pack CairoAI
fills carries a closing date — the reference tender states *"the RFQ closing
date of 14 August 2026"* — so the comparison is available and nobody is making
it.

**Done when:** the closing date is extracted from the tender, compared against
every stored expiry, and an expired or about-to-expire document is raised
before export rather than after rejection. "Expires in 3 weeks, this tender
closes in 5" is worth saying too.

---

## P0-4 · A pre-export disqualification check

Before a draft is exported, run the checks a procurement officer runs first:

- **Every signature line is still empty.** CairoAI never signs, correctly — so
  every `[ ! ]` on a signature line is a task the user must do by hand before
  submission. Count them and say so plainly: *"4 signature lines need signing —
  SBD 1 p2, SBD 4 p3, SBD 6.1 p4, SBD 7 p1."*
- **SBD 4 needs every director.** Where a form requires all directors or
  members to sign, one missing signature disqualifies the whole submission.
  The profile knows how many directors there are.
- **No mandatory field left blank.** Guidance is explicit: leave nothing blank
  unless the form says optional, and never write "not applicable" where a
  yes/no is required — it reads as avoiding disclosure.
- **Claimed points have proof attached.** A B-BBEE claim without the
  certificate scores zero.

**Done when:** the export summary leads with what would disqualify this bid,
ahead of what was filled. A user who reads nothing else should still see the
four signatures they have to add.

---

## P1-5 · Know which form version is in the pack

Some departments still issue the pre-2022 SBD 4, SBD 8 and SBD 9 separately;
the current standard is the consolidated SBD 4. Field meanings differ between
them, and using the wrong assumptions produces a wrong fill.

**Done when:** the classifier records which SBD form and which version each
document is, and the extraction uses it. Never substitute a form from another
pack — the tender's own version is the only correct one.

---

## Constraints

**Drafts only, unchanged.** No signature is ever auto-applied. Everything above
makes the draft more complete and the risks more visible; none of it reduces
what a person must review and sign.

**Never claim what the documents do not support.** A preference point claimed
without qualification is a misrepresentation to an organ of state. Every claim
traces to the B-BBEE certificate or to an answer the user gave through the
confirmed path.

**Pricing stays with the quotation.** SBD 1 asks for a bid price and SBD 7
binds the terms — figures come from the quotation system, which keeps its own
audit trail.

**Verify by looking.** Render filled pages and inspect them. Every defect found
in this project so far was invisible in the counts.

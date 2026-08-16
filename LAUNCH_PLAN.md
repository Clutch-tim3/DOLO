# CairoAI — Launch Readiness Plan

**Audit date:** 16 August 2026 · **Launch:** 1 September 2026 · **16 days**
**Baseline:** 854 tests passing, 0 failing

---

## Contents

1. [Where things stand](#1-where-things-stand)
2. [Track A — work I can do without you](#2-track-a--work-i-can-do-without-you) *(14 items)*
3. [Track B — blocked on you](#3-track-b--blocked-on-you) *(10 items)*
4. [The schedule to 1 September](#4-the-schedule-to-1-september)
5. [Execution brief](#5-execution-brief) *(paste into a fresh session)*
6. [Environment traps](#6-environment-traps)
7. [What is done and verified](#7-what-is-done-and-verified)

---

## 1. Where things stand

The product works. Scanned tenders are read with OCR, ruled lines are recovered
from the bitmap, values are filled, reviewed and exported — verified end to end
in production, not inferred.

What is **not** ready is everything around it: no way to take payment, no way to
onboard a customer, no database backup, and three endpoints that hand any
anonymous caller other companies' data.

None of that is visible from using the app, which is why it survived this long.

| | count |
|---|---|
| P0 blockers | **6** |
| P1 important | **9** |
| P2 hardening | **5** |
| I can do alone | **14** |
| Blocked on you | **10** |

---

## 2. Track A — work I can do without you

No decisions, accounts or credentials of yours required.

### P0 — must be fixed before anyone else uses this

#### A1 · Three endpoints leak every company's data to anonymous callers — ~4h

`/api/tracked-outcomes`, `/api/calendar-events` and `/api/compliance-status`
require no authentication and read whole tables with no tenant filter.

```
$ curl https://api-…run.app/api/tracked-outcomes
200  []

app.py:1530   c.execute("SELECT * FROM tracked_outcomes ORDER BY updated_at DESC")
              ^ no WHERE company_id
```

They return `[]` today **only because those tables are empty**. The moment a
customer tracks an outcome, every other customer can read it.

**Fix:** add `require_principal` to each, filter every query by `company_id`,
add a cross-tenant test per route that fails if the filter is removed.

#### A2 · The company archive is one shared file with no tenant key — ~6h

`get_archived_companies()` (app.py:208) reads `company_archive.json` — a flat
list holding every company's archived documents, with no `company_id` anywhere
in it. Anything built on it is cross-tenant by construction.

**Fix:** move into Cloud SQL as a table keyed by company, migrate the existing
records, route all reads through the principal check the rest of the app uses.

#### A3 · Nine call sites still write to the ephemeral disk in production — ~8h

`app.py` opens `sqlite3.connect(DB_PATH)` directly, bypassing the `agent.db`
abstraction that routes to Cloud SQL. On Cloud Run that writes to `/tmp`, which
is per-instance and wiped on cold start.

```
app.py: 111, 1066, 1166, 1338, 1461, 1484, 1527, 1587, 1638
```

Tracked outcomes, calendar events and predictions are being written to a disk
that disappears.

**Fix:** convert each to `db.connect()`. The shim already handles the dialect
differences — this is the same migration already done for auth, packs and
reviews.

#### A4 · The rate limiter resets itself, so it does not limit — ~2h

`rate_limiter._get_db()` uses raw SQLite on the same ephemeral path. Each new
instance starts with an empty table, so the global throttle protecting your
Anthropic spend resets on every cold start and under any scale-out.

**Fix:** move the counter to Cloud SQL; test that the limit survives a simulated
instance restart.

#### A5 · The site advertises model accuracy that is not real — ~2h

"0.8578 AUC" and "0.8187 AUC" appear in five places:

```
firebase_public/index.html:93,94
firebase_public/system.html:151,159
firebase_public/workspace.html:1009
```

Measured held-out performance is **0.557**, where 0.50 is a coin flip. These are
public accuracy claims about a product sold for government bidding.

**Fix:** drive every displayed figure from `predict/model_validation.py`, which
already reads the real metrics file, so the number cannot go stale again. Bump
the asset version pin. Pairs with decision **B7**.

#### A6 · The company profile endpoint returns invented data — ~3h

`/api/company-profile` (app.py:608) — which the whole workspace sidebar reads —
returns a hardcoded object. Its own comment says so:

```
app.py:612   # In a real app this would query the DB.
             # We'll return mock data matching the design.
```

Every user sees the same fake company name, registration number and track record
regardless of who they are.

**Fix:** return the real profile from `company_store` for the authenticated
company, and real usage stats from `subscription.py`.

### P1 — needed for a credible launch

#### A7 · No audit trail on who confirmed an auto-filled value — ~5h

Confirmation is the gate that makes drafts-only meaningful, and nothing records
which user confirmed which value, or when. On a document submitted to an organ
of state, that trail is the evidence a person reviewed it.

**Fix:** record user, timestamp and value hash per confirmation; surface it on
the export summary.

#### A8 · Prompt injection is undefended — ~6h

The agent reads tender documents supplied by third parties and acts on what it
finds. A tender containing instructions aimed at the model has nothing standing
in its way.

**Fix:** treat document text as data in the prompt structure, constrain the tool
surface reachable from document-derived content, add adversarial fixtures.

#### A9 · Nothing tests the origin real browsers actually use — ~3h

Every test and every deployment check hits the Cloud Run URL directly. Firebase
Hosting sits in front of that in production and strips cookies — which is exactly
how sign-in was broken for every real user while the suite stayed green.

**Fix:** a smoke check against `cairoai.web.app` that signs in and makes an
authenticated call, run as the last step of every deploy.

#### A10 · Test data and placeholder profile sit in production — ~1h

Six packs on the Test account, and `enterprise_corp`'s profile holds placeholders
(`4999999999`, `TCS-TESTPIN`). Harmless now, wrong on launch day.

**Fix:** purge the test packs and their files; clear or replace the placeholders.

#### A11 · Draft the POPIA privacy policy and terms of service — ~4h

There are no legal pages at all. CairoAI stores company registration numbers, tax
numbers, B-BBEE certificates and director details — personal and special personal
information under POPIA.

**Fix:** I draft both pages. **You must have these reviewed** — see **B6**.

#### A12 · Wire up error alerting — ~3h

Errors land in Cloud Logging and nobody is told. The `TypeError` in the prediction
path lived in production until the agent happened to narrate it on screen.

**Fix:** structured error logging plus a Cloud Monitoring alert policy on the
error rate, mailed to you. No third-party service needed.

### P2 — hardening

#### A13 · Build the desktop folder watcher — ~2d

Long-standing open task; the device-token pairing flow it needs already exists.
Real value, but not required to launch — the Vault covers the same job by upload.
**First thing I would cut.**

#### A14 · Operational runbook — ~3h

How to deploy, roll back, rotate a secret, restore a backup and provision a user,
including every trap in [section 6](#6-environment-traps).

---

## 3. Track B — blocked on you

These need your accounts, your money, your signature or your judgement.

### Cheap, and blocking everything

#### B1 · Cloud SQL has no backups. None. — 10 min · P0

```
$ gcloud sql instances describe cairoai-db
backupConfiguration.enabled  False
availabilityType             ZONAL
tier                         db-f1-micro
```

Every user account, company profile, pack, review and confirmation exists in
exactly one place with no copy. A bad migration or an accidental drop loses the
business.

**You decide, I run it.** Daily backups with point-in-time recovery costs a few
rand a month. I have not touched it because it is your infrastructure and your
bill.

#### B2 · Leaked and burned secret versions are still enabled — 15 min · P0

```
GOOGLE_OAUTH_CLIENT_SECRET   enabled: 3, 2, 1
ANTHROPIC_API_KEY            enabled: 2, 1
```

One of the older OAuth versions is the value that was pasted into a chat window.
Anthropic version 1 is a key that was already burned.

**You confirm, I run it.** Destroying a secret version is irreversible.

#### B3 · There is no way to take money — days · P0

No payment provider is integrated anywhere in the codebase. No Stripe, PayFast,
Paystack, Yoco — nothing. On 1 September a customer can use the product and
cannot pay for it.

**You decide and open the account:** which provider (PayFast and Paystack are the
usual South African choices), what the plans cost, and whether launch is paid from
day one or a free pilot. I build the integration once the account exists — budget
3–4 days for checkout, webhooks and the subscription lifecycle.

#### B4 · There are exactly three customers, and they are hardcoded — days · P0

Tiers come from `MOCK_CLIENT_REGISTRY` — a Python dict containing `starter_corp`,
`pro_corp` and `enterprise_corp`. Anyone else silently falls back to the starter
tier. There is no customer record, no company creation, no plan assignment.

**You define it, I build it:** what a customer record holds, what the tiers
actually allow, how a company comes into existence. Same body of work as B3 —
build them together.

#### B5 · Nobody can sign themselves up — ~2d · P0

By deliberate design there is no signup route: without an email channel, "sign up"
means anyone can claim any company. So every account is created by you running a
script. Workable for ten pilot customers, impossible for a public launch.

**You choose the model:** invite-only with you provisioning (works on 1 September,
no build), or self-signup — which needs an email provider account from you before
I can build verification.

### Judgement calls only you can make

#### B6 · Legal review of POPIA compliance and terms — your call · P1

I can draft the pages (A11). I cannot tell you they make you compliant. POPIA
applies to what you are storing; there are obligations around consent, retention,
breach notification and an Information Officer, and penalties are real.

**Needs a person:** a South African attorney or a POPIA compliance service
reviewing the drafts before launch. **Has a lead time — start early.**

#### B7 · What to do about the win probability — your call · P1

Held-out AUC is 0.557. The training data contains no losing bids — only winners
with synthetic negatives — so this is not fixable by tuning, and recovering the
full dataset moved it 0.500 → 0.531, which ruled out data volume as the cause.

**Three honest options:** ship it with the caveat the API already returns; remove
the percentage and show competition level instead, which the data does support; or
hold the feature back. I would not ship a bare percentage.

#### B8 · Domain and branding — ~1h yours · P1

The product lives at `cairoai.web.app`. Launching a paid B2B product to government
suppliers on a `web.app` subdomain costs credibility in the exact moment it
matters.

**You own the DNS:** buy the domain and point it at Firebase Hosting; I update
`PUBLIC_BASE_URL`, the OAuth redirect URIs and the cookie settings.

#### B9 · Database sizing and availability — your call · P2

`db-f1-micro`, zonal, 10GB. Shared CPU and a single zone — a zone outage takes the
product down, and this tier will struggle under concurrent pack processing. Fine
for a pilot; worth revisiting before real volume.

#### B10 · Spend limits on the Anthropic and Vision APIs — ~30 min yours · P2

Production runs on your Anthropic key and your GCP billing account, with no budget
cap. Once the rate limiter is durable (A4) the exposure drops, but a runaway loop
or an abusive user is currently unbounded.

---

## 4. The schedule to 1 September

Ordered so that the things blocking you come first, and nothing I build sits
waiting on a decision. Assumes I work Track A while you work Track B in parallel.

| When | Who | Work |
|---|---|---|
| **Today** | You | B1 backups · B2 destroy old secret versions · B3/B4/B5 payment provider, pricing, invite-only vs self-signup |
| **17–20 Aug** | Me | A1 tenant filters + auth · A2 archive into Cloud SQL · A3 the nine sqlite call sites · A4 durable rate limiter · A10 purge test data |
| **21–22 Aug** | Me | A5 real accuracy figures · A6 real company profile · A9 Hosting-origin smoke test wired into deploys · A12 error alerting |
| **23–27 Aug** | Me | Billing and onboarding, once B3/B4/B5 are answered · A7 confirmation audit trail |
| **28–29 Aug** | Both | A11 drafts to your reviewer *(start earlier — B6 has a lead time)* · B8 domain cutover · A8 prompt injection · A14 runbook |
| **30–31 Aug** | Both | Freeze. Full suite, backup restore drill, signup + payment end to end as a real customer. B10 caps on, monitoring confirmed firing. |

Every Track A item lands with a test that fails without the fix.

### What I would cut if the date holds firm

**A13** the desktop watcher (the Vault already covers it) and **B9** database
sizing. Everything else in Track A is either a data leak, a false public claim,
or a thing that silently loses your users' work.

If **B3** and **B4** slip past 23 August, launch invite-only with manual invoicing
rather than moving the date. That is the only decision here that can safely be
made late.

---

## 5. Execution brief

Paste this into a fresh session. It restates context deliberately, because a new
session has none.

> You are working on CairoAI, a South African government tender procurement
> platform. Launch is 1 September 2026. Work at maximum rigour.
>
> **The project.** Repo: `C:\Users\Thabang\.gemini\antigravity\scratch\DOLO`. The
> directory is named DOLO for historical reasons — the product is CairoAI and must
> never be called DOLO in anything a user sees. FastAPI + Python 3.12, deployed as
> a Firebase Functions gen2 (Cloud Run) function called `api`, fronted by Firebase
> Hosting at https://cairoai.web.app. Function URL:
> `https://api-tja32zbdja-uc.a.run.app`. State is Cloud SQL Postgres
> (`cairoai:us-central1:cairoai-db`) via the Cloud SQL Python Connector with
> pg8000. Generated documents go to `gs://cairoai-generated`. GCP project
> `cairoai`. Test account: `Test` / `Test12345678`, company `enterprise_corp`.
> Baseline: 854 tests passing.
>
> **Non-negotiable principles.**
>
> 1. **Drafts only.** No signature auto-applied, no pricing auto-inserted, every
>    auto-filled value confirmed by a human before export. If a change would relax
>    this, say so and stop.
> 2. **Evidence, not assertion.** This project has a history of work reported as
>    complete that was fabricated or evaluated against the wrong data. A claim
>    without evidence is a failure, not a success.
> 3. **Never fabricate.** No invented prices, accuracy figures, API capabilities or
>    integrations. A module here once returned invented supplier prices with real
>    retailer URLs attached, and it reached a document headed "INVITATION FOR BID".
> 4. **Look at the output.** Counts lie. A PDF fill reported "31 fields filled"
>    while writing every value onto the wrong page. Render it and look at it.
>
> **How to work.** Run `python -m pytest tests/ -q` before and after. Every fix
> lands with a test that fails without it — prove it by reverting the fix, showing
> the failure, restoring it. Commit each item separately explaining *why*, with the
> evidence that found it. Deploy with
> `firebase deploy --only functions --project cairoai` (add `,hosting` for frontend
> changes). Verify against `cairoai.web.app`, **not** the Cloud Run URL — the
> Hosting path is where real users are, and where the cookie bug hid.
>
> **Read section 6 of LAUNCH_PLAN.md before starting.** Those traps will otherwise
> cost you days.
>
> **The work:** items A1 → A14 in section 2, in order.
>
> **Out of scope — raise, then wait:** everything in section 3. Do not enable
> backups, destroy secret versions, integrate payments, redesign the customer
> registry, build signup, "improve" the win-probability model, buy a domain or set
> spend caps without the owner's explicit go-ahead.
>
> **Reporting.** Say what you did, what you verified and how, and what you could
> not do. If something is broken, say it plainly with the output that shows it. Do
> not pad, do not congratulate, and never report something as working that you have
> not seen work.

---

## 6. Environment traps

Each of these cost real time. Read before touching anything.

- **PowerShell blocks `.ps1` scripts on this machine.** `gcloud`, `npm` and
  `claude` each install a `.ps1` wrapper that shadows a working `.cmd`. Call the
  `.cmd` explicitly. gcloud lives at
  `C:\Users\Thabang\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd`
- **Heredocs are a PowerShell parse error.** Anything multi-line — git commit
  messages, inline Python — goes through Bash.
- **PowerShell re-encodes strings piped to native commands.** This silently
  corrupted a password fed to `manage_users.py --password-stdin`, storing a
  credential nobody could reproduce. Byte-exact input goes through Bash.
- **Tool writes may be sandboxed.** Software "installed" by an agent can be
  invisible on the real machine. Never tell the user you installed something —
  give them the command. Network operations (Cloud SQL, GCS, deploys, API calls)
  *do* reach real services.
- **The session cookie must be named `__session`.** Firebase Hosting forwards that
  one cookie and strips all others. Any other name breaks sign-in through
  cairoai.web.app while every test against the Cloud Run URL stays green.
- **Frontend assets are version-pinned.** `workspace.html` loads
  `static/autofill_packs.js?v=N`. Change that JS and you *must* bump `N`, or every
  browser serves its cached copy. This has made a working fix look broken three
  times.
- **`static/` and `firebase_public/` are mirrors.** Change both.
- **`Blank.page_number` is 0-based.** Reading it as 1-based wrote every value one
  page early while all counts stayed correct.
- **Naming a secret in `main.py` before it exists in Secret Manager fails the
  entire deploy**, including unrelated parts.
- **Env vars set with `gcloud run services update` are wiped by
  `firebase deploy`.** Non-secret config belongs in `.env.cairoai`.
- **Deploys fail intermittently** on network/GCP errors. Retry; after three
  attempts inside verified-good connectivity, stop and report.

---

## 7. What is done and verified

- Scanned tenders read end to end — OCR via Cloud Vision, ruled-line detection
  from the bitmap, filling, review, export. Verified in production.
- Values placed within 2pt of ground truth, in a handwriting face, with the
  machine-placed highlight visible over the scan.
- Authentication, sessions, device pairing, and the Hosting cookie fix.
- Durable state in Cloud SQL for auth, packs, reviews, profiles and quotas.
- Generated documents surviving instance restarts, in Cloud Storage.
- Drafts-only enforced throughout: no signature, no pricing, every value confirmed
  by a person before export.
- 854 tests passing, including a regression for every bug found this month.

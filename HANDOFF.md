# Handoff — 9 August 2026

Written at a deliberate pause. The next piece of work is a simpler feature;
this file exists so the complicated one can be resumed without re-deriving it.

Read `CLAUDE.md` first for the traps. Read `agent_autofill/BUILD_STATE.md` for
the Agent Autofill internals. This file is the state *between* those.

---

## 1. Where the code is

| | |
|---|---|
| `main` | `0cfa951` — everything through the OAuth/PKCE work |
| `auth/first-authentication` | `fefe87d` + uncommitted ownership work. **Not merged, not pushed, not deployed.** |
| Deployed to `cairoai.web.app` | matches `main` |

**The auth branch is not deployed, so production is still unauthenticated.**
See §3.

### Uncommitted on the auth branch right now

- `agent/generated_files.py` (new) — file ownership registry
- `app.py` — `/api/generated/{filename}` now requires auth + ownership
- `agent/db.py` — SQLite busy timeout 30s + WAL
- `agent/quotation/quote_builder.py`, `agent/onboarding/accreditation_report.py`,
  `agent_autofill/integration/review_gate.py`, `agent/tool_dispatch.py`,
  `agent/main_agent.py` — `company_id` threaded to the four generation points
- `tests/test_generated_file_ownership.py` (new) — 9 tests, all passing

**The full suite has failures on this branch and they are NOT diagnosed.**
`tests/test_generated_file_ownership.py` passes 9/9 alone; the whole run showed
failures from ~10% onward. That is the first thing to do on resuming — do not
assume it is only the three known ML failures. Run:

    python -m pytest tests/ -q --no-header --tb=line

---

## 2. Live production state, verified

    ok api_key_configured=true stamp_secret_configured=true durable_state=false

- Google OAuth works end to end **up to the token exchange**. Real consent
  completes; the exchange fails `invalid_client` because the deployed secret
  belongs to a replaced client. Client ID in `.env` is
  `...lkch66lf3tqiekqigdtsmi7o4ehq9rg9`; Secret Manager has version 3. If they
  do not match, that is the failure.
- `durable_state=false` — **no Cloud SQL instance exists.** The Admin API is
  not even enabled. All state is on `/tmp`, per-instance, destroyed on cold
  start.

---

## 3. Blockers, in the order they must happen

### 3.1 Cloud SQL — blocks everything

    gcloud services enable sqladmin.googleapis.com --project cairoai
    gcloud sql instances create cairoai-db --database-version=POSTGRES_15 \
      --tier=db-f1-micro --region=us-central1 --storage-size=10GB \
      --storage-auto-increase --backup --project cairoai

Then `CLOUD_SQL_PASSWORD` in Secret Manager, add the binding back to `main.py`
(it was removed — see §5), and `CLOUD_SQL_INSTANCE` / `_DB` / `_USER` in `.env`.

### 3.2 Auth cannot be deployed before 3.1

Users and sessions live in `agent_memory.db`, which is on `/tmp` in production.
Deploy auth first and **every cold start destroys every account and session** —
you create a login, use it, and are locked out of your own site with no
recovery. This ordering is not optional.

### 3.3 Then: merge auth, create a real account, deploy

No signup exists (deliberate — without email anyone could claim any
`company_id`). Accounts come from `scripts/manage_users.py`. A fresh clone
cannot log in.

Delete the throwaway accounts first: `demo@cairoai.test`,
`starter@cairoai.test` in the gitignored `agent_memory.db`.

---

## 4. The security findings, and what is true about each

| Finding | State |
|---|---|
| No authentication anywhere — `X-Company-ID` header, defaulted to `starter_corp` | **Fixed on the branch, verified by me independently.** 401 for all three company IDs; a hostile header on an authenticated request is ignored (byte-identical body). Still live in production. |
| `/api/generated/{filename}` IDOR — stamp verified, ownership never checked | **Fixed on the branch**, 9 tests. Mine, from the verify_export wiring earlier the same day. |
| Export gate: forged DB rows, fabricated stamps, **portable stamps** | Fixed on `main`, 3 rounds, all proven with before/after |
| Quote finalisation from caller-supplied list; the gate was a dead end; `inf`/`NaN` prices | Fixed on `main` |
| Signature blocklist did not generalise (26/27 novel labels passed) | Fixed on `main` |
| Auto-filled values were never confirmed by anyone | Fixed on `main` — bulk confirmation gate |

### Still open, not fixed

- **Unauthenticated routes** that resolve no company: `/api/compliance-status`,
  `/api/calendar-events`, `/api/accuracy-stats`, `/api/tracked-outcomes`,
  `/api/track-outcome`, `/api/system-status`, `/api/model-status`,
  `/api/batch-status/{job_id}`, `/api/files/{filename}`,
  `/api/quotations/{filename}`. **`/api/files/` and `/api/quotations/` serve
  files** — same shape as the IDOR just fixed.
- `data/company_archive.json` is global shared state. Auth put it behind a
  login; it is still not tenant-scoped.
- No MFA, no password reset, no rate limit on device pairing. Lockout is
  per-username, so it doubles as a way to lock a known user out.
- Sessions are not bound to IP or user-agent.
- `auth.purge_expired()` and `oauth_state.purge_expired()` have no scheduled
  caller.

---

## 5. Deploy traps learned the hard way today

1. **Never name a secret in `main.py` before it exists in Secret Manager.** The
   CLI validates every binding before uploading and fails the *whole* deploy.
   `CLOUD_SQL_PASSWORD`, `WEBHOOK_TASK_SECRET` and `DROPBOX_APP_SECRET` are
   currently commented out of the `secrets=[...]` list for this reason. Add each
   back the moment its secret exists.
2. **`gcloud run services update --update-env-vars` is wiped by the next
   `firebase deploy`.** `.env` is the only config a deploy preserves. Public
   values go there; secrets go to Secret Manager. This is how
   `GOOGLE_OAUTH_CLIENT_ID` vanished and the connect route silently 503'd.
3. **`PUBLIC_BASE_URL` must be set** or the OAuth `redirect_uri` is built from
   the Cloud Run backend origin (`http://api-xxxx.run.app`) and Google rejects
   it for scheme *and* host.
4. **Do not import `app` in a test that also exercises stamp signing.**
   `app.py` calls `load_dotenv(".env.local", override=True)`, which re-keys
   `AUTOFILL_STAMP_SECRET` mid-run and invalidates every signature made before
   that point.

---

## 6. The complicated feature, and why it stalled

**Goal:** a tender lands in a folder → CairoAI notices, pre-fills the bid,
returns a draft for human review.

**What was ruled out, with evidence:**

- **Google Drive passive watching is impossible under `drive.file`.** Google's
  wording: access covers files "that you open with an app or that the user
  shares with an app while using the Google Picker API". A file saved into a
  folder from a phone is neither. A CairoAI-created folder does not help.
  Passive watching needs `drive.readonly`/`drive` — the whole Drive, plus
  Google's restricted-scope assessment. The provider actively refuses those.
- **Dropbox App folder *would* have worked** — *"users can provide content to
  your app by moving files into this folder"* — but Dropbox was dropped by
  decision.
- **Android companion app: parked.** No toolchain on this machine — no `adb`,
  `gradle`, `java`, SDK, emulator, or project. Every proof requirement in the
  spec is a real-device test, so it can be written but not verified here.

**The chosen path: a desktop folder watcher** (Windows/Mac). Watches a local
folder with the browser closed, uploads to the existing pipeline. Chosen partly
because it is the one option that can be *proved* on this machine — Windows,
Python present. Blocked on auth, because it needs the device-token credential
that only exists on the auth branch.

**Also unbuilt for this feature:** webhook routes are not mounted in `app.py`,
`register_webhook` is never called, and Cloud Tasks is unprovisioned so
`deployment_readiness()` reports `ready: False`.

---

## 7. What needs the user, not the agent

- Enable the Cloud SQL Admin API and create the instance (§3.1)
- Store the current Google OAuth client's secret so the exchange stops failing
- Destroy `GOOGLE_OAUTH_CLIENT_SECRET` versions 1 and 2 once 3 is confirmed
  working — version 2 held a value that leaked into a chat transcript and was
  rotated in the console
- `google-cloud-sdk/bin/gcloud.cmd config unset auth/access_token_file` and
  delete `access_token.txt` (expired, never committed, but the SDK is still
  pointed at it)

---

## 8. Standing rules for this project

- **Drafts only.** No signature applied, no price written, no declaration
  answered from a stored value. Applies on every platform.
- **Evidence, not claims.** This project has a history of subagent reports that
  did not survive checking. Re-run things; a discrepancy is a finding.
- Baseline before the auth branch: **3 failed, 583 passed, 1 xfailed**. The
  three are pre-existing ML failures in `test_calibration_and_threshold.py` and
  `test_feature_engineering.py`. One is worth fixing: the categorical encoder
  returns "unknown" for both inputs, meaning its fitted categories no longer
  match the data — a stale encoder degrades every win-probability prediction
  silently rather than erroring.

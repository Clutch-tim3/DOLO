# CairoAI operational runbook

Every command here was run against the live `cairoai` project, or is marked as
unverified. Where a procedure does not exist yet, this says so instead of
describing one that would not work.

**Verified:** 16 August 2026, as `thabangmolwantwa0@gmail.com`.

---

## 0. Before anything else

`gcloud` on this machine is **not** on PATH as `gcloud.cmd`. The working binary:

    GC="/c/Users/Thabang/AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud"

PowerShell blocks the `.ps1` wrappers that `gcloud`, `npm` and `claude` install,
and those wrappers shadow the working executables. Call the path above from Git
Bash. Anything multi-line — commit messages, inline Python, heredocs — goes
through Bash too; heredocs are a PowerShell parse error, and PowerShell
re-encodes strings piped to native commands, which once silently corrupted a
password fed to `manage_users.py --password-stdin` and stored a credential
nobody could reproduce.

| | |
|---|---|
| GCP project | `cairoai` |
| Cloud Run service | `api`, region `us-central1` |
| Function URL | `https://api-tja32zbdja-uc.a.run.app` |
| Hosting origin | `https://cairoai.web.app` ← **the one that matters** |
| Cloud SQL | `cairoai:us-central1:cairoai-db` (Postgres, pg8000) |
| Generated files | `gs://cairoai-generated` |

---

## 1. Deploy

    firebase deploy --only functions:api,hosting --project cairoai

`--project cairoai` is required unless you are in the repo root.

**Before:**

    python -m pytest tests/ -q
    python scripts/sync_firebase_public.py     # after ANY frontend change
    python scripts/gen_functions_yaml.py       # after changing options in main.py

`static/` is the source; `firebase_public/` is what Hosting serves. Editing
`static/` alone changes nothing in production. And the Firebase CLI reads
`functions.yaml` **instead of** running discovery — anything missing from that
file is silently dropped from the deploy no matter what the decorators say. A
hand-written version once omitted `timeout_sec` and `secrets`, so every agent
request 504'd and no API key was bound.

**After — not optional:**

    python ops/smoke_check.py --username Test --password '<password>'

Exit 0 means sign-in works end to end through the Hosting origin. Do not trust
"Deploy complete!". Past deploys have succeeded while serving a stale stylesheet
or a function that 504'd on every request.

Verified against production on 16 Aug — all five steps pass, authenticating as
`enterprise_corp`, and `/api/__health` reports
`api_key_configured=true stamp_secret_configured=true durable_state=true`
through both the Hosting origin and the Cloud Run URL. `durable_state=true` is
the one to keep an eye on: it means Cloud SQL is bound and state is not quietly
falling back to SQLite on `/tmp`.

The check **refuses a Cloud Run URL** unless you pass `--allow-non-hosting`.
That is deliberate: Hosting forwards only the `__session` cookie and strips the
rest, so testing the Cloud Run URL is how sign-in stayed broken for every real
user while the suite was green.

**If a deploy fails intermittently**, retry. After three attempts on known-good
connectivity, stop and investigate rather than looping.

---

## 2. Roll back

Cloud Run keeps every revision. Verified list as of 16 Aug: `api-00040-viz` is
current, back through `api-00035-bux` and earlier.

    "$GC" run revisions list --service api --region us-central1 --project cairoai

    "$GC" run services update-traffic api \
        --region us-central1 --project cairoai \
        --to-revisions api-00039-tum=100

Then re-run the smoke check. Rolling back to a revision that was also broken is
easy to do when you are in a hurry.

**This rolls back the function only.** Hosting is deployed separately and is not
reverted by the above — if the frontend is the problem, redeploy the previous
commit's `firebase_public/` with `firebase deploy --only hosting`. Firebase
Hosting also keeps releases and can be rolled back from the console.

**Caution.** Env vars set with `gcloud run services update --update-env-vars`
are wiped by the next `firebase deploy`. That is how `GOOGLE_OAUTH_CLIENT_ID`
vanished once, leaving the connect route returning 503 on an otherwise clean
deploy. Non-secret config belongs in `.env`, which is the only mechanism a
deploy preserves.

---

## 3. Rotate a secret

Current state, verified 16 Aug:

| Secret | Enabled versions |
|---|---|
| `ANTHROPIC_API_KEY` | 2, 1 |
| `AUTOFILL_STAMP_SECRET` | 1 |
| `CLOUD_SQL_PASSWORD` | 1 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | 3, 2, 1 |

**Add a new version** (the function picks up `latest` on next deploy):

    firebase functions:secrets:set ANTHROPIC_API_KEY --project cairoai --data-file <path>

Use `--data-file` with a real path. Piping to `--data-file -` fails on Windows
PowerShell with "Secret Payload cannot be empty".

**Then redeploy**, or the running revision keeps the old value.

**Destroying an old version is irreversible:**

    "$GC" secrets versions destroy 1 --secret=ANTHROPIC_API_KEY --project cairoai

Disable first, watch for a day, then destroy — a disabled version can be
re-enabled, a destroyed one cannot.

> **Outstanding (B2).** `ANTHROPIC_API_KEY` version 1 is a key that was already
> burned, and one of the older `GOOGLE_OAUTH_CLIENT_SECRET` versions is the
> value that was pasted into a chat window. Both are still enabled. This is a
> 15-minute job that has not been done.

**Never name a secret in `main.py` before it exists.** The CLI validates every
binding before uploading and fails the WHOLE deploy — including unrelated parts
— with `Secret [...] not found or has no versions`. Create the secret, then add
the binding, then deploy.

**`.env` must contain no secrets.** The CLI loads it at deploy time and sets
every key as a plaintext env var on the function, shadowing the Secret Manager
binding. `functions.ignore` does not prevent this.

---

## 4. Restore a backup

**There are no backups. This procedure does not exist yet.**

Verified 16 August 2026:

    $ gcloud sql instances describe cairoai-db --project cairoai
    backupConfiguration.enabled   False
    availabilityType              ZONAL
    tier                          db-f1-micro

Every user account, company profile, pack, review and confirmation exists in
exactly one place with no copy. A bad migration or an accidental `DROP` loses
the business. There is nothing to restore from and no point-in-time recovery.

**Enable them first** (LAUNCH_PLAN B1 — a few rand a month, ~10 minutes):

    "$GC" sql instances patch cairoai-db --project cairoai \
        --backup-start-time=02:00 \
        --enable-point-in-time-recovery \
        --retained-backups-count=7

Once backups exist, restore is:

    "$GC" sql backups list --instance cairoai-db --project cairoai
    "$GC" sql backups restore <BACKUP_ID> --restore-instance=cairoai-db --project cairoai

*Both commands above are unverified — they cannot be tested until backups are
enabled.* Do a restore drill before launch. A backup nobody has restored from
is a belief, not a backup.

---

## 5. Provision a user

There is deliberately no signup route and no password reset: both need an email
channel this project does not have, and signup without verification lets anyone
claim any `company_id`.

    python scripts/manage_users.py create --username you@example.test --company pro_corp

Or with a byte-exact password from **Bash, never PowerShell**:

    printf 'the-password' | python scripts/manage_users.py create \
        --username you@example.test --company pro_corp --password-stdin

Other subcommands: `list`, `set-password`, `disable`, `enable`. `disable` also
revokes existing sessions.

`company_id` must be one of the keys in `MOCK_CLIENT_REGISTRY`
(`agent/subscription.py`) or the user silently resolves to the starter tier —
which is the safe direction, but not usually what you meant. Today that
registry holds exactly three companies: `starter_corp`, `pro_corp`,
`enterprise_corp` (LAUNCH_PLAN B4).

**A fresh clone has no accounts and cannot log in until one is created.**

---

## 6. Scripts that exist and when to run them

| Script | When | Safe by default |
|---|---|---|
| `scripts/sync_firebase_public.py` | after every frontend change | yes, and verifies asset resolution |
| `scripts/gen_functions_yaml.py` | after changing options in `main.py` | yes, fails loudly on missing timeout/secrets |
| `scripts/manage_users.py` | provisioning | `disable` revokes sessions |
| `ops/smoke_check.py` | last step of every deploy | read-only |
| `ops/apply_error_alerting.sh` | once, to set up alerting (A12) | `--dry-run` available; creates, never deletes |
| `scripts/migrate_company_archive.py` | once, to move the JSON archive into the DB (A2) | dry run by default; imports **unowned** unless `--assign-to` |
| `scripts/purge_test_data.py` | before launch, to remove test packs (A10) | dry run by default; **run only after backups exist** |

Ordering that matters: **enable backups (B1) before `purge_test_data.py`.** It
deletes production rows and the files behind them.

---

## 7. Diagnosing a live problem

**Is the running instance configured correctly?**

    curl https://cairoai.web.app/api/__health

Plain text, three flags (implemented in `main.py`, not `app.py`):

    ok api_key_configured=true stamp_secret_configured=true durable_state=true

- `durable_state=false` is the dangerous one. **Nothing fails.** The app quietly
  falls back to SQLite on `/tmp` and users lose everything on the next cold
  start while the site looks healthy. The symptom is "the vault forgot my
  documents", not an outage.
- `stamp_secret_configured=false` means Agent Autofill cannot export. It fails
  closed, so there is no other outward symptom until a user tries one.
- `api_key_configured=false` returns **503** rather than 200.

It is answered without touching the ASGI bridge, deliberately. If this responds
and real routes time out, the fault is in the bridge; if this times out too, it
is the runtime or the function config. That split is what identified the
import-time event loop as the cause of a round of 504s.

**Read errors.** Every unhandled exception is one structured line with
`error_type` and `stack_trace` (A12):

    "$GC" logging read \
      'resource.type="cloud_run_revision" AND severity>=ERROR' \
      --limit=20 --project cairoai \
      --format='value(jsonPayload.error_type, jsonPayload.endpoint, jsonPayload.message)'

Group by `error_type` to tell one repeated fault from several different ones.

> Before A12 this returned nothing useful: the only log handler wrote to
> `/tmp/logs/api.log`, which is per-instance and wiped on cold start, and
> Python's last-resort stderr handler never fired because that handler had
> already taken the record. Errors were not going unnoticed in Cloud Logging —
> they were never arriving.

**Alerting is not on yet.** `ops/apply_error_alerting.sh` creates the policy and
the email channel. An unverified email channel silently drops alerts, so click
the confirmation link, then trigger a real error and wait for the mail. A policy
nobody has watched fire is a configuration, not monitoring.

---

## 8. Traps, in one place

Each of these has already caused a production failure or a silently-wrong
deploy.

1. **`static/` and `firebase_public/` are mirrors.** Change both, via
   `sync_firebase_public.py`. Hosting's catch-all rewrite serves `index.html`
   for a missing path, so a broken asset returns HTTP 200 with the wrong body
   instead of a 404.
2. **`functions.yaml` overrides the decorators in `main.py`.** Regenerate it.
3. **`.env` must contain no secrets** — it shadows Secret Manager at deploy time.
4. **Never name a secret in `main.py` before it exists** — it fails the entire
   deploy, including unrelated parts.
5. **The session cookie must be named `__session`.** Hosting forwards that one
   and strips all others. Any other name breaks sign-in through
   `cairoai.web.app` while every test against the Cloud Run URL stays green.
6. **Bump the cache-buster** when you touch a version-pinned asset
   (`autofill_packs.js?v=N`, `style.css?v=NN`). This has made a working fix look
   broken three separate times.
7. **`style.css` has a parallel `#tab-agent` stylesheet** — ~60 rules scoped
   `#tab-agent .foo`. A bare `.foo` selector loses on specificity and silently
   does nothing.
8. **The deployed filesystem is read-only except `/tmp`,** and `/tmp` is
   per-instance and ephemeral. Anything that must survive a restart goes through
   `agent/db.py` or Cloud Storage.
9. **`Blank.page_number` is 0-based.** Reading it as 1-based wrote every value
   one page early while all counts stayed correct.
10. **`PRAGMA table_info` does not exist on Postgres** — use
    `db.table_columns(conn, table)`.
11. **`INSERT OR REPLACE` is deliberately not translated** by the db shim. It
    fails loudly on Postgres rather than meaning something else.
12. **`agent/db.py`'s `with` block closes the connection**, unlike `sqlite3`.
    Leaking them exhausts the Cloud SQL connection limit.
13. **Local `fpdf` is not the deployed `fpdf`.** `requirements.txt` installs
    fpdf2; this machine has fpdf 1.7, and they differ in where `multi_cell`
    leaves the cursor.
14. **Seven fixtures named `.docx` are actually OLE2 `.doc`.** Detect format by
    magic bytes, never by extension.
15. **Tests must not import `app` alongside the stamp tests.** `app.py` calls
    `load_dotenv(".env.local", override=True)`, which replaces an already-set
    `AUTOFILL_STAMP_SECRET` and re-keys every signature made before that point.
16. **The Postgres path has never served a request against real Cloud SQL.** It
    is covered by translation tests and a fake driver. Treat it as unverified
    until it has.

---

## 9. Known-open items

Not traps — decisions and work that are not done. See `LAUNCH_PLAN.md`.

- **B1** no Cloud SQL backups. Verified 16 Aug. Blocks §4 entirely.
- **B2** burned secret versions still enabled. Verified 16 Aug.
- **B3** no payment provider integrated anywhere.
- **B4** three hardcoded companies in `MOCK_CLIENT_REGISTRY`.
- **B5** no signup route by design; every account is provisioned by hand.
- **B6** POPIA review — needs an attorney, has lead time.
- **B8** the product is on a `web.app` subdomain.
- **B10** no spend cap on the Anthropic key or GCP billing.

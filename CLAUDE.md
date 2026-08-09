# CairoAI

Procurement assistant for South African government tenders: ML win-probability
prediction, a Claude-powered agent, compliance vetting, and quotation drafting.

FastAPI backend + static frontend, deployed to Firebase (Hosting + Functions
gen2) at https://cairoai.web.app.

## Naming

The product is **CairoAI**. The repo directory and GitHub remote are still
named `DOLO`, which is the retired name — do not use it in prompts, UI copy,
generated documents, or anything else user-facing. The assistant refers to
itself as "the CairoAI Agent". Internal paths (`/tmp/dolo-db`,
`/tmp/dolo-generated`) keep the old name deliberately; renaming them buys
nothing and breaks paths.

Note: "meth**odolo**gy" matches a case-insensitive grep for the old name.

## Traps that will cost you hours

These are not style preferences. Each one has already caused a production
failure or a silently-wrong deploy.

### 1. The frontend exists twice

`static/` is the source. `firebase_public/` is what Hosting actually serves.
Editing `static/` alone changes nothing in production.

    python scripts/sync_firebase_public.py

Run it after every frontend change. It also verifies that every asset each page
links actually resolves — worth heeding, because Hosting's catch-all rewrite
serves `index.html` for a missing path, so a broken asset returns HTTP 200 with
the wrong body instead of a 404.

### 2. functions.yaml overrides the decorators in main.py

The Firebase CLI reads `functions.yaml` **instead of** running discovery (it
logs "Found functions.yaml"). Anything missing from that file is silently
dropped from the deploy no matter what `@https_fn.on_request(...)` says.

A hand-written version of this file once omitted `timeout_sec` and `secrets`,
which meant the function kept the 60s default (every agent request 504'd) and
never had an API key bound. It is now generated:

    python scripts/gen_functions_yaml.py

Re-run it after changing any option in `main.py`. It fails loudly if the
timeout or the secret binding goes missing.

### 3. .env must contain no secrets

The Firebase CLI loads `.env` at **deploy time** and sets every key in it as a
plaintext env var on the function, which shadows the Secret Manager binding.
`functions.ignore` does not prevent this — that only controls the upload
bundle.

Deployed secrets live in Secret Manager. Local dev secrets go in `.env.local`,
which Firebase never deploys and `app.py` loads explicitly.

    firebase functions:secrets:set ANTHROPIC_API_KEY --project cairoai --data-file <path>

Use `--data-file`; piping to `--data-file -` fails on Windows PowerShell with
"Secret Payload cannot be empty".

**`.env` is for non-secret deploy config, and is the only mechanism a deploy
preserves.** Env vars set with `gcloud run services update --update-env-vars`
are wiped by the next `firebase deploy` — that is how `GOOGLE_OAUTH_CLIENT_ID`
vanished once, leaving the connect route returning 503 on a deploy that
otherwise looked clean. Values that are public by construction (client IDs, app
keys, instance names, queue paths) go in `.env`; values that are not go to
Secret Manager and are bound in `main.py`. `.env` is gitignored, so a fresh
clone needs it rebuilt — the required keys are listed in the file's own
comments.

**Never name a secret in `main.py` before it exists.** The CLI validates every
binding before uploading and fails the WHOLE deploy — including parts unrelated
to that secret — with `Secret [...] not found or has no versions`. Create the
secret first, then add the binding, then deploy. The code behind each secret
already fails closed without it, so a missing binding costs one feature; a
premature binding costs every deploy.

### 4. style.css has a parallel #tab-agent stylesheet

`static/style.css` defines ~60 rules scoped `#tab-agent .foo`. A bare
`.foo` selector written anywhere else loses on specificity and silently does
nothing. When styling the Agent tab, scope your selectors the same way.

Several controls also carry inline `style` attributes, which beat any selector
— those need `!important` to override.

### 5. Bump the cache-buster when you touch a stylesheet

`style.css?v=NN` and `ui-polish.css?v=N.N` are referenced with query strings.
Change the file without bumping the version and browsers keep the old one. This
has already shipped an unstyled page once.

### 6. The deployed filesystem is read-only except /tmp

Cloud Functions cannot write to the bundle. `agent/db_paths.py` and
`agent/file_paths.py` resolve SQLite databases and generated files to `/tmp`
when `K_SERVICE` is set. Anything that writes a file must go through
`file_paths.generated_dir()`, and is served by `/api/generated/<name>`.

`/tmp` is per-instance and ephemeral — fine for "generate it and click the
link", not storage.

### 7. State is in Postgres in production, SQLite locally

`agent/db.py` is the only place a connection to application state is opened.
With `DATABASE_URL` set it talks to Cloud SQL Postgres; without it, SQLite —
which is what local development uses, unchanged.

This exists because `db_paths` resolves SQLite to `/tmp` on Cloud Functions,
and `/tmp` is per-instance and ephemeral. Before this, a cold start silently
discarded every company profile, vault document, autofill review and quote. It
demoed perfectly, which is what made it dangerous: the symptom is "the vault
forgot my documents", not an outage.

Connection is chosen in this order: `CLOUD_SQL_INSTANCE` set means the Cloud
SQL Python Connector; otherwise `DATABASE_URL` means a directly reachable
Postgres; otherwise SQLite. The connector is used rather than the
`/cloudsql/...` unix socket because that needs `--add-cloudsql-instances` on
the Cloud Run service, which `firebase deploy` may drop when it rewrites the
config — a setting that silently disappears on a later deploy is worse than one
that was never there.

- **Only the password is a secret.** `CLOUD_SQL_INSTANCE`, `CLOUD_SQL_DB` and
  `CLOUD_SQL_USER` are plain env vars. With `CLOUD_SQL_IAM_AUTH=true` there is
  no password anywhere — the runtime service account authenticates itself.
- **pg8000, not psycopg.** The connector supports pg8000 / psycopg2 / asyncpg;
  psycopg 3 is not among them. pg8000 is also pure Python, so no build
  toolchain in the deploy.
- **The connector holds background refresh threads**, so it is built lazily and
  keyed to the PID — the same guard as the ASGI bridge, for the same reason.
  Inheriting one across a fork is what made every request 504 once already.
- **`/api/__health` reports `durable_state`.** A missing configuration does not
  fail anything — it falls back to SQLite and loses data later. Check it after
  deploying.
- Raw SQL is kept. `db.connect()` translates `?` placeholders, `AUTOINCREMENT`
  and `INSERT OR IGNORE`, and returns rows addressable by name or index. A
  module changes one line, not its queries — several of those WHERE clauses are
  security gates and rewriting them wholesale is how you break one subtly.
- **`PRAGMA table_info` does not exist on Postgres.** The additive migrations
  use `db.table_columns(conn, table)`.
- `INSERT OR REPLACE` is deliberately NOT translated. Its conflict target
  cannot be guessed, so it fails loudly on Postgres rather than running and
  meaning something else.
- Unlike `sqlite3`, this wrapper's `with` block **closes** the connection.
  Leaking them is how a Cloud SQL instance's connection limit is exhausted.
- `procurement.db` stays SQLite — bundled ML reference data. `cost_tracking`
  and `rate_limiter` still write there, so those counters remain ephemeral;
  they reset in the user's favour, which is why they were left.

**The Postgres path has never run against a real server.** It is covered by
translation tests and a fake driver. Treat it as unverified until it has served
a request on Cloud SQL.

### 8. Local fpdf is not the deployed fpdf

`requirements.txt` installs **fpdf2**; this machine has the older **fpdf 1.7**.
They differ in where `multi_cell` leaves the cursor, which produced a layout
that worked locally and raised "Not enough horizontal space" in production.
Set `x` explicitly rather than inheriting it, and test PDF changes against
fpdf2 if you can.

fpdf's core fonts are latin-1 only. Model-written text routinely contains
em-dashes and curly quotes, so sanitise before writing (see
`agent/onboarding/accreditation_report.py`).

## Running it

    python -m uvicorn app:app --port 8000

Open **`/workspace`**, not `/static/workspace.html`. Stylesheets are linked
relatively (`static/style.css`), so the second URL resolves them to
`/static/static/...`, they 404, and the page loses every mobile rule — which
looks like a layout bug and is not one.

Local agent chat needs `ANTHROPIC_API_KEY` in `.env.local`. A fresh clone will
not have it, so agent endpoints will fail until you supply one.

## Deploying

    firebase deploy --only functions:api,hosting --project cairoai

`--project cairoai` is required unless you are in the repo root (`.firebaserc`
supplies the alias there). Deploying needs Firebase auth this machine has; a
cloud session likely does not.

Do not trust "Deploy complete!" — verify the live site afterwards. Past deploys
have succeeded while serving a stale stylesheet or a function that 504'd on
every request.

## Architecture notes

- **Agent tool loop** — `agent/main_agent.py` runs the full agentic loop:
  `stop_reason == "tool_use"` → execute → feed `tool_result` blocks back →
  repeat to `end_turn`. `agent/tool_dispatch.py` is the only place tools are
  executed; it force-overwrites `company_id` with the session's value (tenant
  pinning) and confines file arguments to the uploads directory.
- **Tier config lives in `agent/subscription.py`**, not a `tier_config.py`.
- **Model IDs**: `claude-opus-5` is current. `claude-3-5-haiku-20241022` is
  retired and will 404.
- **Chat rendering** is built with DOM nodes and `textContent`, never
  `innerHTML` — the backend returns raw model text unescaped, and that is only
  safe because the client does not parse it as markup. If you switch the client
  back to `innerHTML`, you must re-introduce server-side escaping.
- **Mobile** uses an 880px breakpoint. The Agent tab is a full-screen app
  shell: fixed header and bottom nav are hidden, and heights use `100dvh` —
  `100vh` on iOS Safari is the viewport with toolbars retracted, which hides
  the composer behind Safari's toolbar.

## Verifying work

Measure, don't assume. Useful checks that have caught real bugs:

- `node --check` on extracted inline scripts — a duplicated function and an
  orphaned `catch` left the System page blank in every browser for weeks.
- Compare the live page's bytes against `firebase_public/` to confirm a deploy
  actually landed.
- Watch for non-UTF-8 bytes in CSS: `style.css` once had 1,084 NUL bytes from a
  block appended as UTF-16.

## Agent Autofill

An autonomous tender pre-fill system under `agent_autofill/`. **Read
`agent_autofill/BUILD_STATE.md` before touching it** — it records what is
built, what is not, and several spec corrections verified against live
documentation.

The one rule: **drafts only**. No signature is ever applied, no price is ever
written, and a declaration is never answered from a stored value. If a change
would relax that, raise it rather than loosening it.

**`AUTOFILL_STAMP_SECRET` must be set or Agent Autofill will not export.** It
signs review acknowledgements and export stamps so a forged database row or a
hand-built stamp cannot pass as a reviewed bid document. Acknowledging a field
and exporting a reviewed draft both raise `StampSecretMissing` without it —
deliberately, because an unsigned export is the exact artefact the signature
exists to prevent. It lives in Secret Manager for the deployed function and in
`.env.local` locally (trap 3: never `.env`). The test suite sets a fixed test
value in `tests/conftest.py`.

`verify_export(path, company_id, review_id)` is the authority on whether an
export is genuine, not the banner. It is wired into both paths that hand a file
to a person: `autofill_export_document` verifies what it just produced before
returning a link, and `/api/generated/<name>` calls `verify_export_by_path()`
and returns **409** rather than serving an export that does not match its
review. Files that are not autofill exports return `None` there and pass
through untouched.

**Tests must not import `app` alongside the stamp tests.** `app.py` calls
`load_dotenv(".env.local", override=True)`, which REPLACES an already-set
`AUTOFILL_STAMP_SECRET`. Importing it part-way through a run re-keys every
signature made before that point, and the failures look like broken
verification rather than a changed secret. This cost an hour once already. `stamp_docx()` requires a signature to be
present but cannot check one — it has the file, not the record — so a forger
can still produce a document whose banner reads REVIEWED. It will not verify.
Anything user-facing must call `verify_export` with the record.

### Connecting Drive / Dropbox

The providers could always build a consent URL and exchange a code. Nothing
called either, so the `state` value both of them generate was never stored or
checked — the CSRF parameter existed and did nothing.
`providers/oauth_routes.py` and `providers/oauth_state.py` are that missing
half, mounted in `app.py`.

- **The callback takes no `company_id`.** It comes from the stored state. If it
  came from the URL, anyone who completed a genuine consent for their own
  Google account could replay the callback with someone else's company_id and
  attach their Drive to that company. A test asserts the parameter is absent
  from the route signature, because that absence IS the defence.
- **State is single-use, 10-minute TTL, stored as a SHA-256 digest**, and burned
  even on a rejected attempt so it cannot be probed then retried.
- **Consume commits the delete before validating.** Doing the checks inside the
  transaction looked equivalent and was not: the connection rolls back on
  exception, so raising for a wrong provider silently undid the delete and left
  the state reusable. A test caught it; reading the code did not.
- **The return redirect is a fixed internal path.** An open redirect on a domain
  users are being asked to trust with Drive access is worth a lot to a phisher.

- **PKCE is on, S256 only** (`providers/pkce.py`). This is what makes the
  authorization code safe to have in a log line: the code travels as a query
  parameter and the runtime logs request lines before our handlers run, so a
  code will end up in access logs no matter what we do. Bound to a verifier
  this server keeps and never transmits, it cannot be redeemed by whoever
  reads it. The verifier lives beside the state record for the ten minutes the
  flow is open; the browser only ever carries the challenge, and a test asserts
  the verifier does not appear in the authorization URL.
- `plain` is never produced or accepted — it would put the verifier in the URL,
  which is the one place it must not be.

**No consent has ever been completed.** Every OAuth test fakes the exchange.
Real credentials, a real consent screen, and a real token refresh are manual
verification that has not happened — see `providers/VERIFICATION.md`.

Two things that will mislead you otherwise:

- **`tests/fixtures/alfred_duma.pdf` is a 1-page tender summary, not a form
  pack.** It contains no MBD forms. Extraction against it correctly returns
  zero blanks. Use it for eligibility/prediction proofs; use
  `tests/fixtures/sa_forms/` and `data/archive/temp_tender_BID_DOCUMENT_06FY27_.pdf`
  for extraction and fill.
- **Seven fixtures named `.docx` are actually OLE2 `.doc`.** Detect format by
  magic bytes, never by extension — `agent_autofill/extraction/legacy_doc_reader.py`
  does this. Those files can be read (via `antiword`) but never written.

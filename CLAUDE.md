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

### 7. Local fpdf is not the deployed fpdf

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

Two things that will mislead you otherwise:

- **`tests/fixtures/alfred_duma.pdf` is a 1-page tender summary, not a form
  pack.** It contains no MBD forms. Extraction against it correctly returns
  zero blanks. Use it for eligibility/prediction proofs; use
  `tests/fixtures/sa_forms/` and `data/archive/temp_tender_BID_DOCUMENT_06FY27_.pdf`
  for extraction and fill.
- **Seven fixtures named `.docx` are actually OLE2 `.doc`.** Detect format by
  magic bytes, never by extension — `agent_autofill/extraction/legacy_doc_reader.py`
  does this. Those files can be read (via `antiword`) but never written.

# Provider verification checklist

**Status: UNVERIFIED.** Nothing in `agent_autofill/providers/` or
`agent_autofill/webhooks/` has ever completed an OAuth consent flow, called a
live Google or Dropbox API, or received a real webhook. That requires entering
account credentials, which was prohibited in the environment this was built
in, and there is no test account and no registered OAuth client.

Everything below has to be done by a human with real accounts. Until it is,
treat this feature as code that compiles and reasons correctly, not as a
feature that works.

---

## Already proved, offline — you do not need to redo these

`python -m pytest tests/test_agent_autofill_providers.py -v -s`

| Claim | Test |
|---|---|
| Both receivers reject missing/wrong signature, unknown channel, replay, stale, expired | `test_google_webhook_rejection_matrix`, `test_dropbox_webhook_rejection_matrix`, `test_google_webhook_replay_and_stale_message_numbers`, `test_dropbox_replay_is_suppressed` |
| Dropbox HMAC is checked before `json.loads` runs | `test_dropbox_signature_is_verified_before_the_body_is_parsed` |
| Tokens encrypt, decrypt, and are absent in plaintext from the `.db` file | `test_token_encryption_round_trip` |
| No token reaches a log statement (AST scan) or `static/` / `firebase_public/` (content scan) | `test_no_sensitive_value_is_passed_to_a_logger`, `test_no_token_material_under_static_or_firebase_public` |
| A daily renewal cron cannot keep a 24-hour channel alive | `test_daily_cadence_is_provably_insufficient`, `test_six_hourly_cron_survives_one_missed_run_but_daily_does_not` |
| Everything imports and passes without the provider SDKs installed | `test_provider_sdks_are_not_installed`, `test_no_provider_module_imports_an_sdk_at_module_level` |

---

## What a human must do

### 0. Prerequisites

```bash
pip install google-api-python-client google-auth-oauthlib dropbox
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the generated key somewhere safe. Losing it makes every stored connection
unreadable — which is a re-consent, not data loss, but it is a re-consent for
every user.

```bash
firebase functions:secrets:set AGENT_AUTOFILL_TOKEN_KEY --project cairoai --data-file key.txt
```

Use `--data-file` with a real file. CLAUDE.md records that piping to
`--data-file -` fails on Windows PowerShell with "Secret Payload cannot be
empty".

### 1. Google Cloud project setup

- [ ] Enable the **Google Drive API** in the `cairoai` project.
- [ ] Configure the OAuth consent screen. Add **only**
      `https://www.googleapis.com/auth/drive.file`.
- [ ] Create an OAuth client of type **Web application**. Add the redirect URI
      you will use (e.g. `https://cairoai.web.app/oauth/google/callback`).
- [ ] Set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`.

**Confirm on the consent screen itself** that the permission shown is
"See, edit, create, and delete **only the specific Google Drive files you use
with this app**". If it says "See and download **all** your Google Drive
files", the scope has been widened somewhere and must be put back — see the
docstring at the top of `google_drive_provider.py`.

### 2. Domain verification (this one bites)

Drive push notifications require the callback domain to be **verified** and
**registered to the Cloud project**:

- [ ] Verify the domain in Google Search Console with the same Google account
      that owns the Cloud project.
- [ ] Add it under APIs & Services → Domain verification in the Cloud console.
- [ ] The callback must be `https://` with a certificate from a public CA.
      Self-signed will not work, and neither will `http://localhost`.

`files.watch` fails with `pushNotificationCallbackUrlUnauthorized` if this is
not done, and the error does not say which of the two steps is missing.

For local testing, an `ngrok`/Cloud Run HTTPS tunnel on a domain you can
verify is the usual route.

### 3. Google end-to-end

- [ ] Run the consent flow. Confirm a **refresh token** comes back
      (`OAuthToken.has_refresh_token` in the `connect()` result is `True`).
      If it is not, `access_type=offline` and `prompt=consent` were dropped.
- [ ] Pick a folder with the Google Picker. Note the folder id.
- [ ] `register_webhook(company_id, file_id=<folder id>)`. Confirm the
      response contains `resourceId` and an `expiration` **at most 86,400,000
      ms in the future**. If it comes back at ~3,600,000 ms, the `expiration`
      field was not sent.
- [ ] Confirm a `sync` notification arrives immediately, is accepted, and
      `process=False`.
- [ ] Add a file to the folder. Confirm an `add`/`change` notification arrives
      and is accepted with `process=True`.
- [ ] **Forge a notification**: replay the real one with the
      `X-Goog-Channel-Token` header changed by one character. It must be
      rejected with 403 `channel_token_mismatch`.
- [ ] **Replay a notification** verbatim. It must be rejected with
      `stale_or_replayed_message` and nothing enqueued.
- [ ] Call `list_changed_files()` and confirm it returns only files inside the
      picked folder — **not** other files in the Drive. This is the real test
      of the `drive.file` scope.
- [ ] `download_file()` one document. Confirm it lands in
      `data/provider_downloads/` (locally) and is **not** reachable at any
      `/static/...` URL.

### 4. Dropbox app setup

- [ ] Create the app in the Dropbox App Console with access type
      **App folder**, not Full Dropbox. **This is immutable after creation and
      cannot be checked from the token.** Getting it wrong grants read access
      to the user's entire Dropbox and requires recreating the app.
- [ ] Under Permissions, tick **only** `files.metadata.read` and
      `files.content.read`. Submit.
- [ ] Set `DROPBOX_APP_KEY` and `DROPBOX_APP_SECRET`.
- [ ] Add the webhook URI. Dropbox immediately sends a GET with `?challenge=`;
      the endpoint must echo it as `text/plain`. Confirm the console shows the
      URI as **Enabled**.

### 5. Dropbox end-to-end

- [ ] Run the consent flow. Confirm a refresh token comes back — if not,
      `token_access_type=offline` was dropped and the connection will die in a
      few hours.
- [ ] Confirm the connected folder is `/Apps/<AppName>/` and that
      `files_list_folder("")` shows **only** its contents.
- [ ] `register_webhook()` to take the initial cursor.
- [ ] Drop a file into the app folder. Confirm a POST arrives, verifies, and
      is enqueued.
- [ ] **Forge**: resend the same body with the `X-Dropbox-Signature` header
      altered by one character → 403 `signature_mismatch`.
- [ ] **Tamper**: resend the captured signature with one byte changed in the
      body → 403 `signature_mismatch`.
- [ ] **Replay**: resend the delivery verbatim within 15 minutes → 200
      `replayed_delivery`, nothing enqueued.
- [ ] Confirm `list_changed_files()` returns the new file and that the stored
      cursor advanced **after** the entries were collected, not before.

### 6. Renewal, observed over real time

- [ ] Register a channel. Record `expiration_at`.
- [ ] Schedule the cron every 6 hours:

      gcloud scheduler jobs create http autofill-channel-renewal \
          --project cairoai --schedule "0 */6 * * *" \
          --uri "https://.../api/autofill/webhooks/renew" \
          --http-method POST --oidc-service-account-email <sa>@cairoai.iam.gserviceaccount.com

- [ ] **Protect that endpoint.** OIDC as above, or a shared secret checked in
      the handler. An open renewal endpoint lets anyone churn every channel
      and burn the project's Drive quota.
- [ ] Wait 24+ hours. Confirm notifications still arrive after the original
      `expiration_at` has passed, and that the registry shows the original
      channel as `superseded` with a live replacement.
- [ ] Confirm the old channel was stopped (`channels.stop`), or that the
      warning was logged and duplicate deliveries stopped within 24 hours.

### 7. Before deploying

- [ ] **Replace the in-process queue.** `async_queue.deployment_readiness()`
      returns `ready=False` on Cloud Functions for a reason: CPU is throttled
      after the response returns, so background threads may never run. Wire
      Cloud Tasks or Pub/Sub via `async_queue.set_dispatcher(...)`.
- [ ] **Move provider state off /tmp.** The provider database resolves to
      `/tmp/dolo-db/` on Cloud Functions, which is per-instance and ephemeral.
      Connections would vanish on every cold start. Firestore, with the same
      Fernet ciphertext in a column, is the fix. This is the one piece of
      state in this codebase for which /tmp is not an acceptable degradation.
- [ ] Mount the router — it is deliberately not registered in `app.py`:

      from agent_autofill.webhooks.routes import router as autofill_webhooks
      app.include_router(autofill_webhooks)

- [ ] Re-run `python scripts/gen_functions_yaml.py`. The Firebase CLI reads
      `functions.yaml` **instead of** running discovery, so a route that is
      not represented there ships without its timeout and secret bindings.
- [ ] Verify the live site afterwards. "Deploy complete!" has lied before.

---

## Known gaps that no checklist item closes

* `X-Goog-Channel-Token` is the only authenticity signal Drive offers. If it
  is ever logged by an intermediate proxy, that channel is forgeable until it
  expires. Nothing in this codebase logs it; a load balancer that logs request
  headers would.
* The Dropbox replay cache is per-database. Two instances sharing no database
  will each accept the same replayed delivery once. Harmless today, because
  `list_folder/continue` is idempotent, but it stops being harmless if
  processing ever becomes stateful.
* `hmac.compare_digest` is constant-time; the SQLite lookup that precedes it
  is not. Channel-existence is therefore distinguishable by timing. Channel
  ids are 192-bit random strings, so this is not practically exploitable, but
  it is a real difference.

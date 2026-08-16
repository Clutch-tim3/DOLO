# POPIA data inventory — CairoAI

**Read this before the draft policies.** A privacy policy is a description of
what a system actually does. This is that description, derived from the schema
and verified against the live project on 16 August 2026, so the attorney
reviewing the drafts is working from facts rather than from what the drafts
claim.

Three findings below are the ones that need a decision, not just wording. They
are marked **⚠ NEEDS A DECISION**.

---

## 1. Personal information held

### Account records — `agent/auth.py`

| Table | Fields | Notes |
|---|---|---|
| `auth_users` | `username`, `password_hash`, `company_id`, `created_at`, `disabled` | `username` is an email address in practice. Password is PBKDF2-HMAC-SHA256 with a per-user salt — never stored in the clear. |
| `auth_sessions` | `selector`, `verifier_hash`, `user_id`, `created_at`, `expires_at`, `last_seen_at` | `last_seen_at` is activity data about an identified person. |
| `auth_device_tokens` | as above plus `label` | |
| `auth_login_failures` | `username`, failure records | Records attempts against usernames that may not exist. |

### Company profile — `agent/memory/company_store.py`

Personal information about identifiable natural persons:

- `directors` — names of directors
- `standard_contact_person`, `standard_phone`, `standard_cell`, `standard_fax`, `standard_email`
- `authorized_signatory_name`, `authorized_signatory_capacity`
- `postal_address`, `physical_address`

Company identifiers that are nonetheless regulated:

- `registration_number`, `csd_number`, `tax_reference_number`,
  `vat_registration_number`, `tax_compliance_pin`, `bbbee_level`

### Uploaded documents — the Compliance Vault

Five document types are collected: **Tax Clearance, B-BBEE Certificate, CIDB
Grading, CSD Report, CIPC Registration.**

These are the richest source of personal information in the system. A CSD
report and a CIPC registration certificate typically carry director names, ID
numbers, and residential addresses. The system stores the **original files**,
not just extracted fields.

### Conversation log — `company_store.log_conversation`

`user_message` and `agent_response`, stored in full, indefinitely. This is
free text a person typed. It can contain anything they chose to type, including
personal information about third parties the system never asked for.

---

## 2. ⚠ NEEDS A DECISION — special personal information

**B-BBEE certificates encode race-based ownership data.**

A B-BBEE certificate exists to record black ownership percentages. `pdf_parser`
reads exactly that:

    ownership_match = re.search(r'% Owned by black people...', text)

POPIA section 26 prohibits processing of special personal information —
including **race or ethnic origin** — unless a section 27 exemption applies.
Section 27(1)(a) permits it with the data subject's consent; there are also
grounds relating to compliance with an obligation of international public law
and, relevantly here, processing for historical/statistical purposes and
legislation-authorised processing.

CairoAI collects this because South African procurement law requires it, which
is likely to be defensible — but it is not automatic, and it is not something
the drafts should assert. **This needs the attorney's opinion specifically, not
a general POPIA review.**

Note the certificates are stored as files as well as parsed, so the underlying
special personal information is retained even where only `bbbee_level` is used.

---

## 3. ⚠ NEEDS A DECISION — everything is stored outside South Africa

Verified 16 August 2026:

    Cloud SQL cairoai-db        region: us-central1  (Iowa, USA)
    gs://cairoai-generated      location: US-CENTRAL1

Every account, company profile, uploaded document and conversation log is
stored in the United States.

Document text and chat messages are additionally sent to **Anthropic** (Claude
API, `agent/claude_client.py`) and scanned pages to **Google Cloud Vision**
(`agent_autofill/extraction/ocr.py`) for OCR. Both are processing outside South
Africa.

POPIA section 72 restricts transfers of personal information outside the
Republic. It permits them where the recipient is subject to a law, binding
corporate rules or a binding agreement providing an adequate level of
protection; or with the data subject's consent; or where the transfer is
necessary for performance of a contract with the data subject.

**Decisions needed:**

- Which section 72 ground is being relied on, and is it documented?
- Are Data Processing Agreements in place with Google Cloud and Anthropic?
  (Both publish standard terms; whether they have been accepted for this
  project needs checking, not assuming.)
- Should the database be moved to a South African region
  (`africa-south1`, Johannesburg) before launch? This is cheaper to decide now
  than after there is production data to migrate.

---

## 4. ⚠ NEEDS A DECISION — there is no deletion path

Searched for and **not found**: any `DELETE` against `conversation_log` or
`auth_users`.

What exists:

- `company_store.delete_company_profile(company_id)` — profile only
- `manage_users.py disable` — disables an account and revokes sessions, but
  **does not delete it**

So today CairoAI cannot fully honour:

- **Section 24** — correction or deletion of personal information that is
  inaccurate, irrelevant, excessive, or obtained unlawfully
- **Section 14** — records must not be retained longer than necessary for the
  purpose

A data subject asking to be deleted could not be satisfied without manual SQL.
Conversation logs in particular have no retention period at all.

**This is a build item, not a drafting one.** A policy promising deletion the
system cannot perform is worse than no policy.

---

## 5. Other POPIA obligations not yet addressed

| Obligation | Status |
|---|---|
| **Information Officer** (s55–56) | Not appointed. Registration with the Information Regulator is required. |
| **Security safeguards** (s19) | Partly. Passwords hashed, tokens split selector/verifier and compared in constant time, tenant isolation enforced and tested. **But Cloud SQL has no backups** (LAUNCH_PLAN B1), and s19 covers loss as well as unauthorised access. |
| **Breach notification** (s22) | No procedure exists. Error alerting was only wired up in A12 and is not yet applied. |
| **Consent capture** | No mechanism. There is no signup flow, so no point at which consent is recorded. |
| **Retention schedule** (s14) | None defined for any data class. |

---

## 6. What is genuinely in good shape

Worth stating so the review is proportionate:

- Passwords are PBKDF2-HMAC-SHA256 with per-user salts, never stored in clear.
- Session and device tokens are `<selector>.<verifier>` with the verifier stored
  as a SHA-256 digest and compared with `hmac.compare_digest`.
- Tenant isolation is enforced at every company-aware route and covered by
  tests that fail if a filter is removed (A1, A2).
- Documents are served through an ownership check, and exports are verified
  against their review record before a file is handed over.
- No signature is ever auto-applied and no price auto-inserted; every
  auto-filled value is confirmed by a named person, and since A7 the record
  says which person, tamper-evidently.

---

## 7. Provenance

Everything above was read from the code and the live project, not inferred:

- Schema: `agent/auth.py`, `agent/memory/company_store.py`
- Region: `gcloud sql instances describe` / `gcloud storage buckets describe`
- Absence of deletion paths: grep across `agent/` for `DELETE FROM`
- Processors: `agent/claude_client.py`, `agent_autofill/extraction/ocr.py`

Re-verify before the attorney meeting if this is more than a few weeks old.

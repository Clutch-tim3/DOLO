# DRAFT — NOT LEGALLY REVIEWED. DO NOT PUBLISH.

> This is a drafting aid for the attorney doing the B6 review, not a policy.
> It is deliberately **not** in `static/` or `firebase_public/`: a draft privacy
> policy sitting in the directory Hosting serves is one `sync_firebase_public.py`
> away from being live, and a published policy is a representation to data
> subjects and to the Information Regulator.
>
> **Read `POPIA_DATA_INVENTORY.md` first.** Three items there need decisions
> before this can be finalised, and two of them are things the system cannot
> currently do. Passages depending on those are marked `[DECISION NEEDED]`.
>
> Placeholders in `[SQUARE BRACKETS]` must be filled by a person.

---

# Privacy Policy

**CairoAI** · Last updated: `[DATE]`

## 1. Who we are

CairoAI is operated by `[REGISTERED ENTITY NAME]`, registration number
`[REG NUMBER]`, of `[REGISTERED ADDRESS]`, South Africa ("we", "us").

We are the responsible party for the personal information described here, as
that term is used in the Protection of Personal Information Act 4 of 2013
("POPIA").

**Information Officer:** `[NAME]`, `[EMAIL]`.
`[DECISION NEEDED — an Information Officer must be appointed and registered
with the Information Regulator under POPIA s55–56. This has not been done.]`

## 2. What CairoAI does

CairoAI helps South African suppliers respond to government tenders. It reads
tender documents, checks eligibility against your company's details,
pre-fills draft bid forms for a person to review, and drafts quotations.

**CairoAI produces drafts only.** It never applies a signature, never inserts a
price, and never answers a declaration on your behalf. Every value it fills is
confirmed by a named person before a document can be exported.

## 3. What we collect

**Account information.** The email address you sign in with, a securely hashed
form of your password, and records of when you signed in and from which device.
We never store your password itself.

**Company information you give us.** Your company name, registration number,
CSD supplier number, B-BBEE level, tax reference number, VAT number, tax
compliance PIN, physical and postal addresses, industry, and the province and
municipality you operate in.

**Details of people associated with your company.** Director names, the name
and capacity of your authorised signatory, and contact details for your
standard contact person — name, email, telephone, mobile and fax.

**Documents you upload.** Tax Clearance certificates, B-BBEE certificates, CIDB
grading certificates, CSD reports, CIPC registration documents, and any tender
documents you ask us to work on. We keep the original files, not only the
information we read from them. These documents often contain further personal
information about your directors, including identity numbers and addresses.

**Your conversations with the assistant.** The messages you send and the
replies you receive are stored so the assistant can refer back to them.

**We do not** use cookies for advertising or analytics. The only cookie we set
is the one that keeps you signed in.

## 4. Special personal information

A B-BBEE certificate records ownership by black people. That is information
about race, which POPIA treats as special personal information and restricts
under section 26.

We process it because South African preferential procurement law
— the Preferential Procurement Policy Framework Act and its regulations —
requires a bidder's B-BBEE status to be established in order to bid.

`[DECISION NEEDED — which section 27 ground is relied on must be stated
explicitly, and confirmed by the attorney. Do not publish this section as
drafted.]`

## 5. Why we process it, and on what basis

| Purpose | Basis |
|---|---|
| Providing the service you signed up for | Performance of a contract with you (s11(1)(b)) |
| Checking your eligibility for a tender | Performance of a contract with you |
| Establishing B-BBEE status | `[DECISION NEEDED — see section 4]` |
| Keeping your account secure, and detecting abuse | Our legitimate interests (s11(1)(f)) |
| Meeting our own legal obligations | s11(1)(c) |

We do not sell your personal information, and we do not use it to make
automated decisions that produce a legal effect for you. The win-probability
estimate CairoAI shows is advisory and does not decide anything.

## 6. Who we share it with

**Anthropic** — text from your documents and your messages to the assistant are
sent to Anthropic's Claude API so the assistant can read and respond to them.

**Google Cloud** — CairoAI runs on Google Cloud. Scanned pages are sent to
Google Cloud Vision to be read.

Both act as operators processing on our instructions. We do not share your
information with anyone else, and we never share it with other CairoAI
customers — each company's data is separated and that separation is enforced in
the software and tested.

## 7. Where your information is stored

**Your information is currently stored in the United States** (Google Cloud's
`us-central1` region, Iowa), and is processed there and wherever Anthropic
processes API requests.

POPIA section 72 restricts transfers of personal information outside South
Africa. We rely on `[DECISION NEEDED — the s72 ground has not been determined.
Options include the recipient being subject to binding agreements providing
adequate protection, or the transfer being necessary to perform our contract
with you. Confirm which, and confirm the Data Processing Agreements with
Google Cloud and Anthropic are in place.]`

## 8. How long we keep it

`[DECISION NEEDED — no retention schedule exists. POPIA s14 requires that
records not be kept longer than necessary for the purpose. A period must be
set for each of: account records, company profiles, uploaded documents,
generated drafts, and conversation logs.]`

## 9. Your rights

Under POPIA you may:

- ask what personal information we hold about you, and get a copy;
- ask us to correct it if it is wrong, or delete it if it is inaccurate,
  irrelevant, excessive, out of date, or was obtained unlawfully;
- object to processing on legitimate-interests grounds;
- complain to the Information Regulator.

To exercise any of these, contact `[EMAIL]`. We will respond within
`[TIMEFRAME]`.

`[DECISION NEEDED — CairoAI cannot currently delete a user account or a
conversation log; only a company profile can be deleted, and accounts can be
disabled but not removed. This section must not promise deletion until the
system can perform it. Either build the deletion path or narrow this
paragraph.]`

**The Information Regulator (South Africa)**
JD House, 27 Stiemens Street, Braamfontein, Johannesburg, 2001
complaints.IR@inforegulator.org.za

## 10. How we protect it

Passwords are hashed with PBKDF2-HMAC-SHA256 and a per-user salt, so we cannot
read them. Session and device tokens are stored as digests, not in usable form.
Each company's data is separated from every other company's, enforced in the
software and covered by automated tests. Exported documents are verified
against their review record before we hand them over.

`[DECISION NEEDED — POPIA s19 requires safeguards against loss as well as
unauthorised access. The database currently has no backups (LAUNCH_PLAN B1).
Do not publish a security section until that is fixed.]`

## 11. If something goes wrong

If personal information is accessed or acquired by an unauthorised person, we
will notify the Information Regulator and affected data subjects as required by
POPIA section 22.

`[DECISION NEEDED — no breach notification procedure exists.]`

## 12. Changes

We will post any change here and update the date at the top. If a change
materially affects how we use your personal information, we will tell you
directly.

## 13. Contact

`[EMAIL]` · `[POSTAL ADDRESS]`

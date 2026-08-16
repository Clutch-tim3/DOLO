# UI brief — Tender Autofill Packs page

Redesign the Tender Autofill Packs screen in CairoAI. It is the page a user
lands on after uploading tender documents, where they review what the agent
filled in and release a draft.

Repo: `C:\Users\Thabang\.gemini\antigravity\scratch\DOLO`
Files: `static/autofill_packs.js` and `static/workspace.html`
(and their mirrors in `firebase_public/` — see constraints)

## Start here: the page has no stylesheet

Before assuming the design is bad, check this — it changes the whole job:

```
$ grep -oE "'ap-[a-z0-9 -]+'" static/autofill_packs.js | sort -u | wc -l
   57 distinct classes emitted by the JS

$ grep -rlE '\.ap-(doc|item|link|export|values)' --include='*.css' --include='*.html' static/ firebase_public/
   (nothing)
```

**Fifty-seven class names, zero CSS rules.** No stylesheet, no injected
`<style>`, nothing in `workspace.html`. The JS builds a correct semantic DOM
and no styling was ever written for it.

Every complaint about this page traces to that one fact:

- **"White blocks"** — the JS creates 11 native `<button>` elements and one
  `<a>`. With no CSS they render with default operating-system chrome: grey-white
  boxes with beveled borders. Nothing is drawing them; the browser is.
- **"Overwhelming with text"** — with no type scale, every string renders at the
  same size and weight, so a warning, a field label, a hint and a value all shout
  equally. There is no hierarchy to skim.
- **"Preview PDF button broken"** — `ap-preview-link` has no rule at all, so
  PREVIEW DRAFT renders as a default underlined link rather than a button.

So the task is not tidying CSS. It is writing the stylesheet this page never
had, and cutting the copy down to what a person actually needs.

## What to build

### 1. A stylesheet for all 57 classes

The full list, from the JS:

```
ap-ack ap-ack-btn ap-ack-hint ap-ack-note ap-cell-actions ap-cell-count
ap-cell-date ap-cell-name ap-cell-status ap-cell-sub ap-chip ap-doc
ap-doc-count ap-doc-head ap-doc-name ap-download ap-draft-banner ap-export
ap-export-list ap-export-result ap-export-why ap-file ap-file-name
ap-file-none ap-file-remove ap-file-size ap-input ap-item ap-item-done
ap-item-key ap-item-label ap-item-meta ap-item-note ap-item-reason
ap-item-top ap-link-btn ap-meter ap-meter-fill ap-msg ap-pill ap-preview-link
ap-progress ap-progress-count ap-row ap-status-actions ap-status-head
ap-status-line ap-table ap-table-wrap ap-tick ap-value-check ap-value-label
ap-value-row ap-value-text ap-value-value ap-values ap-values-head
ap-values-note
```

Every native `<button>`, `<input>` and `<a>` needs an explicit style — that is
what removes the white blocks. Do not leave any element on browser defaults.

### 2. Cut the copy hard

The page currently explains itself in full sentences at every step. Some of that
is load-bearing and some is noise. Examples of what is on screen now:

- *"Nothing in this pack is signed, priced or declared on your behalf.
  Acknowledging a field records that a person looked at it — it does not fill it
  in."*
- *"Tick each value you have read, then confirm. Export is held until they are
  confirmed."*
- *"One field, one note. A blanket confirmation is refused."*
- *"Every flagged field on this pack has been acknowledged. It is ready to
  export."*
- *"Pre-filling finished. Every field the agent would not answer is listed
  below."*

Reduce this to roughly a third. Rules of thumb:

- One short line of guidance per section, not a paragraph. Most of these
  sentences explain a rule the interface should make obvious by its shape.
- A status the UI can *show* should not also be *narrated* — a progress meter and
  "Counting the documents in this pack…" are the same information twice.
- Keep the meaning, lose the explanation. "Drafts only — nothing is signed or
  priced" carries the whole first bullet above.
- Error messages stay specific. Do not compress "The server refused that (HTTP
  403)" into "Something went wrong".

### 3. Fix PREVIEW DRAFT

It should read as a real, secondary button sitting beside the document name —
clearly distinct from the primary EXPORT action. It opens the filled PDF in a new
tab. It is not an export and must not look like one.

### 4. Make it scannable

This is a review screen operated at speed, not a document read top to bottom.
A user should be able to tell in about two seconds: which pack, how far along,
what needs them, and what is blocked. Surface the summary before the detail, and
encode state in form as well as words — a chip, a meter, a status colour — so
what needs attention reads at a glance.

## Design direction

Match the product it lives in. Do not invent a new visual language.

- **Accent:** gold `#c5a880` — already the app's accent, used for the active nav
  item, `.btn-gold`, and headings.
- **Ground:** the dark console the rest of the app uses. The sign-in overlay is
  the cleanest reference in the codebase: background `#0b0b0d`, card `#141417`,
  border `#2a2a30`, text `#f5f5f7`, muted `#8b8b95` (`static/auth.js`).
- **Type:** Outfit, Plus Jakarta Sans, Inter and JetBrains Mono are already
  loaded. Use the mono for field keys, file sizes and counts — anything that is
  data rather than prose.
- Semantic colour for state (needs review / blocked / done) is separate from the
  gold accent and should not compete with it.

## Hard constraints

**Do not weaken the review gate.** This is the safeguard that makes the whole
product defensible, and a UI simplification is exactly how it gets lost:

- Each flagged field is acknowledged individually, with its own note.
- A blanket "acknowledge all" is refused by design. Do not add one.
- Auto-filled values are ticked individually and confirmed before export.
- Export stays blocked until both are done.
- Advisory items are shown but never block — they are notes about extraction, not
  decisions about the form.

Make it *look* simpler. Do not make it *do* less.

**Mirror both copies.** `static/` and `firebase_public/` are mirrors. Change both
or the deployed site will not match.

**Bump the asset version.** `workspace.html` loads
`static/autofill_packs.js?v=N`. If you touch that JS you must increment `N`, or
every browser serves its cached copy and your work is invisible. This has already
made a working fix look broken three separate times.

## Done when

- No element renders with default browser chrome
- PREVIEW DRAFT looks and behaves like a secondary button
- On-screen copy is roughly a third of what it is now, with no rule lost
- A reviewer can tell at a glance what needs them
- Both file copies updated and the version pin bumped
- Verified by loading `https://cairoai.web.app/workspace` signed in as
  `Test` / `Test12345678`, opening a pack, and looking at it — not by reading
  the diff

Test account has one pack with a filled scanned SBD 1: 7 confirmed values and
one advisory note, which is the state worth designing against.

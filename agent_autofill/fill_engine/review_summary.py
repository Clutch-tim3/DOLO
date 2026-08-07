"""
The review summary.

A separate document, never part of the form. It answers one question for the
reviewer: what did the agent touch, and what still needs a human?

Two distinctions the summary must keep visible, because collapsing them is how
a reviewer starts trusting the output:

  * "blocked by design" (a signature, a declaration, a price) is NOT the same
    as "extraction was unsure". The first will never be automated. The second
    might be, once matching improves. They are separate sections.
  * Nothing here says "ready". The heading states review is required, and the
    export gate — not this file — decides whether a document has been through
    confirmation.
"""

from __future__ import annotations

import html
from datetime import datetime

from .document_filler import FillResult

CATEGORY_TITLES = {
    "blocked": "Left blank by design — these require you",
    "low_confidence": "Not filled — the match was not confident enough",
    "no_data": "Not filled — nothing on file yet",
    "unmatched": "Not recognised — no matching field in your profile",
}

CATEGORY_NOTES = {
    "blocked": ("These are never automated. A signature, a declaration of interest "
                "or a price is yours to give."),
    "low_confidence": ("The label did not match a known field closely enough to fill "
                       "safely, so it was left alone rather than guessed."),
    "no_data": "Add these to your company profile and they will fill next time.",
    "unmatched": ("The agent could not tell what these fields are asking for. "
                  "They may not be company facts at all."),
}


def render_html(result: FillResult, company_name: str = "") -> str:
    e = html.escape
    filled_n, total = len(result.filled), result.fillable_total

    by_cat: dict[str, list] = {}
    for s in result.skipped:
        by_cat.setdefault(s.category, []).append(s)

    rows = "\n".join(
        f"""        <tr>
          <td class="lbl">{e(f.label)}</td>
          <td class="val">{e(f.value)}{' <span class="lc">check this</span>' if f.low_confidence else ''}</td>
          <td class="src">{e(f.source)}</td>
          <td class="loc">{e(f.location)}</td>
        </tr>"""
        for f in result.filled
    ) or '        <tr><td colspan="4" class="none">Nothing was filled automatically.</td></tr>'

    sections = []
    for cat in ("blocked", "low_confidence", "no_data", "unmatched"):
        items = by_cat.get(cat)
        if not items:
            continue
        lis = "\n".join(
            f'          <li><span class="f">{e(i.label)}</span>'
            f'<span class="why">{e(i.reason)}</span>'
            f'<span class="loc">{e(i.location)}</span></li>'
            for i in items
        )
        sections.append(f"""      <section class="grp {cat}">
        <h2>{e(CATEGORY_TITLES[cat])} <span class="n">{len(items)}</span></h2>
        <p class="note">{e(CATEGORY_NOTES[cat])}</p>
        <ul>
{lis}
        </ul>
      </section>""")

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Autofill review — {e(company_name or 'draft')}</title>
<style>
  :root {{ --gold:#c5a880; --ink:#e4e4e7; --muted:#999; --bg:#000; --card:rgba(255,255,255,.03); }}
  body {{ margin:0; padding:40px 28px; background:var(--bg); color:var(--ink);
         font-family:'Plus Jakarta Sans',system-ui,sans-serif; font-size:14px; line-height:1.6; }}
  .wrap {{ max-width:900px; margin:0 auto; }}
  .kicker {{ font-size:10.5px; letter-spacing:.24em; text-transform:uppercase; color:var(--gold); }}
  h1 {{ font-family:Cinzel,serif; font-size:26px; margin:10px 0 6px; letter-spacing:.04em; }}
  .banner {{ margin:20px 0 28px; padding:14px 16px; border-radius:4px;
            border:1px solid rgba(197,168,128,.4); background:rgba(197,168,128,.09); }}
  .banner strong {{ color:var(--gold); }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:30px; }}
  th,td {{ text-align:left; padding:9px 10px; border-bottom:1px solid rgba(255,255,255,.08);
          vertical-align:top; font-size:13px; }}
  th {{ font-size:10px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); }}
  td.val {{ background:rgba(197,168,128,.10); }}
  .lc {{ font-size:10px; color:var(--gold); letter-spacing:.1em; text-transform:uppercase; margin-left:6px; }}
  .src,.loc {{ color:var(--muted); font-size:11.5px; }}
  .none {{ color:var(--muted); font-style:italic; }}
  .grp {{ margin-bottom:26px; }}
  .grp h2 {{ font-size:12px; letter-spacing:.12em; text-transform:uppercase; color:var(--ink);
            margin:0 0 4px; display:flex; gap:10px; align-items:center; }}
  .grp .n {{ font-family:'JetBrains Mono',monospace; font-size:10px; color:var(--gold);
            border:1px solid rgba(197,168,128,.35); border-radius:999px; padding:1px 8px; }}
  .grp.blocked h2 {{ color:var(--gold); }}
  .note {{ color:var(--muted); font-size:12px; margin:0 0 10px; }}
  ul {{ list-style:none; margin:0; padding:0; }}
  li {{ padding:9px 12px; margin-bottom:6px; border-radius:3px; background:var(--card);
       border:1px solid rgba(255,255,255,.07); display:flex; flex-wrap:wrap; gap:4px 14px; }}
  .f {{ font-weight:600; }}
  .why {{ color:var(--muted); font-size:12.5px; flex:1 1 300px; }}
  footer {{ margin-top:34px; padding-top:16px; border-top:1px solid rgba(255,255,255,.08);
           color:var(--muted); font-size:11.5px; }}
  @media (max-width:700px) {{ body {{ padding:24px 14px; }} th:nth-child(4),td:nth-child(4) {{ display:none; }} }}
</style>
<div class="wrap">
  <p class="kicker">Agent Autofill — draft</p>
  <h1>Review before you use this</h1>

  <div class="banner">
    <strong>{filled_n} of {total} fillable fields completed automatically</strong> — review required before use.
    Nothing here is submission-ready until you have confirmed every flagged field.
  </div>

  <h2 class="kicker">Filled automatically</h2>
  <table>
    <thead><tr><th>Field</th><th>Value written</th><th>Source</th><th>Where</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>

{chr(10).join(sections)}

  <footer>
    Source document: {e(result.source_path)}<br>
    Draft written to: {e(result.output_path)}<br>
    Generated {datetime.now().strftime('%d %B %Y, %H:%M')}. Agent Autofill produces drafts only.
    No signature has been applied and no pricing has been entered.
  </footer>
</div>
"""

"""
Line-item pricing.

THERE IS NO PRICE SOURCE. Every item is flagged for a human.

This module previously returned invented figures. An item whose description
contained "paper" came back as R85.50, `HIGH_CONFIDENCE`, sourced to
`https://www.makro.co.za/stationery/paper`; "pen" came back as R120.00 from
Waltons. No network request was ever made — the docstring said it simulated a
web search and that production used a SERP API, but this was production.

Three things made that worse than an obvious stub:

  1. The figures carried a REAL retailer's URL as provenance, so a reader had
     every reason to believe someone had checked.
  2. `HIGH_CONFIDENCE` is not in `quote_audit_log.FLAGGED_STATUSES`, so a paper
     line item passed the finalisation gate with nobody confirming it.
  3. The number reached the line total, the subtotal, the VAT calculation and
     a PDF headed "INVITATION FOR BID — RESPONSE DOCUMENT".

A fabricated price on a government bid is not a cosmetic problem, and the
agent's own system prompt has always said "Never fabricate a price. If
uncertain, flag for manual review."

So that is what happens now. Every item returns no price, no source, and
`MANUAL_REVIEW_REQUIRED`. The quotation gate already handles that correctly:
the draft shows each line as TBC, excludes them from the subtotal, prints an
INCOMPLETE DRAFT warning, and refuses to finalise until a person supplies each
figure through `resolve_quote_item`.

WHEN A REAL SOURCE IS ADDED: return a price only when the lookup actually
succeeded, put the URL it actually came from in `source_url`, and reserve
`HIGH_CONFIDENCE` for a genuine match on the same item — not a substring hit on
one word of the description.
"""

from datetime import datetime

#: What every lookup returns until a real price source exists. Named so the
#: reason survives a skim of the call site.
NO_PRICE_SOURCE_CONFIGURED = "MANUAL_REVIEW_REQUIRED"


def search_price(description: str) -> dict:
    """
    Look up a price for one line item.

    Always returns "needs a human". `description` is unused, deliberately — a
    price that varies with the wording of an item while nothing is being looked
    up is exactly the behaviour this replaced.
    """
    return {
        "price": None,
        "source_url": None,
        "timestamp": datetime.now().isoformat(),
        "price_status": NO_PRICE_SOURCE_CONFIGURED,
        "retailer_name": None,
        # Carried through so the UI and the agent can say why a line is blank,
        # rather than implying a lookup happened and found nothing.
        "price_note": (
            "No automatic price source is configured. This line needs a figure "
            "from you before the quote can be finalised."
        ),
    }


def get_prices_for_items(items: list) -> list:
    priced_items = []
    for item in items:
        price_data = search_price(item["description"])
        priced_item = {**item, **price_data}
        # `total` stays None so quote_builder shows TBC and leaves the line out
        # of the subtotal, rather than silently contributing zero.
        priced_item["total"] = None
        priced_items.append(priced_item)
    return priced_items

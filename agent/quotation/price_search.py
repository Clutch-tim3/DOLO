import random
from datetime import datetime

def search_price(description: str) -> dict:
    """
    Simulates a web search for SA retailer pricing.
    In production, this uses an SERP API + claude-sonnet-5.
    """
    timestamp = datetime.now().isoformat()
    
    # Mock search logic
    desc_lower = description.lower()
    
    if "paper" in desc_lower:
        return {
            "price": 85.50,
            "source_url": "https://www.makro.co.za/stationery/paper",
            "timestamp": timestamp,
            "price_status": "HIGH_CONFIDENCE",
            "retailer_name": "Makro SA"
        }
    elif "pen" in desc_lower:
        return {
            "price": 120.00,
            "source_url": "https://www.waltons.co.za/pens",
            "timestamp": timestamp,
            "price_status": "LOW_CONFIDENCE", # Maybe the box size differs
            "retailer_name": "Waltons"
        }
    elif "file" in desc_lower:
        return {
            "price": None,
            "source_url": None,
            "timestamp": timestamp,
            "price_status": "MANUAL_REVIEW_REQUIRED",
            "retailer_name": None
        }
    else:
        return {
            "price": None,
            "source_url": None,
            "timestamp": timestamp,
            "price_status": "MANUAL_REVIEW_REQUIRED",
            "retailer_name": None
        }

def get_prices_for_items(items: list) -> list:
    priced_items = []
    for item in items:
        price_data = search_price(item["description"])
        priced_item = {**item, **price_data}
        if priced_item["price"]:
            priced_item["total"] = priced_item["price"] * priced_item["quantity"]
        else:
            priced_item["total"] = None
        priced_items.append(priced_item)
    return priced_items

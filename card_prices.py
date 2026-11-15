"""
Prices for the set overview (missing_cards.html)
------------------------------------------------
Each missing card on the overview page shows three prices from
price_search.py's last run, for pricing a card quickly at a card show:

- **Pulse**: PulseAPI's UK market price (price_guide.csv).
- **CM**: Cardmarket's trend price, converted with the rate that run used,
  or its cheapest-copy "from" price when the price file has no trend
  (price_guide.csv).
- **Low**: the cheapest ungraded listing any shop had (offers.csv).

The prices go in the page as one JSON block (see prices_block) that the
page's script spreads over the cards. missing_cards.py fills it from
whatever CSVs are there when it writes the page, and price_search.py
replaces just that block once it has fresh prices (see refresh_html), so
the page doesn't have to be rebuilt from a RareCandy export to update them.
"""
import csv
import datetime
import json
import os
import re
from decimal import Decimal, InvalidOperation

OFFERS_NAME = "offers.csv"
GUIDE_NAME = "price_guide.csv"
BLOCK_ID = "card-prices"
_BLOCK = re.compile(r'<script id="' + BLOCK_ID + r'" type="application/json">.*?</script>', re.S)
_TREND = re.compile(r"\btrend ([A-Z]{3}) ([\d.]+)")


def _rows(path):
    """(rows, price column) of a price_search.py CSV, or ([], None)."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            col = next((c for c in reader.fieldnames or [] if c.startswith("Price (")), None)
            return (list(reader), col) if col else ([], None)
    except FileNotFoundError:
        return [], None


def _money(text):
    try:
        return Decimal(text)
    except (InvalidOperation, TypeError):
        return None


def _mtime(path):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return None


def _shop_names():
    """Marketplace id -> display name, from the installed plugins."""
    try:
        import marketplaces
        return {pid: p.name for pid, p in marketplaces.discover().items()}
    except Exception:  # a broken plugin shouldn't stop the page being written
        return {}


def read_card_prices(offers_path, guide_path, names=None):
    """{"asof": "15 Nov, 07:31" or None, "currency": "GBP",
        "cards": {card id: {"pulse": [price, url], "cm": [price, url, hover],
                            "low": [price, url, hover]}}}
    from price_search.py's CSVs; a card only has the prices that were found.
    Prices are strings with two decimals."""
    names = _shop_names() if names is None else names
    cards, currency = {}, None

    def put(card_id, key, price, value):
        old = cards.setdefault(card_id, {}).get(key)
        if old is None or price < Decimal(old[0]):
            cards[card_id][key] = [f"{price:.2f}", *value]

    guide, gcol = _rows(guide_path)
    for r in guide:
        price = _money(r[gcol])
        if price is None:
            continue
        currency = gcol[len("Price ("):-1]
        if r["Marketplace"] == "pulseapi":
            put(r["TCGdex Card ID"], "pulse", price, [r["URL"]])
        elif r["Marketplace"] == "cardmarket":
            hover = f"Cheapest copy {currency} {price:.2f} (any language or condition)"
            m, listed = _TREND.search(r["Listing Title"] or ""), _money(r["Listed Price"])
            if m and listed and m.group(1) == r["Listed Currency"]:
                trend = (Decimal(m.group(2)) * price / listed).quantize(Decimal("0.01"))
                hover = f"Trend {m.group(1)} {m.group(2)} · {hover}"
                price = trend
            else:
                hover = f"No trend price · {hover}"
            put(r["TCGdex Card ID"], "cm", price, [r["URL"], hover])

    offers, ocol = _rows(offers_path)
    counts = {}
    for r in offers:
        price = _money(r[ocol])
        if price is None or r["Grade"]:
            continue
        currency = ocol[len("Price ("):-1]
        counts[r["TCGdex Card ID"]] = counts.get(r["TCGdex Card ID"], 0) + 1
        shop = names.get(r["Marketplace"], r["Marketplace"])
        hover = shop + (f" · {r['Condition']}" if r["Condition"] else "")
        put(r["TCGdex Card ID"], "low", price, [r["URL"], hover])
    for card_id, n in counts.items():
        if n > 1:
            cards[card_id]["low"][2] += f" · cheapest of {n} listings"

    times = [t for t in (_mtime(guide_path) if guide else None,
                         _mtime(offers_path) if offers else None) if t]
    return {"asof": f"{max(times):%d %b, %H:%M}" if times else None,
            "currency": currency or "GBP", "cards": cards}


def prices_for(folder):
    """read_card_prices for the CSVs in `folder` (price_search.py's default
    names)."""
    return read_card_prices(os.path.join(folder, OFFERS_NAME), os.path.join(folder, GUIDE_NAME))


def prices_block(prices):
    """The page's prices as a JSON <script> block, safe inside HTML."""
    data = json.dumps(prices, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script id="{BLOCK_ID}" type="application/json">{data}</script>'


def refresh_html(path, prices):
    """Swap the prices in an overview page written by missing_cards.py for
    `prices`. False if there's no page, or it's too old to have a prices
    block."""
    try:
        with open(path, encoding="utf-8") as f:
            page = f.read()
    except FileNotFoundError:
        return False
    block = prices_block(prices)
    new, n = _BLOCK.subn(lambda _: block, page, count=1)
    if not n:
        return False
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, path)
    return True

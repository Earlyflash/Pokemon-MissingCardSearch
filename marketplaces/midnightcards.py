"""
Midnight Cards (midnightcards.co.uk), a UK WooCommerce shop with prices in
GBP. This plugin reads its Japanese Pokémon singles.

The shop's pages sit behind a Cloudflare browser check, but WooCommerce's
public Store API (/wp-json/wc/store/v1/products, the read-only endpoint the
shop's own product blocks use) answers plain requests. It can filter by the
shop's "Brand" attribute and serves 100 products a page, and the Japanese
brand is a few hundred cards (about 5 requests), so the plugin reads it once
per run and matches missing cards locally.

Every product title carries the set code and number, in one of two styles:

    Bouffalant — MEGA Dream ex (M2a) 140/193
    Tangrowth – Mega Symphonia (M1S) – 002/063 – Japanese

A card matches on number plus set: the code in brackets equal to its TCGdex
set id, or the product's "Pokémon Set Name" attribute naming its set (by
name, or through totalcards.SET_IDS).

Each product is one print ("Normal", "Holo", "Poké Ball Reverse Holo"),
named by its "Card Type" attribute. A few are variable products with one
variation per print; when those prints differ in price, each variation is
read on its own for its price and stock. The shop doesn't state a condition
for these cards, so condition is left unknown.
"""
import html
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import normalize_number, normalize_text
from marketplaces.totalcards import SET_IDS

SHOP = "https://midnightcards.co.uk"
STORE_API = SHOP + "/wp-json/wc/store/v1/products"
BRAND = "pokemon-japanese"
PRODUCTS_URL = (STORE_API + "?attributes%5B0%5D%5Battribute%5D=pa_brand"
                "&attributes%5B0%5D%5Bslug%5D%5B%5D={brand}&per_page={limit}&page={page}")
VARIATION_URL = STORE_API + "/{id}"
PAGE_SIZE = 100  # the Store API's maximum
MAX_PAGES = 100  # safety stop

DASH = r"\s+[—–-]\s+"
TITLE = re.compile(
    rf"^\s*(?P<name>.+?){DASH}(?P<set>.+)\s+\((?P<code>[^()]+)\)(?:{DASH}|\s+)"
    rf"(?P<number>[A-Za-z0-9-]+)(?:/(?P<total>[A-Za-z0-9-]+))?(?:{DASH}Japanese)?\s*$")


def parse_title(title):
    """{name, set_name, code, number} from a product title, or None if it
    doesn't follow the shop's pattern."""
    m = TITLE.match(html.unescape(title or ""))
    if not m:
        return None
    return {"name": m.group("name"), "set_name": m.group("set"),
            "code": normalize_code(m.group("code")), "number": normalize_number(m.group("number"))}


def attribute(product, taxonomy):
    """The names of a product's terms for one attribute, e.g. its set name."""
    for attr in product.get("attributes") or []:
        if attr.get("taxonomy") == taxonomy:
            return [html.unescape(t.get("name") or "") for t in attr.get("terms") or []]
    return []


def matches(parsed, set_names, card):
    """True if a product is this card: same number, and the title's set code
    or a set-name attribute is the card's set."""
    if not parsed or parsed["number"] != normalize_number(card.local_id):
        return False
    set_id = normalize_code(card.set_id)
    if parsed["code"] == set_id:
        return True
    card_set = normalize_text(card.set_name)
    for name in set_names:
        name = normalize_text(name)
        if (card_set and name == card_set) or normalize_code(SET_IDS.get(name)) == set_id:
            return True
    return False


def price(prices):
    """A Store API price block ("249", minor unit 2) as Decimal("2.49")."""
    return Decimal(prices["price"]).scaleb(-int(prices.get("currency_minor_unit", 2)))


def quantity(product):
    stock = product.get("low_stock_remaining")
    if stock is None:
        stock = ((product.get("extensions") or {}).get("gtm4wp") or {}).get("item", {}).get("stocklevel")
    return stock if isinstance(stock, int) else None


class MidnightCards(Marketplace):
    id = "midnightcards"
    name = "Midnight Cards"
    languages = {"ja"}     # the Japanese brand only
    min_interval = 1.0

    def catalogue(self, ctx):
        """[(product, parsed title)] for the Japanese brand, fetched once per run."""
        if "products" not in ctx.state:
            products, seen = [], set()
            for page in range(1, MAX_PAGES + 1):
                rows = ctx.fetch(PRODUCTS_URL.format(brand=BRAND, limit=PAGE_SIZE, page=page), as_json=True)
                for row in rows:
                    if row.get("id") not in seen:
                        seen.add(row.get("id"))
                        products.append(row)
                if len(rows) < PAGE_SIZE:
                    break
            ctx.debug(f"{len(products)} Japanese Pokémon products")
            ctx.state["products"] = [(p, parse_title(p.get("name"))) for p in products]
        return ctx.state["products"]

    def copies(self, product, ctx):
        """(print, product-or-variation) pairs in stock for one product."""
        if not product.get("is_in_stock"):
            return []
        variations = product.get("variations") or []
        if len(variations) > 1 and (product.get("prices") or {}).get("price_range"):
            out = []
            for variation in variations:
                row = ctx.fetch(VARIATION_URL.format(id=variation["id"]), as_json=True)
                if row.get("is_in_stock"):
                    out.append((re.sub(r"^[^:]*:\s*", "", html.unescape(row.get("variation") or "")), row))
            return out
        return [(", ".join(attribute(product, "pa_card-type")), product)]

    def search_set(self, cards, ctx):
        by_number = {}
        for card in cards:
            by_number.setdefault(normalize_number(card.local_id), []).append(card)
        offers = []
        for product, parsed in self.catalogue(ctx):
            candidates = by_number.get(parsed["number"], []) if parsed else []
            set_names = attribute(product, "pa_pokemon-set-name")
            hits = [c for c in candidates if matches(parsed, set_names, c)]
            if not hits:
                continue
            for print_name, row in self.copies(product, ctx):
                title = html.unescape(product.get("name") or "")
                if print_name:
                    title += f" ({print_name})"
                for card in hits:
                    offers.append(Offer(
                        marketplace=self.id,
                        card_id=card.card_id,
                        url=row.get("permalink") or product.get("permalink"),
                        price=price(row["prices"]),
                        currency=row["prices"].get("currency_code") or "GBP",
                        title=title,
                        match=MATCH_EXACT,
                        quantity=quantity(row),
                    ))
        return offers


PLUGIN = MidnightCards()

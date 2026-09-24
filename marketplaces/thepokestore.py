"""
The Poké Store (thepokestore.co.uk), a UK Shopify shop in Stevenage with
prices in GBP. This plugin reads its Japanese singles collection.

The shop's agents.md lists the public Shopify collection JSON as its
read-only route for agents. It serves 250 products a page and the
collection is a few thousand cards (about 10 requests), so the plugin reads
it once per run and matches missing cards locally rather than searching card
by card.

Every product title is just the card number and name, and the set is the
product's one tag:

    001/062 Froslass ex          tagged "Raging Surf"
    010/086 Virizion             tagged "White Flare"

The shop also has one collection per set, titled with the set code
("Raging Surf (sv3a)"), so the plugin reads the collection list once to
turn a tag into a code. A card matches on number plus set: the tag's code
equal to its TCGdex set id, or the tag equal to its set name.

A product's variants are its prints ("Non-Holo", "Holo", "Poké Ball Holo"),
each with its own price and stock flag; only variants in stock become
offers, with the print named in the offer's title. The shop doesn't state a
condition per card, so condition is left unknown.
"""
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import normalize_number, normalize_text

SHOP = "https://thepokestore.co.uk"
COLLECTION = "browse-japanese-single-cards"
COLLECTION_URL = SHOP + "/collections/{collection}/products.json?limit={limit}&page={page}"
COLLECTIONS_URL = SHOP + "/collections.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}?variant={variant}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 100  # safety stop

TITLE = re.compile(r"^\s*(?P<number>\d+)/(?P<total>\d+)\s+(?P<name>.+?)\s*$")
SET_COLLECTION = re.compile(r"^(?P<name>.+?)\s*\((?P<code>[A-Za-z]{1,3}\d+[A-Za-z]?)\)\s*$")


def parse_title(title):
    """{number, name} from a product title, or None if it doesn't start
    with a number over a total."""
    m = TITLE.match(title or "")
    if not m:
        return None
    return {"number": normalize_number(m.group("number")), "name": m.group("name")}


def set_codes(collections):
    """{normalised set name: normalised set code} from collection titles
    like "Raging Surf (sv3a)"."""
    codes = {}
    for collection in collections:
        m = SET_COLLECTION.match(collection.get("title") or "")
        if m:
            codes[normalize_text(m.group("name"))] = normalize_code(m.group("code"))
    return codes


def matches(parsed, tags, codes, card):
    """True if a product is this card: same card number, and one of its
    tags is the card's set by code or by name."""
    if not parsed or parsed["number"] != normalize_number(card.local_id):
        return False
    set_id, set_name = normalize_code(card.set_id), normalize_text(card.set_name)
    for tag in tags:
        tag = normalize_text(tag)
        if codes.get(tag) == set_id or (set_name and tag == set_name):
            return True
    return False


class ThePokeStore(Marketplace):
    id = "thepokestore"
    name = "The Poké Store"
    languages = {"ja"}     # this collection is Japanese singles only
    min_interval = 1.0

    def _pages(self, ctx, url, key, **fields):
        rows_out = []
        for page in range(1, MAX_PAGES + 1):
            rows = ctx.fetch(url.format(limit=PAGE_SIZE, page=page, **fields), as_json=True).get(key, [])
            rows_out.extend(rows)
            if len(rows) < PAGE_SIZE:
                break
        return rows_out

    def catalogue(self, ctx):
        """(set codes, products) for the Japanese singles collection,
        fetched once per run."""
        if "products" not in ctx.state:
            codes = set_codes(self._pages(ctx, COLLECTIONS_URL, "collections"))
            products, seen = [], set()
            for row in self._pages(ctx, COLLECTION_URL, "products", collection=COLLECTION):
                if row.get("id") not in seen:
                    seen.add(row.get("id"))
                    products.append(row)
            ctx.debug(f"{len(products)} products in the Japanese singles collection, "
                      f"{len(codes)} set codes")
            ctx.state["codes"] = codes
            ctx.state["products"] = [(p, parse_title(p.get("title"))) for p in products]
        return ctx.state["codes"], ctx.state["products"]

    def search_set(self, cards, ctx):
        codes, products = self.catalogue(ctx)
        by_number = {}
        for card in cards:
            by_number.setdefault(normalize_number(card.local_id), []).append(card)
        offers = []
        for product, parsed in products:
            candidates = by_number.get(parsed["number"], []) if parsed else []
            hits = [c for c in candidates if matches(parsed, product.get("tags") or [], codes, c)]
            if not hits:
                continue
            for variant in product.get("variants", []):
                if not variant.get("available") or variant.get("price") is None:
                    continue
                title = f"{product.get('title', '')} ({variant.get('title')}) [{', '.join(product.get('tags') or [])}]"
                for card in hits:
                    offers.append(Offer(
                        marketplace=self.id,
                        card_id=card.card_id,
                        url=PRODUCT_PAGE.format(handle=product["handle"], variant=variant["id"]),
                        price=Decimal(str(variant["price"])),
                        currency="GBP",
                        title=title,
                        match=MATCH_EXACT,
                    ))
        return offers


PLUGIN = ThePokeStore()

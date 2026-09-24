"""
NMD Collectables (nmdcollectables.co.uk), a UK Shopify shop with prices in
GBP. The whole shop is Japanese cards, so this plugin reads every product.

The shop's agents.md lists the public Shopify product JSON as its read-only
route for agents. It serves 250 products a page and the shop is a few
thousand products (about 11 requests), so the plugin reads it once per run
and matches missing cards locally rather than searching card by card.

Titles carry the card name, number over the set total, rarity and set name,
sometimes with the set code, and the product type is the set's English name:

    Suicune - 026/080 - Rare - m2 - Inferno X          type "Inferno X"
    Timburr - 131/086 - Art Rare - Black Bolt          type "Black Bolt"
    Litwick - 015/086 - Pokeball Reverse - Black Bolt  type "Black Bolt"
    Kamado - 082/067 - Secret Rare                     type "Battle Region"

so the product type is mapped to a TCGdex set id (Total Cards' SET_IDS plus
this shop's own spellings), and a card matches on that set id plus number.
Products with no number over a total (booster boxes, bundles, decks) never
match. Poké Ball and Master Ball reverse holos are their own products with
the same number as the plain print; they match too, and the offer's title
names the print.

Each product has one variant, one listing with its own stock flag; only
listings in stock become offers. The shop doesn't state a condition per
card, so condition is left unknown.
"""
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import normalize_number, normalize_text
from marketplaces.totalcards import SET_IDS as TOTALCARDS_SET_IDS

SHOP = "https://www.nmdcollectables.co.uk"
PRODUCTS_URL = SHOP + "/products.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}?variant={variant}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 100  # safety stop

# The shop's product type (English set name) -> TCGdex set id, where Total
# Cards' names differ.
SET_IDS = dict(TOTALCARDS_SET_IDS)
SET_IDS.update({normalize_text(name): set_id for name, set_id in {
    "Mega Dream": "M2a", "Shiny Treasure": "SV4a", "Terastal Festival": "SV8a", "Triple Beat": "SV1a",
}.items()})

NUMBER = re.compile(r"(?<![\w/])(?P<number>\d+)/(?P<total>\d+)(?![\w/])")


def parse_product(product):
    """{number, set_id} for a product, or None if its title has no card
    number over a total or its type isn't a known set. `set_id` is
    normalised (see normalize_code)."""
    m = NUMBER.search(product.get("title") or "")
    set_id = SET_IDS.get(normalize_text(product.get("product_type")))
    if not m or not set_id:
        return None
    return {"number": normalize_number(m.group("number")), "set_id": normalize_code(set_id)}


def matches(parsed, card):
    """True if a parsed product is this card: same card number and set id."""
    return bool(parsed) and parsed["number"] == normalize_number(card.local_id) \
        and parsed["set_id"] == normalize_code(card.set_id)


class NMDCollectables(Marketplace):
    id = "nmdcollectables"
    name = "NMD Collectables"
    languages = {"ja"}     # the shop sells Japanese cards only
    min_interval = 1.0

    def catalogue(self, ctx):
        """Every product in the shop, fetched once per run."""
        if "products" not in ctx.state:
            products, seen = [], set()
            for page in range(1, MAX_PAGES + 1):
                rows = ctx.fetch(PRODUCTS_URL.format(limit=PAGE_SIZE, page=page), as_json=True).get("products", [])
                for row in rows:
                    if row.get("id") not in seen:
                        seen.add(row.get("id"))
                        products.append(row)
                if len(rows) < PAGE_SIZE:
                    break
            ctx.debug(f"{len(products)} products in the shop")
            ctx.state["products"] = [(p, parse_product(p)) for p in products]
        return ctx.state["products"]

    def search_set(self, cards, ctx):
        offers = []
        for product, parsed in self.catalogue(ctx):
            hits = [c for c in cards if matches(parsed, c)]
            if not hits:
                continue
            for variant in product.get("variants", []):
                if not variant.get("available") or variant.get("price") is None:
                    continue
                for card in hits:
                    offers.append(Offer(
                        marketplace=self.id,
                        card_id=card.card_id,
                        url=PRODUCT_PAGE.format(handle=product["handle"], variant=variant["id"]),
                        price=Decimal(str(variant["price"])),
                        currency="GBP",
                        title=product.get("title", ""),
                        match=MATCH_EXACT,
                    ))
        return offers


PLUGIN = NMDCollectables()

"""
Tyneside TCG (tynesidetcg.co.uk), a UK Shopify shop with prices in GBP.
This plugin reads its Japanese singles collection.

The shop's agents.md lists the public Shopify collection JSON as its
read-only route for agents. The collection is a couple of hundred cards
(one request at 250 a page), so the plugin reads it once per run and
matches missing cards locally rather than searching card by card.

Titles carry the card number over the set total, and the set code is one of
the product's tags (the title's "(SV4A)" is there only sometimes):

    Snorunt 200/193 – Mega Dream (Japanese Single)          tagged AR, M2A
    Pinsir 067/066 (SV5A) – Crimson Haze (Japanese Single)  tagged AR, SV5A
    Fuecoco 018/M-P - Mega Evolution Promos (...)           tagged JPPROMO

so a card matches on number plus set: a set-code tag equal to its TCGdex set
id, or a promo number's code ("M-P") equal to its set id. Titles without a
number over a total (Pokédex-numbered promos, "DPBP #470") never match, and
neither do the shop's DP-era cards (tagged JPDP), which TCGdex has no
Japanese sets for.

Each product has one variant, one listing with its own stock flag; only
listings in stock become offers. Condition is only stated when a title says
so ("(LP Condition)").
"""
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import normalize_number

SHOP = "https://www.tynesidetcg.co.uk"
COLLECTION = "japanese-singles"
COLLECTION_URL = SHOP + "/collections/{collection}/products.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}?variant={variant}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 100  # safety stop

NUMBER = re.compile(r"(?<![\w/])(?P<number>[A-Za-z]{0,3}\d+)/(?P<total>[A-Za-z0-9-]+)")
# Set-code tags: "M2A", "SV11W", "S12A", "SV2P". Rarity and type tags ("AR",
# "jex", "vstar") and the shop's buckets ("JPPROMO", "JPDP") have no digit.
SET_TAG = re.compile(r"^[A-Za-z]{1,3}\d+[A-Za-z]?$")
TITLE_CONDITION = re.compile(r"\((NM|LP|MP|HP|DMG) Condition\)", re.I)


def parse_product(product):
    """{number, set_ids} for a product, or None if its title has no card
    number over a total. `set_ids` are normalised (see normalize_code): the
    promo number's code ("M-P" in "018/M-P"), else every set-code tag."""
    m = NUMBER.search(product.get("title") or "")
    if not m:
        return None
    total = m.group("total")
    if not total.isdigit():
        set_ids = {normalize_code(total)}
    else:
        set_ids = {normalize_code(t) for t in product.get("tags") or [] if SET_TAG.match(t.strip())}
    return {"number": normalize_number(m.group("number")), "set_ids": set_ids}


def matches(parsed, card):
    """True if a parsed product is this card: same card number and set id."""
    return bool(parsed) and parsed["number"] == normalize_number(card.local_id) \
        and normalize_code(card.set_id) in parsed["set_ids"]


def condition(product):
    """LP/MP/... when the title says "(LP Condition)", else None."""
    m = TITLE_CONDITION.search(product.get("title") or "")
    return m.group(1).upper() if m else None


class TynesideTCG(Marketplace):
    id = "tynesidetcg"
    name = "Tyneside TCG"
    languages = {"ja"}     # this collection is Japanese singles only
    min_interval = 1.0

    def catalogue(self, ctx):
        """Every product in the Japanese singles collection, fetched once
        per run."""
        if "products" not in ctx.state:
            products, seen = [], set()
            for page in range(1, MAX_PAGES + 1):
                rows = ctx.fetch(COLLECTION_URL.format(collection=COLLECTION, limit=PAGE_SIZE, page=page),
                                 as_json=True).get("products", [])
                for row in rows:
                    if row.get("id") not in seen:
                        seen.add(row.get("id"))
                        products.append(row)
                if len(rows) < PAGE_SIZE:
                    break
            ctx.debug(f"{len(products)} products in the Japanese singles collection")
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
                        condition=condition(product),
                    ))
        return offers


PLUGIN = TynesideTCG()

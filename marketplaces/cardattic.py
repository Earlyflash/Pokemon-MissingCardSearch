"""
The Card Attic (thecardatticshop.co.uk), a small UK Shopify shop with
prices in GBP. This plugin reads its Japanese singles collection.

The shop's agents.md lists the public Shopify collection JSON
(/collections/{handle}/products.json) as a read-only route, and robots.txt
allows it. The collection is a few dozen cards, so one request per run
reads all of it and missing cards are matched locally.

Titles are hand-written as " - "-separated parts: the card name, the card
number over the printed total, and the shop's English set name, sometimes
led by the set code, in either order:

    Steelix - 033/054 - XY11-Bb: Fever-Burst Fighter
    Blastoise ex - 202/165 - SV2a: Pokemon Card 151
    Hop's Zacian ex - Battle Partners - 069/100

The English set names are the same ones Total Cards uses, so they map to
TCGdex set ids through totalcards.SET_IDS; a set code equal to the card's
TCGdex set id (after Japan2UK's XY aliases, "XY11-Bb" is XY11a) or a set
name equal to the card's own also matches. A card matches on number plus
set.

Each product is one card with one "Default Title" variant. The condition
is only stated in the description ("The condition of this card is Near
Mint.", "... is Moderately Played - with a scratch ..."), so it's read from
there and mapped to base.CONDITIONS; a product with no description has an
unknown condition.
"""
import html
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import normalize_number, normalize_text
from marketplaces.japan2uk import CODE_ALIASES
from marketplaces.totalcards import SET_IDS

SHOP = "https://thecardatticshop.co.uk"
COLLECTION_URL = SHOP + "/collections/japanese-singles/products.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 20   # safety stop

NUMBER = re.compile(r"^(?P<number>\d+)/(?P<total>\d+)$")
CODE_PREFIX = re.compile(r"^(?P<code>[A-Za-z]{1,3}\d+[A-Za-z]?(?:-[A-Za-z]{1,2})?)\s*:\s*(?P<name>.+)$")
CONDITION = re.compile(r"condition of this card is\s*:?\s*(?P<condition>[A-Za-z][A-Za-z ]*?)\s*(?:[.,\-(]|$)", re.I)
CONDITIONS = {"mint": "NM", "near mint": "NM", "lightly played": "LP", "light played": "LP",
              "excellent": "LP", "moderately played": "MP", "played": "MP",
              "heavily played": "HP", "damaged": "DMG", "poor": "DMG"}
TAGS = re.compile(r"<[^>]+>")


def parse_title(title):
    """{number, codes, sets} from a product title, or None if no " - " part
    is a card number over a total. `codes` are normalised set-code prefixes
    ("xy11bb"), `sets` normalised set names, both from the parts after the
    card name."""
    parts = [p.strip() for p in (title or "").split(" - ")]
    number, codes, sets = None, [], []
    for part in parts[1:]:
        m = NUMBER.match(part)
        if m and number is None:
            number = normalize_number(m.group("number"))
            continue
        m = CODE_PREFIX.match(part)
        if m:
            codes.append(normalize_code(m.group("code")))
            part = m.group("name")
        sets.append(normalize_text(part))
    if number is None:
        return None
    return {"number": number, "codes": codes, "sets": sets}


def parse_condition(body_html):
    """One of base.CONDITIONS from a product description, or None."""
    text = " ".join(html.unescape(TAGS.sub(" ", body_html or "")).split())
    m = CONDITION.search(text)
    return CONDITIONS.get(m.group("condition").lower()) if m else None


def matches(parsed, card):
    """True if a parsed title is this card: same number, and the set by
    code, by the shop's English set name, or by the card's own set name."""
    if not parsed or parsed["number"] != normalize_number(card.local_id):
        return False
    set_id, set_name = normalize_code(card.set_id), normalize_text(card.set_name)
    if any(CODE_ALIASES.get(code, code) == set_id for code in parsed["codes"]):
        return True
    return any(normalize_code(SET_IDS.get(s)) == set_id or (set_name and s == set_name)
               for s in parsed["sets"])


class CardAttic(Marketplace):
    id = "cardattic"
    name = "The Card Attic"
    languages = {"ja"}     # this collection is Japanese singles only
    min_interval = 1.0

    def catalogue(self, ctx):
        """[(product, parsed title)] for the Japanese singles collection,
        fetched once per run."""
        if "products" not in ctx.state:
            products, seen = [], set()
            for page in range(1, MAX_PAGES + 1):
                rows = ctx.fetch(COLLECTION_URL.format(limit=PAGE_SIZE, page=page), as_json=True).get("products", [])
                for row in rows:
                    if row.get("id") not in seen:
                        seen.add(row.get("id"))
                        products.append(row)
                if len(rows) < PAGE_SIZE:
                    break
            ctx.debug(f"{len(products)} products in the Japanese singles collection")
            ctx.state["products"] = [(p, parse_title(p.get("title"))) for p in products]
        return ctx.state["products"]

    def search_set(self, cards, ctx):
        by_number = {}
        for card in cards:
            by_number.setdefault(normalize_number(card.local_id), []).append(card)
        offers = []
        for product, parsed in self.catalogue(ctx):
            candidates = by_number.get(parsed["number"], []) if parsed else []
            hits = [c for c in candidates if matches(parsed, c)]
            if not hits:
                continue
            condition = parse_condition(product.get("body_html"))
            for variant in product.get("variants", []):
                if not variant.get("available") or variant.get("price") is None:
                    continue
                title = product.get("title", "")
                if variant.get("title") not in (None, "", "Default Title"):
                    title += f" ({variant['title']})"
                for card in hits:
                    offers.append(Offer(
                        marketplace=self.id,
                        card_id=card.card_id,
                        url=PRODUCT_PAGE.format(handle=product["handle"]),
                        price=Decimal(str(variant["price"])),
                        currency="GBP",
                        title=title,
                        match=MATCH_EXACT,
                        condition=condition,
                    ))
        return offers


PLUGIN = CardAttic()

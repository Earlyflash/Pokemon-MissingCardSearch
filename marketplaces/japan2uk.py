"""
Japan2UK (japan2uk.com), a UK Shopify shop with prices in GBP. This plugin
reads its Japanese singles and Japanese graded cards collections.

The shop's agents.md lists the public Shopify collection JSON as its
read-only route for agents. It serves 250 products a page, so the plugin
reads each collection once per run (the singles collection is about 21,000
products, 86 requests, most of them out of stock) and matches missing cards
locally rather than searching card by card.

Every card title ends with the set code, the card number and the kind:

    Pokemon Gengar ex SAR 30th Celebration m6a 131/103 Japanese Single Card
    Pokemon Boss's Orders Non Holo Event Organizer Promo 084/M-P Japanese Single Card
    Pokemon Altaria Holo Dragon Storm sm6a 031/053 Japanese Graded Card PSA 10 #153131669

so a card matches on number plus set: the code equal to its TCGdex set id,
or a promo number's code ("M-P") equal to its set id. A few XY-era codes
differ from TCGdex's (XY1 covers both Collection X and Y, "xy11 Bb" is
XY11a) and are mapped in CODE_ALIASES/NAME_ALIASES. Titles without a
number over a total (sealed products, "059 Base Set") never match.

Each product has one variant, one listing with its own stock flag; only
listings in stock become offers.
"""
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import normalize_number, normalize_text

SHOP = "https://www.japan2uk.com"
COLLECTIONS = ("pokemon-japanese-cards", "pokemon-japanese-graded-cards")
COLLECTION_URL = SHOP + "/collections/{collection}/products.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}?variant={variant}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 200  # safety stop

TITLE = re.compile(r"\s(?:(?P<prefix>xy\d+)\s+)?(?P<code>\S+)\s+(?P<number>[^\s/]+)/(?P<total>[^\s/]+)"
                   r"\s+Japanese (?:Single|Graded) Card\b(?! Set)", re.I)
# Title code -> TCGdex set id, where the two differ.
CODE_ALIASES = {"xy8bb": "xy8a", "xy8br": "xy8b", "xy11bb": "xy11a", "xy11br": "xy11b"}
# Title code shared by two TCGdex sets -> {words in the title: set id}.
NAME_ALIASES = {"xy1": {"collection x": "xy1a", "collection y": "xy1b"},
                "xy5": {"gaia volcano": "xy5a", "tidal storm": "xy5b"}}
CONDITIONS = {"mint": "NM", "near mint": "NM", "light play": "LP", "lightly played": "LP",
              "moderate play": "MP", "moderately played": "MP",
              "heavy play": "HP", "heavily played": "HP", "damaged": "DMG"}
GRADE = re.compile(r"\b(PSA|BGS|CGC|SGC|ARS|ACE|TAG|Beckett)\s*(\d+(?:\.\d)?)\b", re.I)


def parse_title(title):
    """{number, set_id} from a product title, or None if it has no set code
    and card number. `set_id` is normalised (see normalize_code): the title's
    set code mapped to TCGdex's id, or a promo number's code ("M-P" in
    "084/M-P")."""
    m = TITLE.search(title or "")
    if not m:
        return None
    total = m.group("total")
    if not total.isdigit():
        set_id = normalize_code(total)
    else:
        set_id = normalize_code((m.group("prefix") or "") + m.group("code"))
        set_id = CODE_ALIASES.get(set_id, set_id)
        words = normalize_text(title)
        for name, alias in NAME_ALIASES.get(set_id, {}).items():
            if name in words:
                set_id = alias
    return {"number": normalize_number(m.group("number")), "set_id": set_id}


def matches(parsed, card):
    """True if a parsed title is this card: same card number and set id."""
    return bool(parsed) and parsed["number"] == normalize_number(card.local_id) \
        and parsed["set_id"] == normalize_code(card.set_id)


def condition(variant):
    """NM/LP/MP/HP/DMG from the variant's option ("Mint" is the shop's
    ungraded condition), else None."""
    return CONDITIONS.get((variant.get("option1") or "").strip().lower())


def grade(product):
    """'PSA 10', 'ARS 10', ... when the title names a grading company and
    grade after "Japanese Graded Card", else None."""
    _, graded, rest = product.get("title", "").partition("Japanese Graded Card")
    m = GRADE.search(rest) if graded else None
    if not m:
        return None
    company = m.group(1).upper() if m.group(1).lower() != "beckett" else "BGS"
    return f"{company} {m.group(2)}"


class Japan2UK(Marketplace):
    id = "japan2uk"
    name = "Japan2UK"
    languages = {"ja"}     # these collections are Japanese cards only
    min_interval = 1.0

    def catalogue(self, ctx):
        """Every product in the Japanese singles and graded collections,
        fetched once per run."""
        if "products" not in ctx.state:
            products, seen = [], set()
            for collection in COLLECTIONS:
                for page in range(1, MAX_PAGES + 1):
                    rows = ctx.fetch(COLLECTION_URL.format(collection=collection, limit=PAGE_SIZE, page=page),
                                     as_json=True).get("products", [])
                    for row in rows:
                        if row.get("id") not in seen:
                            seen.add(row.get("id"))
                            products.append(row)
                    if len(rows) < PAGE_SIZE:
                        break
            ctx.debug(f"{len(products)} products in the Japanese singles and graded collections")
            ctx.state["products"] = [(p, parse_title(p.get("title"))) for p in products]
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
                        condition=condition(variant),
                        grade=grade(product),
                    ))
        return offers


PLUGIN = Japan2UK()

"""
Cardmarket (cardmarket.com), the big European marketplace, prices in EUR.

Cardmarket's website blocks automated reads and its API takes no new users,
but it publishes its price guide as a free daily download for anyone to use:
one row per product with, among others, `low` (the cheapest copy currently
listed) and `trend`. That's a price per card, not individual listings, so
this is a price-guide plugin: the core reports it apart from real listings.

`low` covers every copy of the product: for English-set cards that includes
the German, French, Italian... prints and any condition, so the real price
for an English near-mint copy can be higher.

The price guide has no card numbers, so cards are tied to Cardmarket
products through TCGdex, whose card records carry the Cardmarket product id
(`pricing.cardmarket.idProduct`), one request per missing card.
"""
import urllib.error
import urllib.parse
from decimal import Decimal

from marketplaces.base import (FINISH_BALL, FINISH_ENERGY, FINISH_NORMAL, MATCH_EXACT,
                               Marketplace, Offer)

PRICE_GUIDE_URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json"
TCGDEX_CARD_URL = "https://api.tcgdex.net/v2/{lang}/cards/{card_id}"
# A card's Cardmarket product never changes, and TCGdex ignores conditional
# requests, so its card lookups are kept for this long rather than the
# usual cache TTL.
TCGDEX_MAX_AGE = 30 * 24 * 3600
# Cardmarket redirects this to the product's page.
PRODUCT_URL = "https://www.cardmarket.com/en/Pokemon/Products?idProduct={id}"


def product_id(tcgdex_card):
    cm = ((tcgdex_card or {}).get("pricing") or {}).get("cardmarket") or {}
    return cm.get("idProduct")


def _foil_name(foil):
    """TCGdex's reverse holo pattern as a name: "loveball" -> "Love Ball",
    "team-rocket" -> "Team Rocket"; None (not recorded) -> "Ball"."""
    if not foil:
        return "Ball"
    if foil == "pokeball":
        return "Poké Ball"
    if foil.endswith("ball"):
        foil = foil[:-4] + " ball"
    return foil.replace("-", " ").title()


def card_variants(tcgdex_card):
    """[(finish key, finish name, Cardmarket idProduct or None), ...] for
    each print of a TCGdex card with reverse holos -- the normal print, then
    the reverses -- read off its `variants_detailed`, where every print has
    its own Cardmarket product (M2a's reverses are in Cardmarket's separate
    "MEGA Dream ex: Additionals" expansion). [] for a card with only one
    print, or whose reverses can't be told apart."""
    detailed = (tcgdex_card or {}).get("variants_detailed") or []
    reverses = [v for v in detailed if v.get("type") == "reverse"]
    if not reverses:
        return []

    def cm_id(v):
        return ((v or {}).get("thirdParty") or {}).get("cardmarket")
    normal = next((v for v in detailed if v.get("type") == "normal"), None)
    found = [(FINISH_NORMAL, "Normal", cm_id(normal) or product_id(tcgdex_card))]
    for v in reverses:
        if v.get("foil") == "energy":
            found.append((FINISH_ENERGY, "Energy Reverse Holo", cm_id(v)))
        else:
            found.append((FINISH_BALL, f"{_foil_name(v.get('foil'))} Reverse Holo", cm_id(v)))
    keys = [key for key, _, _ in found]
    return found if len(keys) == len(set(keys)) else []


def money(value):
    return None if value in (None, "") else Decimal(str(value))


def describe(row):
    text = "Cheapest copy on Cardmarket (any language or condition)"
    trend = money(row.get("trend"))
    return f"{text}; trend EUR {trend:.2f}" if trend else text


class Cardmarket(Marketplace):
    id = "cardmarket"
    name = "Cardmarket"
    languages = None       # English and Japanese prints both have products
    min_interval = 0.2     # TCGdex, one request per card
    price_guide = True

    def price_guide_rows(self, ctx):
        """{idProduct: price guide row}, downloaded once per run (~15 MB)."""
        if "guide" not in ctx.state:
            data = ctx.fetch(PRICE_GUIDE_URL, as_json=True, timeout=120)
            ctx.state["guide"] = {row["idProduct"]: row for row in data.get("priceGuides", [])
                                  if row.get("idProduct") is not None}
            ctx.debug(f"price guide from {data.get('createdAt')}: {len(ctx.state['guide'])} products")
        return ctx.state["guide"]

    def search_set(self, cards, ctx):
        self.price_guide_rows(ctx)  # if the download fails, fail the set once, not every card
        return super().search_set(cards, ctx)

    def search(self, card, ctx):
        url = TCGDEX_CARD_URL.format(lang=urllib.parse.quote(card.tcgdex_lang or "en"),
                                     card_id=urllib.parse.quote(card.card_id))
        try:
            pid = product_id(ctx.fetch(url, as_json=True, max_age=TCGDEX_MAX_AGE))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            ctx.log(f"{card.card_id}: TCGdex has no such card")
            return []
        if pid is None:
            ctx.debug(f"{card.card_id}: TCGdex has no Cardmarket product")
            return []
        row = self.price_guide_rows(ctx).get(pid)
        low = money((row or {}).get("low"))
        if low is None:
            return []
        return [Offer(
            marketplace=self.id,
            card_id=card.card_id,
            url=PRODUCT_URL.format(id=pid),
            price=low,
            currency="EUR",
            title=describe(row),
            match=MATCH_EXACT,
        )]


PLUGIN = Cardmarket()

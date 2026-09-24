"""
Japan Game & TCG Market (japan-game-tcg-market.myshopify.com), a Shopify
shop in Osaka, Japan, with prices in USD; the core converts them. It ships
worldwide from Japan (the UK included), so its cards are imports, and
shipping isn't counted here any more than for the UK shops.

The shop's agents.md lists the public Shopify collection JSON as its
read-only route for agents, and robots.txt allows it. The whole shop is
only about 750 products (3 requests of 250), so the plugin reads the "all"
collection once per run: that covers the singles, graded, promo and vintage
collections in one pass, and consoles and sealed boxes never match.

Every card title starts with the condition (or grade) in 【】 brackets, then
the name and number, then the set, separated by "|" or "/":

    【NM】Mega Dragonite ex SAR 246/193 | Mega Dream ex M2a | Japanese
    【PSA 10】Mew AR 183/172 | VSTAR UNIVERSE S12a | Japanese Pokemon Card
    【NM】Pikachu 001/SV-P | Scarlet Violet Promo | Japanese

so a card matches on number plus set: the set code in the title (M2a)
equal to its TCGdex set id, a promo number's code (SV-P) equal to its set
id, or one of the title's "|" parts equal to its set name. Titles with more
than one card number (pairs, sequential sets), none at all (vintage "No.
149" cards, bulk lots) or another language (a few Chinese cards) never
match.

Each product has one "Default Title" variant with its own stock flag; only
listings in stock become offers.
"""
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import normalize_number, normalize_text

SHOP = "https://japan-game-tcg-market.myshopify.com"
COLLECTION = "all"
COLLECTION_URL = SHOP + "/collections/{collection}/products.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}?variant={variant}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 40   # safety stop

TITLE = re.compile(r"^\s*【(?P<tag>[^】]*)】\s*(?P<rest>.+?)\s*$")
NUMBER = re.compile(r"(?<![\w/])(?P<number>\d{1,4})/(?P<total>\d{1,4}|[A-Za-z]{1,4}-P)(?![\w/])")
# Set codes as the shop writes them: M2a, m1L, SV11B, SM12a, S12a, S8a-P, BW8, DP5, CP6, L3.
CODE = re.compile(r"(?<![\w-])(?:SV|SM|XY|BW|DPt|DP|CP|S|M|L)\d+[a-z]?(?:-P)?(?![\w-])", re.I)
PARTS = re.compile(r"\s*\|\s*|\s+/\s+")
OTHER_LANGUAGES = re.compile(r"\b(?:Chinese|Korean|English)\b", re.I)
CONDITIONS = {"nm": "NM", "mint": "NM", "unopened": "NM", "nm-": "LP", "lp~nm": "LP", "lp": "LP",
              "lp-mp": "MP", "mp": "MP", "hp": "HP", "dmg": "DMG", "damaged": "DMG"}
GRADE = re.compile(r"^(PSA|BGS|CGC|SGC|ARS|ACE|TAG)\s*(\d+(?:\.\d)?)$", re.I)


def parse_title(title):
    """{number, set_id, set_names, tag} from a product title, or None if it
    isn't one Japanese card with a number. `set_id` is normalised (see
    normalize_code): a promo number's code ("SV-P" in "001/SV-P"), else the
    last set code in the title, else None. `set_names` are the title's
    "|"-separated parts, normalised with any set code dropped, for matching
    on set name. `tag` is what's in the 【】 brackets."""
    m = TITLE.match(title or "")
    if not m or OTHER_LANGUAGES.search(m.group("rest")):
        return None
    rest = m.group("rest")
    numbers = NUMBER.findall(rest)
    if len(set(numbers)) != 1:
        return None
    number, total = numbers[0]
    if not total.isdigit():
        set_id = normalize_code(total)
    else:
        codes = CODE.findall(NUMBER.sub(" ", rest))
        set_id = normalize_code(codes[-1]) if codes else None
    set_names = {normalize_text(CODE.sub(" ", part)) for part in PARTS.split(rest)}
    return {"number": normalize_number(number), "set_id": set_id,
            "set_names": set_names - {""}, "tag": m.group("tag").strip()}


def matches(parsed, card):
    """True if a parsed title is this card: same card number, and the same
    set by code, or by name when the title gives no code."""
    if not parsed or parsed["number"] != normalize_number(card.local_id):
        return False
    if parsed["set_id"]:
        return parsed["set_id"] == normalize_code(card.set_id)
    return bool(card.set_name) and normalize_text(card.set_name) in parsed["set_names"]


def condition(parsed):
    """NM/LP/MP/HP/DMG from the title's 【】 tag, else None. The shop's
    "NM-" (near mint minus) counts as LP, and a range ("LP~NM") as its
    lower end."""
    return CONDITIONS.get(re.sub(r"\s+", "", parsed["tag"].lower()))


def grade(parsed):
    """'PSA 10' when the 【】 tag is a grade, else None."""
    m = GRADE.match(parsed["tag"])
    return f"{m.group(1).upper()} {m.group(2)}" if m else None


class JapanGameTCG(Marketplace):
    id = "japangametcg"
    name = "Japan Game & TCG Market"
    languages = {"ja"}     # the shop sells Japanese cards (and a handful of Chinese ones, skipped)
    min_interval = 1.0

    def catalogue(self, ctx):
        """Every product in the shop, with its parsed title, fetched once
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
            ctx.debug(f"{len(products)} products in the shop")
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
                        currency="USD",
                        title=product.get("title", ""),
                        match=MATCH_EXACT,
                        condition=condition(parsed),
                        grade=grade(parsed),
                    ))
        return offers


PLUGIN = JapanGameTCG()

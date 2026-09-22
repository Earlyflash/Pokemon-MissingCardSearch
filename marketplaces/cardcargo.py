"""
CardCargo (cardcargo.com), a UK Shopify shop with prices in GBP. This plugin
reads its Japanese singles collection.

The shop's public Shopify product JSON (the read-only route its agents.md
documents) lists the whole collection 250 products a page, and the
collection is only a few hundred cards, so the plugin reads all of it once
per run and matches missing cards locally rather than searching card by
card.

Every product title follows one pattern:

    (#240/193) Mega Gengar ex - Holo [MEGA Dream ex (JPN)]
    (#152/S-P) Crobat V - Normal [Sword & Shield (JPN)]
    (#NO. 008) Wartortle - Normal [Southern Islands (JPN)]

so a card matches on number plus set: the set name in brackets (with any
code prefix like "SV2a: " dropped) equal to the card's set name, a code
prefix equal to its TCGdex set id, or a promo number's code ("S-P") equal to
its set id. Vintage "NO. 008" numbers are Pokédex numbers, not card numbers,
so those listings never match.

Each product variant is one physical copy with its own condition and price;
only variants in stock become offers, each linking to its variant.
"""
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.deckdhq import normalize_number, normalize_text

SHOP = "https://cardcargo.com"
COLLECTION_URL = SHOP + "/collections/buy-japanese-pokemon-singles/products.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}?variant={variant}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 100  # safety stop

TITLE = re.compile(r"^\(#(?P<number>[^)]+)\)\s*(?P<name>.+?)\s*\[(?P<set>[^\]]+)\]\s*$")
SET_CODE = re.compile(r"^(?P<code>[A-Za-z]{1,3}\d+[A-Za-z]*)\s*:\s*(?P<name>.+)$")
CONDITION_WORDS = {"near mint": "NM", "light play": "LP", "lightly played": "LP",
                   "moderate play": "MP", "moderately played": "MP",
                   "heavy play": "HP", "heavily played": "HP", "damaged": "DMG"}
GRADE = re.compile(r"\b(PSA|BGS|CGC|SGC|ACE|TAG|Beckett)\s*(\d+(?:\.\d)?)\b", re.I)


def normalize_code(code):
    """'SV2a' -> 'sv2a', 'S-P' -> 'sp', so set ids compare case- and
    punctuation-blind."""
    return re.sub(r"[^a-z0-9]", "", str(code or "").lower())


def parse_title(title):
    """{number, promo, set_name, set_code} from a product title, or None if
    it doesn't follow the shop's pattern. `number` is None for a Pokédex
    number ("NO. 008"); `promo` is the code of a promo number ("S-P" in
    "152/S-P")."""
    m = TITLE.match(title or "")
    if not m:
        return None
    raw_number, set_part = m.group("number").strip(), m.group("set").strip()
    set_part = re.sub(r"\s*\((?:JPN|JP|Japanese)\)\s*$", "", set_part, flags=re.I)
    number, promo = None, None
    if not raw_number.upper().startswith("NO."):
        number = normalize_number(raw_number)
        total = raw_number.split("/", 1)[1].strip() if "/" in raw_number else ""
        if total and not total.isdigit():
            promo = total
    code = SET_CODE.match(set_part)
    return {"number": number, "promo": promo, "set_name": set_part,
            "set_code": code.group("code") if code else None,
            "set_short": code.group("name") if code else set_part}


def matches(parsed, card):
    """True if a parsed title is this card: same card number, and the set
    tied by promo code, set code or set name."""
    if not parsed or not parsed["number"] or parsed["number"] != normalize_number(card.local_id):
        return False
    set_id = normalize_code(card.set_id)
    if parsed["promo"]:
        return normalize_code(parsed["promo"]) == set_id
    if parsed["set_code"] and normalize_code(parsed["set_code"]) == set_id:
        return True
    wanted = normalize_text(card.set_name)
    return bool(wanted) and wanted in (normalize_text(parsed["set_name"]), normalize_text(parsed["set_short"]))


def condition(variant):
    """NM/LP/MP/HP/DMG from the variant's Condition option, else None."""
    for text in (variant.get("option1"), variant.get("title")):
        text = (text or "").lower()
        for words, code in CONDITION_WORDS.items():
            if text.startswith(words):
                return code
    return None


def grade(product, variant):
    """'PSA 10', 'CGC 9.5', ... when the title or variant names a grading
    company and grade, else None."""
    m = GRADE.search(f"{product.get('title', '')} {variant.get('title', '')}")
    if not m:
        return None
    company = m.group(1).upper() if m.group(1).lower() != "beckett" else "BGS"
    return f"{company} {m.group(2)}"


class CardCargo(Marketplace):
    id = "cardcargo"
    name = "CardCargo"
    languages = {"ja"}     # this collection is Japanese singles only
    min_interval = 1.0

    def catalogue(self, ctx):
        """Every product in the Japanese singles collection, fetched once per run."""
        if "products" not in ctx.state:
            products, seen = [], set()
            for page in range(1, MAX_PAGES + 1):
                rows = ctx.fetch(COLLECTION_URL.format(limit=PAGE_SIZE, page=page),
                                 as_json=True).get("products", [])
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
        offers = []
        for product, parsed in self.catalogue(ctx):
            hits = [c for c in cards if matches(parsed, c)]
            if not hits:
                continue
            for variant in product.get("variants", []):
                if not variant.get("available") or variant.get("price") is None:
                    continue
                cond = condition(variant)
                title = product.get("title", "")
                if variant.get("option1"):
                    title += f" ({variant['option1']})"
                for card in hits:
                    offers.append(Offer(
                        marketplace=self.id,
                        card_id=card.card_id,
                        url=PRODUCT_PAGE.format(handle=product["handle"], variant=variant["id"]),
                        price=Decimal(str(variant["price"])),
                        currency="GBP",
                        title=title,
                        match=MATCH_EXACT,
                        condition=cond,
                        grade=grade(product, variant),
                    ))
        return offers


PLUGIN = CardCargo()

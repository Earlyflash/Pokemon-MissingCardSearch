"""
TCG House (tcg-house.co.uk), a UK Shopify shop with prices in GBP. This
plugin reads its singles collection, which holds English and Japanese
Pokémon cards side by side.

The shop's robots.txt allows collection pages and its agents.md points
agents at the storefront's public data. The collection serves 250 products a
page and is about 900 products (4 requests), so the plugin reads it once per
run and matches missing cards locally rather than searching card by card.

Titles come in two shapes, both naming the set with the shop's own label:

    Tyrunt Common ME03: Perfect Order 044/088 NM
    Tarountula Holo Art Rare SV1V: Violet ex 079/078 NM
    ME02: Phantasmal Flames #079/094 Ambipom

The label is the only sign of a card's language: Japanese sets carry their
Japanese set code ("SV1V: Violet ex", "M2a: High Class Pack: MEGA Dream
ex"), English ones the English series code ("SV10: Destined Rivals", "ME:
Ascended Heroes"). Codes alone clash ("SV10" is both Destined Rivals and the
Japanese Glory of Team Rocket), so SET_IDS maps each label the shop uses to
a TCGdex language and set id. A label it doesn't know yet still matches when
it reads "<code>: <name>" with the code equal to the card's set id and the
card's set name inside the name, so a new Japanese set works before it's
added. Listings with no set at all ("Dusknoir #037/131"), and reprint
groupings (Deck Exclusives, Prize Pack, Miscellaneous Cards & Products)
whose numbers belong to other sets, never match.

Each product is one copy with a Condition option, always Near Mint so far
("Near Mint", or "Near Mint or Better" in the tags of the second shape).
"""
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import has_phrase, normalize_number, normalize_text

SHOP = "https://tcg-house.co.uk"
COLLECTION_URL = SHOP + "/collections/singles/products.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}?variant={variant}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 100  # safety stop

# The shop's set label -> (TCGdex language, TCGdex set id).
SET_IDS = {normalize_text(label): ids for label, ids in {
    # Japanese
    "SV1S: Scarlet ex": ("ja", "SV1S"), "SV1V: Violet ex": ("ja", "SV1V"),
    "SV1a: Triplet Beat": ("ja", "SV1a"), "SV2P: Snow Hazard": ("ja", "SV2P"),
    "SV2D: Clay Burst": ("ja", "SV2D"), "SV2a: Pokemon Card 151": ("ja", "SV2a"),
    "SV3: Ruler of the Black Flame": ("ja", "SV3"), "SV3a: Raging Surf": ("ja", "SV3a"),
    "SV4K: Ancient Roar": ("ja", "SV4K"), "SV4M: Future Flash": ("ja", "SV4M"),
    "SV4a: Shiny Treasure ex": ("ja", "SV4a"), "SV5K: Wild Force": ("ja", "SV5K"),
    "SV5M: Cyber Judge": ("ja", "SV5M"), "SV5a: Crimson Haze": ("ja", "SV5a"),
    "SV6: Transformation Mask": ("ja", "SV6"), "SV6a: Night Wanderer": ("ja", "SV6a"),
    "SV7: Stellar Miracle": ("ja", "SV7"), "SV7a: Paradise Dragona": ("ja", "SV7a"),
    "SV8: Super Electric Breaker": ("ja", "SV8"), "SV8a: Terastal Festival ex": ("ja", "SV8a"),
    "SV9: Battle Partners": ("ja", "SV9"), "SV9a: Heat Wave Arena": ("ja", "SV9a"),
    "SV10: The Glory of Team Rocket": ("ja", "SV10"),
    "SV11B: Black Bolt": ("ja", "SV11B"), "SV11W: White Flare": ("ja", "SV11W"),
    "S12a: VSTAR Universe": ("ja", "S12a"),
    "M1L: Mega Brave": ("ja", "M1L"), "M1S: Mega Symphonia": ("ja", "M1S"),
    "M2: Inferno X": ("ja", "M2"), "M2a: High Class Pack: MEGA Dream ex": ("ja", "M2a"),
    "M3: Nihil Zero": ("ja", "M3"), "M4: Ninja Spinner": ("ja", "M4"),
    "M5: Abyss Eye": ("ja", "M5"), "M6: Storm Emeralda": ("ja", "M6"),
    # English: Mega Evolution
    "ME01: Mega Evolution": ("en", "me01"), "ME02: Phantasmal Flames": ("en", "me02"),
    "ME: Ascended Heroes": ("en", "me02.5"), "ME03: Perfect Order": ("en", "me03"),
    "ME04: Chaos Rising": ("en", "me04"), "ME05: Pitch Black": ("en", "me05"),
    "ME: 30th Celebration": ("en", "30th"), "ME: Mega Evolution Promo": ("en", "mep"),
    "MEE: Mega Evolution Energies": ("en", "mee"),
    # English: Scarlet & Violet
    "SV01: Scarlet & Violet Base Set": ("en", "sv01"), "SV02: Paldea Evolved": ("en", "sv02"),
    "SV03: Obsidian Flames": ("en", "sv03"), "SV: Scarlet & Violet 151": ("en", "sv03.5"),
    "SV04: Paradox Rift": ("en", "sv04"), "SV: Paldean Fates": ("en", "sv04.5"),
    "SV05: Temporal Forces": ("en", "sv05"), "SV06: Twilight Masquerade": ("en", "sv06"),
    "SV: Shrouded Fable": ("en", "sv06.5"), "SV07: Stellar Crown": ("en", "sv07"),
    "SV08: Surging Sparks": ("en", "sv08"), "SV: Prismatic Evolutions": ("en", "sv08.5"),
    "SV09: Journey Together": ("en", "sv09"), "SV10: Destined Rivals": ("en", "sv10"),
    "SV: Black Bolt": ("en", "sv10.5b"), "SV: White Flare": ("en", "sv10.5w"),
    "SVE: Scarlet & Violet Energies": ("en", "sve"),
    # English: older
    "SWSH04: Vivid Voltage": ("en", "swsh4"), "Shining Fates": ("en", "swsh4.5"),
    "SWSH05: Battle Styles": ("en", "swsh5"), "SWSH06: Chilling Reign": ("en", "swsh6"),
    "SWSH07: Evolving Skies": ("en", "swsh7"), "SWSH08: Fusion Strike": ("en", "swsh8"),
    "SWSH10: Astral Radiance": ("en", "swsh10"), "Pokemon GO": ("en", "swsh10.5"),
    "SWSH11: Lost Origin": ("en", "swsh11"), "SWSH12: Silver Tempest": ("en", "swsh12"),
    "SWSH12: Silver Tempest Trainer Gallery": ("en", "swsh12tg"),
    "SWSH: Sword & Shield Promo Cards": ("en", "swshp"),
    "SM - Guardians Rising": ("en", "sm2"), "SM - Cosmic Eclipse": ("en", "sm12"),
    "SM Promos": ("en", "smp"), "XY - Evolutions": ("en", "xy12"),
    "Generations: Radiant Collection": ("en", "g1"),
}.items()}

NUMBER = r"(?P<number>[A-Za-z]*\d+[a-z]?)(?:/(?P<total>[A-Za-z]*\d+))?"
# "Tyrunt Common ME03: Perfect Order 044/088 NM": the set is somewhere in the head.
TRAILING_NUMBER = re.compile(r"^(?P<head>.+?)\s+" + NUMBER + r"(?:\s+NM)?\s*$")
# "ME02: Phantasmal Flames #079/094 Ambipom": the set is everything before the #.
HASH_NUMBER = re.compile(r"^(?P<head>.*?)\s*#" + NUMBER + r"(?:\s|$)")
# "<code>: <name>" for a label SET_IDS doesn't know; the code needs a digit,
# so series prefixes like "SV:" and "ME:" (always English) aren't read as codes.
CODE_LABEL = re.compile(r"(?:^|\s)(?P<code>[A-Za-z]{1,4}\d{1,2}[A-Za-z]?):\s+(?P<name>.+)$")


def parse_title(title):
    """{number, head} from a product title, or None if it has no card
    number. `head` is the text the set label is in."""
    title = (title or "").strip()
    m = HASH_NUMBER.match(title) or TRAILING_NUMBER.match(title)
    if not m:
        return None
    return {"number": normalize_number(m.group("number")), "head": m.group("head")}


def find_set(head):
    """(language, set id) of the longest known label in `head`, or None."""
    text = normalize_text(head)
    best = None
    for label, ids in SET_IDS.items():
        if (best is None or len(label) > len(best[0])) and has_phrase(text, label):
            best = (label, ids)
    return best[1] if best else None


def matches(parsed, card):
    """True if a parsed title is this card: same number, and its set label
    is the card's set in the card's language."""
    if not parsed or parsed["number"] != normalize_number(card.local_id):
        return False
    known = find_set(parsed["head"])
    if known:
        lang, set_id = known
        return lang == card.tcgdex_lang and normalize_code(set_id) == normalize_code(card.set_id)
    m = CODE_LABEL.search(parsed["head"])
    set_name = normalize_text(card.set_name)
    return bool(m and set_name and normalize_code(m.group("code")) == normalize_code(card.set_id)
                and has_phrase(normalize_text(m.group("name")), set_name))


class TCGHouse(Marketplace):
    id = "tcghouse"
    name = "TCG House"
    languages = {"en", "ja"}
    min_interval = 1.0

    def catalogue(self, ctx):
        """Every product in the singles collection, fetched once per run."""
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
            ctx.debug(f"{len(products)} products in the singles collection")
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
            for variant in product.get("variants", []):
                if not variant.get("available") or variant.get("price") is None:
                    continue
                wording = variant.get("title") or ""
                if wording == "Default Title":  # the second title shape keeps it in a tag
                    wording = " ".join(product.get("tags") or [])
                for card in hits:
                    offers.append(Offer(
                        marketplace=self.id,
                        card_id=card.card_id,
                        url=PRODUCT_PAGE.format(handle=product["handle"], variant=variant["id"]),
                        price=Decimal(str(variant["price"])),
                        currency="GBP",
                        title=product.get("title", ""),
                        match=MATCH_EXACT,
                        condition="NM" if re.search(r"\bNear Mint\b", wording) else None,
                    ))
        return offers


PLUGIN = TCGHouse()

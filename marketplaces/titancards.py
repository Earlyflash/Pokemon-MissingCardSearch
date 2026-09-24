"""
Titan Cards (titancards.co.uk), a UK Shopify shop with prices in GBP. This
plugin reads its Pokémon singles collection, which is almost all English
cards (a handful are Japanese, and say so in the title).

robots.txt allows the public collection pages. The shop's agents.md sits
behind a Cloudflare check, but the collection's products.json answers
directly; it serves 250 products a page and the collection is under a
thousand products (3 requests), so the plugin reads it once per run and
matches missing cards locally rather than searching card by card. Sold-out
cards drop out of the collection, so every listing is normally in stock.

Titles name the card, its number, its rarity and, in brackets, the shop's
own name for the set:

    Gwynn 119/084 Special Illustration Rare Pokemon Card (Mega Evolution Pitch Black)
    Charizard ex 054/091 Double Rare Pokemon Card (SV 4.5 Paldean Fates)
    Donphan 019/025 Ultra Rare Japanese Pokemon Card (Celebrations Classic Collection JP)
    Spark SWSH226 Full Art Pokemon GO Promo Card (SWSH Promo Series)

Dropping the series prefix ("SV", "SWSH04", "Scarlet & Violet", "Mega
Evolution", ...) leaves the set's English name, which SET_NAMES turns into a
TCGdex set id; SET_ALIASES covers the names that don't fit ("Mega Evolution
Base Set ME01"). A set in neither table still matches a card whose set name
equals it once the prefix is dropped, so a new set works before the tables
know it. Black Star promos have no "/total"; their code prefix picks the
promo set, and gallery numbers ("TG05/TG30") pick the gallery's own set.

Each product is one listing with no options. The description says "Near
Mint" for almost every card, which becomes the offer's condition.
"""
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import normalize_number, normalize_text

SHOP = "https://titancards.co.uk"
COLLECTION_URL = SHOP + "/collections/pokemon-singles-uk/products.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}?variant={variant}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 100  # safety stop

# TCGdex's English set name -> set id, for the eras the shop stocks. The
# shop's set name matches one of these once its series prefix is dropped.
SET_NAMES = {normalize_text(name): set_id for name, set_id in {
    "XY": "xy1", "Flashfire": "xy2", "Furious Fists": "xy3", "Phantom Forces": "xy4", "Primal Clash": "xy5",
    "Roaring Skies": "xy6", "Ancient Origins": "xy7", "BREAKthrough": "xy8", "BREAKpoint": "xy9",
    "Fates Collide": "xy10", "Steam Siege": "xy11", "Evolutions": "xy12", "Sun & Moon": "sm1",
    "Guardians Rising": "sm2", "Burning Shadows": "sm3", "Shining Legends": "sm3.5", "Crimson Invasion": "sm4",
    "Ultra Prism": "sm5", "Forbidden Light": "sm6", "Celestial Storm": "sm7", "Dragon Majesty": "sm7.5",
    "Lost Thunder": "sm8", "Team Up": "sm9", "Unbroken Bonds": "sm10", "Unified Minds": "sm11",
    "Hidden Fates": "sm115", "Cosmic Eclipse": "sm12", "Sword & Shield": "swsh1", "Rebel Clash": "swsh2",
    "Darkness Ablaze": "swsh3", "Champion's Path": "swsh3.5", "Vivid Voltage": "swsh4",
    "Shining Fates": "swsh4.5", "Battle Styles": "swsh5", "Chilling Reign": "swsh6", "Evolving Skies": "swsh7",
    "Fusion Strike": "swsh8", "Brilliant Stars": "swsh9", "Astral Radiance": "swsh10", "Pokémon GO": "swsh10.5",
    "Lost Origin": "swsh11", "Silver Tempest": "swsh12", "Crown Zenith": "swsh12.5",
    "Scarlet & Violet": "sv01", "Paldea Evolved": "sv02", "Obsidian Flames": "sv03", "151": "sv03.5",
    "Paradox Rift": "sv04", "Paldean Fates": "sv04.5", "Temporal Forces": "sv05", "Twilight Masquerade": "sv06",
    "Shrouded Fable": "sv06.5", "Stellar Crown": "sv07", "Surging Sparks": "sv08", "Prismatic Evolutions": "sv08.5",
    "Journey Together": "sv09", "Destined Rivals": "sv10", "Black Bolt": "sv10.5b", "White Flare": "sv10.5w",
    "Mega Evolution": "me01", "Phantasmal Flames": "me02", "Ascended Heroes": "me02.5",
    "Perfect Order": "me03", "Chaos Rising": "me04", "Pitch Black": "me05",
}.items()}

# The shop's whole set name -> TCGdex set id, where dropping the series
# prefix doesn't leave one of SET_NAMES.
SET_ALIASES = {normalize_text(name): set_id for name, set_id in {
    "Mega Evolution Base Set ME01": "me01", "Mega Evolution Base Set": "me01",
    "Scarlet & Violet Base": "sv01", "Scarlet & Violet Base Set": "sv01",
    "Sword & Shield Base Set": "swsh1", "Sun & Moon Base Set": "sm1", "XY Base Set": "xy1",
    "Pokemon SV 151": "sv03.5", "Scarlet & Violet 151": "sv03.5",
    "Celebrations 25th Anniversary": "cel25", "Celebrations": "cel25",
    "Celebrations Classic Collection": "cel25c", "Champions Path": "swsh3.5",
    # Japanese: the 25th Anniversary Promo Card Pack, which TCGdex's
    # Japanese dataset doesn't have yet.
    "Celebrations Classic Collection JP": "S8a-P",
    "SWSH Promo Series": "swshp", "SV Promo Series": "svp", "SM Promo Series": "smp",
    "XY Promo Series": "xyp", "Mega Evolution Promo Series": "mep",
}.items()}

# Cards numbered into a set's gallery or vault ("TG05/TG30", "SV107/SV122")
# are their own set in TCGdex.
GALLERIES = {("swsh9", "TG"): "swsh9tg", ("swsh10", "TG"): "swsh10tg", ("swsh11", "TG"): "swsh11tg",
             ("swsh12", "TG"): "swsh12tg", ("swsh12.5", "GG"): "swsh12.5gg",
             ("swsh4.5", "SV"): "swsh4.5sv", ("sm115", "SV"): "sma"}

# A leading series word the shop puts before a set's own name.
SERIES_PREFIX = re.compile(
    r"^(?:pokemon\s+)?(?:mega evolution|scarlet and violet|sword and shield|sun and moon|"
    r"sv|swsh|sm|xy)(?:\s*\d+(?:\s+\d+)?)?\s+")
# Black Star promo code prefix -> TCGdex promo set id.
PROMO_SETS = {"swsh": "swshp", "svp": "svp", "sv": "svp", "sm": "smp", "xy": "xyp", "mep": "mep"}

TITLE = re.compile(r"^\s*(?P<name>.+?)\s+(?P<number>[A-Za-z]{0,4}\d+[a-z]?)/(?P<total>[A-Za-z]{0,4}\d+)\b"
                   r"(?P<rest>.*?)(?:\((?P<set>[^()]+)\))?\s*$")
PROMO = re.compile(r"^\s*(?P<name>.+?)\s+(?P<number>(?P<code>SWSH|SVP|SV|SM|XY|MEP)\d+)\b"
                   r"(?P<rest>.*?)(?:\((?P<set>[^()]+)\))?\s*$", re.I)
JAPANESE = re.compile(r"\bJapanese\b|\bJP\s*$", re.I)


def set_key(shop_set):
    """(TCGdex set id or None, normalised set name without its series
    prefix) for the shop's name for a set."""
    name = normalize_text(shop_set)
    short = SERIES_PREFIX.sub("", name)
    return SET_ALIASES.get(name) or SET_NAMES.get(name) or SET_NAMES.get(short), short


def parse_title(title):
    """{number, name, set_id, set_name, lang, promo} from a product title, or None
    if it names no card number and set (bundles, mystery boxes)."""
    m = TITLE.match(title or "")
    promo = None if m else PROMO.match(title or "")
    if promo:
        m = promo
    if not m or not m.group("set"):
        return None
    set_id, set_name = set_key(m.group("set"))
    if promo:
        set_id = PROMO_SETS[promo.group("code").lower()]
    else:
        prefix = re.match(r"[A-Za-z]*", m.group("number")).group().upper()
        set_id = GALLERIES.get((set_id, prefix), set_id)
    lang = "ja" if JAPANESE.search(m.group("rest") + " " + m.group("set")) else "en"
    return {"number": normalize_number(m.group("number")), "name": m.group("name"),
            "set_id": set_id, "set_name": set_name, "lang": lang, "promo": bool(promo)}


def card_number(card, promo):
    """The card's number as the shop writes it; for a promo, digits only
    since TCGdex writes some promo numbers without their code."""
    number = normalize_number(card.local_id)
    return re.sub(r"^[A-Z]+", "", number) if promo else number


def matches(parsed, card):
    """True if a product is this card: same language, set and number."""
    if not parsed or parsed["lang"] != card.tcgdex_lang:
        return False
    number = re.sub(r"^[A-Z]+", "", parsed["number"]) if parsed["promo"] else parsed["number"]
    if number != card_number(card, parsed["promo"]):
        return False
    if parsed["set_id"]:
        return normalize_code(parsed["set_id"]) == normalize_code(card.set_id)
    return bool(parsed["set_name"]) and parsed["set_name"] == normalize_text(card.set_name)


def condition(product):
    body = normalize_text(re.sub(r"<[^>]+>", " ", product.get("body_html") or ""))
    return "NM" if "near mint" in body else None


class TitanCards(Marketplace):
    id = "titancards"
    name = "Titan Cards"
    languages = {"en", "ja"}   # almost all English; a few Japanese cards
    min_interval = 1.0

    def catalogue(self, ctx):
        """[(product, parsed title)] for the singles collection, fetched
        once per run."""
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
            ctx.debug(f"{len(products)} products in the singles collection")
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
                        condition=condition(product),
                    ))
        return offers


PLUGIN = TitanCards()

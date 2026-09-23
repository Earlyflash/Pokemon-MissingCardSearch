"""
Total Cards (totalcards.net), a UK Shopify shop with prices in GBP. This
plugin reads its Japanese singles collection.

The shop's agents.md lists the public Shopify collection JSON as its
read-only route for agents. It serves 250 products a page; the collection is
about 8,400 products (35 requests), most of them out of stock, so the plugin
reads it once per run and matches missing cards locally rather than
searching card by card.

Titles name the set in English, sometimes after its series, then the card
and its number:

    Pokemon - Pokémon Card 151 - Charizard ex - 201/210
    Pokemon - Mega Evolution - Nihil Zero - Mega Dragonite ex - 095/080
    Pokemon - Terastal Festival ex - Archaludon 113 (Reverse Holo Master Ball)
    Pokemon - Sword & Shield Promos - Pikachu 124/S-P

The shop's English set names are its own translations ("Hot Air Arena",
"Glory of the Rocket Gang"), so SET_IDS maps them to TCGdex set ids; a set
name equal to the card's own set name also matches. A card matches on that
set plus its number, or a promo number's code ("S-P") equal to its set id.
The number after the slash is often the set's full count rather than the
printed total, so it isn't checked.

Most products are one listing with no options. Some carry every language
and condition as options, and those are sold in several languages even in
this Japanese collection, so only variants whose Language is Japanese (or
that have no Language) become offers. Conditions follow the Cardmarket
scale (MT/NM/EX/GD/LP/PL/PO) and map to base.CONDITIONS; a product with no
Condition option has an unknown condition. Graded slabs name the company and
grade in the title ("(PSA 10 Graded Slab)") or in options.
"""
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import normalize_number, normalize_text

SHOP = "https://totalcards.net"
COLLECTION_URL = SHOP + "/collections/pokemon-japanese-single-cards/products.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}?variant={variant}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 200  # safety stop

# The shop's English set name -> TCGdex set id.
SET_IDS = {normalize_text(name): set_id for name, set_id in {
    # Mega Evolution
    "Mega Brave": "M1L", "Mega Symphonia": "M1S", "Inferno X": "M2", "MEGA Dream ex": "M2a",
    "Nihil Zero": "M3", "Ninja Spinner": "M4", "Abyss Eye": "M5", "Storm Emeralda": "M6",
    # Scarlet & Violet
    "Scarlet ex": "SV1S", "Violet ex": "SV1V", "Triplet Beat": "SV1a",
    "Snow Hazard": "SV2P", "Clay Burst": "SV2D",
    "Pokémon Card 151": "SV2a", "Pokemon Card 151": "SV2a", "151": "SV2a",
    "Ruler of the Black Flame": "SV3", "Raging Surf": "SV3a",
    "Ancient Roar": "SV4K", "Future Flash": "SV4M", "Shiny Treasure ex": "SV4a",
    "Wild Force": "SV5K", "Cyber Judge": "SV5M", "Crimson Haze": "SV5a",
    "Mask of Change": "SV6", "Night Wanderer": "SV6a",
    "Stellar Miracle": "SV7", "Paradise Dragona": "SV7a",
    "Super Electric Breaker": "SV8", "Supercharged Breaker": "SV8", "Terastal Festival ex": "SV8a",
    "Battle Partners": "SV9", "Hot Air Arena": "SV9a", "Heat Wave Arena": "SV9a",
    "Glory of the Rocket Gang": "SV10", "Glory of Team Rocket": "SV10", "The Glory of Team Rocket": "SV10",
    "Black Bolt": "SV11B", "White Flare": "SV11W",
    # Sword & Shield
    "Sword": "S1W", "Shield": "S1H", "VMAX Rising": "S1a", "Rebellion Crash": "S2",
    "Explosive Walker": "S2a", "Infinity Zone": "S3", "Legendary Heartbeat": "S3a",
    "Shocking Volt Tackle": "S4", "Amazing Volt Tackle": "S4", "Shiny Star V": "S4a",
    "Single Strike Master": "S5I", "Rapid Strike Master": "S5R", "Matchless Fighters": "S5a",
    "Silver Lance": "S6H", "Jet-Black Spirit": "S6K", "Jet-Black Poltergeist": "S6K", "Eevee Heroes": "S6a",
    "Blue Sky Stream": "S7R", "Skyscraping Perfection": "S7D", "Towering Perfection": "S7D",
    "Fusion Arts": "S8", "25th Anniversary Collection": "S8a", "VMAX Climax": "S8b",
    "Star Birth": "S9", "Battle Region": "S9a",
    "Time Gazer": "S10D", "Space Juggler": "S10P", "Dark Phantasma": "S10a", "Pokémon GO": "S10b",
    "Lost Abyss": "S11", "Incandescent Arcana": "S11a", "Paradigm Trigger": "S12", "VSTAR Universe": "S12a",
    # Sun & Moon
    "Forbidden Light": "SM6", "Dragon Storm": "SM6a", "Champion Road": "SM6b",
    "Sky-Splitting Charisma": "SM7", "Thunderclap Spark": "SM7a", "Fairy Rise": "SM7b",
    "Super-Burst Impact": "SM8", "Dark Order": "SM8a", "GX Ultra Shiny": "SM8b",
    "Tag Bolt": "SM9", "Night Unison": "SM9a", "Full Metal Wall": "SM9b",
    "Double Blaze": "SM10", "GG End": "SM10a", "Sky Legend": "SM10b",
    "Miracle Twin": "SM11", "Remix Bout": "SM11a", "Dream League": "SM11b",
    "Alter Genesis": "SM12", "Tag All Stars": "SM12a",
    "Collection Sun": "SM1S", "Collection Moon": "SM1M", "Islands Await You": "SM2K",
    "Alolan Moonlight": "SM2L", "To Have Seen the Battle Rainbow": "SM3H",
    "Darkness that Consumes Light": "SM3N", "Shining Legends": "SM3+", "Awakened Heroes": "SM4S",
    "Ultradimensional Beasts": "SM4A", "GX Battle Boost": "SM4+", "Ultra Sun": "SM5S",
    "Ultra Moon": "SM5M", "Ultra Force": "SM5+",
    # XY
    "Collection X": "XY1a", "Collection Y": "XY1b", "Wild Blaze": "XY2", "Rising Fist": "XY3",
    "Phantom Gate": "XY4", "Gaia Volcano": "XY5a", "Tidal Storm": "XY5b", "Emerald Break": "XY6",
    "Bandit Ring": "XY7", "Blue Shock": "XY8a", "Red Flash": "XY8b",
    "Rage of the Broken Heavens": "XY9", "Awakening Psychic King": "XY10",
    "Fever-Burst Fighter": "XY11a", "Cruel Traitor": "XY11b", "Double Crisis": "CP1",
    "Legendary Shine Collection": "CP2", "Pokekyun Collection": "CP3", "20th Anniversary": "CP6",
    # Legend, DP-era PCG, e-Card and Neo
    "HeartGold Collection": "L1a", "SoulSilver Collection": "L1b", "Reviving Legends": "L2",
    "Clash at the Summit": "L3", "Legendary Flight": "PCG1", "Clash of the Blue Sky": "PCG2",
    "Rocket Gang Strikes Back": "PCG3", "Golden Sky, Silvery Ocean": "PCG4", "Mirage Forest": "PCG5",
    "Holon Research Tower": "PCG6", "Holon Phantoms": "PCG7", "Miracle Crystal": "PCG8",
    "Pokémon Card VS": "VS1", "Pokemon Card VS": "VS1",
    "Gold, Silver, to a New World...": "neo1", "Crossing the Ruins...": "neo2",
    "Awakening Legends": "neo3", "Darkness, and to Light...": "neo4",
    # Original era
    "Expansion Pack": "PMCG1", "Pokémon Jungle": "PMCG2", "Pokemon Jungle": "PMCG2",
    "Mystery of the Fossils": "PMCG3", "Rocket Gang": "PMCG4", "Leaders' Stadium": "PMCG5",
    "Challenge from the Darkness": "PMCG6",
}.items()}

PREFIX = re.compile(r"^\s*Pok[eé]mon\s*-\s*", re.I)
# The number is the title's last word, give or take bracketed notes and stray
# condition or language words ("005/086 Japanese", "033/102 NM / Japanese").
NUMBER = re.compile(r"(?:^|\s)(?P<number>[A-Za-z]{0,3}\d+[a-z]?)(?:/(?P<total>[A-Za-z0-9-]+))?"
                    r"(?:\s*\([^)]*\)|\s+(?:MT|NM|EX|GD|LP|PL|PO|Japanese|JPN|JP)\b|\s*/)*\s*$", re.I)
LANGUAGE_SUFFIX = re.compile(r"\s+(?:JP|JPN|Japanese)$", re.I)
CONDITIONS = {"MT": "NM", "NM": "NM", "EX": "LP", "GD": "MP", "LP": "MP", "PL": "HP", "PO": "DMG"}
GRADE = re.compile(r"\b(PSA|BGS|CGC|SGC|ACE|TAG|ARS|GetGraded|Beckett)\b[^()\d]{0,20}?(\d+(?:\.\d)?)\b", re.I)


def parse_title(title):
    """{number, sets, promo} from a product title, or None if it doesn't end
    with a card number. `sets` is every normalised " - " segment before the
    one holding the number (the set, and any series name before it);
    `promo` is the code of a promo number ("S-P" in "124/S-P")."""
    body = PREFIX.sub("", title or "")
    m = NUMBER.search(body)
    if not m:
        return None
    total = m.group("total") or ""
    segments = [s.strip() for s in body[:m.start()].split(" - ")]
    # The last segment holds the card name (and, when the number shares it,
    # nothing else), so only the ones before it can name the set.
    sets = [normalize_text(LANGUAGE_SUFFIX.sub("", s)) for s in segments[:-1] if s]
    return {"number": normalize_number(m.group("number")), "sets": sets,
            "promo": total if total and not total.isdigit() else None}


def matches(parsed, card):
    """True if a parsed title is this card: same card number, and the set
    tied by promo code, SET_IDS or set name."""
    if not parsed or parsed["number"] != normalize_number(card.local_id):
        return False
    set_id = normalize_code(card.set_id)
    if parsed["promo"]:
        return normalize_code(parsed["promo"]) == set_id
    set_name = normalize_text(card.set_name)
    return any(normalize_code(SET_IDS.get(s)) == set_id or (set_name and s == set_name)
               for s in parsed["sets"])


def options(product, variant):
    """{option name: value} for one variant, e.g. {"Language": "Japanese",
    "Condition": "NM"}."""
    names = [o.get("name") for o in product.get("options", [])]
    return {name: variant.get(f"option{i}") for i, name in enumerate(names, 1) if name}


def grade(product, opts):
    """'PSA 10', 'ACE 10', ... from the variant's grading options or the
    title, else None."""
    company = opts.get("Grading Company")
    level = opts.get("Grade") or opts.get("Grading")
    if company and level:
        return f"{company.upper()} {level}"
    # Only in brackets, so a card name like "TAG TEAM GX 001/173" isn't read as a grade.
    m = next(filter(None, map(GRADE.search, re.findall(r"\(([^)]*)\)", product.get("title", "")))), None)
    if not m:
        return None
    name = m.group(1)
    name = "BGS" if name.lower() == "beckett" else "GetGraded" if name.lower() == "getgraded" else name.upper()
    return f"{name} {m.group(2)}"


class TotalCards(Marketplace):
    id = "totalcards"
    name = "Total Cards"
    languages = {"ja"}     # this collection is Japanese singles; other languages are filtered out
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
                opts = options(product, variant)
                if opts.get("Language", "Japanese") != "Japanese":
                    continue
                title = product.get("title", "")
                if variant.get("title") and variant["title"] != "Default Title":
                    title += f" ({variant['title']})"
                for card in hits:
                    offers.append(Offer(
                        marketplace=self.id,
                        card_id=card.card_id,
                        url=PRODUCT_PAGE.format(handle=product["handle"], variant=variant["id"]),
                        price=Decimal(str(variant["price"])),
                        currency="GBP",
                        title=title,
                        match=MATCH_EXACT,
                        condition=CONDITIONS.get((opts.get("Condition") or "").strip().upper()),
                        grade=grade(product, opts),
                    ))
        return offers


PLUGIN = TotalCards()

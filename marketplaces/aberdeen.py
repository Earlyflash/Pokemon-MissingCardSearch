"""
Aberdeen Collectables (aberdeencollectables.co.uk), a UK Shopify shop with
prices in GBP. This plugin reads its raw singles and graded cards
collections, which mix Japanese, English, Korean and Chinese cards.

The shop's agents.md lists the public Shopify product JSON as a read-only
route for agents, and robots.txt allows collections. Both collections are
small (about 85 raw singles and 16 graded cards), so the plugin reads each
once per run and matches missing cards locally.

Every product says the same things three ways, not always consistently:

    title: Pikachu ex #044/193 – MEGA Dream ex (Japanese)
    tags:  Set: MEGA Dream ex, Language: Japanese, Condition: Lightly Played,
           Finish: Holo (graded cards add Grader: PSA, Grade: 10)
    body:  Set: MEGA Dream ex (M2a) ... Card number: 044/193

The handle, and sometimes the Set tag, are left over from another product
the listing was copied from, so the handle is never read and the Set tag
only when the body names no set. A card
matches on its print language (the Language tag), its number and its set.
The set comes from the code in brackets after the body's set name, a promo
number's code ("S-P" in "143/S-P"), or the set name: the shop's English
names for Japanese, Korean and Chinese sets map through Total Cards'
SET_IDS table (plus a few more here), English codes and names through
EN_SETS, and any name equal to the card's own set name matches too.

The shop's data has typos, so a match is only `uncertain` when the listing
contradicts itself (the title and body give different card numbers, or the
set code and set name point at different sets) or when the title's card
name doesn't agree with the card's English name. Each product has one
variant, one physical copy; only copies in stock become offers.
"""
import html
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, MATCH_UNCERTAIN, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import normalize_number, normalize_text
from marketplaces.radams import name_agrees
from marketplaces.totalcards import SET_IDS

SHOP = "https://aberdeencollectables.co.uk"
COLLECTIONS = ("raw-singles", "graded-cards-1")
COLLECTION_URL = SHOP + "/collections/{collection}/products.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}?variant={variant}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 50   # safety stop

# The Language tag -> TCGdex dataset code.
LANGUAGES = {"japanese": "ja", "english": "en", "korean": "ko",
             "simplified chinese": "zh-cn", "traditional chinese": "zh-tw"}
CONDITIONS = {"near mint": "NM", "mint": "NM", "lightly played": "LP", "light play": "LP",
              "moderately played": "MP", "moderate play": "MP",
              "heavily played": "HP", "heavy play": "HP", "damaged": "DMG"}

# Asian-print set names the shop uses that Total Cards' SET_IDS doesn't have.
EXTRA_SET_IDS = {normalize_text(name): set_id for name, set_id in {
    "Miracle Twins": "SM11", "The Glory of Team Rocket": "SV10", "ADV Expansion Pack": "ADV1",
    "Neo Discovery": "neo2", "20th Anniversary Collection": "CP6",
}.items()}

# English sets: TCGdex's official abbreviation (the code the shop puts in
# brackets, "PFL") and its set name, both -> TCGdex en set id. Generated from
# api.tcgdex.net/v2/en/sets/{id} on 2026-09-24.
EN_SETS = {normalize_text(key): set_id for key, set_id in {
    "BS": "base1", "JU": "base2", "FO": "base3", "B2": "base4", "RO": "base5", "G1": "gym1",
    "G2": "gym2", "N1": "neo1", "N2": "neo2", "SI": "si1", "N3": "neo3", "N4": "neo4", "LC": "lc",
    "EX": "ecard1", "AQ": "ecard2", "SK": "ecard3", "RS": "ex1", "SS": "ex2", "DR": "ex3",
    "MA": "ex4", "HL": "ex5", "TK1A": "tk-ex-latia", "TK1O": "tk-ex-latio", "RG": "ex6",
    "P1": "pop1", "TR": "ex7", "DX": "ex8", "EM": "ex9", "P2": "pop2", "UF": "ex10", "DS": "ex11",
    "LM": "ex12", "TK2M": "tk-ex-m", "TK2P": "tk-ex-p", "P3": "pop3", "HP": "ex13", "P4": "pop4",
    "CG": "ex14", "DF": "ex15", "PK": "ex16", "P5": "pop5", "DP": "dp1", "MT": "dp2",
    "TK3L": "tk-dp-l", "TK3M": "tk-dp-m", "P6": "pop6", "SW": "dp3", "GE": "dp4", "P7": "pop7",
    "MD": "dp5", "LA": "dp6", "P8": "pop8", "SF": "dp7", "PL": "pl1", "P9": "pop9", "RR": "pl2",
    "SV": "pl3", "AR": "pl4", "RM": "ru1", "HS": "hgss1", "TK4R": "tk-hs-r", "TK4G": "tk-hs-g",
    "UL": "hgss2", "UND": "hgss3", "TRI": "hgss4", "COL": "col1", "BLW": "bw1", "BWP": "bwp",
    "MCD11": "2011bw", "EP": "bw2", "TK5E": "tk-bw-e", "TK5Z": "tk-bw-z", "NVI": "bw3",
    "NXD": "bw4", "DEX": "bw5", "MCD12": "2012bw", "DRX": "bw6", "DRV": "dv1", "BCR": "bw7",
    "PLS": "bw8", "PLF": "bw9", "PLB": "bw10", "XYP": "xyp", "LTR": "bw11", "KSS": "xy0",
    "XY": "xy1", "TK6N": "tk-xy-n", "TK6S": "tk-xy-sy", "FLF": "xy2", "MCD14": "2014xy",
    "FFI": "xy3", "TK7A": "tk-xy-b", "TK7B": "tk-xy-w", "PHF": "xy4", "PRC": "xy5", "DCR": "dc1",
    "TK8O": "tk-xy-latio", "TK8A": "tk-xy-latia", "ROS": "xy6", "AOR": "xy7", "BKT": "xy8",
    "MCD15": "2015xy", "BKP": "xy9", "GEN": "g1", "TK9P": "tk-xy-p", "TK9S": "tk-xy-su",
    "FCO": "xy10", "STS": "xy11", "MCD16": "2016xy", "EVO": "xy12", "SUM": "sm1", "SMP": "smp",
    "TK10A": "tk-sm-r", "TK10L": "tk-sm-l", "GRI": "sm2", "MCD17": "2017sm", "BUS": "sm3",
    "SLG": "sm3.5", "CIN": "sm4", "UPR": "sm5", "FLI": "sm6", "CES": "sm7", "DRM": "sm7.5",
    "MCD18": "2018sm", "LOT": "sm8", "TEU": "sm9", "DET": "det1", "UNB": "sm10", "UNM": "sm11",
    "HIF": "sm115", "MCD19": "2019sm", "CEC": "sm12", "SSH": "swsh1", "RCL": "swsh2",
    "DAA": "swsh3", "FUT20": "fut2020", "CPA": "swsh3.5", "VIV": "swsh4", "MCD21": "2021swsh",
    "SHF": "swsh4.5", "SHF:SV": "swsh4.5sv", "BST": "swsh5", "CRE": "swsh6", "EVS": "swsh7",
    "CEL:CC": "cel25cc", "CEL": "cel25", "FST": "swsh8", "BRS:TG": "swsh9tg", "BRS": "swsh9",
    "ASR": "swsh10", "ASR:TG": "swsh10tg", "PGO": "swsh10.5", "MCD22": "2022swsh", "LOR": "swsh11",
    "LOR:TG": "swsh11tg", "SIT": "swsh12", "SIT:TG": "swsh12tg", "CRZ": "swsh12.5",
    "CRZ:GG": "swsh12.5gg", "SVI": "sv01", "SVE": "sve", "SVP": "svp", "PAL": "sv02",
    "MCD23": "2023sv", "OBF": "sv03", "MEW": "sv03.5", "MFB": "mfb", "PAR": "sv04",
    "PAF": "sv04.5", "TEF": "sv05", "TWM": "sv06", "SFA": "sv06.5", "SCR": "sv07", "SSP": "sv08",
    "MCD24": "2024sv", "PRE": "sv08.5", "JTG": "sv09", "DRI": "sv10", "BLK": "sv10.5b",
    "WHT": "sv10.5w", "MEE": "mee", "MEG": "me01", "MEP": "mep", "PFL": "me02", "ASC": "me02.5",
    "POR": "me03", "CRI": "me04", "PBL": "me05",
    # The shop's own names and codes for promos.
    "SWSH": "swshp", "Sword & Shield Black Star Promos": "swshp",
    "Scarlet & Violet Black Star Promos": "svp", "Mega Evolution Black Star Promos": "mep",
}.items()}
EN_SETS.update({normalize_text(name): set_id for name, set_id in {
    "Miscellaneous Promos": "miscp", "Base Set": "base1", "Jungle": "base2",
    "Wizards Black Star Promos": "basep", "W Promotional": "wp", "Fossil": "base3",
    "Jumbo cards": "jumbo", "Base Set 2": "base4", "Team Rocket": "base5", "Gym Heroes": "gym1",
    "Gym Challenge": "gym2", "Neo Genesis": "neo1", "Neo Discovery": "neo2",
    "Southern Islands": "si1", "Neo Revelation": "neo3", "Neo Destiny": "neo4",
    "Legendary Collection": "lc", "Sample": "sp", "Expedition Base Set": "ecard1",
    "Best of game": "bog", "Aquapolis": "ecard2", "Skyridge": "ecard3", "Ruby & Sapphire": "ex1",
    "Sandstorm": "ex2", "Nintendo Black Star Promos": "np", "Dragon": "ex3",
    "Team Magma vs Team Aqua": "ex4", "Hidden Legends": "ex5",
    "EX trainer Kit (Latias)": "tk-ex-latia", "EX trainer Kit (Latios)": "tk-ex-latio",
    "Poké Card Creator Pack": "ex5.5", "FireRed & LeafGreen": "ex6", "POP Series 1": "pop1",
    "Team Rocket Returns": "ex7", "Deoxys": "ex8", "Emerald": "ex9", "POP Series 2": "pop2",
    "Unseen Forces": "ex10", "Unseen Forces Unown Collection": "exu", "Delta Species": "ex11",
    "Legend Maker": "ex12", "EX trainer Kit 2 (Minun)": "tk-ex-m",
    "EX trainer Kit 2 (Plusle)": "tk-ex-p", "POP Series 3": "pop3", "Holon Phantoms": "ex13",
    "POP Series 4": "pop4", "Crystal Guardians": "ex14", "Dragon Frontiers": "ex15",
    "Power Keepers": "ex16", "POP Series 5": "pop5", "DP Black Star Promos": "dpp",
    "Diamond & Pearl": "dp1", "Mysterious Treasures": "dp2", "DP trainer Kit (Lucario)": "tk-dp-l",
    "DP trainer Kit (Manaphy)": "tk-dp-m", "POP Series 6": "pop6", "Secret Wonders": "dp3",
    "Great Encounters": "dp4", "POP Series 7": "pop7", "Majestic Dawn": "dp5",
    "Legends Awakened": "dp6", "POP Series 8": "pop8", "Stormfront": "dp7", "Platinum": "pl1",
    "POP Series 9": "pop9", "Rising Rivals": "pl2", "Supreme Victors": "pl3", "Arceus": "pl4",
    "Pokémon Rumble": "ru1", "HeartGold SoulSilver": "hgss1", "HGSS Black Star Promos": "hgssp",
    "HS trainer Kit (Raichu)": "tk-hs-r", "HS trainer Kit (Gyarados)": "tk-hs-g",
    "Unleashed": "hgss2", "Undaunted": "hgss3", "Triumphant": "hgss4", "Call of Legends": "col1",
    "Black & White": "bw1", "BW Black Star Promos": "bwp", "McDonald's Collection 2011": "2011bw",
    "Emerging Powers": "bw2", "BW trainer Kit (Excadrill)": "tk-bw-e",
    "BW trainer Kit (Zoroark)": "tk-bw-z", "Noble Victories": "bw3", "Next Destinies": "bw4",
    "Dark Explorers": "bw5", "McDonald's Collection 2012": "2012bw", "Dragons Exalted": "bw6",
    "Dragon Vault": "dv1", "Boundaries Crossed": "bw7", "Plasma Storm": "bw8",
    "Plasma Freeze": "bw9", "Plasma Blast": "bw10", "XY Black Star Promos": "xyp",
    "Radiant Collection": "rc", "Legendary Treasures": "bw11", "Kalos Starter Set": "xy0",
    "XY": "xy1", "Yellow A Alternate": "xya", "XY trainer Kit (Noivern)": "tk-xy-n",
    "XY trainer Kit (Sylveon)": "tk-xy-sy", "Flashfire": "xy2",
    "McDonald's Collection 2014": "2014xy", "Furious Fists": "xy3",
    "XY trainer Kit (Bisharp)": "tk-xy-b", "XY trainer Kit (Wigglytuff)": "tk-xy-w",
    "Phantom Forces": "xy4", "Primal Clash": "xy5", "Double Crisis": "dc1",
    "XY trainer Kit (Latios)": "tk-xy-latio", "XY trainer Kit (Latias)": "tk-xy-latia",
    "Roaring Skies": "xy6", "Ancient Origins": "xy7", "BREAKthrough": "xy8",
    "McDonald's Collection 2015": "2015xy", "BREAKpoint": "xy9", "Generations": "g1",
    "XY trainer Kit (Pikachu Libre)": "tk-xy-p", "XY trainer Kit (Suicune)": "tk-xy-su",
    "Fates Collide": "xy10", "Steam Siege": "xy11", "McDonald's Collection 2016": "2016xy",
    "Evolutions": "xy12", "Sun & Moon": "sm1", "SM Black Star Promos": "smp",
    "SM trainer Kit (Alolan Raichu)": "tk-sm-r", "SM trainer Kit (Lycanroc)": "tk-sm-l",
    "Guardians Rising": "sm2", "McDonald's Collection 2017": "2017sm", "Burning Shadows": "sm3",
    "Shining Legends": "sm3.5", "Crimson Invasion": "sm4", "Ultra Prism": "sm5",
    "Forbidden Light": "sm6", "Celestial Storm": "sm7", "Dragon Majesty": "sm7.5",
    "McDonald's Collection 2018": "2018sm", "Lost Thunder": "sm8", "Team Up": "sm9",
    "Detective Pikachu": "det1", "Unbroken Bonds": "sm10", "Unified Minds": "sm11",
    "Hidden Fates Shiny Vault": "sma", "Hidden Fates": "sm115",
    "McDonald's Collection 2019": "2019sm", "Cosmic Eclipse": "sm12",
    "SWSH Black Star Promos": "swshp", "Sword & Shield": "swsh1", "Rebel Clash": "swsh2",
    "Darkness Ablaze": "swsh3", "Pokémon Futsal 2020": "fut2020", "Champion's Path": "swsh3.5",
    "Vivid Voltage": "swsh4", "McDonald's Collection 2021": "2021swsh", "Shining Fates": "swsh4.5",
    "Shining Fates Shiny Vault": "swsh4.5sv", "Battle Styles": "swsh5", "Chilling Reign": "swsh6",
    "Evolving Skies": "swsh7", "Celebrations Classic Collection": "cel25cc",
    "Celebrations": "cel25", "Fusion Strike": "swsh8",
    "Brilliant Stars Trainer Gallery": "swsh9tg", "Brilliant Stars": "swsh9",
    "Astral Radiance": "swsh10", "Astral Radiance Trainer Gallery": "swsh10tg",
    "Pokémon GO": "swsh10.5", "McDonald's Collection 2022": "2022swsh", "Lost Origin": "swsh11",
    "Lost Origin Trainer Gallery": "swsh11tg", "Silver Tempest": "swsh12",
    "Silver Tempest Trainer Gallery": "swsh12tg", "Crown Zenith": "swsh12.5",
    "Crown Zenith Galarian Gallery": "swsh12.5gg", "Scarlet & Violet": "sv01",
    "Scarlet & Violet Energy": "sve", "SVP Black Star Promos": "svp", "Paldea Evolved": "sv02",
    "McDonald's Collection 2023": "2023sv", "Obsidian Flames": "sv03", "151": "sv03.5",
    "My First Battle": "mfb", "Paradox Rift": "sv04", "Paldean Fates": "sv04.5",
    "Temporal Forces": "sv05", "Twilight Masquerade": "sv06", "Shrouded Fable": "sv06.5",
    "Stellar Crown": "sv07", "Surging Sparks": "sv08", "McDonald's Collection 2024": "2024sv",
    "Prismatic Evolutions": "sv08.5", "Journey Together": "sv09", "Destined Rivals": "sv10",
    "Black Bolt": "sv10.5b", "White Flare": "sv10.5w", "Mega Evolution Energy": "mee",
    "Mega Evolution": "me01", "MEP Black Star Promos": "mep", "Phantasmal Flames": "me02",
    "Ascended Heroes": "me02.5", "Perfect Order": "me03", "Chaos Rising": "me04",
    "Pitch Black": "me05", "30th Celebration": "30th", "30th Classic Collection": "30th-c",
}.items() if normalize_text(name) not in EN_SETS})

TITLE_NUMBER = re.compile(r"#\s*(?P<number>No\.\s*\d+|[^\s/]+)(?:/(?P<total>[^\s/]+))?")
BODY_FIELD = re.compile(r"<li>\s*(?P<key>[^:<]+):\s*(?P<value>[^<]*)</li>")
SET_CODE = re.compile(r"^(?P<name>.*?)\s*\((?P<code>[^()]*)\)\s*$")
NAME_PREFIX = re.compile(r"^(?:pokemon japanese\s+|.*\u2014\s*)", re.I)


def number(raw):
    """(number, promo code) from "044/193", "143/S-P", "SWSH049" or "093";
    (None, None) for a Pokédex number ("No. 217") or nothing."""
    m = TITLE_NUMBER.match("#" + (raw or "").strip().lstrip("#"))
    if not raw or not m or m.group("number").lower().startswith("no"):
        return None, None
    total = m.group("total") or ""
    return normalize_number(m.group("number")), (normalize_code(total) if total and not total.isdigit() else None)


def set_ids(name, code, lang):
    """Normalised TCGdex set ids a shop set name and bracketed code point at:
    (from the code, from the name)."""
    name = normalize_text(NAME_PREFIX.sub("", html.unescape(name or "")))
    code = normalize_code(html.unescape(code or ""))
    if lang == "en":
        by_code = normalize_code(EN_SETS.get(normalize_text(code), code)) if code else None
        by_name = normalize_code(EN_SETS.get(name)) or None
    else:
        by_code = code or None
        by_name = normalize_code(SET_IDS.get(name) or EXTRA_SET_IDS.get(name)) or None
    return by_code, by_name


def parse(product):
    """What the plugin needs from one product: language, card numbers, set
    ids and names, card name, and whether the listing contradicts itself."""
    tags = {}
    for tag in product.get("tags", []):
        key, _, value = tag.partition(":")
        tags.setdefault(key.strip().lower(), value.strip())
    body = {m.group("key").strip().lower(): html.unescape(m.group("value")).strip()
            for m in BODY_FIELD.finditer(product.get("body_html") or "")}
    lang = LANGUAGES.get((tags.get("language") or body.get("language") or "").lower())

    title = product.get("title", "")
    card_name, _, rest = title.partition("#")
    m = TITLE_NUMBER.match("#" + rest)
    numbers, promos = set(), set()
    for raw in (m.group(0) if m else None, body.get("card number")):
        n, promo = number(raw)
        if n:
            numbers.add(n)
        if promo:
            promos.add(promo)

    # The Set tag is sometimes left over from another product the listing was
    # copied from, so it only counts when the body names no set.
    set_line = body.get("set") or tags.get("set") or ""
    sm = SET_CODE.match(set_line)
    set_name, code = (sm.group("name"), sm.group("code")) if sm else (set_line, None)
    by_code, by_name = set_ids(set_name, code, lang)
    names = {normalize_text(NAME_PREFIX.sub("", html.unescape(set_name)))} - {""}
    ids = {i for i in (by_code, by_name) if i} | promos
    # "ADV" for ADV1 is a loose code, not a different set; "m15" for M1S is.
    sets_disagree = bool(by_code and by_name and not by_name.startswith(by_code))
    conflict = len(numbers) > 1 or sets_disagree
    return {"lang": lang, "numbers": numbers, "set_ids": ids, "set_names": names,
            "card_name": card_name.strip(), "conflict": conflict, "tags": tags,
            "finish": body.get("card finish") or tags.get("finish") or "",
            "condition": body.get("condition") or tags.get("condition") or ""}


def match(parsed, card):
    """MATCH_EXACT or MATCH_UNCERTAIN if this product is the card, else None."""
    if parsed["lang"] != card.tcgdex_lang or normalize_number(card.local_id) not in parsed["numbers"]:
        return None
    if normalize_code(card.set_id) not in parsed["set_ids"] \
            and normalize_text(card.set_name) not in parsed["set_names"]:
        return None
    english = card.name_en or (card.name if card.tcgdex_lang == "en" else None)
    if parsed["conflict"] or (english and not name_agrees(english, parsed["card_name"])):
        return MATCH_UNCERTAIN
    return MATCH_EXACT


def grade(tags):
    """'PSA 10', 'GetGraded 9.5', ... from the Grader and Grade tags, else None."""
    grader, level = tags.get("grader"), tags.get("grade")
    if not grader or not level:
        return None
    return f"{grader.upper() if len(grader) <= 4 else grader} {level}"


class AberdeenCollectables(Marketplace):
    id = "aberdeen"
    name = "Aberdeen Collectables"
    languages = set(LANGUAGES.values())
    min_interval = 1.0

    def catalogue(self, ctx):
        """Every product in the raw singles and graded collections, fetched once per run."""
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
            ctx.debug(f"{len(products)} products in the raw singles and graded collections")
            ctx.state["products"] = [(p, parse(p)) for p in products]
        return ctx.state["products"]

    def search_set(self, cards, ctx):
        offers = []
        for product, parsed in self.catalogue(ctx):
            hits = [(c, level) for c in cards for level in [match(parsed, c)] if level]
            if not hits:
                continue
            tags = parsed["tags"]
            title = product.get("title", "")
            finish = parsed["finish"]
            if finish and finish.lower() not in ("regular", "holo"):
                title += f" ({finish})"
            graded = grade(tags)
            for variant in product.get("variants", []):
                if not variant.get("available") or variant.get("price") is None:
                    continue
                for card, level in hits:
                    offers.append(Offer(
                        marketplace=self.id,
                        card_id=card.card_id,
                        url=PRODUCT_PAGE.format(handle=product["handle"], variant=variant["id"]),
                        price=Decimal(str(variant["price"])),
                        currency="GBP",
                        title=title,
                        match=level,
                        condition=None if graded else CONDITIONS.get(parsed["condition"].lower()),
                        grade=graded,
                    ))
        return offers


PLUGIN = AberdeenCollectables()

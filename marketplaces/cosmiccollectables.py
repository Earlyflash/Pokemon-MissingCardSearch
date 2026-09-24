"""
Cosmic Collectables (cosmiccollectables.co.uk), a UK Shopify shop with prices
in GBP. This plugin reads its Japanese singles collection.

The shop's agents.md and robots.txt allow its public storefront, and the
collection is under a thousand products, so the plugin reads the whole
collection JSON once per run (250 products a page, about 3 requests) and
matches missing cards locally. The site's Cloudflare front answers quick
repeat requests with a 429 challenge, so requests are spaced out.

Most titles carry the series, set name, set code and number:

    SWORD AND SHIELD, Shiny Star V (s4a) - 216/190 : Cinderace (Shiny Vault)
    Japanese - SCARLET & VIOLET, Shiny Treasure ex (sv4a) - 135/190 : Noivern ex (Half Art)
    Heat Wave Arena sv9a - 003/063 : Yanmega ex (Half Art) *Japanese*
    Mega Brave m1L- 003/063 : Mega Venusaur ex (Half Art) *Japanese*
    Japanese - SCARLET & VIOLET - Promos 069/SV-P : Glaceon (Holo)

so a card matches on number plus set: the title's code equal to its TCGdex
set id, or a promo number's code ("SV-P") equal to its set id. A few titles
(graded copies mostly) name the set without a code:

    PSA - Pokemon - Sword & Shield, Vmax Climax - 232/184 : Sylveon GX (Full Art) - PSA 10

and those take the code other titles in the collection give the same set
name ("VMAX Climax (s8b)"). The shop's Set_ tags and SKUs are not used: both
are sometimes wrong (sv9a tagged "Destined Rivals", SKU numbers off by ten).

The collection also holds a few Korean copies ("*Korean*", "Korean - ...",
or a Language_Korean tag); those never match. Each product has one variant,
its own listing with a stock flag; only listings in stock become offers. The
description gives the condition ("Card is in NM-M+ condition.").
"""
import re
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, Marketplace, Offer
from marketplaces.cardcargo import normalize_code
from marketplaces.deckdhq import normalize_number, normalize_text

SHOP = "https://cosmiccollectables.co.uk"
COLLECTION_URL = SHOP + "/collections/single-cards-japanese/products.json?limit={limit}&page={page}"
PRODUCT_PAGE = SHOP + "/products/{handle}?variant={variant}"
PAGE_SIZE = 250  # Shopify's maximum
MAX_PAGES = 100  # safety stop

CODE = r"[A-Za-z]{1,3}\d+[A-Za-z]?"
# "..., Shiny Star V (s4a) - 216/190 :", "Heat Wave Arena sv9a - 003/063 :", "Mega Brave m1L- 003/063 :"
WITH_CODE = re.compile(r"(?:^|,)\s*(?P<name>[^,]*?)\s*(?:\((?P<code>" + CODE + r")\)|\b(?P<bare>" + CODE + r"))"
                       r"\s*-\s*(?P<number>\d+)/(?P<total>\d+)\b")
# "Promo - 168/S-P :", "Promos 069/SV-P :"
PROMO = re.compile(r"\s(?P<number>\d+)/(?P<promo>[A-Za-z]+-P)\b")
# "..., Vmax Climax - 232/184 :" or "sv5K, Wild Force - 093/071 :" (no code in brackets)
WITH_NAME = re.compile(r"(?:^|-\s)\s*(?P<series>[^,:-]*?),\s*(?P<name>[^,:]+?)\s+-\s+(?P<number>\d+)/(?P<total>\d+)\b")
KOREAN = re.compile(r"\*Korean\*|^\s*Korean\b", re.I)
GRADE = re.compile(r"-\s*(PSA|BGS|CGC|SGC|ACE|TAG|Beckett)\s*(\d+(?:\.\d)?)\s*$", re.I)
CONDITION = re.compile(r"Card is in (?P<condition>[A-Z+-]+) condition", re.I)
CONDITIONS = {"NM": "NM", "NM-M": "NM", "M": "NM", "LP": "LP", "EX": "LP", "MP": "MP", "HP": "HP", "DMG": "DMG"}


def parse_title(title):
    """{number, set_id, set_name} from a product title, or None if it has no
    card number. `set_id` is the normalised code (see normalize_code), or None
    when the title only names the set; `set_name` is the normalised set name
    in front of the number, when there is one."""
    title = title or ""
    m = PROMO.search(title)
    if m:
        return {"number": normalize_number(m.group("number")), "set_id": normalize_code(m.group("promo")),
                "set_name": None}
    m = WITH_CODE.search(title)
    if m:
        name = re.sub(r"^Japanese\s*-\s*", "", m.group("name"), flags=re.I)
        return {"number": normalize_number(m.group("number")),
                "set_id": normalize_code(m.group("code") or m.group("bare")),
                "set_name": normalize_text(name) or None}
    m = WITH_NAME.search(title)
    if m:
        series = m.group("series").strip()
        code = normalize_code(series) if re.fullmatch(CODE, series) else None
        return {"number": normalize_number(m.group("number")), "set_id": code,
                "set_name": normalize_text(m.group("name"))}
    return None


def learn_codes(parsed_titles):
    """{normalised set name: set code} from titles that give both, so a
    title naming only the set can borrow the code. A name seen with two
    different codes is left out."""
    codes, clashes = {}, set()
    for parsed in parsed_titles:
        if parsed and parsed["set_id"] and parsed["set_name"]:
            if codes.setdefault(parsed["set_name"], parsed["set_id"]) != parsed["set_id"]:
                clashes.add(parsed["set_name"])
    for name in clashes:
        del codes[name]
    return codes


def is_korean(product):
    return bool(KOREAN.search(product.get("title") or "")) or "Language_Korean" in (product.get("tags") or [])


def matches(parsed, card):
    """True if a parsed title is this card: same card number and set id."""
    return bool(parsed) and bool(parsed["set_id"]) and parsed["number"] == normalize_number(card.local_id) \
        and parsed["set_id"] == normalize_code(card.set_id)


def condition(product):
    """NM/LP/... from the description's "Card is in NM-M+ condition.", else None."""
    m = CONDITION.search(re.sub(r"<[^>]+>", " ", product.get("body_html") or ""))
    return CONDITIONS.get(m.group("condition").upper().rstrip("+")) if m else None


def grade(product):
    """'PSA 10', ... when the title ends with a grading company and grade, else None."""
    m = GRADE.search(product.get("title") or "")
    if not m:
        return None
    company = m.group(1).upper() if m.group(1).lower() != "beckett" else "BGS"
    return f"{company} {m.group(2)}"


class CosmicCollectables(Marketplace):
    id = "cosmiccollectables"
    name = "Cosmic Collectables"
    languages = {"ja"}     # this collection is Japanese singles (Korean copies are skipped)
    min_interval = 5.0     # Cloudflare challenges quicker repeat requests

    def catalogue(self, ctx):
        """(product, parsed title) for every product in the Japanese singles
        collection, fetched once per run. Korean copies parse to None."""
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
            parsed = [None if is_korean(p) else parse_title(p.get("title")) for p in products]
            codes = learn_codes(parsed)
            for p in parsed:
                if p and not p["set_id"] and p["set_name"]:
                    p["set_id"] = codes.get(p["set_name"])
            ctx.debug(f"{len(products)} products in the Japanese singles collection")
            ctx.state["products"] = list(zip(products, parsed))
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
                graded = grade(product)
                for card in hits:
                    offers.append(Offer(
                        marketplace=self.id,
                        card_id=card.card_id,
                        url=PRODUCT_PAGE.format(handle=product["handle"], variant=variant["id"]),
                        price=Decimal(str(variant["price"])),
                        currency="GBP",
                        title=product.get("title", ""),
                        match=MATCH_EXACT,
                        condition=None if graded else condition(product),
                        grade=graded,
                    ))
        return offers


PLUGIN = CosmicCollectables()

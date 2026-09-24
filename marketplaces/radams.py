"""
Radam's Poké Stop (radamspokestop.co.uk), a UK Squarespace shop with prices
in GBP, selling English, Japanese, Korean and Chinese singles.

Squarespace's JSON view (`?format=json`) is disallowed by the shop's
robots.txt, so the plugin reads the ordinary shop pages instead: the "shop
all" listing shows 200 products a page (about 10 pages for the whole shop),
read once per run and matched locally. Each product carries structured
tags as CSS classes, which is what makes matching reliable:

    tag-language-japanese tag-graded-no tag-set-sv2a-151-jpn tag-rarity-poke-ball

while the title is free text written by hand:

    Rapidash Poke Ball #078 sv2a 151 JPN
    Pikachu 12/30 #034 30th Celebrations 30C
    Snorlax GX #001/SM-p JPN Promo
    Manaphy #SWSH275 Promo

A card matches on language (the language tag), card number (the "#" number
in the title, or "SWSH275"-style promo numbers) and set: a set code in the
set tag or title ("sv2a", "cs4ac") equal to the card's TCGdex set id, a
promo code after the number ("001/SM-p") equal to its set id, or, for
English sets, the set tag naming the card's set ("swsh-evolving-skies").
The product URL also carries the number ("rapidash-078/165-sv2a-151"); when
it names a different number than the title, the listing is a typo one way
or the other and is only shown as "uncertain".

Matched products in stock are then opened one by one (their page embeds the
variant list), because a product can hold several copies in different
conditions and prices ("from £1.00" in the listing).
"""
import difflib
import html
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from marketplaces.base import MATCH_EXACT, MATCH_UNCERTAIN, Marketplace, Offer
from marketplaces.cardcargo import GRADE, normalize_code
from marketplaces.deckdhq import normalize_number, normalize_text

SHOP = "https://www.radamspokestop.co.uk"
LIST_URL = SHOP + "/shop-all"
MAX_PAGES = 50  # safety stop; the shop was 10 pages in September 2026

# The shop's language tags, as TCGdex dataset codes.
LANGUAGES = {
    "english": "en",
    "japanese": "ja",
    "korean": "ko",
    "simplified-chinese": "zh-cn",
    "traditional-chinese": "zh-tw",
}
# Words in set tags that say the language, not the set.
LANGUAGE_WORDS = {"jpn", "japanese", "kor", "korean", "chn", "chinese", "english", "eng"}
# Era prefixes on English set tags ("swsh-evolving-skies", "xy-evolutions").
ERA_WORDS = {"swsh", "xy", "bw", "sm", "dp", "hgss", "sv", "ex"}
# Letter prefixes English promo numbers carry ("SVP011"), by TCGdex promo set id.
PROMO_PREFIXES = {"svp": "SVP", "swshp": "SWSH", "mep": "MEP", "smp": "SM", "xyp": "XY", "bwp": "BW"}
# The shop's English set tags that don't spell out the TCGdex set name
# (a few are typos), as set_name_key() of that name.
TAG_ALIASES = {
    "expedition": "expedition base set",
    "sm": "sun moon",                       # "sm-base"
    "origins": "ancient origins",           # "xy-origins"
    "plamsa blast": "plasma blast",
    "diamon pearl": "diamond pearl",
    "m24": "mcdonald collection 2024",
}
CONDITIONS = {"NM": "NM", "EX": "LP", "LP": "LP", "MP": "MP", "HP": "HP", "DMG": "DMG", "D": "DMG"}

ITEM = re.compile(r'<div\s+class="product-list-item\s+([^"]*)"')
SET_CODE = re.compile(r"^[a-z]{1,4}\d+(?:\.\d+)?[a-z]*$")
TITLE_NUMBER = re.compile(r"#\s*([A-Za-z]{0,4}\d+[A-Za-z]?)(?:\s*/\s*([A-Za-z0-9-]+))?")
BARE_NUMBER = re.compile(r"(?<![\d/])(\d{1,3})/(\d{1,3})(?![\d/])")
TITLE_CODE = re.compile(r"(?<![#A-Za-z0-9])[A-Za-z]{1,4}\d+(?:\.\d+)?[A-Za-z]*\b")
URL_NUMBER = re.compile(r"(?<![a-z0-9])([a-z]{0,4}\d{1,3}[a-z]?)/([a-z0-9-]*?\d+|[a-z]+-?[a-z]*)(?=-|$)")


def _text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def parse_listing_page(page):
    """[{id, title, url, price, sold_out, tags}] for every product on one
    "shop all" page, plus the next page's URL (or None)."""
    products = []
    starts = [m for m in ITEM.finditer(page)]
    for i, m in enumerate(starts):
        block = page[m.start():starts[i + 1].start() if i + 1 < len(starts) else len(page)]
        classes = m.group(1).split()
        tags = {}
        for c in classes:
            if c.startswith("tag-"):
                key, _, value = c[4:].partition("-")
                tags.setdefault(key, []).append(value)
        pid = re.search(r'data-product-id="([^"]+)"', block)
        href = re.search(r'class="product-list-item-link"\s+href=("?)([^"\s>]+)\1', block)
        title = re.search(r'product-list-item-title">(.*?)</div>', block, re.S)
        price = re.search(r'product-list-item-price">(.*?)</div>\s*</div>', block, re.S)
        if not (href and title):
            continue
        products.append({
            "id": pid.group(1) if pid else None,
            "title": _text(title.group(1)),
            "url": urljoin(SHOP, href.group(2)),
            "price": list_price(_text(price.group(1)) if price else ""),
            "sold_out": "sold-out" in classes,
            "tags": tags,
        })
    nxt = re.search(r'class="list-pagination-next"\s+href="([^"]+)"', page)
    return products, (urljoin(SHOP, html.unescape(nxt.group(1))) if nxt else None)


def list_price(text):
    """The price a listing shows: '£1.30' -> 1.30; 'Sale Price: £3.50
    Original Price: £3.80' -> 3.50; 'from £1.00' -> 1.00 (the cheapest copy)."""
    m = re.search(r"£\s*([\d,]+(?:\.\d+)?)", text or "")
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def promo_code(total):
    """The promo set code written where a set total would be ("SM-p" in
    "001/SM-p"), or None for a real total ("165", "TG30", "GG70")."""
    return total if total and not re.search(r"\d", total) else None


def title_number(title):
    """(number, promo code) from a title's "#" number: '#078' -> ('78', None),
    '#001/SM-p' -> ('1', 'SM-p'), '#SWSH275' -> ('SWSH275', None). A
    "12/30"-style subset number before the "#" is ignored; with no "#" at
    all, a "058/078" number counts instead."""
    m = TITLE_NUMBER.search(title or "")
    if not m:
        bare = BARE_NUMBER.search(title or "")
        return (normalize_number(bare.group(1)), None) if bare else (None, None)
    return normalize_number(m.group(1)), promo_code(m.group(2))


def url_numbers(url):
    """Card numbers in a product URL's "078/165"-style fragments, and any
    promo code in their place ('272/s-p' -> promo 's-p')."""
    slug = url.rsplit("/p/", 1)[-1].lower()
    numbers, promos = set(), set()
    for number, total in URL_NUMBER.findall(slug):
        numbers.add(normalize_number(number))
        if promo_code(total):
            promos.add(total)
    return numbers, promos


def set_keys(product):
    """(codes, names) that identify a product's set: set codes ("sv2a",
    "cs4ac") from the title, or from the set tags when the title has none
    (the title wins because a few tags are wrong, e.g. "sv11b-white-flare"),
    and English set names from the set tags, each with and without an era
    prefix ("swsh-"), a leading "30c"/"25th", a trailing "base" or a plural."""
    tag_codes, names = set(), set()
    for tag in product["tags"].get("set", []):
        words = [w for w in tag.split("-") if w not in LANGUAGE_WORDS]
        if not words:
            continue
        if SET_CODE.match(words[0]):
            tag_codes.add(normalize_code(words[0]))
        variants = {tuple(words)}
        if len(words) > 1 and (words[0] in ERA_WORDS or re.search(r"\d", words[0])):
            variants.add(tuple(words[1:]))
        for v in list(variants):
            if len(v) > 1 and v[-1] == "base":
                variants.add(v[:-1])
        for v in list(variants):
            if v[-1].endswith("s"):
                variants.add(v[:-1] + (v[-1][:-1],))
        for v in variants:
            key = " ".join(v)
            names.add(key)
            if key in TAG_ALIASES:
                names.add(TAG_ALIASES[key])
    # "#"-numbers are card numbers, not set codes, so TITLE_CODE skips them.
    title_codes = {normalize_code(m.group()) for m in TITLE_CODE.finditer(product["title"])}
    return title_codes or tag_codes, names


def set_name_key(name):
    """'Scarlet & Violet' -> 'scarlet violet', "McDonald's Collection" ->
    'mcdonald collection', to compare with set tags."""
    return " ".join(w for w in normalize_text(name).split() if w not in ("and", "s"))


def name_agrees(card_name, title):
    """True if an English card's name shows up in the title, allowing the
    shop's typos ("Amoongus ex" for Amoonguss ex): some word of four or more
    letters in the name is close to a word in the title, or, for short names
    ("Mew"), the whole word is there."""
    title_words = normalize_text(title).split()
    words = [w for w in normalize_text(card_name).split() if len(w) >= 4] or normalize_text(card_name).split()
    return any(w in title_words or (len(w) >= 4 and difflib.get_close_matches(w, title_words, 1, 0.8))
               for w in words)


def prepare(product):
    number, promo = title_number(product["title"])
    numbers, promos = url_numbers(product["url"])
    codes, names = set_keys(product)
    product = dict(product)
    product.update(number=number, promo=promo or (sorted(promos)[0] if promos else None),
                   url_numbers=numbers, codes=codes, names=names,
                   lang=LANGUAGES.get((product["tags"].get("language") or [""])[0]))
    return product


def match(product, card):
    """MATCH_EXACT or MATCH_UNCERTAIN if this product is the card, else None."""
    if not product["number"] or product["lang"] != card.tcgdex_lang:
        return None
    set_id = normalize_code(card.set_id)
    number = normalize_number(card.local_id)
    prefix = PROMO_PREFIXES.get(card.set_id.lower())
    if prefix and not card.local_id.upper().startswith(prefix):
        prefixed = normalize_number(prefix + card.local_id)
    else:
        prefixed = normalize_number(card.local_id) if prefix else None
    if prefixed and prefixed[0].isalpha() and product["number"] == prefixed:
        found = True  # "SVP011": the prefix pins the promo set on its own
    elif product["number"] != number:
        return None
    elif product["promo"]:
        found = normalize_code(product["promo"]) == set_id
    elif set_id in product["codes"]:
        found = True
    else:
        wanted = set_name_key(card.set_name)
        found = wanted in product["names"] or (
            # "#TG12 Brilliant Stars" is in "Brilliant Stars Trainer Gallery"
            number[0].isalpha() and any(wanted.startswith(n + " ") for n in product["names"]))
    if not found:
        return None
    if product["url_numbers"] and product["number"] not in product["url_numbers"]:
        return MATCH_UNCERTAIN  # title and URL disagree on the number
    if card.tcgdex_lang == "en" and card.name and not name_agrees(card.name, product["title"]):
        return MATCH_UNCERTAIN  # right number and set, but another card's name
    return MATCH_EXACT


def variants(page):
    """The product's variants from its page: [{price, condition, quantity}]."""
    m = re.search(r'"variants":\[', page)
    if not m:
        return []
    try:
        rows, _ = json.JSONDecoder().raw_decode(page, m.end() - 1)
    except ValueError:
        return []
    out = []
    for v in rows:
        price = v.get("salePrice") if v.get("onSale") else v.get("price")
        try:
            price = Decimal(str((price or {}).get("decimalValue")))
        except InvalidOperation:
            continue
        stock = v.get("stock") or {}
        quantity = None if stock.get("unlimited") else stock.get("quantity")
        raw = str((v.get("attributes") or {}).get("Condition") or "").strip()
        out.append({"price": price, "quantity": quantity, "condition_text": raw,
                    "condition": CONDITIONS.get(raw.upper())})
    return out


def grade(product):
    if (product["tags"].get("graded") or [""])[0] != "yes":
        return None
    m = GRADE.search(product["title"])
    if not m:
        return "graded"
    company = m.group(1).upper() if m.group(1).lower() != "beckett" else "BGS"
    return f"{company} {m.group(2)}"


class Radams(Marketplace):
    id = "radams"
    name = "Radam's Poké Stop"
    languages = set(LANGUAGES.values())
    min_interval = 1.0

    def catalogue(self, ctx):
        """Every product in the shop, fetched once per run."""
        if "products" not in ctx.state:
            products, seen, url = [], set(), LIST_URL
            for _ in range(MAX_PAGES):
                rows, url = parse_listing_page(ctx.fetch(url))
                for row in rows:
                    if row["url"] not in seen:
                        seen.add(row["url"])
                        products.append(prepare(row))
                if not url:
                    break
            ctx.debug(f"{len(products)} products in the shop")
            ctx.state["products"] = products
        return ctx.state["products"]

    def offers_for(self, product, ctx):
        """(price, condition, quantity, label) for each copy in stock. Falls
        back to the listing's own price if the product page can't be read."""
        try:
            found = variants(ctx.fetch(product["url"]))
        except Exception as e:  # noqa: BLE001 -- keep the listing price instead
            ctx.log(f"{product['url']}: {e}")
            found = []
        if not found:
            return [(product["price"], None, None, "")] if product["price"] is not None else []
        copies = [v for v in found if v["quantity"] != 0]
        return [(v["price"], v["condition"], v["quantity"],
                 f" (Condition: {v['condition_text']})" if v["condition_text"] else "") for v in copies]

    def search_set(self, cards, ctx):
        offers = []
        for product in self.catalogue(ctx):
            if product["sold_out"]:
                continue
            hits = [(c, m) for c in cards for m in [match(product, c)] if m]
            if not hits:
                continue
            for price, cond, quantity, label in self.offers_for(product, ctx):
                for card, level in hits:
                    offers.append(Offer(
                        marketplace=self.id,
                        card_id=card.card_id,
                        url=product["url"],
                        price=price,
                        currency="GBP",
                        title=product["title"] + label,
                        match=level,
                        condition=cond,
                        grade=grade(product),
                        quantity=quantity,
                    ))
        return offers


PLUGIN = Radams()

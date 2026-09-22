"""
DeckdHQ (deckdhq.com), a UK marketplace with prices in GBP.

The site's own public JSON API (no login) lists every active Pokémon listing
a page at a time; the whole catalogue is only ~11 pages, so the plugin reads
all of it once per run and matches missing cards against it locally rather
than searching card by card.

Listings come in three kinds, flagged by `externalSource`:
  * native (null) and Shopify imports ("shopify") carry a set name and a
    plain card number, so they match on set + number + language: "exact".
  * eBay imports ("ebay") usually have no set name; the set only appears in
    the listing title (the raw eBay title). Those match on the set name
    appearing in the title plus the card number: "likely".

`language` is "English", "Japanese" or null. Null doesn't mean English (some
Shopify rows are Japanese sets), so a null language is accepted but the
match is capped at "likely", unless the title names another language.

Prices use `buyerPrice`: the card price with Deckd's buyer fee included,
in pounds, excluding shipping.
"""
import re
import unicodedata
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, MATCH_LIKELY, CONDITIONS, Marketplace, Offer

API = "https://prod-deckd-backend.onrender.com/api"
LISTINGS_URL = API + "/listings?type=WTS&game=pokemon&status=active&limit=100&page={page}"
LISTING_PAGE = "https://www.deckdhq.com/listing/{id}"
MAX_PAGES = 100  # safety stop in case totalPages is ever wrong

# Language words sellers put in titles, for listings whose `language` is null.
TITLE_LANGUAGES = {
    "japanese": "japanese", "jpn": "japanese", "jp": "japanese",
    "korean": "korean", "kor": "korean",
    "chinese": "chinese",
    "english": "english", "eng": "english",
}


def normalize_number(raw):
    """Same rule as missing_cards.normalize_number, plus inner spaces dropped:
    '073/072' -> '73', 'TG05/TG30' -> 'TG5', 'SVP 176' -> 'SVP176'."""
    s = re.sub(r"\s+", "", str(raw or "").strip().lstrip("#").split("/")[0].upper())
    return re.sub(r"\d+", lambda m: str(int(m.group())), s)


def normalize_text(s):
    """Lowercase, accents and punctuation stripped, '&' as 'and', words
    separated by single spaces: 'Pokémon TCG: Scarlet & Violet' ->
    'pokemon tcg scarlet and violet'."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", s))


def has_phrase(haystack, phrase):
    """Whole-word phrase match on normalize_text() output."""
    return bool(phrase) and f" {phrase} " in f" {haystack} "


def title_numbers(title):
    """Card numbers written in a title, e.g. 'Charmander 044 Scarlet & Violet'."""
    return {normalize_number(t) for t in re.findall(r"\b[A-Za-z]{0,4}\d{1,3}(?:/[A-Za-z]{0,4}\d{1,3})?\b",
                                                    title or "")}


def listing_language(listing):
    """'english', 'japanese', ... or None when neither the field nor the title says."""
    if listing.get("language"):
        lang = listing["language"].lower()
        # One "Japanese"-tagged listing was really Korean; trust the title over the tag.
        words = normalize_text(listing.get("cardName")).split()
        if lang != "korean" and ("kor" in words or "korean" in words):
            return "korean"
        return lang
    for word in normalize_text(listing.get("cardName")).split():
        if word in TITLE_LANGUAGES:
            return TITLE_LANGUAGES[word]
    return None


def set_matches(listing, card):
    """(matched, from_title): does the listing belong to the card's set?"""
    wanted = normalize_text(card.set_name)
    code = normalize_text(card.set_id)
    set_name = listing.get("setName")
    if set_name:
        have = normalize_text(set_name)
        if have == wanted:
            return True, False
        # Shopify rows sometimes prefix a set code: "m2 Inferno X", "Black Star Promo SVP".
        words = have.split()
        if words and (words[0] == code or words[-1] == code):
            return True, False
        if len(words) > 1 and " ".join(words[1:]) == wanted:
            return True, False
        return False, False
    return has_phrase(normalize_text(listing.get("cardName")), wanted), True


def number_matches(listing, card):
    """Card numbers equal, also when the listing prefixes the set code
    ('SVP 176' for Scarlet & Violet promo 176)."""
    want = normalize_number(card.local_id)
    if listing.get("cardNumber"):
        have = {normalize_number(listing["cardNumber"])}
    else:
        have = title_numbers(listing.get("cardName"))
    code = normalize_number(card.set_id).replace(".", "")
    return want in have or any(h.startswith(code) and h[len(code):] == want for h in have if code)


def match_level(listing, card):
    """MATCH_EXACT / MATCH_LIKELY if the listing is this card, else None."""
    ok, from_title = set_matches(listing, card)
    if not ok or not number_matches(listing, card):
        return None
    lang = listing_language(listing)
    if lang is not None and lang != (card.language or "").lower():
        return None
    return MATCH_LIKELY if from_title or lang is None else MATCH_EXACT


def describe(listing):
    parts = [listing.get("cardName") or ""]
    if listing.get("setName"):
        parts.append(f"({listing['setName']})")
    if listing.get("gradingCompany") or listing.get("grade"):
        parts.append(f"[graded {listing.get('gradingCompany') or ''} {listing.get('grade') or ''}]"
                     .replace("  ", " "))
    return " ".join(p for p in parts if p)


class DeckdHQ(Marketplace):
    id = "deckdhq"
    name = "DeckdHQ"
    languages = None       # English and Japanese both listed
    min_interval = 1.0

    def catalogue(self, ctx):
        """Every active Pokémon listing, fetched once per run."""
        if "listings" not in ctx.state:
            listings, seen, page, pages = [], set(), 1, 1
            while page <= min(pages, MAX_PAGES):
                data = ctx.fetch(LISTINGS_URL.format(page=page), as_json=True, timeout=90)
                pages = int(data.get("totalPages") or 1)
                for row in data.get("data", []):
                    if row.get("id") not in seen:  # rows can shift pages mid-fetch
                        seen.add(row.get("id"))
                        listings.append(row)
                page += 1
            ctx.debug(f"{len(listings)} active listings across {pages} page(s)")
            ctx.state["listings"] = listings
        return ctx.state["listings"]

    def search_set(self, cards, ctx):
        offers = []
        for listing in self.catalogue(ctx):
            if listing.get("buyerPrice") is None or listing.get("isBundle"):
                continue
            for card in cards:
                level = match_level(listing, card)
                if level is None:
                    continue
                condition = listing.get("condition")
                offers.append(Offer(
                    marketplace=self.id,
                    card_id=card.card_id,
                    url=LISTING_PAGE.format(id=listing["id"]),
                    price=Decimal(str(listing["buyerPrice"])),
                    currency="GBP",
                    title=describe(listing),
                    match=level,
                    condition=condition if condition in CONDITIONS else None,
                    seller=(listing.get("seller") or {}).get("username"),
                ))
        return offers


PLUGIN = DeckdHQ()

"""
PulseAPI (pulseapi.dev), the pricing API behind PulseTCG (pulsetcg.io),
prices in GBP.

PulseAPI publishes a market price per card rather than listings, so this is a
price-guide plugin: the core reports it apart from real listings. The price
is `prices.market_price`, PulseAPI's UK market price for a near-mint,
ungraded copy (graded and played variants are separate products and are left
out). When a card has no UK price, its blended UK+US `market_price_global` is
used and the title says so.

It needs an API key from the PulseAPI dashboard in the PULSEAPI_KEY
environment variable. The free tier allows 20 requests a minute, so requests
are spaced accordingly, and a 429 waits out its Retry-After and tries again.

PulseAPI's set codes aren't TCGdex's (its 151 is "sv3pt5" where TCGdex says
"sv03.5"), so each set is found once per run: first by trying the TCGdex set
id and its usual spellings as PulseAPI's `set_id` filter, then, if none of
those is a set PulseAPI has in the card's language, by searching a few missing cards by name and
taking the set the matching numbers come from. The whole set is then read
(100 cards a page) and cards match on set plus card number, so a set costs a
few requests however many cards are missing from it.
"""
import difflib
import json
import re
import time
import urllib.error
import urllib.parse
from collections import Counter
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, MATCH_LIKELY, Marketplace, Offer

SEARCH_URL = "https://q.pulseapi.dev/api/v1/cards/search?{query}"
CARD_URL = "https://pulsetcg.io/card/{slug}"
PAGE_SIZE = 100          # the free tier's maximum
MAX_PAGES = 20           # no Pokemon set has 2,000 ungraded near-mint cards
NAME_PROBES = 3          # missing cards to search by name when the set id guesses miss
MAX_RETRIES = 3          # 429s in a row before giving up on a request
MAX_RETRY_WAIT = 65      # seconds; the per-minute window is the one normally hit


def norm_number(number):
    """'199/165' -> '199', '002' -> '2', 'tg05/tg30' -> 'TG5', so PulseAPI's
    numbers and TCGdex's local ids compare equal."""
    first = str(number or "").split("/")[0].strip().upper()
    return re.sub(r"(?<!\d)0+(?=\d)", "", first)


def set_id_guesses(set_id):
    """PulseAPI set codes to try for a TCGdex set id: the id itself, lowercase,
    and pokemontcg.io's spelling (TCGdex 'sv03.5' -> 'sv3pt5', 'swsh07' ->
    'swsh7')."""
    guesses = [set_id, set_id.lower()]
    m = re.fullmatch(r"([a-z]+)0*(\d+)(\.5)?", set_id.lower())
    if m:
        guesses.append(f"{m.group(1)}{m.group(2)}{'pt5' if m.group(3) else ''}")
    return list(dict.fromkeys(g for g in guesses if g))


def is_plain_copy(hit):
    """An ungraded, near-mint product (no 7th condition slot in its id)."""
    return not hit.get("graded_by") and len(str(hit.get("product_id", "")).split("|")) == 6


def money(value):
    return None if value in (None, "") else Decimal(str(value))


class PulseAPI(Marketplace):
    id = "pulseapi"
    name = "PulseAPI"
    languages = None       # it prices English and Japanese cards; the language filter does the rest
    needs = ("PULSEAPI_KEY",)
    min_interval = 3.1     # free tier: 20 requests a minute
    price_guide = True
    guide_label = "market price"
    guide_prefix = ""
    guide_description = "PulseAPI's UK market price for a near-mint ungraded copy"

    def __init__(self, sleep=time.sleep):
        self._sleep = sleep

    def _get(self, ctx, **params):
        params = {k: v for k, v in params.items() if v not in (None, "")}
        url = SEARCH_URL.format(query=urllib.parse.urlencode(params))
        headers = {"x-api-key": ctx.config["PULSEAPI_KEY"]}
        for attempt in range(MAX_RETRIES + 1):
            try:
                body = ctx.fetch(url, headers=headers, as_json=True)
                break
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise RuntimeError("PulseAPI refused the key in PULSEAPI_KEY (401)") from None
                if e.code != 429 or attempt == MAX_RETRIES:
                    raise
                wait = min(MAX_RETRY_WAIT, int(e.headers.get("Retry-After") or 60) if e.headers else 60)
                ctx.progress(f"rate limited; waiting {wait}s")
                self._sleep(wait)
        if not body.get("success", True):
            raise RuntimeError(f"PulseAPI error: {json.dumps(body.get('error'))}")
        return body.get("data") or [], body.get("meta") or {}

    def _search(self, ctx, language, **params):
        return self._get(ctx, language=language, exclude_graded="true",
                         exclude_conditioned="true", limit=PAGE_SIZE, **params)

    def find_set(self, cards, ctx):
        """(PulseAPI set_id, first page of its cards) for these missing cards,
        or (None, []) when PulseAPI doesn't seem to have the set."""
        first = cards[0]
        for guess in set_id_guesses(first.set_id):
            data, meta = self._search(ctx, first.language, set_id=guess)
            hits = [h for h in data if str(h.get("set_id", "")).lower() == guess.lower()]
            if hits:  # the filter is exact, so any card back means it's the set
                ctx.debug(f"{first.set_name}: PulseAPI set_id {hits[0]['set_id']!r}")
                return hits[0]["set_id"], (data, meta)
        votes, names = Counter(), {}
        for card in cards[:NAME_PROBES]:
            for q in dict.fromkeys(n for n in (card.name_en, card.name) if n and len(n) >= 2):
                data, _ = self._search(ctx, card.language, q=q)
                hits = [h for h in data if h.get("set_id")
                        and norm_number(h.get("card_number")) == norm_number(card.local_id)]
                for h in hits:
                    votes[h["set_id"]] += 1
                    names[h["set_id"]] = h.get("set_name") or ""
                if hits:
                    break
        if not votes:
            return None, ([], {})
        similarity = lambda sid: difflib.SequenceMatcher(  # noqa: E731
            None, names[sid].lower(), (first.set_name or "").lower()).ratio()
        best = max(votes, key=lambda sid: (votes[sid], similarity(sid)))
        ctx.debug(f"{first.set_name}: PulseAPI set_id {best!r} ({names[best]}) from a name search")
        return best, None

    def set_cards(self, set_id, language, first_page, ctx):
        """Every plain copy PulseAPI has in the set, by normalised card number."""
        if first_page is None:
            first_page = self._search(ctx, language, set_id=set_id)
        data, meta = first_page
        hits = list(data)
        pages = min(int(meta.get("total_pages") or 1), MAX_PAGES)
        for page in range(2, pages + 1):
            more, _ = self._search(ctx, language, set_id=set_id, page=page)
            hits.extend(more)
        by_number = {}
        for h in hits:
            if is_plain_copy(h) and str(h.get("set_id", "")).lower() == set_id.lower():
                by_number.setdefault(norm_number(h.get("card_number")), []).append(h)
        return by_number

    def search_set(self, cards, ctx):
        set_id, first_page = self.find_set(cards, ctx)
        if set_id is None:
            ctx.log(f"{cards[0].set_name}: PulseAPI doesn't seem to have this set "
                    f"in {cards[0].language}")
            return []
        by_number = self.set_cards(set_id, cards[0].language, first_page, ctx)
        offers = []
        for card in cards:
            hits = by_number.get(norm_number(card.local_id), [])
            # Several products share a number when a card has more than one
            # finish (e.g. a reverse holo). Missing cards don't say which
            # finish, so prefer the standard print, else offer each.
            standard = [h for h in hits if not h.get("material") and not h.get("promo_info")]
            chosen = standard or hits
            for h in chosen:
                offer = self.offer(card, h, MATCH_EXACT if len(chosen) == 1 else MATCH_LIKELY)
                if offer:
                    offers.append(offer)
        return offers

    def offer(self, card, hit, match):
        prices = hit.get("prices") or {}
        price, note = money(prices.get("market_price")), "UK market price"
        if price is None:
            price, note = money(prices.get("market_price_global")), "UK+US market price"
        if price is None:
            return None
        variant = ", ".join(v for v in (hit.get("material"), hit.get("promo_info")) if v)
        title = f"{hit.get('card_name', '')} {hit.get('card_number', '')} ({hit.get('set_name', '')})"
        title += f" {variant}" if variant else ""
        slug = hit.get("slug")
        return Offer(
            marketplace=self.id,
            card_id=card.card_id,
            url=CARD_URL.format(slug=urllib.parse.quote(slug)) if slug else "https://pulsetcg.io",
            price=price,
            currency="GBP",
            title=f"{note}: {title}",
            match=match,
        )


PLUGIN = PulseAPI()

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
environment variable (or .env). Requests go out as fast as PulseAPI allows:
when a per-minute limit is hit (20 a minute on the free tier), the 429 reply's
Retry-After is waited out and the request tried again, so a small run isn't
held back by pacing meant for a big one. A daily or monthly quota that's used
up stops the search with a message instead of waiting hours.

There's no endpoint that prices a list of TCGdex cards: the batch endpoint
(paid tier only) takes PulseAPI's own product ids, which only come from a
search, and a search already returns prices. So reading a set costs one
search per page, 500 cards a page on a paid key or 100 on the free tier.

PulseAPI's set codes aren't TCGdex's (its 151 is "sv3pt5" where TCGdex says
"sv03.5", and Japanese sets carry a suffix, "m2_jp"), so each set is found
once: first by trying the TCGdex set id and its usual spellings as PulseAPI's
`set_id` filter, then, if none of those is a set PulseAPI has in the card's
language, by searching a few missing cards by name and taking the set their
numbers come from, provided more than one card agrees or the set's name is
close to the one RareCandy gives. The whole set is then
read and cards match on set plus card number, so a set costs a few requests
however many cards are missing from it. Which PulseAPI set (or none) each
TCGdex set turned out to be is kept in the cache folder, so later runs skip
straight to reading the set.

PulseAPI sometimes sends a card with every price empty (on 2026-11-15 its
03:05 sync blanked 37 of 66 cards that were priced the night before, and
they were back that evening). So the last price each product had is kept in
the cache folder too, and a card that comes back without one is priced at
that, for up to LAST_PRICE_DAYS, with the title saying when it was seen.
Each set's cards that came back without a price, or weren't there at all,
are logged with PulseAPI's synced_at time, to work out why it happens.
"""
import datetime
import difflib
import json
import os
import re
import time
import urllib.error
import urllib.parse
from collections import Counter
from decimal import Decimal

from marketplaces.base import MATCH_EXACT, MATCH_LIKELY, Marketplace, Offer

SEARCH_URL = "https://q.pulseapi.dev/api/v1/cards/search?{query}"
CARD_URL = "https://pulsetcg.io/card/{slug}"
PAGE_SIZES = (500, 100)  # the paid tier's maximum, then the free tier's
MAX_PAGES = 20           # no Pokemon set has 2,000 ungraded near-mint cards
NAME_PROBES = 3          # missing cards to search by name when the set id guesses miss
MAX_RETRIES = 5          # 429s in a row before giving up on a request
MAX_RETRY_WAIT = 65      # seconds; longer means a daily or monthly quota, not worth waiting for
# v2: v1 could hold a wrong set found by a single name match (M2 -> l2_jp).
SET_MAP_FILE = "set_ids_v2.json"
SET_MAP_TTL = {True: 30 * 86400, False: 7 * 86400}  # found / not found; new sets appear daily
LAST_PRICES_FILE = "last_prices.json"
LAST_PRICE_DAYS = 30     # how long a product's last price stands in for a missing one
# Card languages PulseTCG has sets for (its site lists English, Japanese and
# Chinese sets); a German or French print would only waste requests.
LANGUAGES = {"English", "Japanese", "Chinese"}
LANGUAGE_SUFFIXES = {"Japanese": "jp", "Chinese": "cn"}  # PulseAPI set id suffix
# A set found by searching card names is only trusted when more than one
# probe card lands in it, or its name is close to the set's own name: a
# single card number matching is often another set's card with that number.
MIN_NAME_SIMILARITY = 0.6


def norm_number(number):
    """'199/165' -> '199', '002' -> '2', 'tg05/tg30' -> 'TG5', so PulseAPI's
    numbers and TCGdex's local ids compare equal."""
    first = str(number or "").split("/")[0].strip().upper()
    return re.sub(r"(?<!\d)0+(?=\d)", "", first)


def set_id_guesses(set_id, language="English"):
    """PulseAPI set codes to try for a TCGdex set id. Non-English sets carry a
    language suffix ('m2_jp' for Japanese Inferno X), so that comes first for
    them; then the id itself, lowercase, and pokemontcg.io's spelling (TCGdex
    'sv03.5' -> 'sv3pt5', 'swsh07' -> 'swsh7')."""
    suffix = LANGUAGE_SUFFIXES.get(language)
    guesses = [f"{set_id.lower()}_{suffix}"] if suffix else []
    guesses += [set_id, set_id.lower()]
    m = re.fullmatch(r"([a-z]+)0*(\d+)(\.5)?", set_id.lower())
    if m:
        guesses.append(f"{m.group(1)}{m.group(2)}{'pt5' if m.group(3) else ''}")
    return list(dict.fromkeys(g for g in guesses if g))


# Titles of prices PulseAPI sent this run (not a remembered one).
LIVE_NOTES = ("UK market price:", "UK+US market price:")


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
    min_interval = 0       # rate limits are handled by waiting out 429s
    retry_rate_limits = False  # ...here, so a used-up quota can be told apart
    price_guide = True
    guide_label = "market price"
    guide_prefix = ""
    market_reference = True
    guide_description = "PulseAPI's UK market price for a near-mint ungraded copy"

    def __init__(self, sleep=time.sleep, clock=time.time):
        self._sleep = sleep
        self._clock = clock

    def handles(self, card):
        return card.language in LANGUAGES

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
                try:
                    wait = int((e.headers or {}).get("Retry-After") or 60)
                except ValueError:
                    wait = 60
                if wait > MAX_RETRY_WAIT:
                    raise RuntimeError(f"PulseAPI's request quota is used up; it resets in "
                                       f"{wait // 60} minute(s)") from None
                ctx.progress(f"PulseAPI rate limit reached; waiting {wait}s")
                self._sleep(wait)
        if not body.get("success", True):
            raise RuntimeError(f"PulseAPI error: {json.dumps(body.get('error'))}")
        return body.get("data") or [], body.get("meta") or {}

    def _search(self, ctx, language, **params):
        """One page of plain (ungraded, near-mint) cards. Asks for the paid
        tier's 500 a page until PulseAPI turns that down, then 100."""
        while True:
            size = ctx.state.get("page_size", PAGE_SIZES[0])
            try:
                return self._get(ctx, language=language, exclude_graded="true",
                                 exclude_conditioned="true", limit=size, **params)
            except urllib.error.HTTPError as e:
                if e.code != 400 or size == PAGE_SIZES[-1]:
                    raise
                ctx.debug(f"page size {size} refused; using {PAGE_SIZES[-1]}")
                ctx.state["page_size"] = PAGE_SIZES[-1]

    # -- which PulseAPI set a TCGdex set is, remembered between runs --

    def _set_map_path(self, ctx):
        return os.path.join(ctx.cache_dir, self.id, SET_MAP_FILE) if ctx.cache_dir else None

    def _set_map(self, ctx):
        if "set_map" not in ctx.state:
            path, data = self._set_map_path(ctx), {}
            if path and os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    data = {}
            ctx.state["set_map"] = data
        return ctx.state["set_map"]

    def remembered_set(self, card, ctx):
        """(True, PulseAPI set id or None) when an earlier run worked it out
        recently enough, else (False, None)."""
        entry = self._set_map(ctx).get(f"{card.language}|{card.set_id}")
        if not entry:
            return False, None
        found = entry.get("set_id") is not None
        if self._clock() - entry.get("at", 0) > SET_MAP_TTL[found]:
            return False, None
        return True, entry.get("set_id")

    def remember_set(self, card, set_id, ctx):
        data = self._set_map(ctx)
        data[f"{card.language}|{card.set_id}"] = {"set_id": set_id, "at": self._clock()}
        path = self._set_map_path(ctx)
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, sort_keys=True)

    # -- each product's last price, for nights PulseAPI sends none --

    def _last_prices_path(self, ctx):
        return os.path.join(ctx.cache_dir, self.id, LAST_PRICES_FILE) if ctx.cache_dir else None

    def _last_prices(self, ctx):
        if "last_prices" not in ctx.state:
            path, data = self._last_prices_path(ctx), {}
            if path and os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    data = {}
            ctx.state["last_prices"] = data
        return ctx.state["last_prices"]

    def _save_last_prices(self, ctx):
        path = self._last_prices_path(ctx)
        if path and "last_prices" in ctx.state:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(ctx.state["last_prices"], f, indent=0, sort_keys=True)
            os.replace(tmp, path)

    def find_set(self, cards, ctx):
        """(PulseAPI set_id, first page of its cards) for these missing cards,
        or (None, []) when PulseAPI doesn't seem to have the set."""
        first = cards[0]
        known, set_id = self.remembered_set(first, ctx)
        if known:
            ctx.debug(f"{first.set_name}: PulseAPI set_id {set_id!r} (remembered)")
            return set_id, None
        set_id, first_page = self._find_set(cards, ctx)
        self.remember_set(first, set_id, ctx)
        return set_id, first_page

    def _find_set(self, cards, ctx):
        first = cards[0]
        for guess in set_id_guesses(first.set_id, first.language):
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
        if votes[best] < 2 and similarity(best) < MIN_NAME_SIMILARITY:
            ctx.debug(f"{first.set_name}: not trusting PulseAPI set {best!r} ({names[best]}): "
                      f"only one card number matched and the set name differs")
            return None, ([], {})
        ctx.debug(f"{first.set_name}: PulseAPI set_id {best!r} ({names[best]}) from a name search")
        return best, None

    def set_cards(self, set_id, language, first_page, ctx):
        """Every plain copy PulseAPI has in the set, by normalised card number.
        A set is read once a run, so pricing the spares after the missing
        cards (see price_search.py) costs no more requests."""
        read = ctx.state.setdefault("set_cards", {})
        if (set_id, language) not in read:
            read[(set_id, language)] = self._read_set(set_id, language, first_page, ctx)
        return read[(set_id, language)]

    def _read_set(self, set_id, language, first_page, ctx):
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
        offers, absent, unpriced, reused, synced = [], [], [], [], set()
        for card in cards:
            hits = by_number.get(norm_number(card.local_id), [])
            if not hits:
                absent.append(card.card_id)
                continue
            # Several products share a number when a card has more than one
            # finish (e.g. a reverse holo). Missing cards don't say which
            # finish, so prefer the standard print, else offer each.
            standard = [h for h in hits if not h.get("material") and not h.get("promo_info")]
            chosen = standard or hits
            found = []
            for h in chosen:
                offer = self.offer(card, h, MATCH_EXACT if len(chosen) == 1 else MATCH_LIKELY, ctx)
                if offer:
                    found.append(offer)
            if not any(o.title.startswith(LIVE_NOTES) for o in found):
                unpriced.append(card.card_id)
                synced.update(h.get("synced_at") for h in chosen if h.get("synced_at"))
                if found:
                    reused.append(card.card_id)
            offers.extend(found)
        self._save_last_prices(ctx)
        name = f"{cards[0].set_name} ({set_id})"
        if absent:
            ctx.log(f"{name}: {len(absent)} card(s) not on PulseAPI: {', '.join(absent)}")
        if unpriced:
            ctx.log(f"{name}: {len(unpriced)} of {len(cards) - len(absent)} card(s) came back "
                    f"with no price ({', '.join(unpriced)}; PulseAPI synced "
                    f"{', '.join(sorted(synced)) or 'at an unknown time'}); "
                    f"last price seen used for {len(reused)}"
                    + (f" ({', '.join(reused)})" if reused else ""))
        return offers

    def offer(self, card, hit, match, ctx=None):
        prices = hit.get("prices") or {}
        price, note = money(prices.get("market_price")), "UK market price"
        if price is None:
            price, note = money(prices.get("market_price_global")), "UK+US market price"
        last = self._last_prices(ctx) if ctx is not None else {}
        key = str(hit.get("product_id") or hit.get("slug") or "")
        if price is not None and key:
            last[key] = {"price": str(price), "note": note, "at": self._clock()}
        elif key in last and self._clock() - last[key].get("at", 0) <= LAST_PRICE_DAYS * 86400:
            seen = datetime.datetime.fromtimestamp(last[key]["at"])
            price = Decimal(last[key]["price"])
            note = f"{last[key].get('note', 'UK market price')} last seen {seen:%d %b} (none sent today)"
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

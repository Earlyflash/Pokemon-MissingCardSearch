"""
The contract every marketplace plugin implements.

A plugin turns missing cards into offers: listings on its site that could be
bought to fill the gap. It doesn't convert currencies, pick the cheapest
offer, read settings from the environment, or pace its own requests -- the
core (price_search.py) does all of that, so a plugin only has to know its own
site.

How a plugin gets its data is up to it: an official API, reading web pages,
driving a browser, or a file the user exported by hand all fit the same
contract.
"""
import email.utils
import gzip
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

# How sure a plugin is that an offer is the exact card asked for.
MATCH_EXACT = "exact"          # matched on set + card number (e.g. a structured listing)
MATCH_LIKELY = "likely"        # matched on name + number in free text
MATCH_UNCERTAIN = "uncertain"  # could be a different print; shown, but not counted by default
MATCH_LEVELS = (MATCH_EXACT, MATCH_LIKELY, MATCH_UNCERTAIN)

# Normalised card conditions, best first. Plugins map their site's wording to
# one of these, or leave condition as None when the site doesn't say.
CONDITIONS = ("NM", "LP", "MP", "HP", "DMG")

# Print finishes, for sets whose RareCandy export says which finish each copy
# is. So far that's only MEGA Dream ex (M2a): most of its #001-193 come as a
# normal print and two reverse holos, one with an Energy pattern and one with
# a Poké Ball, Love Ball, ... or Team Rocket pattern ("ball" covers them all,
# since a card only has one).
FINISH_NORMAL, FINISH_ENERGY, FINISH_BALL = "normal", "energy", "ball"
FINISHES = (FINISH_NORMAL, FINISH_ENERGY, FINISH_BALL)


def finish_key(text):
    """FINISH_NORMAL, FINISH_ENERGY or FINISH_BALL for a finish however it's
    worded: RareCandy's "Energy Reverse Holofoil" or "Love Ball Reverse
    Holofoil", the missing cards CSV's "Team Rocket Reverse Holo", or a key
    itself. None when blank."""
    s = (text or "").strip().lower()
    if not s:
        return None
    if "energy" in s:
        return FINISH_ENERGY
    if any(word in s for word in ("reverse", "ball", "rocket")):
        return FINISH_BALL
    return FINISH_NORMAL


@dataclass(frozen=True)
class MissingCard:
    """One card from missing_cards.py's --json output."""
    card_id: str               # TCGdex id, e.g. "M2a-002"
    set_id: str                # TCGdex set id, e.g. "M2a"
    set_name: str              # set name as RareCandy shows it, e.g. "MEGA Dream ex"
    local_id: str              # card number within the set, e.g. "002"
    name: str                  # card name from TCGdex, in the print's language
    language: str              # print language as RareCandy names it, e.g. "Japanese"
    tcgdex_lang: str           # TCGdex dataset code, e.g. "ja"
    rarity: Optional[str] = None
    finish: Optional[str] = None
    name_en: Optional[str] = None    # English name, when missing_cards.py could find one

    @classmethod
    def from_json(cls, d):
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


@dataclass
class Offer:
    """A listing that could be bought to fill a missing card."""
    marketplace: str           # plugin id, e.g. "deckdhq"
    card_id: str               # the MissingCard.card_id this offer answers
    url: str                   # where to buy it
    price: Decimal             # item price in `currency`, excluding shipping
    currency: str              # ISO 4217 code, e.g. "GBP", "EUR", "JPY"
    title: str = ""            # the listing's own wording, for eyeballing matches
    match: str = MATCH_EXACT   # one of MATCH_LEVELS
    condition: Optional[str] = None  # one of CONDITIONS, or None if unknown
    grade: Optional[str] = None      # e.g. "PSA 9" for a graded slab; None for a raw card
    seller: Optional[str] = None
    quantity: Optional[int] = None   # copies available, if the site says


class Marketplace:
    """Base class for plugins. Subclass it in marketplaces/<id>.py, set the
    class attributes, implement search() (or search_set() when the site is
    cheaper to read a whole set at a time), and end the module with
    `PLUGIN = YourMarketplace()`."""

    id = ""                    # short name used on the command line, e.g. "deckdhq"
    name = ""                  # display name, e.g. "DeckdHQ"
    languages = None           # TCGdex language codes it sells, e.g. {"en", "ja"}; None = all
    needs = ()                 # environment variables it requires, e.g. ("EBAY_APP_ID",)
    min_interval = 1.0         # minimum seconds between this plugin's HTTP requests
    # True for a site that only publishes a price per card (e.g. its cheapest
    # copy in any language or condition) rather than individual listings. The
    # core reports those prices separately and never ranks them against real
    # listings.
    price_guide = False
    # How a price guide's prices are labelled: the price table's column says
    # "<name> (<guide_label>)" and each price reads "<guide_prefix>£1.23";
    # the text report describes the prices as `guide_description`.
    guide_label = "price guide"
    guide_prefix = "from "
    guide_description = "the cheapest copy it lists, in any language or condition"
    # True for a price guide whose price is a card's market value (not a
    # cheapest copy), so the price table flags listings priced above it.
    market_reference = False
    # False for a plugin that handles 429 (Too Many Requests) itself; otherwise
    # the core waits as long as the site asks, retries, and slows the plugin.
    retry_rate_limits = True

    def search(self, card, ctx):
        """Return a list of Offers for one MissingCard."""
        raise NotImplementedError

    def search_set(self, cards, ctx):
        """Return Offers for several MissingCards from the same set. The default
        asks search() for each card, so one bad card doesn't lose the rest."""
        offers = []
        for card in cards:
            try:
                offers.extend(self.search(card, ctx))
            except Exception as e:  # noqa: BLE001 -- keep going with the other cards
                ctx.log(f"{card.card_id}: {e}")
        return offers

    def missing_config(self, environ=os.environ):
        return [k for k in self.needs if not environ.get(k)]

    def handles(self, card):
        return self.languages is None or card.tcgdex_lang in self.languages


# A 429 (Too Many Requests) is waited out and retried this many times, as long
# as the site doesn't ask for a longer wait than MAX_RATE_LIMIT_WAIT seconds.
# With no Retry-After header the waits are RATE_LIMIT_WAIT, then twice that,
# and so on. After a 429 the plugin's requests are spaced twice as far apart
# for the rest of the run, up to MAX_MIN_INTERVAL seconds.
MAX_RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT = 30
MAX_RATE_LIMIT_WAIT = 120
MAX_MIN_INTERVAL = 10.0


def retry_after(headers, default):
    """Seconds a 429's Retry-After header asks for (a number or an HTTP
    date), or `default` when it doesn't say."""
    value = (headers or {}).get("Retry-After")
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return default


class SearchContext:
    """What the core hands a plugin for one run: its settings, a paced and
    cached HTTP fetch, a logger, and `state` for anything it wants to keep
    between search calls (e.g. a catalogue it fetched once).

    While a plugin works through many pages, fetch() prints a progress line
    to stderr at most every `progress_interval` seconds, so a long search
    isn't silent and stdout keeps only the results."""

    def __init__(self, plugin, config=None, cache_dir=None, cache_ttl=6 * 3600,
                 verbose=False, sleep=time.sleep, clock=time.monotonic,
                 progress_interval=5.0, progress_stream=None):
        self.plugin_id = plugin.id
        self.config = config or {}
        self.cache_dir = cache_dir
        self.cache_ttl = cache_ttl
        self.verbose = verbose
        self.state = {}  # scratch space a plugin can keep for the length of one run
        self._min_interval = plugin.min_interval
        self._retry_rate_limits = plugin.retry_rate_limits
        self._last_request = None
        self._lock = threading.Lock()
        self._sleep = sleep
        self._clock = clock
        self.fetched = 0       # pages downloaded this run
        self.cache_hits = 0    # pages read from the on-disk cache without asking the site
        self.unchanged = 0     # cached pages the site said were unchanged (304 Not Modified)
        self.stale = 0         # cached pages used because the site couldn't be reached
        self._progress_interval = progress_interval
        self._progress_stream = progress_stream
        self._last_progress = clock()

    def log(self, msg):
        print(f"[{self.plugin_id}] {msg}")

    def progress(self, msg):
        """A status line for the person waiting, on stderr (None = sys.stderr
        at the time, so redirecting it works)."""
        stream = self._progress_stream or sys.stderr
        # One write per line, so lines from plugins running in parallel
        # don't run into each other.
        stream.write(f"[{self.plugin_id}] {msg}\n")
        stream.flush()

    def pages_summary(self):
        parts = [f"{self.fetched} page(s) fetched"]
        if self.unchanged:
            parts.append(f"{self.unchanged} unchanged since last run")
        if self.cache_hits:
            parts.append(f"{self.cache_hits} from cache")
        if self.stale:
            parts.append(f"{self.stale} old cached cop(ies) used")
        return ", ".join(parts)

    def _counted(self, kind):
        with self._lock:
            setattr(self, kind, getattr(self, kind) + 1)
            now = self._clock()
            due = now - self._last_progress >= self._progress_interval
            if due:
                self._last_progress = now
        if due:
            self.progress(f"still working: {self.pages_summary()} so far...")

    def debug(self, msg):
        if self.verbose:
            self.log(msg)

    def _cache_path(self, url, headers):
        key = hashlib.sha256(json.dumps([url, sorted((headers or {}).items())]).encode()).hexdigest()
        return os.path.join(self.cache_dir, self.plugin_id, key[:32])

    def _pace(self):
        with self._lock:
            if self._last_request is not None:
                wait = self._min_interval - (self._clock() - self._last_request)
                if wait > 0:
                    self._sleep(wait)
            self._last_request = self._clock()

    def _get(self, url, headers, timeout):
        """(status, body text or None, response headers) for one paced GET.
        304 Not Modified comes back as a status rather than an error, and a
        429 is waited out and retried (unless the plugin handles its own)."""
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            self._pace()
            self.debug(f"GET {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "Pokemon-MissingCardSearch",
                                                       "Accept-Encoding": "gzip", **headers})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                    return resp.status, raw.decode(resp.headers.get_content_charset() or "utf-8"), resp.headers
            except urllib.error.HTTPError as e:
                if e.code == 304:
                    return 304, None, e.headers
                if e.code != 429 or not self._retry_rate_limits or attempt == MAX_RATE_LIMIT_RETRIES:
                    raise
                wait = retry_after(e.headers, RATE_LIMIT_WAIT * (attempt + 1))
                if wait > MAX_RATE_LIMIT_WAIT:
                    raise
                with self._lock:
                    self._min_interval = min(max(self._min_interval * 2, 1.0), MAX_MIN_INTERVAL)
                self.progress(f"rate limited (429); waiting {wait:.0f}s, then spacing requests "
                              f"{self._min_interval:g}s apart")
                self._sleep(wait)

    def fetch(self, url, headers=None, as_json=False, timeout=30, max_age=None):
        """GET `url` and return its body (text, or parsed JSON with as_json).

        A cached copy younger than the cache TTL (or `max_age` seconds, for
        data that hardly ever changes, if that's longer) is used without
        asking the site. An older one is checked with the site using its
        ETag / Last-Modified, so an unchanged page costs a 304 rather than a
        download, and is still used if the site can't be reached. Requests
        from one plugin are spaced at least `min_interval` seconds apart."""
        headers = headers or {}
        path = self._cache_path(url, headers) if self.cache_dir else None
        age = None
        if path and os.path.isfile(path):
            age = time.time() - os.path.getmtime(path)
            if age < max(self.cache_ttl, max_age or 0):
                self.debug(f"cache hit {url}")
                self._counted("cache_hits")
                return self._parse(self._read(path), as_json)
        validators = {}
        if age is not None:
            meta = self._read_meta(path)
            if meta.get("etag"):
                validators["If-None-Match"] = meta["etag"]
            if meta.get("last_modified"):
                validators["If-Modified-Since"] = meta["last_modified"]
        try:
            status, body, resp_headers = self._get(url, {**headers, **validators}, timeout)
        except Exception as e:  # noqa: BLE001 -- an old copy beats no copy
            # Only for a site that's down or still rate limiting; a 404 or a
            # refused key is an answer, not an outage.
            gone = isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429
            if age is None or gone:
                raise
            if not self.stale:
                self.progress(f"couldn't refresh from the site ({e}); using cached copies "
                              f"(this one is {age / 3600:.0f}h old)")
            self._counted("stale")
            return self._parse(self._read(path), as_json)
        if status == 304:
            self.debug(f"not modified {url}")
            os.utime(path)  # fresh again for another cache TTL
            self._counted("unchanged")
            return self._parse(self._read(path), as_json)
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            with open(path + ".meta", "w", encoding="utf-8") as f:
                json.dump({"url": url, "etag": resp_headers.get("ETag"),
                           "last_modified": resp_headers.get("Last-Modified")}, f)
        self._counted("fetched")
        return self._parse(body, as_json)

    @staticmethod
    def _parse(body, as_json):
        return json.loads(body) if as_json else body

    @staticmethod
    def _read(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _read_meta(path):
        try:
            with open(path + ".meta", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

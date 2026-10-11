#!/usr/bin/env python3
"""
Pokémon TCG Missing Card Finder
-------------------------------
Works out which cards you're missing from each set you collect, by comparing
a RareCandy portfolio export against TCGdex's full card list for each set.

Nothing here re-implements work the sibling tools already do:

  * The RareCandy export is produced by RareCandyExporter (vendored as a git
    submodule under vendor/RareCandyExporter and run unchanged), or you can
    pass a CSV you already exported with it.
  * TCGdex set lookup reuses pokemon-binder-cover-tool's binder_cover.py
    (vendored under vendor/pokemon-binder-cover-tool) -- its set search,
    HTTP fetch helper and dataset constants are imported, not copied.

Usage examples:

  python missing_cards.py --csv earlyflash.csv
  python missing_cards.py --profile Earlyflash --out missing_cards.csv
  python missing_cards.py --csv earlyflash.csv --set "MEGA Dream ex" --set "Abyss Eye"

Run `python missing_cards.py --help` for the full flag list.
"""
import argparse
import concurrent.futures
import csv
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import sys

from data_dir import data_path, ensure_parent

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BINDER_TOOL_DIR = os.path.join(SCRIPT_DIR, "vendor", "pokemon-binder-cover-tool")
EXPORTER_DIR = os.path.join(SCRIPT_DIR, "vendor", "RareCandyExporter")
DEFAULT_SET_MAP = os.path.join(SCRIPT_DIR, "set_map.json")
DEFAULT_ORDERED = data_path("ordered.txt")
DEFAULT_SESSION = data_path(".rarecandy-session.json")
# English names already found, so each card is only looked up once.
DEFAULT_NAMES_CACHE = data_path(".english_names.json")

if not os.path.isfile(os.path.join(BINDER_TOOL_DIR, "binder_cover.py")):
    sys.exit("vendor/pokemon-binder-cover-tool is empty -- run "
             "`git submodule update --init` first.")
sys.path.insert(0, BINDER_TOOL_DIR)
try:
    import PIL  # noqa: F401
except ImportError:
    # binder_cover imports Pillow at load time for its drawing code, but the
    # TCGdex lookup this tool reuses never touches it. Stand in empty modules
    # so Pillow isn't a required install just to satisfy that import.
    import types
    _pil = types.ModuleType("PIL")
    for _sub in ("Image", "ImageDraw", "ImageFont", "ImageFilter"):
        setattr(_pil, _sub, types.ModuleType(f"PIL.{_sub}"))
        sys.modules[f"PIL.{_sub}"] = getattr(_pil, _sub)
    sys.modules["PIL"] = _pil
import binder_cover  # noqa: E402  (path set up just above)
from binder_cover import TCGDEX_BASE, TCGDEX_LANGS  # noqa: E402
from marketplaces.base import FINISH_NORMAL, FINISHES, finish_key  # noqa: E402
from marketplaces.cardmarket import card_variants, product_id  # noqa: E402
from ordered import card_key, default_arrived_path, prune_arrived  # noqa: E402

# RareCandyExporter writes a full language name per card (detected from the
# card's scrydex image URL). These are the only TCGdex datasets whose card list
# is used for each: Japanese, Chinese and Korean sets are numbered differently
# from each other and from English ones, so another dataset's list would
# report the wrong cards as missing (e.g. zh-tw's S10b list stops at #71, so
# a Japanese S10b #079 looked unowned). Languages TCGdex's five binder-tool datasets
# don't cover (German, French, ...) share the English print run's card list,
# so they use "en".
LANGUAGE_TO_TCGDEX = {
    "english": ("en",),
    "japanese": ("ja",),
    "chinese": ("zh-tw", "zh-cn"),
    "korean": ("ko",),
}

OUT_COLUMNS = ["Set Name", "TCGdex Set", "Language", "Card Number", "Card Name", "TCGdex Card ID",
               "English Name"]
PRINTS_NAME = "missing_prints.csv"
HTML_NAME = "missing_cards.html"

# TCGdex has pictures for English cards but none for Japanese ones, which
# scrydex (where RareCandy's own card pictures come from) has under
# "<set id>_ja-<number>".
SCRYDEX_IMAGES = "https://images.scrydex.com/pokemon"

# English names for the series TCGdex's Japanese sets are in (by series id),
# for grouping the set overview; other datasets' series are named in English.
JA_SERIES_NAMES = {
    "PMCG": "Base", "neo": "Neo", "VS": "VS", "web": "Web", "e": "E-Card", "ADV": "ADV",
    "PCG": "PCG", "L": "Legend", "XY": "XY", "XYb": "XY BREAK", "SM": "Sun & Moon",
    "S": "Sword & Shield", "SV": "Scarlet & Violet", "M": "Mega Evolution",
}

# Cardmarket's public product list names every product in English, Japanese
# prints included, and TCGdex's card records carry the Cardmarket product id.
# Together they give an English name for cards TCGdex only has in Japanese
# (or Chinese/Korean) -- the same route the Cardmarket marketplace plugin uses
# for prices.
CARDMARKET_PRODUCTS_URL = ("https://downloads.s3.cardmarket.com/productCatalog/productList/"
                           "products_singles_6.json")


# ------------------------------------------------------ RareCandy export --

def run_exporter(profile, out_csv, extra_args=(), headful=False):
    """Run RareCandyExporter's export.js as-is to produce `out_csv`.

    With a RareCandy login cached (or `headful`, which opens a browser to log
    in and caches it), it also asks for each MEGA Dream ex card's finishes
    (--m2a-variants), which only the profile's owner can see. If that fails
    -- the login has expired, say -- it exports again without them."""
    export_js = os.path.join(EXPORTER_DIR, "export.js")
    if not os.path.isfile(export_js):
        sys.exit("vendor/RareCandyExporter is empty -- run `git submodule update --init` first.")
    if not os.path.isdir(os.path.join(EXPORTER_DIR, "node_modules")):
        sys.exit("RareCandyExporter's dependencies aren't installed -- run `npm install` "
                 "in vendor/RareCandyExporter first.")
    node = shutil.which("node")
    if not node:
        sys.exit("Node.js 18+ is needed to run RareCandyExporter (`node` not found on PATH).")
    ensure_parent(out_csv)
    if "--cookies" not in extra_args:
        # Keep the RareCandy login with your other files, not in the repo.
        extra_args = [*extra_args, "--cookies", DEFAULT_SESSION]
    cmd = [node, export_js, profile, os.path.abspath(out_csv), *extra_args]
    cookies = extra_args[list(extra_args).index("--cookies") + 1]
    if headful or os.path.isfile(cookies):
        variant_cmd = [*cmd, "--m2a-variants", *(["--headful"] if headful else [])]
        print(f"[export] Running RareCandyExporter: {' '.join(variant_cmd[1:])}")
        if subprocess.run(variant_cmd).returncode == 0:
            return
        print("[export] The export with MEGA Dream ex finishes failed; exporting without them "
              "(run with --headful to sign in to RareCandy again).")
    else:
        print(f"[export] No RareCandy login cached in {cookies}, so MEGA Dream ex finishes "
              "aren't exported: run once with --headful to sign in.")
    print(f"[export] Running RareCandyExporter: {' '.join(cmd[1:])}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"RareCandyExporter failed (exit code {result.returncode}).")


def normalize_number(raw):
    """Canonical form of a card number for comparing RareCandy against TCGdex:
    drops a leading '#' and any '/total' suffix, uppercases, and strips leading
    zeros from each digit run -- so '#076', '76/193' and TCGdex's '076' all
    become '76', and 'TG01' matches 'tg1'."""
    s = str(raw or "").strip().lstrip("#").split("/")[0].strip().upper()
    return re.sub(r"\d+", lambda m: str(int(m.group())), s)


# RareCandy gives basic Energy no card number ("Basic Fire Energy" with a
# blank #), where TCGdex numbers them by type in sets that list them (M6a's
# FIR, GRA, ...), so the name stands in for the number.
BASIC_ENERGY_NUMBERS = {
    "grass": "GRA", "fire": "FIR", "water": "WAT", "lightning": "LIG", "psychic": "PSY",
    "fighting": "FIG", "darkness": "DAR", "metal": "MET",
}


def basic_energy_number(product_name):
    """TCGdex's number for a basic Energy RareCandy names "Basic <Type> Energy",
    or "" for any other card."""
    m = re.fullmatch(r"basic (\w+) energy", (product_name or "").strip().lower())
    return BASIC_ENERGY_NUMBERS.get(m.group(1), "") if m else ""


def read_owned(csv_path, finishes=None):
    """Read a RareCandyExporter CSV. Returns {(set_name, language): set of
    normalized card numbers owned}. Rows with no set name or card number
    (untracked cards) can't be matched to a set list, so they're skipped,
    except basic Energy, numbered by type (see BASIC_ENERGY_NUMBERS).

    Pass a dict as `finishes` to also get {(set_name, language): {number:
    set of finish keys}} in it, for sets where the export gives any row a
    Material/Finish (MEGA Dream ex, exported with --m2a-variants). A row
    there with no finish adds None to its number's set."""
    owned, seen = {}, {}
    skipped = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            set_name = (row.get("Set Name") or "").strip()
            number = (normalize_number(row.get("Card Number"))
                      or basic_energy_number(row.get("Product Name")))
            if not set_name or not number:
                skipped += 1
                continue
            try:
                if int(row.get("Quantity") or 1) <= 0:
                    continue
            except ValueError:
                pass
            language = (row.get("Language") or "").strip() or "English"
            owned.setdefault((set_name, language), set()).add(number)
            seen.setdefault((set_name, language), {}).setdefault(number, set()).add(
                finish_key(row.get("Material/Finish")))
    if finishes is not None:
        finishes.update({k: v for k, v in seen.items()
                         if any(f for got in v.values() for f in got)})
    if skipped:
        print(f"[export] Skipped {skipped} row(s) with no set name or card number.")
    return owned


# ------------------------------------------------------------ set lookup --

def load_set_map(path):
    """Load the RareCandy set name -> TCGdex set code overrides. Shape:
    {"Japanese": {"MEGA Dream ex": "m2a"}, "Any": {"Some Set": "xy1"}} --
    language-specific entries win over "Any", and --map (stored under "cli")
    wins over both."""
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {lang.lower(): {k.lower(): v for k, v in names.items()}
            for lang, names in data.items() if isinstance(names, dict)}


def mapped_code(set_map, set_name, language):
    key = set_name.lower()
    for lang in ("cli", language.lower(), "any"):
        code = set_map.get(lang, {}).get(key)
        if code:
            return code
    return None


_LISTING_CACHE = {}


def _set_listing(lang):
    """TCGdex's set list for one dataset, fetched once per run."""
    if lang not in _LISTING_CACHE:
        listing = binder_cover._fetch_json(f"{TCGDEX_BASE}/{lang}/sets")
        if listing is None:
            return []  # don't cache a failure
        _LISTING_CACHE[lang] = listing
    return _LISTING_CACHE[lang]


def _exact_match(value, field, langs=TCGDEX_LANGS):
    """Set id whose `field` ("id" or "name") equals `value` exactly (case-
    insensitive) in any dataset, from the cached listings, trying `langs` in
    order. binder_cover's _find_set_id re-fetches every listing per call and
    only does substring name matching, which calls "Mega Evolution" ambiguous
    next to "Mega Evolution Black Star Promos"; an exact match settles that
    without a manual mapping, and _find_set_id stays the fallback."""
    needle = value.strip().lower()
    for lang in langs:
        hits = {s["id"] for s in _set_listing(lang)
                if s.get("id") and str(s.get(field, "")).strip().lower() == needle}
        if len(hits) == 1:
            return hits.pop()
    return None


def resolve_set_id(set_name, language, set_map):
    """TCGdex set id for a RareCandy set name, or None. Order: set_map.json
    override, exact name match, then binder_cover's code/name search.

    The cards' own language's datasets are searched first, as the same name
    can be a different set in each: English "30th Celebration" is TCGdex's
    en "30th", where the ja dataset calls M6a "30th CELEBRATION"."""
    own = tcgdex_langs_for(language)
    langs = (*own, *(lang for lang in TCGDEX_LANGS if lang not in own))
    code = mapped_code(set_map, set_name, language)
    if code:
        return _exact_match(code, "id", langs) or code
    return _exact_match(set_name, "name", langs) or binder_cover._find_set_id(set_name)


def series_name(detail, lang):
    """The English name of the series a TCGdex set is in ("Scarlet &
    Violet", ...), or "Other"."""
    serie = detail.get("serie") or {}
    if lang == "ja" and serie.get("id") in JA_SERIES_NAMES:
        return JA_SERIES_NAMES[serie["id"]]
    return serie.get("name") or "Other"


def tcgdex_langs_for(language):
    return LANGUAGE_TO_TCGDEX.get(language.lower(), ("en",))


def fetch_card_list(set_id, language):
    """The set's full card list from the TCGdex dataset matching the cards'
    print language (never another language's -- see LANGUAGE_TO_TCGDEX).
    Returns (cards, tcgdex_lang_used, set_detail) or (None, None, None)."""
    for lang in tcgdex_langs_for(language):
        detail = binder_cover._fetch_json(f"{TCGDEX_BASE}/{lang}/sets/{set_id}")
        if detail and detail.get("cards"):
            return detail["cards"], lang, detail
    return None, None, None


# ------------------------------------------------------- English names --

def _cardmarket_base_name(name):
    """Cardmarket disambiguates same-named Pokémon with their attack names in
    brackets ("Tangela [Poison Powder | Hook]"), sometimes mid-name ("... [...]
    Energy"); drop just the bracket group and keep the rest, including any
    parenthetical like "Pikachu (Top Deck)"."""
    return " ".join(re.sub(r"\s*\[[^\]]*\]", "", name or "").split())


def _read_names_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            names = json.load(f)
        return names if isinstance(names, dict) else {}
    except (OSError, ValueError):
        return {}


def add_english_names(results, cache_path=None):
    """Set c["name_en"] on every missing card. English-dataset cards already
    have it; for the rest it's looked up via each card's Cardmarket product
    (one TCGdex request per card, run concurrently, plus Cardmarket's product
    list once). Cards it can't resolve are left with name_en None.

    A card's name doesn't change, so names found are kept in `cache_path`
    (JSON, {"<lang>/<card id>": name}) and only cards not in it are looked
    up. Cards not found aren't kept, so they're tried again next run (a
    network hiccup, or a set too new for TCGdex to link to Cardmarket)."""
    cache = _read_names_cache(cache_path) if cache_path else {}
    todo, prints = [], {}
    for r in results:
        for c in [*r["missing"], *r.get("prints", ())]:
            if r["tcgdex_lang"] == "en":
                c["name_en"] = c.get("name")
            else:
                c["name_en"] = None
                # A card missing in several finishes is looked up once.
                key = (r["tcgdex_lang"], c.get("id"))
                cached = cache.get("/".join(key))
                if cached:
                    c["name_en"] = cached
                    continue
                if key not in prints:
                    todo.append((r["tcgdex_lang"], c))
                prints.setdefault(key, []).append(c)
    if not todo:
        return

    print(f"\n[names] Looking up English names for {len(todo)} card(s) via Cardmarket...")
    products = binder_cover._fetch_json(CARDMARKET_PRODUCTS_URL)
    if not products:
        print("[warning] Couldn't download Cardmarket's product list, so English names "
              "are left blank.")
        return
    names = {p["idProduct"]: p.get("name") for p in products.get("products", [])
             if p.get("idProduct") is not None}

    def lookup(item):
        lang, c = item
        detail = binder_cover._fetch_json(f"{TCGDEX_BASE}/{lang}/cards/{c.get('id')}")
        return (lang, c.get("id")), _cardmarket_base_name(names.get(product_id(detail))) or None

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for key, name_en in pool.map(lookup, todo):
            for c in prints[key]:
                c["name_en"] = name_en
            if name_en:
                cache["/".join(key)] = name_en
    found = sum(1 for _, c in todo if c["name_en"])
    if found < len(todo):
        print(f"[names] Found {found}/{len(todo)}; the rest couldn't be fetched from TCGdex or "
              "have no Cardmarket product there yet, and will be tried again next run.")
    if cache_path and found:
        ensure_parent(cache_path)
        tmp = f"{cache_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=0, sort_keys=True)
        os.replace(tmp, cache_path)


# ------------------------------------------------------------- diffing --

def _sort_key(number):
    """Numbers first in numeric order, then lettered ones (TG01, SV001, ...)."""
    m = re.match(r"^(\D*)(\d*)(.*)$", number)
    prefix, digits, rest = m.groups()
    return (prefix != "", prefix, int(digits) if digits else 0, rest)


def card_prints(cards, lang):
    """{card id: card_variants()} for every card in the list that has
    reverse holos, one TCGdex request per card."""
    def one(c):
        detail = binder_cover._fetch_json(f"{TCGDEX_BASE}/{lang}/cards/{c.get('id')}")
        return c.get("id"), card_variants(detail)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        return {card_id: prints for card_id, prints in pool.map(one, cards) if prints}


def missing_prints(listed, finishes, prints):
    """For a set whose export says each copy's finish: (each print not owned
    of every card with reverse holos, as card dicts with "finish" and
    "finish_name" added -- so a card owned only as a reverse holo still has
    its normal print listed -- and the numbers of owned cards whose finish
    the export didn't give, which are counted as normal). `finishes` is
    {number: set of finish keys owned}. Cards with a single print are left
    to the plain card-number check."""
    missing, unknown = [], []
    for number, c in listed.items():
        variants = prints.get(c.get("id"))
        if not variants or number not in finishes:
            continue
        got = finishes[number]
        if None in got:
            unknown.append(number)
            got = (got - {None}) | {FINISH_NORMAL}
        missing += [{**c, "finish": key, "finish_name": name}
                    for key, name, _ in variants if key not in got]
    return missing, unknown


def find_missing(owned, set_map, finishes=None):
    """Returns (results, unmatched). results is one dict per (set, language)
    group: set_name, language, set_id, tcgdex_lang, total, owned_count,
    missing (list of TCGdex card dicts: cards with no copy owned), prints
    (the same, but print by print where that was checked; see below),
    unknown_owned (owned numbers TCGdex doesn't list), series (see
    series_name), released (the set's release date, "" if unknown), card_keys
    (ordered.card_key of every card, and of every print checked, that TCGdex
    lists for the set). unmatched is [(set_name, language, reason)].

    For groups in `finishes` (see read_owned), prints counts every print of
    a card with reverse holos separately: each one not owned, its "finish"
    set. That's for the wants list only: missing, completion and the store
    search still go by card, any print."""
    finishes = finishes or {}
    results, unmatched = [], []
    for (set_name, language) in sorted(owned, key=lambda k: (k[0].lower(), k[1])):
        numbers = owned[(set_name, language)]
        print(f"\n[set] {set_name} ({language}, {len(numbers)} owned)")
        set_id = resolve_set_id(set_name, language, set_map)
        if not set_id:
            reason = "no TCGdex set found"
            if language.lower() != "english":
                # RareCandy names non-English sets in English, but TCGdex only
                # has their local-language name, so these need a mapping.
                reason += (f" (TCGdex only has {language} set names, so add the set "
                           "code to set_map.json)")
            unmatched.append((set_name, language, reason))
            continue
        cards, lang_used, detail = fetch_card_list(set_id, language)
        if not cards:
            unmatched.append((set_name, language,
                              f"matched TCGdex set '{set_id}', but TCGdex has no {language} "
                              "card list for it"))
            continue

        listed = {normalize_number(c.get("localId")): c for c in cards}
        missing = sorted((c for n, c in listed.items() if n not in numbers),
                         key=lambda c: _sort_key(normalize_number(c.get("localId"))))
        by_print = missing
        keys = [card_key(c.get("id")) for c in listed.values() if c.get("id")]
        if (set_name, language) in finishes:
            print(f"[set] Checking every print of {len(listed)} card(s) for the wants list: "
                  "the export says which finish each copy is...")
            prints = card_prints(listed.values(), lang_used)
            # Missing cards with reverse holos come back as every print.
            found, no_finish = missing_prints(
                listed, {**{n: set() for n in listed if n not in numbers},
                         **finishes[(set_name, language)]}, prints)
            order = {key: i for i, key in enumerate((None, *FINISHES))}
            by_print = sorted([c for c in missing if c.get("id") not in prints] + found,
                              key=lambda c: (_sort_key(normalize_number(c.get("localId"))),
                                             order.get(c.get("finish"), 0)))
            keys += [card_key(i, key) for i, variants in prints.items() for key, _, _ in variants]
            if no_finish:
                print(f"[set] {len(no_finish)} card(s) owned with no finish in the export, "
                      f"counted as normal: {', '.join(sorted(no_finish, key=_sort_key))}")
        unknown = sorted((n for n in numbers if n not in listed), key=_sort_key)
        results.append({
            "set_name": set_name, "language": language, "set_id": set_id,
            "tcgdex_lang": lang_used, "total": len(listed),
            "owned_count": len(numbers) - len(unknown),
            "missing": missing, "prints": by_print, "unknown_owned": unknown,
            "card_keys": keys, "series": series_name(detail, lang_used),
            "released": detail.get("releaseDate") or "",
        })
    return results, unmatched


def completion(r):
    """Percentage of the set's cards owned (0-100)."""
    return 100.0 * r["owned_count"] / r["total"] if r["total"] else 0.0


def apply_threshold(results, min_complete):
    """Split results into (kept, below): only sets at least `min_complete`
    percent complete are worth hunting the rest of."""
    kept = [r for r in results if completion(r) >= min_complete]
    below = [r for r in results if completion(r) < min_complete]
    return kept, below


def prune_ordered(results, path, arrived_path=None):
    """Move cards on order (ordered.txt) that are now in the collection out to
    the arrived file, judged against every set just checked -- before the
    --min-complete cut, so a card from a barely started set still counts. A
    card in two checked collections of one set (an English and a German one,
    say) only counts as arrived once neither is missing it."""
    checked = {key for r in results for key in r.get("card_keys", ())}
    missing = {card_key(c["id"], c.get("finish")) for r in results
               for c in r.get("prints", r["missing"]) if c.get("id")}
    arrived, names = prune_arrived(path, checked, missing, arrived_path)
    if arrived:
        print(f"\n{len(arrived)} ordered card(s) now in the collection, moved from "
              f"{path} to {arrived_path or default_arrived_path(path)}:")
        for line in arrived:
            print(f"  {line}")
    if names:
        print(f"\n{names} line(s) in {path} name a card rather than a TCGdex card id, so "
              "can't be checked against the collection: take them out by hand once they arrive.")


def print_report(results, unmatched, below=(), min_complete=0):
    print("\n" + "=" * 60)
    for r in results:
        print(f"\n{r['set_name']} [{r['set_id']}, {r['language']}]: own "
              f"{r['owned_count']}/{r['total']} ({completion(r):.0f}%), "
              f"missing {len(r['missing'])}")
        for c in r["missing"]:
            name, name_en = c.get("name", ""), c.get("name_en")
            shown = f"{name} ({name_en})" if name_en and name_en != name else name
            print(f"  #{c.get('localId', '?'):<8} {shown}")
        if r["unknown_owned"]:
            print(f"  (owned but not in TCGdex's list: {', '.join(r['unknown_owned'])} -- "
                  "numbering mismatch or wrong set match)")
    if below:
        shown = ", ".join(f"{r['set_name']} ({completion(r):.0f}%)" for r in below)
        print(f"\nLeft out {len(below)} set(s) under {min_complete:g}% complete: {shown}")
    if unmatched:
        print("\nCouldn't check these sets -- add them to set_map.json "
              "(or pass --map \"Set Name=CODE\"):")
        for set_name, language, reason in unmatched:
            print(f"  {set_name} ({language}): {reason}")
    total_missing = sum(len(r["missing"]) for r in results)
    print(f"\n{total_missing} missing card(s) across {len(results)} set(s).")
    for r in results:
        extra = sum(1 for c in r.get("prints", ()) if c.get("finish"))
        if extra:
            print(f"{r['set_name']}: {extra} print(s) of cards with reverse holos missing "
                  "for the wants list (missing_prints.csv).")


def write_csv(results, path):
    # utf-8-sig (with a BOM) so Excel on Windows reads Japanese card names
    # correctly instead of showing them as mojibake.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(OUT_COLUMNS)
        for r in results:
            for c in r["missing"]:
                w.writerow([r["set_name"], r["set_id"], r["language"],
                            c.get("localId", ""), c.get("name", ""), c.get("id", ""),
                            c.get("name_en") or ""])


def write_prints_csv(results, path):
    """missing_cards.csv's rows print by print, for wants_list.py: the same
    columns plus Finish, with each print not owned of cards whose finishes
    were checked (see find_missing) and the plain missing cards of every
    other set."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([*OUT_COLUMNS, "Finish"])
        for r in results:
            for c in r.get("prints", r["missing"]):
                w.writerow([r["set_name"], r["set_id"], r["language"],
                            c.get("localId", ""), c.get("name", ""), c.get("id", ""),
                            c.get("name_en") or "", c.get("finish_name") or ""])


def write_json(results, unmatched, path, below=(), min_complete=0):
    """Machine-readable version of the report, grouped by set, for downstream
    tools (e.g. marketplace search). Rarity and finish aren't known here --
    TCGdex's bulk set listing doesn't carry them -- so they're always null."""
    sets = []
    for r in results:
        sets.append({
            "set_id": r["set_id"],
            "set_name": r["set_name"],
            "language": r["language"],
            "tcgdex_lang": r["tcgdex_lang"],
            "total": r["total"],
            "owned": r["owned_count"],
            "percent_complete": round(completion(r), 1),
            "missing": [{
                "card_id": c.get("id"),
                "set_id": r["set_id"],
                "set_name": r["set_name"],
                "local_id": c.get("localId"),
                "name": c.get("name"),
                "name_en": c.get("name_en"),
                "language": r["language"],
                "tcgdex_lang": r["tcgdex_lang"],
                "rarity": None,
                "finish": None,
            } for c in r["missing"]],
        })
    data = {
        "min_complete": min_complete,
        "sets": sets,
        "below_threshold": [{
            "set_id": r["set_id"], "set_name": r["set_name"], "language": r["language"],
            "total": r["total"], "owned": r["owned_count"],
            "percent_complete": round(completion(r), 1),
        } for r in below],
        "unmatched": [{"set_name": n, "language": lang, "reason": why}
                      for n, lang, why in unmatched],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def card_images(r, c):
    """(small, large) picture URLs for a missing card, or (None, None):
    TCGdex's own where it has one, else scrydex's for Japanese cards."""
    if c.get("image"):
        return f"{c['image']}/low.webp", f"{c['image']}/high.webp"
    if r["tcgdex_lang"] == "ja" and c.get("localId"):
        base = (f"{SCRYDEX_IMAGES}/{r['set_id'].lower()}_ja-"
                f"{normalize_number(c['localId']).lower()}")
        return f"{base}/medium", f"{base}/large"
    return None, None


HTML_STYLE = """
:root {
  --bg: #f4f3ef; --panel: #ffffff; --text: #1c1c1e; --muted: #6e6e73; --line: #e4e2dc;
  --track: #ebe9e3; --shadow: 0 1px 2px rgba(20, 20, 30, .06), 0 4px 14px rgba(20, 20, 30, .06);
  --lift: 0 2px 4px rgba(20, 20, 30, .08), 0 12px 28px rgba(20, 20, 30, .12);
  --t0: #d1495b; --t1: #df7a22; --t2: #c79a00; --t3: #6a9f1e; --t4: #1f9d63; --t5: #7c5cff;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111113; --panel: #1b1b1e; --text: #f2f2f4; --muted: #9d9da6; --line: #2c2c31;
    --track: #2a2a2f; --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 4px 14px rgba(0, 0, 0, .25);
    --lift: 0 2px 4px rgba(0, 0, 0, .5), 0 14px 30px rgba(0, 0, 0, .45);
    --t0: #f07a8a; --t1: #f5a050; --t2: #e8c547; --t3: #9fd04a; --t4: #3fcf8e; --t5: #a58bff;
  }
}
.t0 { --tier: var(--t0); } .t1 { --tier: var(--t1); } .t2 { --tier: var(--t2); }
.t3 { --tier: var(--t3); } .t4 { --tier: var(--t4); } .t5 { --tier: var(--t5); }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); -webkit-font-smoothing: antialiased;
       font: 15px/1.45 system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Yu Gothic", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 36px 16px 64px; }
header.top { display: flex; flex-wrap: wrap; gap: 24px; align-items: center; margin-bottom: 24px; }
header.top .intro { flex: 1 1 260px; }
h1 { font-size: 32px; letter-spacing: -0.02em; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0; }
.stats { display: flex; flex-wrap: wrap; gap: 10px; }
.stat { background: var(--panel); border-radius: 14px; padding: 12px 16px; box-shadow: var(--shadow);
        min-width: 120px; }
.stat b { display: block; font-size: 24px; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.stat span { color: var(--muted); font-size: 13px; }
.stat.overall b { color: var(--tier); }
.controls { display: flex; flex-wrap: wrap; gap: 12px 22px; align-items: center; margin-bottom: 8px;
            padding: 10px 14px; background: var(--panel); border-radius: 14px; box-shadow: var(--shadow); }
.switch { display: inline-flex; gap: 10px; align-items: center; cursor: pointer; user-select: none; }
.switch input { position: absolute; opacity: 0; pointer-events: none; }
.switch i { width: 38px; height: 22px; border-radius: 11px; background: var(--track); position: relative;
            transition: background .2s; flex: none; }
.switch i::after { content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px;
                   border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0, 0, 0, .3);
                   transition: transform .2s; }
.switch input:checked + i { background: var(--t4); }
.switch input:checked + i::after { transform: translateX(16px); }
.switch input:focus-visible + i { outline: 2px solid var(--t4); outline-offset: 2px; }
.controls select { font: inherit; padding: 5px 10px; border-radius: 8px; border: 1px solid var(--line);
                   background: var(--bg); color: var(--text); cursor: pointer; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-left: auto; color: var(--muted);
          font-size: 13px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend span::before { content: ""; width: 10px; height: 10px; border-radius: 50%; background: var(--tier); }
section { margin-top: 40px; }
h2 { font-size: 24px; letter-spacing: -0.01em; margin: 0; }
.lang-sub { color: var(--muted); margin: 2px 0 16px; }
h3 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted);
     margin: 26px 0 12px; font-weight: 650; display: flex; align-items: center; gap: 10px; }
h3::after { content: ""; flex: 1; height: 1px; background: var(--line); }
h3 span { font-weight: 500; text-transform: none; letter-spacing: 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(232px, 1fr)); gap: 12px; }
.tile { font: inherit; color: inherit; text-align: left; width: 100%;
        display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 4px 14px; align-items: center;
        padding: 14px; border-radius: 16px; border: 1px solid color-mix(in srgb, var(--tier) 22%, var(--line));
        background: linear-gradient(150deg, color-mix(in srgb, var(--tier) 13%, var(--panel)), var(--panel) 65%);
        box-shadow: var(--shadow); transition: transform .15s ease, box-shadow .15s ease, border-color .15s; }
button.tile { cursor: pointer; }
button.tile:hover { transform: translateY(-2px); box-shadow: var(--lift); }
button.tile:focus-visible { outline: 2px solid var(--tier); outline-offset: 2px; }
.tile[aria-expanded="true"] { border-color: var(--tier);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--tier) 30%, transparent), var(--lift); }
.ring { position: relative; width: 76px; height: 76px; grid-row: span 3; }
.ring svg { width: 100%; height: 100%; transform: rotate(-90deg); display: block; }
.ring circle { fill: none; stroke-width: 7; }
.ring .bg { stroke: var(--track); }
.ring .fg { stroke: var(--tier); stroke-linecap: round; }
.ring b { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
          font-size: 21px;
          letter-spacing: -0.03em; font-variant-numeric: tabular-nums; }
.ring b small { font-size: 12px; font-weight: 600; margin-left: 1px; }
.tname { font-weight: 650; line-height: 1.25; overflow-wrap: anywhere; align-self: end; }
.meta { color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }
.chip { justify-self: start; align-self: start; font-size: 12px; font-weight: 600; padding: 2px 9px;
        border-radius: 999px; color: var(--tier); background: color-mix(in srgb, var(--tier) 14%, transparent); }
.hint { color: var(--muted); font-size: 14px; margin: 14px 2px 0; }
section.has-open .hint { display: none; }
.panel { margin-top: 16px; background: var(--panel); border-radius: 18px;
         box-shadow: var(--lift); border-top: 4px solid var(--tier); overflow: hidden;
         animation: rise .2s ease; }
@keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
.panel[hidden] { display: none; }
.phead { display: flex; align-items: center; gap: 14px; padding: 16px 18px;
         border-bottom: 1px solid var(--line);
         background: linear-gradient(90deg, color-mix(in srgb, var(--tier) 10%, var(--panel)), var(--panel)); }
.phead .ring { width: 56px; height: 56px; }
.phead .ring b { font-size: 15px; }
.phead div { flex: 1; min-width: 0; }
.phead h4 { margin: 0; font-size: 19px; letter-spacing: -0.01em; }
.phead p { margin: 0; color: var(--muted); font-size: 14px; }
.close { font: inherit; font-size: 22px; line-height: 1; width: 36px; height: 36px; border-radius: 50%;
         border: 0; background: transparent; color: var(--muted); cursor: pointer; flex: none; }
.close:hover { background: var(--track); color: var(--text); }
.cards { list-style: none; margin: 0; padding: 14px 18px 18px; display: grid; gap: 4px 20px;
         grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
.card { display: flex; gap: 8px; align-items: baseline; min-width: 0; padding: 3px 0; }
.card .num { color: var(--muted); font-variant-numeric: tabular-nums; min-width: 3.6em; font-size: 13px; }
.card .en { color: var(--muted); }
.card a { display: none; }
.pics .cards { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 18px 16px; }
.pics .card { flex-direction: column; gap: 1px; font-size: 13px; padding: 0; }
.pics .card a { display: block; width: 100%; aspect-ratio: 63 / 88; border-radius: 8px; margin-bottom: 6px;
                background: var(--track); overflow: hidden; box-shadow: var(--shadow);
                transition: transform .15s ease, box-shadow .15s ease; }
.pics .card a:hover { transform: translateY(-3px) scale(1.02); box-shadow: var(--lift); }
.pics .card img { width: 100%; height: 100%; object-fit: cover; display: block; }
.pics .card img.broken { display: none; }
.pics .card .cname { font-weight: 600; }
details.unmatched { margin-top: 44px; color: var(--muted); }
details.unmatched summary { cursor: pointer; font-weight: 600; }
details.unmatched ul { padding-left: 20px; font-size: 14px; }
@media (max-width: 560px) {
  h1 { font-size: 26px; }
  .grid { grid-template-columns: 1fr 1fr; gap: 10px; }
  .tile { grid-template-columns: 1fr; padding: 12px; }
  .ring { grid-row: auto; width: 64px; height: 64px; }
  .legend { margin-left: 0; }
  .pics .cards { grid-template-columns: repeat(2, 1fr); }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
"""

HTML_SCRIPT = """
const box = document.getElementById('pics');
function loadPics(root) {
  if (!document.body.classList.contains('pics')) return;
  root.querySelectorAll('img[data-src]').forEach(img => {
    img.src = img.dataset.src; img.removeAttribute('data-src');
  });
}
function showPics(on) {
  document.body.classList.toggle('pics', on);
  document.querySelectorAll('.panel:not([hidden])').forEach(loadPics);
  try { localStorage.setItem('pics', on ? '1' : ''); } catch (e) {}
}
box.addEventListener('change', () => showPics(box.checked));
try { if (localStorage.getItem('pics')) { box.checked = true; showPics(true); } } catch (e) {}
function closeAll(section) {
  section.querySelectorAll('.panel').forEach(p => p.hidden = true);
  section.querySelectorAll('button.tile').forEach(t => t.setAttribute('aria-expanded', 'false'));
  section.classList.remove('has-open');
}
document.querySelectorAll('button.tile').forEach(tile => tile.addEventListener('click', () => {
  const section = tile.closest('section');
  const panel = document.getElementById(tile.dataset.panel);
  const wasOpen = !panel.hidden;
  closeAll(section);
  if (wasOpen) return;
  panel.hidden = false;
  tile.setAttribute('aria-expanded', 'true');
  section.classList.add('has-open');
  loadPics(panel);
  if (panel.getBoundingClientRect().top > innerHeight - 160)
    panel.scrollIntoView({behavior: 'smooth', block: 'start'});
}));
document.querySelectorAll('.close').forEach(b => b.addEventListener('click', () => {
  const section = b.closest('section');
  const tile = section.querySelector('button.tile[aria-expanded="true"]');
  closeAll(section);
  if (tile) tile.focus({preventScroll: true});
}));
document.getElementById('sort').addEventListener('change', e => {
  const by = e.target.value;
  document.querySelectorAll('.sets').forEach(list => [...list.children].sort((a, b) => by === 'name'
      ? a.dataset.name.localeCompare(b.dataset.name)
      : by === 'missing' ? a.dataset.missing - b.dataset.missing || b.dataset.pct - a.dataset.pct
      : b.dataset.pct - a.dataset.pct || a.dataset.missing - b.dataset.missing)
    .forEach(el => list.appendChild(el)));
});
"""

# Completion colours: (lowest percentage, label), most complete first; a set
# takes the first one it reaches. Their classes are t5 down to t0.
TIERS = [(100, "Complete"), (90, "90%+"), (80, "80%+"), (70, "70%+"), (50, "50%+"), (0, "Under 50%")]


def _tier(pct):
    """CSS class for a completion percentage's colour (see TIERS)."""
    return next(f"t{len(TIERS) - 1 - i}" for i, (low, _) in enumerate(TIERS) if pct >= low)


def _ring(pct):
    """A completion ring with the percentage in the middle, rounded down so
    a set is only shown at 100% (or 90%) once it's there."""
    length = 2 * 3.14159 * 28 * pct / 100
    return (f'<span class="ring"><svg viewBox="0 0 64 64" aria-hidden="true">'
            f'<circle class="bg" cx="32" cy="32" r="28"/>'
            f'<circle class="fg" cx="32" cy="32" r="28" stroke-dasharray="{length:.1f} 999"/>'
            f'</svg><b>{int(pct)}<small>%</small></b></span>')


def _set_tile(r, panel_id):
    """A set's tile: completion ring, name and missing count. Clicking it
    opens the panel `panel_id` with its missing cards; a complete set's
    tile has nothing to open."""
    e = html.escape
    pct, missing = completion(r), len(r["missing"])
    inner = (f'{_ring(pct)}<span class="tname">{e(r["set_name"])}</span>'
             f'<span class="meta">{e(r["set_id"])} · {r["owned_count"]}/{r["total"]} owned</span>'
             f'<span class="chip">{f"{missing} missing" if missing else "Complete"}</span>')
    data = (f'data-name="{e(r["set_name"].lower())}" '
            f'data-pct="{pct:.2f}" '
            f'data-missing="{missing}"')
    if not missing:
        return f'<div class="tile {_tier(pct)}" {data}>{inner}</div>'
    return (f'<button type="button" class="tile {_tier(pct)}" {data} data-panel="{panel_id}" '
            f'aria-expanded="false" aria-controls="{panel_id}">{inner}</button>')


def _set_panel(r, panel_id):
    """A set's missing cards, hidden until its tile is clicked."""
    e = html.escape
    cards = []
    for c in r["missing"]:
        name, name_en = c.get("name") or "", c.get("name_en")
        small, large = card_images(r, c)
        pic = (f'<a href="{e(large)}" target="_blank" rel="noopener">'
               f'<img data-src="{e(small)}" alt="" loading="lazy" '
               f'onerror="this.className=\'broken\'"></a>' if small else "<a></a>")
        en = f'<span class="en">{e(name_en)}</span>' if name_en and name_en != name else ""
        cards.append(f'<li class="card">{pic}<span class="num">#{e(c.get("localId") or "?")}'
                     f'</span><span class="cname">{e(name)}</span>{en}</li>')
    pct = completion(r)
    return (f'<div class="panel {_tier(pct)}" id="{panel_id}" hidden>'
            f'<div class="phead">{_ring(pct)}<div><h4>{e(r["set_name"])}</h4>'
            f'<p>{e(r["set_id"])} · {e(r["language"])} · {r["owned_count"]}/{r["total"]} owned · '
            f'{len(r["missing"])} missing</p></div>'
            f'<button type="button" class="close" aria-label="Close">×</button></div>'
            f'<ul class="cards">{"".join(cards)}</ul></div>')


def _set_group(sets, prefix):
    """A grid of set tiles, closest to finished first, with their panels
    below it."""
    sets = sorted(sets, key=lambda r: (-completion(r), len(r["missing"]), r["set_name"].lower()))
    ids = [f"{prefix}-{i}" for i in range(len(sets))]
    tiles = "".join(_set_tile(r, i) for r, i in zip(sets, ids))
    panels = "".join(_set_panel(r, i) for r, i in zip(sets, ids) if r["missing"])
    return f'<div class="sets grid">{tiles}</div>{panels}'


def write_html(results, unmatched, path, below=(), min_complete=0, hide_below=10):
    """A page to look over: a section per language, the one you own the most
    cards in first, split by series, newest first, with a tile per set
    coloured by how complete it is and saying how many cards it's missing,
    closest to finished first. Clicking a tile shows its missing cards under
    that series' tiles, one set per language at a time. Sets under
    `hide_below` percent complete are left out, and so is a series or
    language with none at or above it, and out of every total. Card pictures are off until "Show
    card pictures" is ticked, and then only the open set's are loaded."""
    e = html.escape
    shown = [r for r in [*results, *below] if completion(r) >= hide_below]
    hidden = {}
    for r in [*results, *below]:
        if completion(r) < hide_below:
            hidden[r["language"]] = hidden.get(r["language"], 0) + 1
    languages = {}
    for r in shown:
        languages.setdefault(r["language"], []).append(r)

    owned = sum(r["owned_count"] for r in shown)
    total = sum(r["total"] for r in shown)
    missing = sum(len(r["missing"]) for r in shown)
    overall = 100.0 * owned / total if total else 0
    stats = "".join(f'<div class="stat{cls}"><b>{v}</b><span>{label}</span></div>' for v, label, cls in (
        (f"{int(overall)}%" if total else "–", "complete overall", f" overall {_tier(overall)}"),
        (missing, "cards missing", ""), (len(shown), "sets", ""),
        (f"{owned}/{total}", "cards owned", "")))
    legend = "".join(f'<span class="t{len(TIERS) - 1 - i}">{label}</span>'
                     for i, (_, label) in enumerate(TIERS))
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>Missing Cards</title><style>{HTML_STYLE}</style></head><body><main>',
        '<header class="top"><div class="intro"><h1>Missing cards</h1>'
        f'<p class="sub">Sets under {hide_below:g}% complete are left out · updated '
        f'{datetime.datetime.now():%d %b %Y, %H:%M}</p></div>'
        f'<div class="stats">{stats}</div></header>',
        '<div class="controls"><label class="switch"><input type="checkbox" id="pics"><i></i>'
        'Show card pictures</label>'
        '<label>Sort <select id="sort"><option value="pct">Most complete</option>'
        '<option value="missing">Fewest missing</option><option value="name">Set name</option>'
        f'</select></label><div class="legend">{legend}</div></div>',
    ]
    for n, (language, sets) in enumerate(sorted(
            languages.items(), key=lambda kv: (-sum(r["owned_count"] for r in kv[1]), kv[0]))):
        sub = [f"{sum(r['owned_count'] for r in sets)} cards owned", f"{len(sets)} set(s)",
               f"{sum(len(r['missing']) for r in sets)} missing"]
        if hidden.get(language):
            sub.append(f"{hidden[language]} set(s) under {hide_below:g}% hidden")
        parts.append(f"<section><h2>{e(language)}</h2>"
                     f'<p class="lang-sub">{" · ".join(sub)}</p>')
        series = {}
        for r in sets:
            series.setdefault(r.get("series") or "Other", []).append(r)
        # Newest series first, by its latest set.
        for m, (name, group) in enumerate(sorted(
                series.items(), key=lambda kv: max(r.get("released") or "" for r in kv[1]),
                reverse=True)):
            parts.append(f'<h3>{e(name)} <span>{len(group)} set(s)</span></h3>'
                         + _set_group(group, f"s{n}-{m}"))
        parts.append('<p class="hint">Pick a set to see the cards it\'s missing.</p></section>')
    if unmatched:
        items = "".join(f"<li>{e(n)} ({e(lang)}): {e(why)}</li>" for n, lang, why in unmatched)
        parts.append(f'<details class="unmatched"><summary>Couldn\'t check {len(unmatched)} '
                     f"set(s)</summary><ul>{items}</ul></details>")
    parts.append(f"</main><script>{HTML_SCRIPT}</script></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


# --------------------------------------------------------------------- CLI --

def build_arg_parser():
    p = argparse.ArgumentParser(
        description="List the Pokémon TCG cards missing from each set in a RareCandy collection.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="A CSV already exported with RareCandyExporter.")
    src.add_argument("--profile", help="RareCandy profile name or URL; runs RareCandyExporter "
                                       "(vendor/RareCandyExporter) to export it first.")
    p.add_argument("--export-csv", default=data_path("rarecandy_export.csv"),
                   help="Where --profile saves the RareCandy export (default: %(default)s).")
    p.add_argument("--headful", action="store_true",
                   help="With --profile, open a browser to sign in to RareCandy, so the export "
                        "can say which finish each MEGA Dream ex card is (the login is then "
                        "cached for later runs).")
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="NAME",
                   help="Only check this RareCandy set name (repeatable). Default: every set "
                        "in the collection.")
    p.add_argument("--map", dest="maps", action="append", default=[], metavar="NAME=CODE",
                   help="Map a RareCandy set name to a TCGdex set code for this run "
                        "(repeatable), on top of set_map.json.")
    p.add_argument("--set-map", default=DEFAULT_SET_MAP,
                   help="Set name -> TCGdex code overrides file (default: set_map.json).")
    p.add_argument("--min-complete", type=float, default=70, metavar="PERCENT",
                   help="Only list sets you already own at least this percentage of "
                        "(default: %(default)g). 0 lists every set you own a card from.")
    p.add_argument("--out", default=data_path("missing_cards.csv"),
                   help="CSV to write the missing cards to (default: %(default)s).")
    p.add_argument("--prints-out", metavar="FILE",
                   help="CSV of the missing cards print by print, for wants_list.py: MEGA Dream "
                        "ex's reverse holos as their own rows when the export says each copy's "
                        "finish (default: missing_prints.csv next to --out).")
    p.add_argument("--html", metavar="FILE",
                   help="Page giving an overview of every set: how complete it is, how many "
                        "cards it's missing, and those cards, with pictures if you want them "
                        "(default: missing_cards.html next to --out).")
    p.add_argument("--html-min-complete", type=float, default=10, metavar="PERCENT",
                   help="Leave sets under this percentage complete off the --html page "
                        "(default: %(default)g).")
    p.add_argument("--no-english-names", dest="english_names", action="store_false",
                   help="Skip looking up English names for non-English cards (saves "
                        "Cardmarket's ~14 MB product list and one TCGdex request per card).")
    p.add_argument("--json", metavar="FILE",
                   help="Also write the missing cards as JSON, grouped by set.")
    p.add_argument("--ordered", default=DEFAULT_ORDERED, metavar="FILE",
                   help="Cards on order (default: ordered.txt in ~/PokemonData, if it "
                        "exists). Any that are now in the collection are moved out to "
                        "--arrived.")
    p.add_argument("--arrived", metavar="FILE",
                   help="Where cards that have arrived go from --ordered (default: "
                        "ordered_arrived.txt next to it).")
    p.add_argument("--keep-ordered", dest="prune_ordered", action="store_false",
                   help="Leave --ordered as it is, even for cards now in the collection.")
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        # Same as binder_cover's main(): a legacy Windows console codepage
        # can't encode Japanese card names, so print '?' instead of crashing.
        sys.stdout.reconfigure(errors="replace")
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if not 0 <= args.min_complete <= 100:
        parser.error("--min-complete must be between 0 and 100.")

    csv_path = args.csv
    if args.profile:
        run_exporter(args.profile, args.export_csv, headful=args.headful)
        csv_path = args.export_csv

    finishes = {}
    owned = read_owned(csv_path, finishes)
    if args.sets:
        wanted = {s.lower() for s in args.sets}
        owned = {k: v for k, v in owned.items() if k[0].lower() in wanted}
    if not owned:
        sys.exit("No matching sets found in the export.")

    set_map = load_set_map(args.set_map)
    for m in args.maps:
        name, sep, code = m.partition("=")
        if not sep or not name.strip() or not code.strip():
            sys.exit(f'Invalid --map "{m}": expected "Set Name=CODE".')
        set_map.setdefault("cli", {})[name.strip().lower()] = code.strip()

    results, unmatched = find_missing(owned, set_map, finishes)
    if args.prune_ordered:
        prune_ordered(results, args.ordered, args.arrived)
    results, below = apply_threshold(results, args.min_complete)
    if args.english_names:
        add_english_names(results, DEFAULT_NAMES_CACHE)
    print_report(results, unmatched, below, args.min_complete)
    ensure_parent(args.out)
    write_csv(results, args.out)
    print(f"Wrote {sum(len(r['missing']) for r in results)} row(s) to {os.path.abspath(args.out)}")
    prints_out = args.prints_out or os.path.join(os.path.dirname(os.path.abspath(args.out)),
                                                 PRINTS_NAME)
    ensure_parent(prints_out)
    write_prints_csv(results, prints_out)
    print(f"Wrote {sum(len(r.get('prints', r['missing'])) for r in results)} row(s) to "
          f"{os.path.abspath(prints_out)} for the wants list")
    html_out = args.html or os.path.join(os.path.dirname(os.path.abspath(args.out)), HTML_NAME)
    ensure_parent(html_out)
    write_html(results, unmatched, html_out, below, args.min_complete, args.html_min_complete)
    print(f"Wrote the set overview to {os.path.abspath(html_out)}")
    if args.json:
        ensure_parent(args.json)
        write_json(results, unmatched, args.json, below, args.min_complete)
        print(f"Wrote JSON to {os.path.abspath(args.json)}")


if __name__ == "__main__":
    main()

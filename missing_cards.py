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
import csv
import json
import os
import re
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BINDER_TOOL_DIR = os.path.join(SCRIPT_DIR, "vendor", "pokemon-binder-cover-tool")
EXPORTER_DIR = os.path.join(SCRIPT_DIR, "vendor", "RareCandyExporter")
DEFAULT_SET_MAP = os.path.join(SCRIPT_DIR, "set_map.json")

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
from binder_cover import TCGDEX_BASE, TCGDEX_LANGS, TCGDEX_FIELD_PRIORITY  # noqa: E402

# RareCandyExporter writes a full language name per card (detected from the
# card's scrydex image URL). These are the TCGdex datasets to try, in order,
# for each. Languages TCGdex's five binder-tool datasets don't cover (German,
# French, ...) share the English print run's card list, so they use "en".
LANGUAGE_TO_TCGDEX = {
    "english": ("en",),
    "japanese": ("ja",),
    "chinese": ("zh-tw", "zh-cn"),
    "korean": ("ko",),
}

OUT_COLUMNS = ["Set Name", "TCGdex Set", "Language", "Card Number", "Card Name", "TCGdex Card ID"]


# ------------------------------------------------------ RareCandy export --

def run_exporter(profile, out_csv, extra_args=()):
    """Run RareCandyExporter's export.js as-is to produce `out_csv`."""
    export_js = os.path.join(EXPORTER_DIR, "export.js")
    if not os.path.isfile(export_js):
        sys.exit("vendor/RareCandyExporter is empty -- run `git submodule update --init` first.")
    if not os.path.isdir(os.path.join(EXPORTER_DIR, "node_modules")):
        sys.exit("RareCandyExporter's dependencies aren't installed -- run `npm install` "
                 "in vendor/RareCandyExporter first.")
    node = shutil.which("node")
    if not node:
        sys.exit("Node.js 18+ is needed to run RareCandyExporter (`node` not found on PATH).")
    cmd = [node, export_js, profile, os.path.abspath(out_csv), *extra_args]
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


def read_owned(csv_path):
    """Read a RareCandyExporter CSV. Returns {(set_name, language): set of
    normalized card numbers owned}. Rows with no set name or card number
    (untracked cards) can't be matched to a set list, so they're skipped."""
    owned = {}
    skipped = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            set_name = (row.get("Set Name") or "").strip()
            number = normalize_number(row.get("Card Number"))
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


def _exact_match(value, field):
    """Set id whose `field` ("id" or "name") equals `value` exactly (case-
    insensitive) in any dataset, from the cached listings. binder_cover's
    _find_set_id re-fetches every listing per call and only does substring name
    matching, which calls "Mega Evolution" ambiguous next to "Mega Evolution
    Black Star Promos"; an exact match settles that without a manual mapping,
    and _find_set_id stays the fallback."""
    needle = value.strip().lower()
    for lang in TCGDEX_LANGS:
        hits = {s["id"] for s in _set_listing(lang)
                if s.get("id") and str(s.get(field, "")).strip().lower() == needle}
        if len(hits) == 1:
            return hits.pop()
    return None


def resolve_set_id(set_name, language, set_map):
    """TCGdex set id for a RareCandy set name, or None. Order: set_map.json
    override, exact name match, then binder_cover's code/name search."""
    code = mapped_code(set_map, set_name, language)
    if code:
        return _exact_match(code, "id") or code
    return _exact_match(set_name, "name") or binder_cover._find_set_id(set_name)


def tcgdex_langs_for(language):
    preferred = LANGUAGE_TO_TCGDEX.get(language.lower(), ("en",))
    return list(preferred) + [lang for lang in TCGDEX_FIELD_PRIORITY if lang not in preferred]


def fetch_card_list(set_id, language):
    """The set's full card list from the TCGdex dataset matching the cards'
    print language, falling back to the other datasets. Returns
    (cards, tcgdex_lang_used, set_detail) or (None, None, None)."""
    for lang in tcgdex_langs_for(language):
        detail = binder_cover._fetch_json(f"{TCGDEX_BASE}/{lang}/sets/{set_id}")
        if detail and detail.get("cards"):
            return detail["cards"], lang, detail
    return None, None, None


# ------------------------------------------------------------- diffing --

def _sort_key(number):
    """Numbers first in numeric order, then lettered ones (TG01, SV001, ...)."""
    m = re.match(r"^(\D*)(\d*)(.*)$", number)
    prefix, digits, rest = m.groups()
    return (prefix != "", prefix, int(digits) if digits else 0, rest)


def find_missing(owned, set_map):
    """Returns (results, unmatched). results is one dict per (set, language)
    group: set_name, language, set_id, tcgdex_lang, total, owned_count,
    missing (list of TCGdex card dicts), unknown_owned (owned numbers TCGdex
    doesn't list). unmatched is [(set_name, language, reason)]."""
    results, unmatched = [], []
    for (set_name, language) in sorted(owned, key=lambda k: (k[0].lower(), k[1])):
        numbers = owned[(set_name, language)]
        print(f"\n[set] {set_name} ({language}, {len(numbers)} owned)")
        set_id = resolve_set_id(set_name, language, set_map)
        if not set_id:
            unmatched.append((set_name, language, "no TCGdex set found"))
            continue
        cards, lang_used, _ = fetch_card_list(set_id, language)
        if not cards:
            unmatched.append((set_name, language, f"TCGdex set '{set_id}' has no card list"))
            continue
        if lang_used not in LANGUAGE_TO_TCGDEX.get(language.lower(), ("en",)):
            print(f"[warning] '{set_id}' isn't in TCGdex's {language} data, so its "
                  f"{lang_used} card list is being used instead. If that's the wrong set, "
                  "add a mapping for it to set_map.json.")

        listed = {normalize_number(c.get("localId")): c for c in cards}
        missing = sorted((c for n, c in listed.items() if n not in numbers),
                         key=lambda c: _sort_key(normalize_number(c.get("localId"))))
        unknown = sorted((n for n in numbers if n not in listed), key=_sort_key)
        results.append({
            "set_name": set_name, "language": language, "set_id": set_id,
            "tcgdex_lang": lang_used, "total": len(listed),
            "owned_count": len(numbers) - len(unknown),
            "missing": missing, "unknown_owned": unknown,
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


def print_report(results, unmatched, below=(), min_complete=0):
    print("\n" + "=" * 60)
    for r in results:
        print(f"\n{r['set_name']} [{r['set_id']}, {r['language']}]: own "
              f"{r['owned_count']}/{r['total']} ({completion(r):.0f}%), "
              f"missing {len(r['missing'])}")
        for c in r["missing"]:
            print(f"  #{c.get('localId', '?'):<8} {c.get('name', '')}")
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


def write_csv(results, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(OUT_COLUMNS)
        for r in results:
            for c in r["missing"]:
                w.writerow([r["set_name"], r["set_id"], r["language"],
                            c.get("localId", ""), c.get("name", ""), c.get("id", "")])


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


# --------------------------------------------------------------------- CLI --

def build_arg_parser():
    p = argparse.ArgumentParser(
        description="List the Pokémon TCG cards missing from each set in a RareCandy collection.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="A CSV already exported with RareCandyExporter.")
    src.add_argument("--profile", help="RareCandy profile name or URL; runs RareCandyExporter "
                                       "(vendor/RareCandyExporter) to export it first.")
    p.add_argument("--export-csv", default="rarecandy_export.csv",
                   help="Where --profile saves the RareCandy export (default: %(default)s).")
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="NAME",
                   help="Only check this RareCandy set name (repeatable). Default: every set "
                        "in the collection.")
    p.add_argument("--map", dest="maps", action="append", default=[], metavar="NAME=CODE",
                   help="Map a RareCandy set name to a TCGdex set code for this run "
                        "(repeatable), on top of set_map.json.")
    p.add_argument("--set-map", default=DEFAULT_SET_MAP,
                   help="Set name -> TCGdex code overrides file (default: set_map.json).")
    p.add_argument("--min-complete", type=float, default=75, metavar="PERCENT",
                   help="Only list sets you already own at least this percentage of "
                        "(default: %(default)g). 0 lists every set you own a card from.")
    p.add_argument("--out", default="missing_cards.csv",
                   help="CSV to write the missing cards to (default: %(default)s).")
    p.add_argument("--json", metavar="FILE",
                   help="Also write the missing cards as JSON, grouped by set.")
    return p


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if not 0 <= args.min_complete <= 100:
        parser.error("--min-complete must be between 0 and 100.")

    csv_path = args.csv
    if args.profile:
        run_exporter(args.profile, args.export_csv)
        csv_path = args.export_csv

    owned = read_owned(csv_path)
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

    results, unmatched = find_missing(owned, set_map)
    results, below = apply_threshold(results, args.min_complete)
    print_report(results, unmatched, below, args.min_complete)
    write_csv(results, args.out)
    print(f"Wrote {sum(len(r['missing']) for r in results)} row(s) to {os.path.abspath(args.out)}")
    if args.json:
        write_json(results, unmatched, args.json, below, args.min_complete)
        print(f"Wrote JSON to {os.path.abspath(args.json)}")


if __name__ == "__main__":
    main()

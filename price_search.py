#!/usr/bin/env python3
"""
Pokémon TCG Missing Card Price Search
-------------------------------------
Takes the missing cards written by `missing_cards.py --json FILE` and asks
each marketplace plugin (in marketplaces/) which of them are for sale and at
what price, then lists every offer found, cheapest first per card, in one
currency.

Shipping isn't counted: prices are the listed item price only.

Usage examples:

  python price_search.py missing.json
  python price_search.py missing.json --marketplace deckdhq --cheapest-only
  python price_search.py --list-marketplaces

Run `python price_search.py --help` for the full flag list.
"""
import argparse
import concurrent.futures
import csv
import json
import os
import sys
import urllib.request
from decimal import Decimal, ROUND_HALF_UP

import marketplaces
from marketplaces.base import MATCH_LEVELS, MATCH_UNCERTAIN, MissingCard, Offer, SearchContext

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE_DIR = os.path.join(SCRIPT_DIR, ".price_cache")
# Same free exchange-rate service RareCandyExporter's --currency uses.
RATES_URL = "https://api.frankfurter.app/latest?from={src}&to={dst}"
PENNY = Decimal("0.01")


# ------------------------------------------------------------------ input --

def load_missing(path, only_sets=()):
    """[(set_entry, [MissingCard, ...]), ...] from missing_cards.py's JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    wanted = {s.lower() for s in only_sets}
    groups = []
    for s in data.get("sets", []):
        if wanted and s["set_name"].lower() not in wanted and s["set_id"].lower() not in wanted:
            continue
        cards = [MissingCard.from_json(c) for c in s.get("missing", [])]
        if cards:
            groups.append((s, cards))
    return groups


# ---------------------------------------------------------------- plugins --

def select_plugins(available, requested, environ=os.environ):
    """(plugins to run, {id: reason} for ones skipped). With no --marketplace
    given, every plugin whose settings are present runs."""
    unknown = [r for r in requested if r not in available]
    if unknown:
        sys.exit(f"Unknown marketplace(s): {', '.join(unknown)}. "
                 "Run with --list-marketplaces to see what's available.")
    chosen, skipped = [], {}
    for pid in (requested or sorted(available)):
        plugin = available[pid]
        missing = plugin.missing_config(environ)
        if missing:
            skipped[pid] = f"needs {', '.join(missing)} set"
        else:
            chosen.append(plugin)
    return chosen, skipped


def run_plugin(plugin, groups, ctx):
    """All of one plugin's offers across every set, as (set index, Offer)
    pairs, plus how many sets failed. The set index keeps offers apart when
    two collections share TCGdex card ids (e.g. English and German prints both
    use the English dataset). Sets run one after another so a plugin's own
    requests stay paced."""
    offers, failures = [], 0
    for gi, (set_entry, cards) in enumerate(groups):
        wanted = [c for c in cards if plugin.handles(c)]
        if not wanted:
            continue
        ids = {c.card_id for c in wanted}
        try:
            found = plugin.search_set(wanted, ctx)
        except Exception as e:  # noqa: BLE001 -- a broken plugin mustn't stop the others
            ctx.log(f"{set_entry['set_name']}: search failed: {e}")
            failures += 1
            continue
        for o in found:
            if not isinstance(o, Offer) or o.card_id not in ids or o.match not in MATCH_LEVELS:
                ctx.log(f"ignoring malformed offer: {o!r}")
                continue
            o.marketplace = plugin.id
            offers.append((gi, o))
    return offers, failures


def search_all(plugins, groups, make_ctx):
    """{plugin id: (offers, failed set count)}, running marketplaces in parallel."""
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(plugins))) as pool:
        futures = {pool.submit(run_plugin, p, groups, make_ctx(p)): p.id for p in plugins}
        for fut in concurrent.futures.as_completed(futures):
            results[futures[fut]] = fut.result()
    return results


# --------------------------------------------------------------- currency --

def fetch_rate(src, dst):
    url = RATES_URL.format(src=src, dst=dst)
    with urllib.request.urlopen(url, timeout=15) as resp:
        return Decimal(str(json.load(resp)["rates"][dst]))


def exchange_rates(currencies, target, fetch=None):
    """{currency: rate into target}. A currency whose rate can't be fetched is
    left out, and its offers are listed without a converted price."""
    fetch = fetch or fetch_rate
    rates = {target: Decimal(1)}
    for cur in sorted(set(currencies) - {target}):
        try:
            rates[cur] = fetch(cur, target)
        except Exception as e:  # noqa: BLE001
            print(f"[currency] Couldn't get a {cur}->{target} rate ({e}); "
                  f"{cur} offers won't be ranked.")
    return rates


def converted(offer, rates):
    rate = rates.get(offer.currency.upper())
    return None if rate is None else (Decimal(str(offer.price)) * rate).quantize(PENNY, ROUND_HALF_UP)


# ----------------------------------------------------------------- output --

def rank_offers(groups, offers, rates, include_uncertain=False):
    """{(set index, card_id): [(converted price or None, Offer), ...]}
    cheapest first, raw cards ahead of graded slabs (a slab is only the
    cheapest when no raw copy is for sale); offers with no converted price
    go last."""
    ranked = {(gi, c.card_id): [] for gi, (_, cards) in enumerate(groups) for c in cards}
    for gi, o in offers:
        if o.match == MATCH_UNCERTAIN and not include_uncertain:
            continue
        ranked[(gi, o.card_id)].append((converted(o, rates), o))
    for lst in ranked.values():
        lst.sort(key=lambda po: (po[1].grade is not None, po[0] is None, po[0] or 0))
    return ranked


def print_report(groups, ranked, currency, plugin_results, skipped):
    print("\n" + "=" * 60)
    grand_total, grand_found, grand_missing = Decimal(0), 0, 0
    for gi, (set_entry, cards) in enumerate(groups):
        best = {c.card_id: ranked[(gi, c.card_id)] for c in cards}
        found = [c for c in cards if best[c.card_id]]
        total = sum((best[c.card_id][0][0] for c in found if best[c.card_id][0][0] is not None),
                    Decimal(0))
        grand_total += total
        grand_found += len(found)
        grand_missing += len(cards)
        print(f"\n{set_entry['set_name']} [{set_entry['set_id']}, {set_entry['language']}]: "
              f"{len(found)}/{len(cards)} missing card(s) for sale, "
              f"cheapest of each totals {currency} {total:.2f}")
        for c in cards:
            if best[c.card_id]:
                price, o = best[c.card_id][0]
                shown = f"{currency} {price:.2f}" if price is not None else f"{o.currency} {o.price}"
                graded = f" (graded {o.grade})" if o.grade else ""
                print(f"  #{c.local_id:<8} {c.name}  {shown} on {o.marketplace}{graded}")
        not_found = [c.local_id for c in cards if not best[c.card_id]]
        if not_found:
            print(f"  Not found for sale: {', '.join('#' + n for n in not_found)}")
    print(f"\n{grand_found}/{grand_missing} missing card(s) found for sale; "
          f"buying the cheapest of each comes to {currency} {grand_total:.2f} before shipping.")
    for pid, (offers, failures) in sorted(plugin_results.items()):
        extra = f", {failures} set(s) failed" if failures else ""
        print(f"  {pid}: {len(offers)} offer(s){extra}")
    for pid, why in sorted(skipped.items()):
        print(f"  {pid}: skipped ({why})")


def write_csv(groups, ranked, currency, path, cheapest_only=False):
    cols = ["Set Name", "TCGdex Set", "Language", "Card Number", "Card Name", "TCGdex Card ID",
            "Marketplace", f"Price ({currency})", "Listed Price", "Listed Currency", "Condition",
            "Grade", "Seller", "Quantity", "Match", "Listing Title", "URL"]
    rows = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for gi, (set_entry, cards) in enumerate(groups):
            for c in cards:
                offers = ranked[(gi, c.card_id)]
                if cheapest_only:
                    offers = offers[:1]
                for price, o in offers:
                    w.writerow([set_entry["set_name"], c.set_id, c.language, c.local_id, c.name,
                                c.card_id, o.marketplace, "" if price is None else f"{price:.2f}",
                                str(o.price), o.currency, o.condition or "", o.grade or "",
                                o.seller or "",
                                "" if o.quantity is None else o.quantity, o.match, o.title, o.url])
                    rows += 1
    return rows


def list_marketplaces(available, environ=os.environ):
    if not available:
        print("No marketplace plugins installed yet (add one to marketplaces/).")
        return
    for pid, p in sorted(available.items()):
        langs = ", ".join(sorted(p.languages)) if p.languages else "all"
        missing = p.missing_config(environ)
        state = f"needs {', '.join(missing)} set" if missing else "ready"
        print(f"{pid:<14} {p.name:<24} languages: {langs:<16} {state}")


# -------------------------------------------------------------------- CLI --

def build_arg_parser():
    p = argparse.ArgumentParser(
        description="Search marketplaces for the cards listed by missing_cards.py --json.")
    p.add_argument("missing_json", nargs="?", help="JSON written by missing_cards.py --json.")
    p.add_argument("--marketplace", dest="marketplaces", action="append", default=[], metavar="ID",
                   help="Only search this marketplace (repeatable). Default: every marketplace "
                        "whose settings are present.")
    p.add_argument("--list-marketplaces", action="store_true",
                   help="List the installed marketplace plugins and exit.")
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="NAME",
                   help="Only search this set, by name or TCGdex set id (repeatable).")
    p.add_argument("--currency", default="GBP",
                   help="Currency to compare prices in (default: %(default)s).")
    p.add_argument("--out", default="offers.csv",
                   help="CSV to write the offers to (default: %(default)s).")
    p.add_argument("--cheapest-only", action="store_true",
                   help="Write only the cheapest offer per card instead of every offer.")
    p.add_argument("--include-uncertain", action="store_true",
                   help="Also count offers a marketplace isn't sure are the right print.")
    p.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                   help="Where marketplace responses are cached (default: .price_cache).")
    p.add_argument("--no-cache", action="store_true", help="Always fetch fresh results.")
    p.add_argument("-v", "--verbose", action="store_true", help="Print every request made.")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    available = marketplaces.discover()
    if args.list_marketplaces:
        list_marketplaces(available)
        return
    if not args.missing_json:
        sys.exit("Give the JSON file written by `missing_cards.py --json FILE`.")

    currency = args.currency.upper()
    groups = load_missing(args.missing_json, args.sets)
    if not groups:
        sys.exit("No missing cards to search for in that file.")
    plugins, skipped = select_plugins(available, args.marketplaces)
    for pid, why in sorted(skipped.items()):
        print(f"[{pid}] Skipped: {why}.")
    if not plugins:
        sys.exit("No marketplaces to search. Run with --list-marketplaces to see what's available.")

    n_cards = sum(len(cards) for _, cards in groups)
    print(f"Searching {', '.join(p.id for p in plugins)} for {n_cards} missing card(s) "
          f"across {len(groups)} set(s)...")
    cache_dir = None if args.no_cache else args.cache_dir
    plugin_results = search_all(
        plugins, groups,
        lambda p: SearchContext(p, config={k: os.environ[k] for k in p.needs},
                                cache_dir=cache_dir, verbose=args.verbose))

    offers = [pair for found, _ in plugin_results.values() for pair in found]
    rates = exchange_rates({o.currency.upper() for _, o in offers}, currency)
    ranked = rank_offers(groups, offers, rates, args.include_uncertain)
    print_report(groups, ranked, currency, plugin_results, skipped)
    rows = write_csv(groups, ranked, currency, args.out, args.cheapest_only)
    print(f"Wrote {rows} offer(s) to {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()

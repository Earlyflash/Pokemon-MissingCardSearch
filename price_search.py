#!/usr/bin/env python3
"""
Pokémon TCG Missing Card Price Search
-------------------------------------
Takes the missing cards written by missing_cards.py (its missing_cards.csv,
or the file from its --json flag) and asks
each marketplace plugin (in marketplaces/) which of them are for sale and at
what price, then lists every offer found, cheapest first per card, in one
currency.

Shipping isn't counted: prices are the listed item price only.

Usage examples:

  python price_search.py missing_cards.csv
  python price_search.py missing.json --marketplace deckdhq --cheapest-only
  python price_search.py --list-marketplaces

Run `python price_search.py --help` for the full flag list.
"""
import argparse
import concurrent.futures
import csv
import datetime
import html
import json
import os
import sys
import time
import urllib.request
from decimal import Decimal, ROUND_HALF_UP

import marketplaces
from marketplaces.base import MATCH_LEVELS, MATCH_UNCERTAIN, MissingCard, Offer, SearchContext

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE_DIR = os.path.join(SCRIPT_DIR, ".price_cache")
# Same free exchange-rate service RareCandyExporter's --currency uses (it has
# since moved from api.frankfurter.app, which now redirects here).
RATES_URL = "https://api.frankfurter.dev/v1/latest?from={src}&to={dst}"
PENNY = Decimal("0.01")


# ------------------------------------------------------------------ input --

# missing_cards.py's print languages -> TCGdex dataset, for reading its CSV
# (the JSON carries tcgdex_lang itself). Other languages use the English list.
CSV_LANGUAGE_TO_TCGDEX = {"english": "en", "japanese": "ja", "chinese": "zh-tw", "korean": "ko"}


def read_missing_csv(f):
    """The same {"sets": [...]} shape as the JSON, rebuilt from missing_cards.csv."""
    sets = {}
    for row in csv.DictReader(f):
        set_name, set_id, language = row["Set Name"], row["TCGdex Set"], row["Language"]
        entry = sets.setdefault((set_name, set_id, language), {
            "set_id": set_id, "set_name": set_name, "language": language,
            "tcgdex_lang": CSV_LANGUAGE_TO_TCGDEX.get(language.strip().lower(), "en"),
            "missing": [],
        })
        entry["missing"].append({
            "card_id": row["TCGdex Card ID"], "set_id": set_id, "set_name": set_name,
            "local_id": row["Card Number"], "name": row["Card Name"],
            "language": language, "tcgdex_lang": entry["tcgdex_lang"],
            "name_en": row.get("English Name") or None,
        })
    return {"sets": list(sets.values())}


def load_missing(path, only_sets=()):
    """[(set_entry, [MissingCard, ...]), ...] from missing_cards.py's --json
    file or its missing_cards.csv, whichever `path` is."""
    # utf-8-sig: missing_cards.csv starts with a BOM so Excel reads it right.
    with open(path, encoding="utf-8-sig", newline="") as f:
        head = f.read(1)
        f.seek(0)
        if head in ("{", "["):
            data = json.load(f)
        else:
            try:
                data = read_missing_csv(f)
            except KeyError as e:
                sys.exit(f"{path} isn't missing_cards.py output: no {e} column. Give it "
                         "missing_cards.csv or the file written by `missing_cards.py --json FILE`.")
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
    requests stay paced. Progress goes to ctx.progress (stderr)."""
    offers, failures = [], 0
    todo = [(gi, set_entry, [c for c in cards if plugin.handles(c)])
            for gi, (set_entry, cards) in enumerate(groups)]
    todo = [t for t in todo if t[2]]
    noun = "price(s)" if plugin.price_guide else "offer(s)"
    if not todo:
        ctx.progress("nothing to search (none of the missing cards are in languages it sells)")
    else:
        ctx.progress(f"started: {len(todo)} set(s) to search")
    for n, (gi, set_entry, wanted) in enumerate(todo, 1):
        ids = {c.card_id for c in wanted}
        try:
            found = plugin.search_set(wanted, ctx)
        except Exception as e:  # noqa: BLE001 -- a broken plugin mustn't stop the others
            ctx.log(f"{set_entry['set_name']}: search failed: {e}")
            failures += 1
            continue
        before = len(offers)
        for o in found:
            if not isinstance(o, Offer) or o.card_id not in ids or o.match not in MATCH_LEVELS:
                ctx.log(f"ignoring malformed offer: {o!r}")
                continue
            o.marketplace = plugin.id
            offers.append((gi, o))
        ctx.progress(f"set {n}/{len(todo)} {set_entry['set_name']}: "
                     f"{len(offers) - before} {noun}")
    return offers, failures


def search_all(plugins, groups, make_ctx):
    """{plugin id: (offers, failed set count)}, running marketplaces in parallel
    and saying on stderr as each one finishes."""
    results = {}
    start = time.monotonic()
    ctxs = {p.id: make_ctx(p) for p in plugins}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(plugins))) as pool:
        futures = {pool.submit(run_plugin, p, groups, ctxs[p.id]): p for p in plugins}
        for fut in concurrent.futures.as_completed(futures):
            p = futures[fut]
            results[p.id] = fut.result()
            offers, failures = results[p.id]
            ctx = ctxs[p.id]
            noun = "price(s)" if p.price_guide else "offer(s)"
            failed = f", {failures} set(s) failed" if failures else ""
            left = [q.id for q in plugins if q.id not in results]
            waiting = f"; still waiting on {', '.join(left)}" if left else ""
            ctx.progress(f"done in {time.monotonic() - start:.0f}s: {len(offers)} {noun}{failed}, "
                         f"{ctx.pages_summary()} ({len(results)}/{len(plugins)} finished{waiting})")
    return results


# --------------------------------------------------------------- currency --

def fetch_rate(src, dst):
    # Its Cloudflare front refuses urllib's default User-Agent (error 1010).
    req = urllib.request.Request(RATES_URL.format(src=src, dst=dst),
                                 headers={"User-Agent": "Pokemon-MissingCardSearch"})
    with urllib.request.urlopen(req, timeout=15) as resp:
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
    cheapest first, graded slabs included; offers with no converted price
    go last."""
    ranked = {(gi, c.card_id): [] for gi, (_, cards) in enumerate(groups) for c in cards}
    for gi, o in offers:
        if o.match == MATCH_UNCERTAIN and not include_uncertain:
            continue
        ranked[(gi, o.card_id)].append((converted(o, rates), o))
    for lst in ranked.values():
        lst.sort(key=lambda po: (po[0] is None, po[0] or 0))
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
                print(f"            {o.url}")
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


def print_guide_report(groups, ranked, currency, plugin_results, plugins=()):
    """Prices from price-guide marketplaces (one price per card, not
    listings), kept apart from the offers above."""
    print("\n" + "=" * 60)
    print("\nPrice guides: one price per card, not individual listings, so not counted "
          "in the totals above.")
    for p in plugins:
        print(f"  {p.id}: {p.guide_description}.")
    guides = {p.id: p.guide_prefix for p in plugins}
    for gi, (set_entry, cards) in enumerate(groups):
        priced = [(c, ranked[(gi, c.card_id)]) for c in cards if ranked[(gi, c.card_id)]]
        if not priced:
            continue
        total = sum((lst[0][0] for _, lst in priced if lst[0][0] is not None), Decimal(0))
        print(f"\n{set_entry['set_name']} [{set_entry['set_id']}, {set_entry['language']}]: "
              f"{len(priced)}/{len(cards)} missing card(s) priced, together {currency} {total:.2f}")
        for c, lst in priced:
            price, o = lst[0]
            shown = f"{currency} {price:.2f}" if price is not None else f"{o.currency} {o.price}"
            prefix = guides.get(o.marketplace, "from ")
            print(f"  #{c.local_id:<8} {c.name}  {prefix}{shown} on {o.marketplace}")
    for pid, (offers, failures) in sorted(plugin_results.items()):
        extra = f", {failures} set(s) failed" if failures else ""
        print(f"  {pid}: {len(offers)} price(s){extra}")


def write_csv(groups, ranked, currency, path, cheapest_only=False):
    cols = ["Set Name", "TCGdex Set", "Language", "Card Number", "Card Name", "TCGdex Card ID",
            "Marketplace", f"Price ({currency})", "Listed Price", "Listed Currency", "Condition",
            "Grade", "Seller", "Quantity", "Match", "Listing Title", "URL"]
    rows = 0
    # utf-8-sig (with a BOM) so Excel on Windows reads Japanese card names.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
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


CURRENCY_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "JPY": "¥"}

HTML_STYLE = """
:root { --bg: #fff; --fg: #1d1d1f; --muted: #6e6e73; --line: #d9d9de; --head: #f2f2f5;
        --set: #e6ecf5; --best: #d4f5dc; --best-fg: #0b5d1e; --link: #0a58ca; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #151517; --fg: #ececf0; --muted: #9a9aa2; --line: #34343a; --head: #202024;
          --set: #1f2a3a; --best: #174a26; --best-fg: #b8f0c6; --link: #7fb2ff; }
}
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg);
       font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
h1 { font-size: 20px; margin: 0 0 4px; }
p { margin: 4px 0; color: var(--muted); }
label { display: inline-block; margin: 8px 0 12px; }
table { border-collapse: collapse; min-width: 100%; }
th, td { border-bottom: 1px solid var(--line); padding: 6px 10px; text-align: left;
         vertical-align: top; }
thead th { position: sticky; top: 0; z-index: 1; background: var(--head); white-space: nowrap;
           box-shadow: inset 0 -1px var(--line); }
tfoot th, tfoot td { background: var(--head); font-weight: 600; border-top: 2px solid var(--line); }
tfoot small { font-weight: normal; }
tr.set th { background: var(--set); font-weight: 600; }
tr.set th span { font-weight: normal; color: var(--muted); }
td.num { white-space: nowrap; color: var(--muted); }
td.price { white-space: nowrap; }
td.best { background: var(--best); }
td.best a { color: var(--best-fg); font-weight: 600; }
td.guide, td.guide a { color: var(--muted); }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
small { display: block; color: var(--muted); }
body.for-sale-only tr.unsold { display: none; }
"""


def _money(amount, currency):
    return f"{CURRENCY_SYMBOLS.get(currency, currency + ' ')}{amount:.2f}"


def _html_cell(offers, currency, guide=False, best=False, prefix="from "):
    """One marketplace's cell for one card: its cheapest offer, linked."""
    esc = html.escape
    if not offers:
        return '<td class="price"></td>'
    price, o = offers[0]
    shown = _money(price, currency) if price is not None else f"{o.currency} {o.price}"
    notes = [o.grade and f"graded {o.grade}", o.condition, len(offers) > 1 and f"+{len(offers) - 1} more"]
    note = " · ".join(n for n in notes if n)
    classes = "price" + (" guide" if guide else "") + (" best" if best else "")
    return (f'<td class="{classes}"><a href="{esc(o.url)}" title="{esc(o.title)}" target="_blank" '
            f'rel="noopener">{prefix if guide else ""}{esc(shown)}</a>'
            f'{f"<small>{esc(note)}</small>" if note else ""}</td>')


def write_html(groups, listing_ranked, guide_ranked, plugins, currency, path):
    """A table with one row per missing card and one column per marketplace.
    Each cell is that marketplace's cheapest copy, linked to the listing; the
    cheapest listing for the card is highlighted. Price-guide marketplaces get
    their own columns as "from" prices and are never highlighted."""
    esc = html.escape
    columns = ([(p, False) for p in plugins if not p.price_guide]
               + [(p, True) for p in plugins if p.price_guide])
    head = "".join(f"<th>{esc(p.name)}{f' ({esc(p.guide_label)})' if g else ''}</th>"
                   for p, g in columns)
    body, grand_found, grand_cards, grand_total = [], 0, 0, Decimal(0)
    shop_totals = {p.id: [Decimal(0), 0] for p, _ in columns}  # [sum of cheapest copies, cards]
    for gi, (set_entry, cards) in enumerate(groups):
        rows, found, total = [], 0, Decimal(0)
        for c in cards:
            listed = listing_ranked[(gi, c.card_id)] if listing_ranked else []
            guided = guide_ranked[(gi, c.card_id)] if guide_ranked else []
            if listed:
                found += 1
                total += listed[0][0] or 0
            best_id = listed[0][1].marketplace if listed and listed[0][0] is not None else None
            cells = []
            for p, g in columns:
                mine = [po for po in (guided if g else listed) if po[1].marketplace == p.id]
                if mine and mine[0][0] is not None:
                    shop_totals[p.id][0] += mine[0][0]
                    shop_totals[p.id][1] += 1
                cells.append(_html_cell(mine, currency, guide=g, best=not g and p.id == best_id,
                                        prefix=p.guide_prefix))
            if c.name_en and c.name_en != c.name:
                card = f'{esc(c.name_en)}<small>{esc(c.name)}</small>'
            else:
                card = esc(c.name)
            rows.append(f'<tr class="{"sold" if listed else "unsold"}"><td class="num">'
                        f'#{esc(c.local_id)}</td><td>{card}</td>{"".join(cells)}</tr>')
        grand_found += found
        grand_cards += len(cards)
        grand_total += total
        body.append(f'<tr class="set"><th colspan="{2 + len(columns)}">'
                    f'{esc(set_entry["set_name"])} <span>{esc(set_entry["set_id"])} · '
                    f'{esc(set_entry["language"])} · {found}/{len(cards)} for sale, cheapest of '
                    f'each {esc(_money(total, currency))}</span></th></tr>')
        body.extend(rows)
    foot = "".join(
        f'<td class="price{" guide" if g else ""}">{esc(p.guide_prefix) if g else ""}'
        f'{esc(_money(shop_totals[p.id][0], currency))}'
        f'<small>{shop_totals[p.id][1]} card(s)</small></td>' for p, g in columns)
    guide_note = "".join(f" {p.name} shows {p.guide_description}." for p, g in columns if g)
    if guide_note:
        guide_note += (" Those price-guide columns aren't listings and don't count towards "
                       "the totals.")
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Missing card prices</title>
<style>{HTML_STYLE}</style>
</head>
<body>
<h1>Missing card prices</h1>
<p>{grand_found}/{grand_cards} missing card(s) for sale; buying the cheapest of each comes to
{esc(_money(grand_total, currency))} before shipping. The cheapest listing for each card is
highlighted; click a price to open the listing.{guide_note}</p>
<p>Generated {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}.</p>
<label><input type="checkbox" id="only"> Only show cards that are for sale</label>
<table>
<thead><tr><th>#</th><th>Card</th>{head}</tr></thead>
<tbody>
{chr(10).join(body)}
</tbody>
<tfoot><tr><th colspan="2">Total per shop<small>cheapest copy of each card it has</small></th>{foot}</tr></tfoot>
</table>
<script>
document.getElementById("only").addEventListener("change", function (e) {{
  document.body.classList.toggle("for-sale-only", e.target.checked);
}});
</script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)


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
    p.add_argument("missing_json", nargs="?", metavar="MISSING_FILE",
                   help="missing_cards.csv, or the JSON written by missing_cards.py --json.")
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
    p.add_argument("--guide-out", default="price_guide.csv",
                   help="CSV for prices from price-guide marketplaces such as Cardmarket, which "
                        "publish one price per card rather than listings and are kept apart from "
                        "the offers (default: %(default)s).")
    p.add_argument("--html-out", metavar="FILE",
                   help="HTML table to write, one row per missing card and one column per "
                        "marketplace, each price linking to its listing (default: "
                        "price_table.html next to --out).")
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
    if hasattr(sys.stdout, "reconfigure"):
        # Same as missing_cards.py: a legacy Windows console codepage can't
        # encode Japanese card names, so print '?' instead of crashing.
        sys.stdout.reconfigure(errors="replace")
    args = build_arg_parser().parse_args(argv)
    available = marketplaces.discover()
    if args.list_marketplaces:
        list_marketplaces(available)
        return
    if not args.missing_json:
        sys.exit("Give missing_cards.csv, or the file written by `missing_cards.py --json FILE`.")

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

    guide_ids = {p.id for p in plugins if p.price_guide}
    offers = [pair for found, _ in plugin_results.values() for pair in found]
    rates = exchange_rates({o.currency.upper() for _, o in offers}, currency)
    listings = {pid: r for pid, r in plugin_results.items() if pid not in guide_ids}
    guides = {pid: r for pid, r in plugin_results.items() if pid in guide_ids}
    listing_ranked = guide_ranked = None
    if listings or not guides:
        listing_ranked = rank_offers(groups, [pair for found, _ in listings.values() for pair in found],
                                     rates, args.include_uncertain)
        print_report(groups, listing_ranked, currency, listings, skipped)
        rows = write_csv(groups, listing_ranked, currency, args.out, args.cheapest_only)
        print(f"Wrote {rows} offer(s) to {os.path.abspath(args.out)}")
    if guides:
        guide_ranked = rank_offers(groups, [pair for found, _ in guides.values() for pair in found],
                                   rates, args.include_uncertain)
        print_guide_report(groups, guide_ranked, currency, guides,
                           [p for p in plugins if p.id in guide_ids])
        rows = write_csv(groups, guide_ranked, currency, args.guide_out)
        print(f"Wrote {rows} price guide price(s) to {os.path.abspath(args.guide_out)}")
    html_out = args.html_out or os.path.join(os.path.dirname(args.out), "price_table.html")
    write_html(groups, listing_ranked, guide_ranked, plugins, currency, html_out)
    print(f"Wrote the price table to {os.path.abspath(html_out)}")

if __name__ == "__main__":
    main()

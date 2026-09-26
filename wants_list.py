#!/usr/bin/env python3
"""
Cardmarket Wants List Export
----------------------------
Turns the missing cards written by missing_cards.py (its missing_cards.csv,
or the file from its --json flag) into a text list for Cardmarket's
"Add Deck List" box on a wants list, so every missing card can be added to
one wants list in a single paste.

Cardmarket's box takes one card per line as
"<amount> <name> <attacks> (V.<version>) (<expansion>)", e.g.
"1 Mega Absol ex Terminal Period Claw of Darkness (V.2) (Mega Evolution)":
a Pokémon's name alone isn't enough, its attacks (or ability) pick the card,
while trainers and energy need only the name. Without the expansion it adds
the card from any set, and without the version any print within the set
(Mega Absol ex has three in Mega Evolution: #086, #161 and #180 are V.1-3).
Cardmarket's own product names carry the name and attacks ("Mega Absol ex
[Terminal Period | Claw of Darkness]"), so each card is tied to its
Cardmarket product through TCGdex (the same route as the Cardmarket
marketplace plugin) and the line is built from that product.

Prices come from Cardmarket's public daily price guide, so the list can be
filtered by price, e.g. leave out every card worth more than £20.

Usage examples:

  python wants_list.py missing_cards.csv
  python wants_list.py missing.json --max-price 20 --out cheap_wants.txt
  python wants_list.py missing.json --set "Abyss Eye" --min-price 1

Run `python wants_list.py --help` for the full flag list.
"""
import argparse
import concurrent.futures
import csv
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP

from marketplaces.base import SearchContext
from marketplaces.cardmarket import PLUGIN as CARDMARKET, PRODUCT_URL, TCGDEX_CARD_URL, product_id
from price_search import (DEFAULT_CACHE_DIR, DEFAULT_ORDERED, exchange_rates, load_missing,
                          read_ordered)

PRODUCTS_URL = ("https://downloads.s3.cardmarket.com/productCatalog/productList/"
                "products_singles_6.json")
# Sealed products (boosters, boxes, tins...): the only public place that
# names Cardmarket's expansions, via products like "Abyss Eye Booster".
NONSINGLES_URL = ("https://downloads.s3.cardmarket.com/productCatalog/productList/"
                  "products_nonsingles_6.json")
PENNY = Decimal("0.01")
PRICE_FIELDS = ("trend", "low", "avg", "avg7", "avg30")


def deck_list_name(product_name):
    """Cardmarket's product name as its deck list box wants it: the attack
    brackets flattened, "Tangela [Poison Powder | Hook]" -> "Tangela Poison
    Powder Hook". Anything outside the brackets is kept."""
    return " ".join(re.sub(r"[\[\]|]", " ", product_name or "").split())


def expansion_names(nonsingles):
    """{idExpansion: Cardmarket's expansion name}, read off its booster
    products: "Abyss Eye Booster" -> "Abyss Eye". The shortest such name
    wins, so "Mega Evolution Enhanced Booster" doesn't shadow "Mega Evolution
    Booster"; "X Booster Box" is used when a set has no plain booster.
    Expansions with neither are left out."""
    plain, boxes = {}, {}
    for p in nonsingles:
        exp, name = p.get("idExpansion"), p.get("name") or ""
        for pattern, found in ((r"(.+?) Booster", plain), (r"(.+?) Booster Box", boxes)):
            m = re.fullmatch(pattern, name)
            if m and exp is not None and len(m.group(1)) < len(found.get(exp, m.group(1) + "?")):
                found[exp] = m.group(1)
    return {**boxes, **plain}


def versions(products):
    """{idProduct: version number} for products that share their name with
    another in the same expansion, numbered in idProduct order the way
    Cardmarket numbers them (checked on Mega Evolution's three Mega Absol ex:
    #086, #161, #180 are V.1, V.2, V.3). Products with a unique name are
    left out: they need no version."""
    groups = {}
    for p in products:
        groups.setdefault((p.get("idExpansion"), p.get("name")), []).append(p["idProduct"])
    return {pid: n for ids in groups.values() if len(ids) > 1
            for n, pid in enumerate(sorted(ids), 1)}


def deck_list_line_name(product, version=None, expansion=None):
    """Everything after the amount on a deck list line: flattened name and
    attacks, then the version and expansion when known."""
    parts = [deck_list_name((product or {}).get("name"))]
    if not parts[0]:
        return None
    if version:
        parts.append(f"(V.{version})")
    if expansion:
        parts.append(f"({expansion})")
    return " ".join(parts)


def card_price(row, field, rate):
    """The card's price-guide `field` converted with `rate`, or None. Falls
    back to `low` when a product has no `field` price (new cards often have
    no trend yet)."""
    row = row or {}
    value = row.get(field)
    if value in (None, ""):
        value = row.get("low")
    if value in (None, "") or rate is None:
        return None
    return (Decimal(str(value)) * rate).quantize(PENNY, ROUND_HALF_UP)


def lookup_products(cards, ctx, workers=8):
    """{card_id: idProduct or None} for every card, via TCGdex (cached)."""
    def one(c):
        url = TCGDEX_CARD_URL.format(lang=urllib.parse.quote(c.tcgdex_lang or "en"),
                                     card_id=urllib.parse.quote(c.card_id))
        try:
            return c.card_id, product_id(ctx.fetch(url, as_json=True))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                ctx.log(f"{c.card_id}: TCGdex lookup failed ({e})")
            return c.card_id, None
        except Exception as e:  # noqa: BLE001 -- one bad card mustn't stop the list
            ctx.log(f"{c.card_id}: TCGdex lookup failed ({e})")
            return c.card_id, None
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(one, cards))


def build_rows(groups, pids, products, nonsingles, guide, field, rate):
    """One dict per missing card: where it's from, its deck list line name
    (None if it has no Cardmarket product) and its converted price. A set
    Cardmarket's sealed products don't name falls back to the set name in
    the missing cards file."""
    by_id = {p["idProduct"]: p for p in products if p.get("idProduct") is not None}
    expansions, numbered = expansion_names(nonsingles), versions(by_id.values())
    rows = []
    for set_entry, cards in groups:
        for c in cards:
            pid = pids.get(c.card_id)
            product = by_id.get(pid)
            expansion = product and (expansions.get(product.get("idExpansion"))
                                     or set_entry["set_name"])
            rows.append({
                "set_name": set_entry["set_name"], "set_id": c.set_id, "language": c.language,
                "local_id": c.local_id, "name": c.name_en or c.name, "card_id": c.card_id,
                "id_product": pid, "line_name": deck_list_line_name(product, numbered.get(pid), expansion),
                "price": card_price(guide.get(pid), field, rate) if pid else None,
            })
    return rows


def keep(row, min_price=None, max_price=None, skip_unpriced=False):
    """Whether a card passes the price filters. A card with no price is kept
    unless skip_unpriced, since its price isn't known to be out of range."""
    price = row["price"]
    if price is None:
        return not skip_unpriced
    if max_price is not None and price > max_price:
        return False
    if min_price is not None and price < min_price:
        return False
    return True


def mark_ordered(rows, ordered):
    """Set status "already ordered" on rows covered by `ordered` (from
    read_ordered): every row with a listed card id, and for wants list
    names, as many rows with that line as the amount on order."""
    ids, names = ordered[0], Counter(ordered[1])
    for r in rows:
        if r["status"] != "yes":
            continue
        key = (r["line_name"] or "").casefold()
        if r["card_id"].lower() in ids:
            r["status"] = "already ordered"
        elif names[key] > 0:
            names[key] -= 1
            r["status"] = "already ordered"


def deck_list_lines(rows):
    """"<amount> <name>" lines in alphabetical order (ignoring case and
    accents, so "Poké Pad" sorts with "Poke..."). Cards whose lines come out
    the same (e.g. an English and a German collection of one set) share a
    line with the amount added up."""
    counts = Counter(r["line_name"] for r in rows)
    return [f"{counts[name]} {name}" for name in sorted(counts, key=_sort_key)]


def _sort_key(text):
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return plain.casefold(), text


def write_csv(rows, currency, path):
    """What went into the list and why each other card was left out, for
    checking the paste against Cardmarket's report of what it added."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Set Name", "TCGdex Set", "Language", "Card Number", "Card Name",
                    "TCGdex Card ID", "Deck List Line", f"Price ({currency})", "In List",
                    "Cardmarket Link"])
        for r in rows:
            w.writerow([r["set_name"], r["set_id"], r["language"], r["local_id"], r["name"],
                        r["card_id"], r["line_name"] or "",
                        "" if r["price"] is None else f"{r['price']:.2f}", r["status"],
                        PRODUCT_URL.format(id=r["id_product"]) if r["id_product"] else ""])


def build_arg_parser():
    p = argparse.ArgumentParser(
        description="Write missing cards as a list to paste into Cardmarket's wants list "
                    "\"Add Deck List\" box.")
    p.add_argument("missing_file", metavar="MISSING_FILE",
                   help="missing_cards.csv, or the JSON written by missing_cards.py --json.")
    p.add_argument("--out", default="cardmarket_wants.txt",
                   help="Text file to write the list to (default: %(default)s).")
    p.add_argument("--csv-out", metavar="FILE",
                   help="CSV of every missing card with its price and whether it made the list "
                        "(default: next to --out, with .csv in place of .txt).")
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="NAME",
                   help="Only include this set, by name or TCGdex set id (repeatable).")
    p.add_argument("--max-price", type=Decimal, metavar="AMOUNT",
                   help="Leave out cards priced above this, in --currency (e.g. 20).")
    p.add_argument("--min-price", type=Decimal, metavar="AMOUNT",
                   help="Leave out cards priced below this, in --currency.")
    p.add_argument("--ordered", default=DEFAULT_ORDERED, metavar="FILE",
                   help="Cards already ordered, left out of the list: one per line, as a TCGdex "
                        "card id (me01-161) or a line copied from an earlier list (default: "
                        "ordered.txt next to this script, if it exists).")
    p.add_argument("--skip-unpriced", action="store_true",
                   help="With a price filter, also leave out cards Cardmarket has no price for.")
    p.add_argument("--price", dest="price_field", choices=PRICE_FIELDS, default="trend",
                   help="Which Cardmarket price-guide figure the filters use (default: "
                        "%(default)s; 'low' is the cheapest copy in any language or condition).")
    p.add_argument("--currency", default="GBP",
                   help="Currency for prices and the filters (default: %(default)s).")
    p.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                   help="Where downloads are cached, shared with price_search.py "
                        "(default: .price_cache).")
    p.add_argument("--no-cache", action="store_true", help="Always fetch fresh data.")
    p.add_argument("-v", "--verbose", action="store_true", help="Print every request made.")
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = build_arg_parser().parse_args(argv)
    currency = args.currency.upper()
    groups = load_missing(args.missing_file, args.sets)
    if not groups:
        sys.exit("No missing cards in that file.")
    cards = [c for _, cards in groups for c in cards]
    filtering = args.max_price is not None or args.min_price is not None or args.skip_unpriced

    ctx = SearchContext(CARDMARKET, cache_dir=None if args.no_cache else args.cache_dir,
                        verbose=args.verbose)
    print(f"Finding the Cardmarket product for {len(cards)} missing card(s) via TCGdex...")
    pids = lookup_products(cards, ctx)
    print("Downloading Cardmarket's product lists...")
    products = ctx.fetch(PRODUCTS_URL, as_json=True, timeout=120)
    nonsingles = ctx.fetch(NONSINGLES_URL, as_json=True, timeout=120)
    guide, rate = {}, None
    try:
        guide = CARDMARKET.price_guide_rows(ctx)
        rate = exchange_rates({"EUR"}, currency).get("EUR")
    except Exception as e:  # noqa: BLE001
        if filtering:
            sys.exit(f"Couldn't get Cardmarket's price guide ({e}), so can't filter by price.")
        print(f"[warning] Couldn't get Cardmarket's price guide ({e}); prices left blank.")
    if filtering and rate is None:
        sys.exit(f"Couldn't get a EUR->{currency} rate, so can't filter by price.")

    rows = build_rows(groups, pids, products.get("products", []),
                      nonsingles.get("products", []), guide, args.price_field, rate)
    for r in rows:
        if not r["line_name"]:
            r["status"] = "no Cardmarket product"
        elif not keep(r, args.min_price, args.max_price, args.skip_unpriced):
            r["status"] = "price filter"
        else:
            r["status"] = "yes"
    ordered = read_ordered(args.ordered)
    mark_ordered(rows, ordered)
    listed = [r for r in rows if r["status"] == "yes"]
    lines = deck_list_lines(listed)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    csv_out = args.csv_out or os.path.splitext(args.out)[0] + ".csv"
    write_csv(rows, currency, csv_out)

    no_product = [r for r in rows if r["status"] == "no Cardmarket product"]
    filtered = [r for r in rows if r["status"] == "price filter"]
    total = sum((r["price"] for r in listed if r["price"] is not None), Decimal(0))
    print(f"\n{len(listed)}/{len(rows)} missing card(s) in the list ({len(lines)} line(s)), "
          f"together about {currency} {total:.2f} at Cardmarket's {args.price_field} price.")
    if filtered:
        print(f"{len(filtered)} left out by the price filter.")
    on_order = sum(1 for r in rows if r["status"] == "already ordered")
    if on_order:
        print(f"{on_order} left out as already ordered ({args.ordered}).")
    if no_product:
        print(f"{len(no_product)} left out because TCGdex has no Cardmarket product for them:")
        for r in no_product:
            print(f"  {r['set_name']} #{r['local_id']} {r['name']}")
    unpriced = sum(1 for r in listed if r["price"] is None)
    if unpriced and filtering and not args.skip_unpriced:
        print(f"{unpriced} card(s) in the list have no Cardmarket price and were kept "
              "(--skip-unpriced leaves them out).")
    print(f"Wrote the list to {os.path.abspath(args.out)} and every card's details to "
          f"{os.path.abspath(csv_out)}.")
    print("Paste the list into a Cardmarket wants list's \"Add Deck List\" box.")


if __name__ == "__main__":
    main()

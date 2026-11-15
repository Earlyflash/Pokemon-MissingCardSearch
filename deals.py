"""
Deals: missing cards for sale under their market price
------------------------------------------------------
Reads the last price search (offers.csv and price_guide.csv in
~/PokemonData) and lists every missing card whose cheapest ungraded listing
is under its Cardmarket trend price or its PulseAPI market price (the same
prices the set overview shows; see card_prices.py). Cards already in
ordered.txt are left out.

    python deals.py                    # print the deals
    python deals.py --email-dir DIR    # also write DIR/subject.txt and DIR/body.html

deals_email.sh runs it at 7am and emails the deals. Each deal (card, listing
and price) that has been emailed is noted in deals_alerted.txt, so with
--only-new nothing is written unless there's a deal that wasn't in an
earlier email (a new listing, or the same one cheaper); the email still
lists every deal, new ones first and marked. `--mark-sent` records the
deals just written as emailed.
"""
import argparse
import csv
import datetime
import html
import os
import sys
from decimal import Decimal

from card_prices import GUIDE_NAME, OFFERS_NAME, read_card_prices
from data_dir import data_path, ensure_parent
from ordered import card_key, read_ordered_shops

ALERTED_NAME = "deals_alerted.txt"
MISSING_NAME = "missing_cards.csv"
ORDERED_NAME = "ordered.txt"


def read_missing(path):
    """{card id: row of missing_cards.csv}."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return {r["TCGdex Card ID"]: r for r in csv.DictReader(f)}
    except FileNotFoundError:
        return {}


def on_order(card_id, ordered):
    key = card_key(card_id)
    return any(k == key or k.startswith(key + " ") for k in ordered)


def find_deals(prices, missing, ordered=(), min_under=0.0):
    """Deals cheapest first by how far under: [{card_id, set, number, name,
    price, url, shop, pulse, cm, under_pulse, under_cm}], prices as Decimals
    and `under_*` the percentage under that price (None when not under it or
    there's no such price, or it's under by `min_under` percent or less).
    Only cards still missing and not on order."""
    deals = []
    for card_id, p in prices["cards"].items():
        if "low" not in p or card_id not in missing or on_order(card_id, ordered):
            continue
        low = Decimal(p["low"][0])
        refs = {k: Decimal(p[k][0]) if k in p else None for k in ("pulse", "cm")}
        under = {k: float((v - low) / v * 100) if v and low < v else None for k, v in refs.items()}
        under = {k: u if u is not None and u > min_under else None for k, u in under.items()}
        if not any(under.values()):
            continue
        row = missing[card_id]
        deals.append({
            "card_id": card_id, "set": row["Set Name"], "number": row["Card Number"],
            "name": row["English Name"] or row["Card Name"], "price": low, "url": p["low"][1],
            "shop": p["low"][2].split(" · ")[0], "pulse": refs["pulse"], "cm": refs["cm"],
            "under_pulse": under["pulse"], "under_cm": under["cm"],
        })
    deals.sort(key=lambda d: -max(v for v in (d["under_pulse"], d["under_cm"]) if v))
    return deals


def deal_key(d):
    return f"{d['card_id']} | {d['price']:.2f} | {d['url']}"


def read_alerted(path):
    """{deal key: date it was emailed} from deals_alerted.txt."""
    seen = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, sep, day = line.rstrip("\n").rpartition(" | ")
                if sep:
                    seen[key] = day
    except FileNotFoundError:
        pass
    return seen


def is_new(d, alerted):
    """Not emailed before at this price or lower, from this listing."""
    for key in alerted:
        card_id, _, rest = key.partition(" | ")
        price, _, url = rest.partition(" | ")
        if card_id == d["card_id"] and url == d["url"] and Decimal(price) <= d["price"]:
            return False
    return True


def _under(d):
    parts = []
    if d["under_cm"]:
        parts.append(f"{d['under_cm']:.0f}% under CM £{d['cm']:.2f}")
    if d["under_pulse"]:
        parts.append(f"{d['under_pulse']:.0f}% under Pulse £{d['pulse']:.2f}")
    return ", ".join(parts)


def text_report(deals, new, asof):
    if not deals:
        return "No missing cards are listed under their Cardmarket or PulseAPI price."
    lines = [f"{len(deals)} missing card(s) listed under market price (prices from {asof}):"]
    for d in deals:
        flag = "NEW " if deal_key(d) in new else "    "
        lines.append(f"{flag}{d['card_id']:9} {d['name']} — £{d['price']:.2f} at {d['shop']} "
                     f"({_under(d)})\n         {d['url']}")
    return "\n".join(lines)


def email(deals, new, asof):
    """(subject, HTML body). The body uses only the tags Outlook's send tool
    keeps (no styles or spans)."""
    e = html.escape
    n_new = sum(deal_key(d) in new for d in deals)
    subject = (f"Pokémon deals: {n_new} new, {len(deals)} under market price"
               if n_new != len(deals) else f"Pokémon deals: {len(deals)} card(s) under market price")
    rows = []
    for d in sorted(deals, key=lambda d: deal_key(d) not in new):
        mark = "<b>NEW</b> " if deal_key(d) in new else ""
        rows.append(
            f"<tr><td>{mark}{e(d['card_id'])}</td><td>{e(d['name'])}<br>{e(d['set'])} "
            f"#{e(d['number'])}</td><td><a href=\"{e(d['url'])}\"><b>£{d['price']:.2f}</b></a>"
            f"<br>{e(d['shop'])}</td>"
            f"<td>{'£%.2f' % d['cm'] if d['cm'] else '–'}"
            f"{'<br>%.0f%% under' % d['under_cm'] if d['under_cm'] else ''}</td>"
            f"<td>{'£%.2f' % d['pulse'] if d['pulse'] else '–'}"
            f"{'<br>%.0f%% under' % d['under_pulse'] if d['under_pulse'] else ''}</td></tr>")
    body = (f"<p>These missing cards are listed under their Cardmarket trend or PulseAPI market "
            f"price, from the price search of {e(asof or 'unknown time')}. Cards on order are "
            f"left out. Prices don't include shipping.</p>"
            f"<table border=\"1\" cellpadding=\"4\"><tr><th>Card</th><th>Name</th>"
            f"<th>Cheapest</th><th>CM trend</th><th>Pulse</th></tr>{''.join(rows)}</table>")
    return subject, body


def main(argv=None):
    p = argparse.ArgumentParser(description="List missing cards listed under their market price.")
    p.add_argument("--offers", default=data_path(OFFERS_NAME))
    p.add_argument("--guide", default=data_path(GUIDE_NAME))
    p.add_argument("--missing", default=data_path(MISSING_NAME))
    p.add_argument("--ordered", default=data_path(ORDERED_NAME))
    p.add_argument("--alerted", default=data_path(ALERTED_NAME),
                   help="Deals already emailed (default: %(default)s).")
    p.add_argument("--min-under", type=float, default=float(os.environ.get("DEALS_MIN_UNDER") or 0),
                   metavar="PERCENT",
                   help="Only count a card this many percent or more under a market price "
                        "(default: DEALS_MIN_UNDER, or 0: any amount under).")
    p.add_argument("--email-dir", help="Write subject.txt and body.html here for an email.")
    p.add_argument("--only-new", action="store_true",
                   help="With --email-dir, write nothing unless a deal wasn't emailed before.")
    p.add_argument("--mark-sent", action="store_true",
                   help="Record the current deals as emailed, in --alerted.")
    args = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    prices = read_card_prices(args.offers, args.guide)
    deals = find_deals(prices, read_missing(args.missing), read_ordered_shops(args.ordered),
                       args.min_under)
    alerted = read_alerted(args.alerted)
    new = {deal_key(d) for d in deals if is_new(d, alerted)}

    if args.mark_sent:
        today = datetime.date.today().isoformat()
        ensure_parent(args.alerted)
        with open(args.alerted, "a", encoding="utf-8") as f:
            for d in deals:
                if deal_key(d) in new:
                    f.write(f"{deal_key(d)} | {today}\n")
        print(f"Marked {len(new)} deal(s) as emailed.")
        return
    print(text_report(deals, new, prices["asof"]))
    if args.email_dir:
        if not deals or (args.only_new and not new):
            print("Nothing new to email.")
            return
        os.makedirs(args.email_dir, exist_ok=True)
        subject, body = email(deals, new, prices["asof"])
        with open(os.path.join(args.email_dir, "subject.txt"), "w", encoding="utf-8") as f:
            f.write(subject)
        with open(os.path.join(args.email_dir, "body.html"), "w", encoding="utf-8") as f:
            f.write(body)


if __name__ == "__main__":
    main()

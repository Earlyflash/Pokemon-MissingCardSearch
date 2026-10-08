"""
ordered.txt: cards bought but not yet in the RareCandy collection
-----------------------------------------------------------------
One card per line. The first field is the card, either a TCGdex card id
("me01-161", "M2a-003"), followed by "energy" or "ball" for one of a
card's reverse holos where missing_cards.py lists them ("M2a-032 energy"), or a line copied from an earlier Cardmarket wants
list ("1 Mega Absol ex Terminal Period Claw of Darkness (V.2) (Mega
Evolution)"; the amount says how many are on order, 1 if left off). Anything
after a " | " is notes for people and for whatever adds the lines (an email
task, say), e.g.

    me01-161 | 2026-09-28 | Cardmarket | 1234567890 | seller KardKafe | Mega Absol ex 161/132

A "seller <name>" field names the seller of a marketplace order, and
sort_file sorts by it after the date and shop.

Blank lines and lines starting with # are skipped. price_search.py and
wants_list.py read it so ordered cards aren't bought twice, and
missing_cards.py moves a card's line out to ordered_arrived.txt once the card
turns up in the collection (see prune_arrived).
"""
import datetime
import os
import re
from collections import Counter

from marketplaces.base import FINISH_NORMAL, FINISHES

FIELD_SEP = " | "
ARRIVED_NAME = "ordered_arrived.txt"
_CARD_ID = re.compile(r"([^\s()]+-[^\s()]+)(?:\s+(" + "|".join(FINISHES) + r"))?", re.IGNORECASE)


def card_key(card_id, finish=None):
    """How ordered.txt names one print of a card, in lower case: the card id,
    plus the finish for a reverse holo ("m2a-032 energy"). A plain card id is
    the normal print."""
    card_id = (card_id or "").lower()
    return card_id if finish in (None, FINISH_NORMAL) else f"{card_id} {finish}"


def parse_line(line):
    """(card key (see card_key) or None, amount, wants list name in casefold
    or None) for one line, or None for a blank or comment line. A card id line
    is always one card; to order two, list it twice."""
    card = line.split(FIELD_SEP, 1)[0].strip()
    if not card or card.startswith("#"):  # whole-line only: "Blaine's Quiz #1" is a card
        return None
    m = _CARD_ID.fullmatch(card)
    if m:
        return card_key(m.group(1), (m.group(2) or "").lower() or None), 1, None
    m = re.fullmatch(r"(\d+)\s*[xX]?\s+(.+)", card)
    amount, text = (int(m.group(1)), m.group(2).strip()) if m else (1, card)
    return None, amount, text.casefold()


def _read_lines(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def read_ordered(path):
    """Returns (set of card keys, Counter of wants list names). A missing file
    means nothing is on order."""
    ids, names = set(), Counter()
    for line in _read_lines(path):
        parsed = parse_line(line)
        if not parsed:
            continue
        card_id, amount, name = parsed
        if card_id:
            ids.add(card_id)
        else:
            names[name] += amount
    return ids, names


def read_ordered_shops(path):
    """{card key: [shop, ...]} for every card id line, the shop being the
    third field ("me01-161 | 2026-09-28 | Cardmarket | ..."), one per line
    that names one. A card with no shop noted maps to an empty list."""
    shops = {}
    for line in _read_lines(path):
        parsed = parse_line(line)
        if not parsed or not parsed[0]:
            continue
        fields = [f.strip() for f in line.split(FIELD_SEP.strip())]
        mine = shops.setdefault(parsed[0], [])
        if len(fields) > 2 and fields[2] and fields[2] not in mine:
            mine.append(fields[2])
    return shops


def default_arrived_path(ordered_path):
    return os.path.join(os.path.dirname(os.path.abspath(ordered_path)), ARRIVED_NAME)


def prune_arrived(path, checked_ids, missing_ids, arrived_path=None, today=None):
    """Move every card id line whose card is now in the collection out of
    `path` and onto the end of `arrived_path` (default ordered_arrived.txt
    beside it), tagged "| arrived <date>". A card counts as arrived when its
    key (see card_key) is in `checked_ids` (cards of every set just checked
    against the collection) but not in `missing_ids` (lower case, both). Cards from sets
    that weren't checked stay put, as do wants list name lines, which can't
    be tied to a card.

    The arrived file keeps each line's notes, so whatever adds lines can tell
    an order it has already added from a new one.

    Returns (arrived lines, number of wants list name lines left in place)."""
    lines = _read_lines(path)
    keep, arrived, names = [], [], 0
    for line in lines:
        parsed = parse_line(line)
        card_id = parsed and parsed[0]
        if card_id and card_id in checked_ids and card_id not in missing_ids:
            arrived.append(line.strip())
            continue
        if parsed and not card_id:
            names += 1
        keep.append(line)
    if not arrived:
        return [], names
    arrived_path = arrived_path or default_arrived_path(path)
    date = (today or datetime.date.today()).isoformat()
    with open(arrived_path, "a", encoding="utf-8") as f:
        f.writelines(f"{line}{FIELD_SEP}arrived {date}\n" for line in arrived)
    _write_lines(path, keep)
    return arrived, names


def _write_lines(path, lines):
    # Written to a temp file and swapped in, so a reader (or an email task
    # appending at the same moment) never sees a half-written file.
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(f"{line}\n" for line in lines)
    os.replace(tmp, path)


def _order_key(line):
    """How sort_file orders a line: by order date, then shop, then seller
    (a "seller <name>" field), then order number. A cancelled order's line
    ("# cancelled me01-161 | ...") sorts as the order it was. None for
    blank lines and other comments."""
    body = re.sub(r"^#\s*cancelled\s+", "", line.strip(), flags=re.IGNORECASE)
    if not body or body.startswith("#"):
        return None
    fields = [f.strip() for f in body.split(FIELD_SEP.strip())] + ["", "", ""]
    seller = next((f[len("seller "):] for f in fields[3:] if f.lower().startswith("seller ")), "")
    return fields[1], fields[2].casefold(), seller.casefold(), fields[3]


def sort_file(path):
    """Sort ordered.txt by order date, shop, seller and order number, so one
    order's cards sit together. Comments other than cancelled orders stay at
    the top in their own order; lines of one order keep theirs."""
    lines = [line.rstrip("\n") for line in _read_lines(path)]
    header = [line for line in lines if line.strip() and _order_key(line) is None]
    orders = sorted((line for line in lines if _order_key(line) is not None), key=_order_key)
    if header + orders != lines:
        _write_lines(path, header + orders)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != "sort":
        sys.exit("Usage: python ordered.py sort ORDERED_FILE")
    sort_file(sys.argv[2])

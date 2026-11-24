#!/usr/bin/env python3
"""
Spares page (spares.html)
-------------------------
Every card you own more than once, from missing_cards.py's spares.csv, with
roughly what it's worth: PulseAPI's UK market price for each copy (from
price_search.py's spare_prices.csv) and that times the copies spare. It's
for trading: search by name (English or Japanese), number or set, pick a
set, sort by what it's worth, hide the bulk, and copy what's on screen as
plain text to paste into a message.

"Bulk" is a card in a set's base (numbered within its printed count, so not
a secret rare) worth `min_price` or less each, or with no price: the cards
the overview's Spares section leaves out. Here they're listed but can be
hidden.

missing_cards.py writes the page next to spares.csv, and price_search.py
rewrites it once it has priced the spares, so the overnight run keeps it up
to date. To rebuild it by hand:

  python spares_page.py
  python spares_page.py --spares spares.csv --prices spare_prices.csv --out spares.html
"""
import argparse
import csv
import datetime
import html
import os
from decimal import Decimal, InvalidOperation

from card_prices import SPARE_PRICES_NAME, read_spare_prices
from data_dir import data_path, ensure_parent
from marketplaces.base import FINISH_NORMAL, finish_key

SPARES_NAME = "spares.csv"
PAGE_NAME = "spares.html"
SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "JPY": "¥"}


def read_spares(path):
    """spares.csv's rows as dicts, or [] if missing_cards.py hasn't written one."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def _decimal(text):
    try:
        return Decimal(text)
    except (InvalidOperation, TypeError):
        return None


def _number_key(number):
    """Card numbers in set order: 001 < 002 < 010 < 101 < TG01."""
    digits = "".join(ch for ch in number if ch.isdigit())
    return (not number.isdigit(), int(digits) if digits else 0, number)


def spare_rows(spares, prices, min_price=1):
    """One dict per spare card for the page: its spares.csv fields plus
    "count", "each" (Decimal or None), "url", "hover", "old" (PulseAPI sent
    no price this time, so it's the last one it did), "value" (each times
    count, or None), "base" and "bulk". Only plain prints are priced, as
    PulseAPI's price is the standard print's (see price_search.py)."""
    rows = []
    for r in spares:
        try:
            count = int(r.get("Spares") or 0)
        except ValueError:
            continue
        if count <= 0:
            continue
        plain = finish_key(r.get("Finish")) in (None, FINISH_NORMAL)
        price = prices.get(r["TCGdex Card ID"]) if plain else None
        each = _decimal(price[0]) if price else None
        base = (r.get("Base Set") or "").strip().lower() == "yes"
        rows.append({
            **r, "count": count, "each": each,
            "url": price[1] if price else "", "hover": price[2] if price else "",
            "old": bool(price and "last seen" in price[2]),
            "value": each * count if each is not None else None,
            "base": base, "bulk": base and (each is None or each <= Decimal(str(min_price))),
        })
    return rows


STYLE = """
:root {
  --bg: #f4f3ef; --panel: #ffffff; --text: #1c1c1e; --muted: #6e6e73; --line: #e4e2dc;
  --track: #ebe9e3; --accent: #1a7fd4; --good: #1f9d63;
  --shadow: 0 1px 2px rgba(20, 20, 30, .06), 0 4px 14px rgba(20, 20, 30, .06);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111113; --panel: #1b1b1e; --text: #f2f2f4; --muted: #9d9da6; --line: #2c2c31;
    --track: #2a2a2f; --accent: #5cb3ff; --good: #3fcf8e;
    --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 4px 14px rgba(0, 0, 0, .25);
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); -webkit-font-smoothing: antialiased;
       font: 15px/1.45 system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Yu Gothic", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 36px 16px 64px; }
header.top { display: flex; flex-wrap: wrap; gap: 24px; align-items: center; margin-bottom: 24px; }
header.top .intro { flex: 1 1 300px; }
h1 { font-size: 32px; letter-spacing: -0.02em; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0; }
.sub a { color: var(--accent); }
.stats { display: flex; flex-wrap: wrap; gap: 10px; }
.stat { background: var(--panel); border-radius: 14px; padding: 12px 16px; box-shadow: var(--shadow);
        min-width: 120px; }
.stat b { display: block; font-size: 24px; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.stat span { color: var(--muted); font-size: 13px; }
.controls { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: 10px 18px;
            align-items: center; padding: 10px 14px; background: var(--panel); border-radius: 14px;
            box-shadow: var(--shadow); }
.controls input[type=search], .controls select {
  font: inherit; padding: 6px 10px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--bg); color: var(--text); }
.controls input[type=search] { flex: 1 1 220px; min-width: 0; }
.controls select { max-width: 100%; }
#set { max-width: 260px; }
.switch { display: inline-flex; gap: 10px; align-items: center; cursor: pointer; user-select: none; }
.switch input { position: absolute; opacity: 0; pointer-events: none; }
.switch i { width: 38px; height: 22px; border-radius: 11px; background: var(--track); position: relative;
            transition: background .2s; flex: none; }
.switch i::after { content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px;
                   border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0, 0, 0, .3);
                   transition: transform .2s; }
.switch input:checked + i { background: var(--good); }
.switch input:checked + i::after { transform: translateX(16px); }
.switch input:focus-visible + i { outline: 2px solid var(--good); outline-offset: 2px; }
button.copy { font: inherit; font-weight: 600; padding: 6px 14px; border-radius: 999px; border: 0;
              background: var(--accent); color: #fff; cursor: pointer; }
button.copy:hover { filter: brightness(1.08); }
.showing { color: var(--muted); margin: 14px 4px 8px; font-variant-numeric: tabular-nums; }
.showing b { color: var(--text); }
.wrap { background: var(--panel); border-radius: 16px; box-shadow: var(--shadow); overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 8px 12px; text-align: left; vertical-align: middle; border-bottom: 1px solid var(--line); }
thead th { font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted);
           font-weight: 650; white-space: nowrap; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover td { background: color-mix(in srgb, var(--accent) 5%, transparent); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
td.pic { width: 52px; padding-right: 0; }
td.pic a { display: block; width: 40px; aspect-ratio: 63 / 88; border-radius: 4px; overflow: hidden;
           background: var(--track); }
td.pic img { width: 100%; height: 100%; object-fit: cover; display: block; }
td.pic img.broken { display: none; }
body:not(.pics) .pic { display: none; }
.cname { font-weight: 600; }
.alt, .set small, .where { display: block; color: var(--muted); font-size: 13px; }
.where { display: none; }
.tag { display: inline-block; margin-left: 6px; padding: 0 7px; border-radius: 999px; font-size: 11px;
       font-weight: 700; vertical-align: 1px; white-space: nowrap; }
.tag.secret { color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent); }
.tag.bulk { color: var(--muted); background: var(--track); }
.qty { font-weight: 700; }
td.each a { color: var(--text); text-decoration: none; }
td.each a:hover { text-decoration: underline; }
td.each a.old { color: var(--muted); }
td.each a.old::after { content: "*"; }
td.value { font-weight: 700; }
.none, td.paid { color: var(--muted); }
tr.bulk td { opacity: .7; }
body.hide-bulk tr.bulk { display: none; }
.empty { padding: 28px; text-align: center; color: var(--muted); }
.note { color: var(--muted); font-size: 13px; margin: 14px 4px 0; }
@media (max-width: 700px) {
  h1 { font-size: 26px; }
  #set { max-width: none; flex: 1 1 100%; width: 100%; min-width: 0; }
  .paid, .set { display: none; }
  .where { display: block; }
  th, td { padding: 8px 6px; }
  td.pic a { width: 34px; }
}
"""

SCRIPT = """
const rows = [...document.querySelectorAll('tbody tr')];
const body = document.querySelector('tbody');
const q = document.getElementById('q'), set = document.getElementById('set');
const sort = document.getElementById('sort');
const bulk = document.getElementById('bulk'), pics = document.getElementById('pics');
const cur = document.body.dataset.cur;
const money = v => cur + v.toLocaleString('en-GB', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const num = (tr, key) => tr.dataset[key] === '' ? -1 : parseFloat(tr.dataset[key]);
const visible = () => rows.filter(tr => !tr.hidden && !(bulk.checked && tr.classList.contains('bulk')));
function update() {
  const words = q.value.toLowerCase().split(/\\s+/).filter(Boolean);
  rows.forEach(tr => {
    tr.hidden = (set.value && tr.dataset.set !== set.value)
      || !words.every(w => tr.dataset.search.includes(w));
  });
  const shown = visible();
  let copies = 0, worth = 0;
  shown.forEach(tr => { copies += +tr.dataset.count; worth += Math.max(num(tr, 'value'), 0); });
  document.getElementById('showing').innerHTML = shown.length
    ? `Showing <b>${shown.length}</b> card(s), <b>${copies}</b> cop${copies === 1 ? 'y' : 'ies'}, worth <b>${money(worth)}</b>`
    : 'No spares match.';
}
function order() {
  const by = sort.value;
  const natural = (a, b) => a.localeCompare(b, undefined, {numeric: true, sensitivity: 'base'});
  rows.sort((a, b) => by === 'each' ? num(b, 'each') - num(a, 'each')
      : by === 'set' ? natural(a.dataset.set, b.dataset.set) || natural(a.dataset.no, b.dataset.no)
      : by === 'name' ? natural(a.dataset.name, b.dataset.name)
      : by === 'count' ? b.dataset.count - a.dataset.count || num(b, 'value') - num(a, 'value')
      : num(b, 'value') - num(a, 'value'))
    .forEach(tr => body.append(tr));
}
function remember(key, box) {
  try {
    const saved = localStorage.getItem('spares-' + key);
    if (saved !== null) box.checked = saved === '1';
  } catch (e) {}
  const apply = () => {
    document.body.classList.toggle(key === 'bulk' ? 'hide-bulk' : 'pics', box.checked);
    if (key === 'pics' && box.checked)
      document.querySelectorAll('img[data-src]').forEach(img => { img.src = img.dataset.src; img.removeAttribute('data-src'); });
    try { localStorage.setItem('spares-' + key, box.checked ? '1' : '0'); } catch (e) {}
    update();
  };
  box.addEventListener('change', apply);
  apply();
}
q.addEventListener('input', update);
set.addEventListener('change', update);
sort.addEventListener('change', order);
remember('bulk', bulk);
remember('pics', pics);
document.getElementById('copy').addEventListener('click', async e => {
  const text = visible().map(tr => tr.dataset.line).join('\\n');
  const btn = e.currentTarget, label = btn.textContent;
  try { await navigator.clipboard.writeText(text); btn.textContent = 'Copied'; }
  catch (err) { btn.textContent = 'Couldn\\'t copy'; }
  setTimeout(() => { btn.textContent = label; }, 1500);
});
"""


def write_spares_html(spares, prices, path, min_price=1, overview=None):
    """Write the spares page. `spares` is spares.csv's rows (read_spares),
    `prices` read_spare_prices' result, `overview` the set overview's file
    name to link back to (None for no link). Returns the cards listed."""
    e = html.escape
    sym = SYMBOLS.get(prices.get("currency") or "GBP", (prices.get("currency") or "") + " ")
    money = lambda v: f"{sym}{v:,.2f}"  # noqa: E731
    rows = spare_rows(spares, prices.get("cards", {}), min_price)
    rows.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0)))
    sets = sorted({(r["Set Name"], r["Language"]) for r in rows},
                  key=lambda s: (s[0].lower(), s[1]))

    body = []
    for r in rows:
        name, en = r.get("Card Name") or "", r.get("English Name") or ""
        number = r.get("Card Number") or "?"
        set_key = f'{r["Set Name"]} ({r["Language"]})'
        small, large = r.get("Picture") or "", r.get("Large Picture") or ""
        pic = (f'<a href="{e(large or small)}" target="_blank" rel="noopener">'
               f'<img data-src="{e(small)}" alt="" loading="lazy" '
               f'onerror="this.className=\'broken\'"></a>' if small else "<a></a>")
        tags = ('<span class="tag secret">Secret rare</span>' if not r["base"]
                else '<span class="tag bulk">Bulk</span>' if r["bulk"] else "")
        alt = " · ".join(x for x in (en if en and en != name else "", r.get("Finish") or "") if x)
        if r["each"] is not None:
            title = f'PulseAPI UK market price · {r["hover"]}' if r["hover"] else "PulseAPI UK market price"
            each = (f'<a href="{e(r["url"])}" target="_blank" rel="noopener" title="{e(title)}"'
                    f'{" class=old" if r["old"] else ""}>{money(r["each"])}</a>')
            value = money(r["value"])
        else:
            why = ("Not priced: PulseAPI's price is the standard print's"
                   if finish_key(r.get("Finish")) not in (None, FINISH_NORMAL)
                   else "No PulseAPI price")
            each, value = f'<span class="none" title="{e(why)}">–</span>', '<span class="none">–</span>'
        paid = e(r.get("Purchase Price") or "–")
        label, finish = en or name, r.get("Finish") or ""
        line = (f'{label}{f" ({name})" if en and en != name else ""}'
                f'{f" [{finish}]" if finish else ""} · '
                f'{r["Set Name"]} {r["TCGdex Set"]} #{number} · {r["Language"]} · ×{r["count"]}'
                + (f' · ~{money(r["each"])} each' if r["each"] is not None else ""))
        search = " ".join([name, en, number, number.lstrip("0"), r["Set Name"], r["TCGdex Set"],
                           r["Language"], r.get("Finish") or ""]).lower()
        body.append(
            f'<tr class="{"bulk" if r["bulk"] else ""}" data-set="{e(set_key)}" '
            f'data-search="{e(search)}" data-name="{e(label.lower())}" data-no="{e(number)}" '
            f'data-count="{r["count"]}" data-each="{"" if r["each"] is None else r["each"]}" '
            f'data-value="{"" if r["value"] is None else r["value"]}" data-line="{e(line)}">'
            f'<td class="pic">{pic}</td>'
            f'<td><span class="cname">{e(name)}</span>{tags}'
            f'{f"<span class=alt>{e(alt)}</span>" if alt else ""}'
            f'<span class="where">{e(r["Set Name"])} #{e(number)}</span></td>'
            f'<td class="set">{e(r["Set Name"])}<small>{e(r["TCGdex Set"])} #{e(number)} · '
            f'{e(r["Language"])}</small></td>'
            f'<td class="num qty">×{r["count"]}</td><td class="num each">{each}</td>'
            f'<td class="num value">{value}</td><td class="num paid">{paid}</td></tr>')

    copies = sum(r["count"] for r in rows)
    worth = sum(r["value"] or 0 for r in rows)
    kept = sum(r["value"] or 0 for r in rows if not r["bulk"])
    priced = sum(1 for r in rows if r["each"] is not None)
    stats = "".join(f'<div class="stat"><b>{v}</b><span>{label}</span></div>' for v, label in (
        (copies, "spare copies"), (len(rows), "cards"), (money(worth), "worth in all"),
        (money(kept), "without bulk")))
    options = "".join(f'<option value="{e(f"{n} ({lang})")}">{e(n)} ({e(lang)})</option>'
                      for n, lang in sets)
    asof = f" from {prices['asof']}" if prices.get("asof") else ""
    back = f' · <a href="{e(overview)}">Missing cards</a>' if overview else ""
    unpriced = len(rows) - priced
    note = (f"{unpriced} card(s) have no price: PulseAPI sends none for some commons, "
            "has no promos from some sets, and reverse holos aren't looked up. "
            if unpriced else "")
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>Spares</title><style>{STYLE}</style></head>'
        f'<body class="pics" data-cur="{e(sym)}"><main>'
        '<header class="top"><div class="intro"><h1>Spares</h1>'
        f'<p class="sub">Cards owned more than once, priced at PulseAPI\'s UK market price for a '
        f'near-mint copy{e(asof)} · updated {datetime.datetime.now():%d %b %Y, %H:%M}{back}</p></div>'
        f'<div class="stats">{stats}</div></header>'
        '<div class="controls">'
        '<input type="search" id="q" placeholder="Search name, number or set" aria-label="Search">'
        f'<select id="set" aria-label="Set"><option value="">All sets</option>{options}</select>'
        '<select id="sort" aria-label="Sort"><option value="value">Most valuable</option>'
        '<option value="each">Highest price each</option><option value="count">Most copies</option>'
        '<option value="set">Set and number</option><option value="name">Name</option></select>'
        '<label class="switch"><input type="checkbox" id="bulk"><i></i>Hide bulk</label>'
        '<label class="switch"><input type="checkbox" id="pics" checked><i></i>Pictures</label>'
        '<button type="button" class="copy" id="copy">Copy list</button></div>'
        '<p class="showing" id="showing"></p>'
        '<div class="wrap"><table><thead><tr><th class="pic"></th><th>Card</th><th class="set">Set</th>'
        '<th class="num">Spare</th><th class="num">Each</th><th class="num">Total</th>'
        '<th class="num paid">Paid</th></tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>'
        f'{"" if rows else "<p class=empty>No spares yet.</p>"}</div>'
        f'<p class="note">Bulk is a card in a set\'s base (not a secret rare) worth '
        f'{money(Decimal(str(min_price)))} or less, or with no price. {note}'
        'Prices marked * are the last PulseAPI sent, as it sent none this time. Paid is '
        'RareCandy\'s purchase price, in whatever currency it was entered in. '
        '<b>Copy list</b> copies the cards shown, one per line.</p>'
        f'</main><script>{SCRIPT}</script></body></html>')
    ensure_parent(path)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, path)
    return len(rows)


def build(spares_path, prices_path, out, min_price=1, overview="missing_cards.html"):
    """Read the CSVs and write the page; returns the cards listed, or None
    when there's no spares.csv to build from."""
    if not os.path.isfile(spares_path):
        return None
    return write_spares_html(read_spares(spares_path), read_spare_prices(prices_path), out,
                             min_price, overview)


def main(argv=None):
    p = argparse.ArgumentParser(description="Write the spares page from missing_cards.py's "
                                            "spares.csv and price_search.py's spare_prices.csv.")
    p.add_argument("--spares", default=data_path(SPARES_NAME),
                   help="Spares listed by missing_cards.py (default: %(default)s).")
    p.add_argument("--prices", default=data_path(SPARE_PRICES_NAME),
                   help="Their PulseAPI prices from price_search.py (default: %(default)s).")
    p.add_argument("--out", default=data_path(PAGE_NAME),
                   help="Page to write (default: %(default)s).")
    p.add_argument("--min-price", type=float, default=1, metavar="AMOUNT",
                   help="Base cards worth this or less each count as bulk (default: %(default)g).")
    args = p.parse_args(argv)
    n = build(args.spares, args.prices, args.out, args.min_price)
    if n is None:
        raise SystemExit(f"No {args.spares}: run missing_cards.py first.")
    print(f"Wrote {n} spare card(s) to {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()

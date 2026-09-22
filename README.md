# Pokemon-MissingCardSearch

Lists the Pokémon TCG cards you're **missing from each set you collect**, by
comparing your [RareCandy](https://rarecandy.com) portfolio against the full
card list for each set on [TCGdex](https://tcgdex.dev).

It builds on two existing tools rather than re-implementing them, both
vendored as git submodules under `vendor/`:

- [RareCandyExporter](https://github.com/Earlyflash/RareCandyExporter) does
  the RareCandy export, run unchanged.
- [pokemon-binder-cover-tool](https://github.com/Earlyflash/pokemon-binder-cover-tool)'s
  `binder_cover.py` provides the TCGdex set search, fetch helper and dataset
  list. It's imported, not copied.

## Setup

```bash
git clone --recurse-submodules https://github.com/Earlyflash/Pokemon-MissingCardSearch
cd Pokemon-MissingCardSearch

# Only needed for --profile (running the export for you):
cd vendor/RareCandyExporter && npm install && cd ../..
```

Already cloned without `--recurse-submodules`? Run `git submodule update --init`.

The Python side needs only the standard library (Python 3.9+). Pillow isn't
needed, even though the binder cover tool uses it for drawing.

## Usage

Export and compare in one go:

```bash
python missing_cards.py --profile Earlyflash
```

Or use a CSV you already exported with RareCandyExporter:

```bash
python missing_cards.py --csv earlyflash.csv
python missing_cards.py --csv earlyflash.csv --set "MEGA Dream ex" --set "Abyss Eye"
```

Only sets you already own at least one card from are checked, and by default
only sets you're **at least 75% of the way through** are listed. The rest are
named in a one-line summary; change the cut-off with `--min-complete`.

It prints a per-set report and writes every missing card to
`missing_cards.csv`:

```
MEGA Dream ex [M2a, Japanese]: own <owned>/<total> (<pct>%), missing <n>
  #002      フシギソウ
  ...
```

| Column | Meaning |
|---|---|
| Set Name | The set's name as RareCandy shows it |
| TCGdex Set | The TCGdex set id it was matched to |
| Language | Print language (from the RareCandy export) |
| Card Number | TCGdex's card number within the set |
| Card Name | TCGdex's card name (in that language's dataset) |
| TCGdex Card ID | Stable id, handy for the next step (marketplace search) |

`--json FILE` writes the same data grouped by set, plus any sets it couldn't
match, for other tools to consume:

```json
{
  "min_complete": 75,
  "sets": [{
    "set_id": "M2a", "set_name": "MEGA Dream ex", "language": "Japanese",
    "tcgdex_lang": "ja", "total": 250, "owned": 200, "percent_complete": 80.0,
    "missing": [{
      "card_id": "M2a-002", "set_id": "M2a", "set_name": "MEGA Dream ex",
      "local_id": "002", "name": "フシギソウ", "language": "Japanese",
      "tcgdex_lang": "ja", "rarity": null, "finish": null
    }]
  }],
  "below_threshold": [{"set_id": "M5", "set_name": "Abyss Eye", "language": "Japanese",
                       "total": 118, "owned": 40, "percent_complete": 33.9}],
  "unmatched": [{"set_name": "...", "language": "English", "reason": "no TCGdex set found"}]
}
```

`rarity` and `finish` are always `null` for now: TCGdex's set listing doesn't
include them, and fetching rarity costs one request per card.

### Flags

| Flag | Meaning |
|---|---|
| `--csv FILE` | A CSV exported with RareCandyExporter. |
| `--profile NAME` | RareCandy profile name or URL; runs RareCandyExporter first. |
| `--export-csv FILE` | Where `--profile` saves the export (default `rarecandy_export.csv`). |
| `--set NAME` | Only check this RareCandy set (repeatable). Default: every set in the collection. |
| `--map "NAME=CODE"` | Match a RareCandy set name to a TCGdex set code for this run (repeatable). |
| `--set-map FILE` | Set name overrides file (default `set_map.json`). |
| `--min-complete PERCENT` | Only list sets you already own at least this much of (default `75`). `0` lists every set you own a card from. |
| `--out FILE` | Where to write the missing cards (default `missing_cards.csv`). |
| `--json FILE` | Also write the missing cards as JSON, grouped by set (see below). |

## Pricing the missing cards

`price_search.py` takes `missing_cards.csv` (or the `--json` file above) and
asks each marketplace plugin which of those cards are for sale, then lists
every offer found, cheapest first per card, in GBP:

```bash
python missing_cards.py --csv earlyflash.csv
python price_search.py missing_cards.csv
python price_search.py missing.json            # the --json file works too
python price_search.py missing.json --marketplace deckdhq --cheapest-only
python price_search.py --list-marketplaces
```

It prints a per-set summary (how many missing cards are for sale, and what
buying the cheapest of each would cost) and writes the offers to
`offers.csv`. Prices are the listed item price converted to one currency;
**shipping isn't counted**. Graded slabs are included and compete on price
like any other copy; they're labelled with their grading company and grade
(e.g. `PSA 9`) in the `Grade` column and in the printed summary.

Some marketplaces only publish a price guide (one "from" price per card, the
cheapest copy in any language or condition) rather than listings. Those
prices are printed in their own section after the offers, are never ranked
against real listings or counted in the totals, and go to their own CSV,
`price_guide.csv` (same columns as `offers.csv`).

| Flag | Meaning |
|---|---|
| `--marketplace ID` | Only search this marketplace (repeatable). Default: every marketplace whose settings are present. |
| `--list-marketplaces` | List the installed marketplace plugins and whether they're ready. |
| `--set NAME` | Only search this set, by name or TCGdex set id (repeatable). |
| `--currency CODE` | Currency to compare in (default `GBP`), converted with the same free rate service RareCandyExporter uses. |
| `--out FILE` | Where to write the offers (default `offers.csv`). |
| `--guide-out FILE` | Where to write price-guide prices, e.g. Cardmarket's (default `price_guide.csv`). |
| `--cheapest-only` | Write only the cheapest offer per card. |
| `--include-uncertain` | Also count offers a marketplace isn't sure are the right print. |
| `--no-cache` / `--cache-dir DIR` | Marketplace responses are cached for 6 hours in `.price_cache/`. |
| `-v` | Print every request made. |

### Marketplaces

| ID | Marketplace | How it matches |
|---|---|---|
| `deckdhq` | [DeckdHQ](https://www.deckdhq.com), UK, GBP | Reads every active Pokémon listing from the site's public API once per run (about 11 requests). Listings with a set name match on set, card number and language (`exact`). eBay imports have no set name, so they match on the set name appearing in the title plus the number (`likely`), as do listings with no language. Promo listings match across DeckdHQ's various promo set names only when the number carries the card's set prefix (`SWSH277`, `SVP 176`) or the set name names the same promo series (e.g. "Scarlet & Violet Black Star Promos" for SVP); numbers like `063/SV-P` are Japanese promos and never match English promo sets, and Celebrations Classic Collection cards match on name because sellers use the original print numbers. A set code in the set name (`s12a VSTAR Universe`) outweighs a contradicting language tag. Prices include DeckdHQ's buyer fee. |
| `cardmarket` | [Cardmarket](https://www.cardmarket.com), EU, EUR. **Price guide, not listings.** | Cardmarket's site blocks automated reads and its API takes no new users, so this reads the price guide Cardmarket publishes as a free daily download (one ~15 MB file per run). The price is its `low`: the cheapest copy currently listed, in any language or condition and from any seller country, so an English near-mint copy shipped to the UK may cost more. Cards are tied to Cardmarket products through TCGdex, whose card records carry the Cardmarket product id (one TCGdex request per missing card), and link to the card's Cardmarket page. |

### Adding a marketplace

Each marketplace is one file in `marketplaces/`. Subclass `Marketplace` from
`marketplaces/base.py`, implement `search()` (one card) or `search_set()`
(a whole set at once, for sites that are cheaper to read that way), and end
the module with `PLUGIN = YourMarketplace()`. It's picked up automatically.
Set `price_guide = True` on a site that only publishes a price per card
rather than listings, and its prices are reported separately.

```python
from decimal import Decimal
from marketplaces.base import Marketplace, Offer, MATCH_EXACT

class ExampleShop(Marketplace):
    id = "exampleshop"             # used with --marketplace
    name = "Example Shop"
    languages = {"en", "ja"}       # TCGdex language codes it sells; None = all
    needs = ("EXAMPLE_API_KEY",)   # environment variables it requires
    min_interval = 1.0             # seconds between its requests

    def search(self, card, ctx):
        data = ctx.fetch(f"https://api.example/search?set={card.set_id}&no={card.local_id}",
                         headers={"Authorization": ctx.config["EXAMPLE_API_KEY"]}, as_json=True)
        return [Offer(marketplace=self.id, card_id=card.card_id, url=hit["url"],
                      price=Decimal(hit["price"]), currency=hit["currency"],
                      title=hit["title"], condition="NM", match=MATCH_EXACT)
                for hit in data["results"]]

PLUGIN = ExampleShop()
```

A plugin only has to know its own site. The core handles the rest: it sends
each plugin only the cards in languages it sells, runs marketplaces in
parallel while spacing each one's own requests, caches responses, converts
currencies, and carries on if one marketplace fails. How a plugin reads its
site is up to it (an API, web pages, a browser, or a file exported by hand).
Each offer says how sure the match is: `exact` (matched on set and card
number), `likely`, or `uncertain` (might be a different print; left out of
the cheapest unless `--include-uncertain`).

## How it works

1. Every card in the export is grouped by **set name + print language**, so
   an English and a Japanese collection of the same set are checked
   separately against the right card list.
2. Each set name is matched to a TCGdex set id: first via `set_map.json`,
   then an exact name match across TCGdex's Japanese, English, Chinese and
   Korean datasets, then the binder cover tool's code/name search.
3. The set's card list is fetched from the TCGdex dataset for that print
   language only (English for languages TCGdex's datasets here don't cover,
   such as German or French). Japanese, Chinese and Korean sets are numbered
   differently, so if TCGdex has no list in the right language the set is
   reported as unmatched rather than checked against the wrong list.
4. Card numbers are compared with leading zeros and `/total` suffixes
   ignored, so RareCandy's `76` matches TCGdex's `076`.

## When a set isn't found

RareCandy names Japan-exclusive sets in English (e.g. "MEGA Dream ex"), but
TCGdex only stores their Japanese name until they get an English release.
Those sets need an entry in `set_map.json`, keyed by the language RareCandy
reports:

```json
{
  "Japanese": { "MEGA Dream ex": "M2a" },
  "Any": { "Some Set": "sv01" }
}
```

The report lists any set it couldn't match. Find the right code with the
binder cover tool's `python vendor/pokemon-binder-cover-tool/binder_cover.py --list-sets`.

The report also flags cards you own whose number isn't in TCGdex's list,
which usually means the set was matched to the wrong TCGdex set.

## Limitations

- A card counts as owned if you own any copy of it. Print finishes (e.g.
  MEGA Dream ex's reverse holo variants) aren't checked separately.
- TCGdex can lag behind brand-new releases; a set it hasn't indexed yet
  will show as unmatched or incomplete.
- Completion counts distinct card numbers owned against TCGdex's total for
  the set, secret rares included.

## Tests

```bash
python -m unittest discover -s tests -v
```

TCGdex calls are mocked, so the suite runs offline.

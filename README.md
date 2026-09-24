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
  #002      フシギソウ (Ivysaur)
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
| English Name | The card's English name, also for Japanese/Chinese/Korean cards (blank if unknown) |

TCGdex only has Japanese names for Japanese cards, so English names come
from Cardmarket, which lists every card in English: each card's Cardmarket
product id comes from TCGdex, and its name from Cardmarket's free daily
product list (about 14 MB, downloaded once per run), plus one TCGdex request
per missing card. Cards TCGdex hasn't linked to a Cardmarket product yet,
usually from very new sets, are left blank. `--no-english-names` skips all of
this.

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
      "local_id": "002", "name": "フシギソウ", "name_en": "Ivysaur", "language": "Japanese",
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
| `--no-english-names` | Don't look up English names for non-English cards. |

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

While it runs, each marketplace reports its progress on stderr: when it
starts, how many pages it has fetched every few seconds, each set as it's
searched, and when it's done (with how many marketplaces are still going).
The slowest shops read their whole catalogue, e.g. Japan2UK's ~88 pages take
about a minute and a half, and later runs within 6 hours use the cache.

When it's finished it prints a per-set summary (how many missing cards are for sale, and what
buying the cheapest of each would cost) and writes the offers to
`offers.csv`. Prices are the listed item price converted to one currency;
**shipping isn't counted**. Graded slabs are included and compete on price
like any other copy; they're labelled with their grading company and grade
(e.g. `PSA 9`) in the `Grade` column and in the printed summary.

Some marketplaces only publish a price guide (one price per card, such as
Cardmarket's "from" price, the cheapest copy in any language or condition,
or PulseAPI's market price) rather than listings. Those
prices are printed in their own section after the offers, are never ranked
against real listings or counted in the totals, and go to their own CSV,
`price_guide.csv` (same columns as `offers.csv`).

It also writes `price_table.html` (next to `offers.csv`), the easiest way to
read the results: open it in a browser for one row per missing card, grouped
by set, and one column per marketplace. Cards are named in English where
missing_cards.py found an English name, with the printed name underneath, and
the marketplace names stay at the top of the screen as you scroll. Each cell is that marketplace's
cheapest copy (with its condition or grade, and how many more copies it has),
and the price links straight to the listing. The cheapest listing for each
card is highlighted. Price-guide marketplaces get their own columns and are
never highlighted: Cardmarket's as "from" prices, PulseAPI's as "market
price". When PulseAPI has a market price for a card, any listing priced above
it gets a red "▲N%" next to its price (how far over), and any priced below
it a green "▼N%" (how far under); hover it for the amounts. The cell itself
isn't recoloured, so the cheapest listing's highlight still shows. Cells are
kept compact: under the price is only the grade, condition and "+N" other
copies (hover for the full wording), and column headers wrap. PulseAPI's
market price is the first column after the card name, in bold, followed by
Cardmarket's, then the shops; the card number, name and market price stay in
view when you scroll sideways. The last row totals each
marketplace: the sum of its cheapest copy of every card it has, and how many
cards that covers. A checkbox hides the cards
nobody has for sale.

| Flag | Meaning |
|---|---|
| `--marketplace ID` | Only search this marketplace (repeatable). Default: every marketplace whose settings are present. |
| `--list-marketplaces` | List the installed marketplace plugins and whether they're ready. |
| `--set NAME` | Only search this set, by name or TCGdex set id (repeatable). |
| `--currency CODE` | Currency to compare in (default `GBP`), converted with the same free rate service RareCandyExporter uses. |
| `--out FILE` | Where to write the offers (default `offers.csv`). |
| `--guide-out FILE` | Where to write price-guide prices, e.g. Cardmarket's (default `price_guide.csv`). |
| `--html-out FILE` | Where to write the HTML price table (default `price_table.html` next to `--out`). |
| `--cheapest-only` | Write only the cheapest offer per card. |
| `--include-uncertain` | Also count offers a marketplace isn't sure are the right print. |
| `--no-cache` / `--cache-dir DIR` | Marketplace responses are cached for 6 hours in `.price_cache/`. |
| `--env-file FILE` | File of `KEY=value` settings such as `PULSEAPI_KEY` (default `.env` next to `price_search.py`; git-ignored). Variables already set in the environment win. |
| `-v` | Print every request made. |

### Marketplaces

| ID | Marketplace | How it matches |
|---|---|---|
| `deckdhq` | [DeckdHQ](https://www.deckdhq.com), UK, GBP | Reads every active Pokémon listing from the site's public API once per run (about 11 requests). Listings with a set name match on set, card number and language (`exact`). eBay imports have no set name, so they match on the set name appearing in the title plus the number (`likely`), as do listings with no language. Promo listings match across DeckdHQ's various promo set names only when the number carries the card's set prefix (`SWSH277`, `SVP 176`) or the set name names the same promo series (e.g. "Scarlet & Violet Black Star Promos" for SVP); numbers like `063/SV-P` are Japanese promos and never match English promo sets, and Celebrations Classic Collection cards match on name because sellers use the original print numbers. A set code in the set name (`s12a VSTAR Universe`) outweighs a contradicting language tag. Prices include DeckdHQ's buyer fee. |
| `cardcargo` | [CardCargo](https://cardcargo.com), UK, GBP. Japanese cards only. | Reads the shop's whole Japanese singles collection from its public Shopify product JSON once per run (two requests for its ~400 products). Titles all look like `(#173/165) Pikachu - Holo [SV2a: Pokemon Card 151 (JPN)]`, so a card matches (`exact`) on its number plus the set name, with or without the code prefix, or a code prefix equal to its TCGdex set id; promo numbers like `152/S-P` match on the promo code instead. Vintage listings numbered `NO. 008` carry a Pokédex number, not a card number, so they never match. Each copy in stock is its own offer with its own condition, linked straight to that copy. |
| `radams` | [Radam's Poké Stop](https://www.radamspokestop.co.uk), UK, GBP. English, Japanese, Korean and Chinese cards. | Reads the whole shop from its ordinary "shop all" pages once per run (about 10 requests for its ~2,000 products; the shop's robots.txt disallows Squarespace's JSON view, so the plugin doesn't use it). Each product carries language and set tags, so a card matches (`exact`) on language, the `#` number in the title, and its set: a set code in the title or set tag (`sv2a`, `cs4aC`) equal to its TCGdex set id, a promo code after the number (`001/SM-p`), or for English sets the set tag naming the set (`swsh-evolving-skies`). Titles are hand-written, so a listing whose URL gives a different number than its title, or an English listing whose title doesn't name the card, is only `uncertain`. Matched products in stock are opened one by one for their copies: each condition in stock is its own offer, with sale prices applied. Korean and most Chinese sets rarely match because TCGdex has few of their card lists. |
| `japan2uk` | [Japan2UK](https://www.japan2uk.com), UK, GBP. Japanese cards only. | Reads the shop's whole Japanese singles and Japanese graded cards collections from its public Shopify product JSON once per run (about 88 requests for ~21,500 products, most of them sold out, so a run spends about a minute and a half here). Titles end in the set code and number, like `Pokemon Jolteon Reverse Holo Pokemon 151 sv2a 135/165 Japanese Single Card`, so a card matches (`exact`) on its number plus a set code equal to its TCGdex set id; promo numbers like `237/SV-P` match on the promo code instead, and a few XY-era codes are mapped to TCGdex's (`xy11 Bb` is XY11a, `XY1` Collection X is XY1a). Graded copies are labelled with their grade (`PSA 10`); vintage graded listings with no set code never match. Every print of a number (normal, reverse holo, Master Ball) is offered for that card, with the print in the title. |
| `cardmarket` | [Cardmarket](https://www.cardmarket.com), EU, EUR. **Price guide, not listings.** | Cardmarket's site blocks automated reads and its API takes no new users, so this reads the price guide Cardmarket publishes as a free daily download (one ~15 MB file per run). The price is its `low`: the cheapest copy currently listed, in any language or condition and from any seller country, so an English near-mint copy shipped to the UK may cost more. Cards are tied to Cardmarket products through TCGdex, whose card records carry the Cardmarket product id (one TCGdex request per missing card), and link to the card's Cardmarket page. |
| `pulseapi` | [PulseAPI](https://pulseapi.dev) (the pricing API behind [PulseTCG](https://pulsetcg.io)), GBP. **Market price, not listings.** Needs an API key. | Create a key on the PulseAPI dashboard and put it in a `.env` file next to `price_search.py` (copy `.env.example` to `.env` and fill it in: `PULSEAPI_KEY=pk_live_...`), or set `PULSEAPI_KEY` as an environment variable; without it the plugin is skipped. `.env` is git-ignored, so the key never gets committed. The price is PulseAPI's UK market price for a near-mint, ungraded copy (graded and played copies are separate PulseAPI products and are left out), or its blended UK+US price when there's no UK one, and links to the card's PulseTCG page. PulseAPI has its own set codes, so each set is found by trying the TCGdex set id with PulseAPI's language suffix (`m2_jp` for Japanese Inferno X), the id itself and its pokemontcg.io spelling (`sv03.5` is `sv3pt5`), and if none is a set PulseAPI has in that language, by searching a few missing cards by name (English name first) and taking the set their numbers come from, as long as more than one card agrees or the set name is close; the whole set is then read and cards match on set and number. Which PulseAPI set each of your sets turned out to be (or that it has none) is remembered in `.price_cache/pulseapi/set_ids_v2.json`, so later runs go straight to reading the set, and anything fetched in the last 6 hours comes from the cache. A card with several finishes gets the standard print's price, or each finish's (`likely`) when there's no standard print. Requests aren't spaced out: when PulseAPI's per-minute limit is reached (20 a minute on the free tier) the plugin waits as long as PulseAPI asks and carries on, and a used-up daily or monthly quota stops it with a message. Sets are read 500 cards a request on a paid key and 100 on the free tier. PulseAPI's batch endpoint isn't used: it only takes PulseAPI's own card ids, 50 at a time, so reading whole sets needs fewer requests. Only English, Japanese and Chinese prints are looked up. |
| `thepokestore` | [The Poké Store](https://thepokestore.co.uk), UK, GBP. Japanese cards only. | Reads the shop's whole Japanese singles collection from its public Shopify product JSON once per run (about 10 requests for its ~2,400 products, mostly Scarlet & Violet and Mega Evolution sets), plus its collection list. Titles are just `001/062 Froslass ex` and the set is the product's tag ("Raging Surf"); the shop's per-set collections are titled with the set code ("Raging Surf (sv3a)"), so a card matches (`exact`) on its number plus the tag's code equal to its TCGdex set id, or the tag equal to its set name. Each print in stock (Non-Holo, Holo, Poké Ball Holo, ...) is its own offer, named in the offer's title. The shop doesn't give a condition. |
| `cosmiccollectables` | [Cosmic Collectables](https://cosmiccollectables.co.uk), UK, GBP. Japanese cards only. | Reads the shop's whole Japanese singles collection from its public Shopify product JSON once per run (3 requests for its ~720 products, mostly Sword & Shield and Sun & Moon sets; requests are 5 seconds apart because the site's Cloudflare front turns away quicker ones). Titles look like `SWORD AND SHIELD, Shiny Star V (s4a) - 216/190 : Cinderace (Shiny Vault)` or `Heat Wave Arena sv9a - 003/063 : ...`, so a card matches (`exact`) on its number plus the title's set code equal to its TCGdex set id; promo numbers like `069/SV-P` match on the promo code. Titles that only name the set (mostly PSA slabs, `Vmax Climax - 232/184`) borrow the code other titles give that set name. Korean copies in the collection never match. Graded slabs are labelled with their grade; other listings are near mint per their description. The shop's titles are hand-typed and a few carry the wrong number or name, so check the offer's title. |
| `totalcards` | [Total Cards](https://totalcards.net), UK, GBP. Japanese cards only. | Reads the shop's whole Japanese singles collection from its public Shopify product JSON once per run (about 35 requests for its ~8,400 products, of which only ~600 are in stock). Titles look like `Pokemon - Mega Evolution - Nihil Zero - Mega Starmie ex - 111/080` with the shop's own English set names ("Hot Air Arena", "Glory of the Rocket Gang"), which the plugin maps to TCGdex set ids; a card matches (`exact`) on that set plus its number, or on the promo code of numbers like `124/S-P`. Some listings offer several languages as options even in this Japanese collection, so only Japanese copies count. Conditions on the Cardmarket scale are mapped (EX to LP, GD and LP to MP, PL to HP, PO to DMG); plain listings have no stated condition. Graded slabs are labelled from the title or options. |
| `japangametcg` | [Japan Game & TCG Market](https://japan-game-tcg-market.myshopify.com), **Osaka, Japan, USD** (converted like any other currency; ships to the UK from Japan). Japanese cards only. | Reads the whole shop (about 750 products: singles, graded, promos, vintage, plus sealed and consoles that never match) from its public Shopify product JSON once per run, 3 requests. Titles look like `【NM】Mega Dragonite ex SAR 246/193 | Mega Dream ex M2a | Japanese`, so a card matches (`exact`) on its number plus the set code equal to its TCGdex set id; promo numbers like `001/SV-P` match on the promo code, and titles with no code (vintage VS/e-Series) match on a `|` part equal to the set name. Condition comes from the 【】 tag (`NM-` counts as LP); `【PSA 10】` copies are labelled graded. Listings with two card numbers, bulk lots and the few Chinese cards never match. |
| `tcghouse` | [TCG House](https://tcg-house.co.uk), UK, GBP. English and Japanese cards. | Reads the shop's whole singles collection from its public Shopify product JSON once per run (4 requests for its ~900 products). Titles name the set with the shop's own label, like `Tyrunt Common ME03: Perfect Order 044/088 NM` or `ME02: Phantasmal Flames #079/094 Ambipom`, and the label is the only sign of language: Japanese sets carry their Japanese code (`SV1V: Violet ex`), English ones the English series code, and codes clash (`SV10` is both Destined Rivals and the Japanese Glory of Team Rocket), so the plugin maps each label to a TCGdex language and set; a card matches (`exact`) on that plus its number. An unmapped `<code>: <name>` label still matches when the code is the card's set id and the name contains its set name. Listings with no set (`Dusknoir #037/131`) and reprint groupings (Deck Exclusives, Prize Pack, Miscellaneous) never match. Everything is sold as Near Mint. |
| `tynesidetcg` | [Tyneside TCG](https://www.tynesidetcg.co.uk), UK, GBP. Japanese cards only. | Reads the shop's whole Japanese singles collection from its public Shopify product JSON once per run (one request for its ~200 products, mostly Scarlet & Violet, Sword & Shield and Mega Evolution art rares and full arts). Titles look like `Snorunt 200/193 – Mega Dream (Japanese Single)` and the set code is one of the product's tags (`M2A`), so a card matches (`exact`) on its number plus that tag equal to its TCGdex set id; promo numbers like `018/M-P` match on the promo code instead. Pokédex-numbered promos (`No. 184`), DP-era cards and Classic Collection cards never match. Each product is one listing; condition is only given when the title says so (`(LP Condition)`). |
| `midnightcards` | [Midnight Cards](https://midnightcards.co.uk), UK, GBP. Japanese cards only. | The shop's pages sit behind a Cloudflare browser check, so this reads its public WooCommerce Store API instead, filtered to the "Pokémon Japanese" brand, once per run (about 5 requests for its ~420 products, mostly Mega Evolution and recent Scarlet & Violet sets). Titles carry the set code and number (`Bouffalant — MEGA Dream ex (M2a) 140/193`), so a card matches (`exact`) on its number plus that code equal to its TCGdex set id, or the product's set-name attribute naming its set. Each product is one print, named in the offer's title; a product offering several prints at different prices has each one read for its own price and stock. The shop doesn't give a condition. |
| `titancards` | [Titan Cards](https://titancards.co.uk), UK, GBP. Almost all English cards; a few Japanese. | Reads the shop's whole Pokémon singles collection from its public Shopify product JSON once per run (3 requests for its ~680 products, all in stock, mostly Scarlet & Violet, Mega Evolution and Sword & Shield ex/V and illustration rares). Titles look like `Gwynn 119/084 Special Illustration Rare Pokemon Card (Mega Evolution Pitch Black)`; the bracketed set name, minus its series prefix, is mapped to a TCGdex set id, so a card matches (`exact`) on language, set and number. Black Star promos (`SWSH226`) match the promo set, and gallery numbers (`TG05/TG30`) the gallery's own set. The few Japanese cards are from the 25th Anniversary promo pack, which TCGdex doesn't list yet, so they can't match for now. Bundles and mystery boxes are skipped. Every card is sold as near mint. |
| `cardattic` | [The Card Attic](https://thecardatticshop.co.uk), UK, GBP. Japanese cards only. | Reads the shop's Japanese singles collection from its public Shopify product JSON once per run (one request for its few dozen cards). Titles are hand-written like `Steelix - 033/054 - XY11-Bb: Fever-Burst Fighter` or `Hop's Zacian ex - Battle Partners - 069/100`, with the same English set names Total Cards uses, so a card matches (`exact`) on its number plus that set name, a set code equal to its TCGdex set id (`XY11-Bb` is XY11a), or its own set name. The condition comes from the product description ("The condition of this card is Near Mint."); a product without one has no stated condition. |
| `aberdeen` | [Aberdeen Collectables](https://aberdeencollectables.co.uk), UK, GBP. Japanese, English, Korean and Chinese cards. | Reads the shop's raw singles and graded cards collections from its public Shopify product JSON once per run (two requests, about 100 products). Each listing's description gives the set with its code and the card number (`Set: MEGA Dream ex (M2a)`, `Card number: 044/193`) and its Language tag gives the print, so a card matches on language, number and set: the code, a promo number's code (`262/SV-P`), or the set name (Japanese sets' English names as for Total Cards, English sets' official codes like `PFL` and names from TCGdex). The shop copies listings from each other and doesn't always tidy up, so a match is `uncertain` when the title and description give different numbers or the code and name point at different sets, or when the title's card name doesn't agree with the card's English name (a few Japanese listings carry another card's name). Graded copies are labelled with their grade (`PSA 10`, `GetGraded 9.5`). |
| `nmdcollectables` | [NMD Collectables](https://www.nmdcollectables.co.uk), UK, GBP. Japanese cards only. | The whole shop is Japanese cards, so this reads every product from its public Shopify product JSON once per run (about 11 requests for its ~2,600 products, mostly Scarlet & Violet and Mega Evolution sets, commons up to special art rares). Titles look like `Suicune - 026/080 - Rare - m2 - Inferno X` and the product type is the set's English name, which is mapped to a TCGdex set id, so a card matches (`exact`) on that set plus its number. Poké Ball and Master Ball reverse holos are separate products with the same number, so they show up as extra offers named in the title. Booster boxes and bundles never match. Each product is one listing; the shop gives no condition. |

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

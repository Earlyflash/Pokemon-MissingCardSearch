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
pip install -r requirements.txt

# Only needed for --profile (running the export for you):
cd vendor/RareCandyExporter && npm install && cd ../..
```

Already cloned without `--recurse-submodules`? Run `git submodule update --init`.

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

It prints a per-set report and writes every missing card to
`missing_cards.csv`:

```
MEGA Dream ex [M2a, Japanese]: own <owned>/<total>, missing <n>
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
  "sets": [{
    "set_id": "M2a", "set_name": "MEGA Dream ex", "language": "Japanese",
    "tcgdex_lang": "ja", "total": 250, "owned": 180,
    "missing": [{
      "card_id": "M2a-002", "set_id": "M2a", "set_name": "MEGA Dream ex",
      "local_id": "002", "name": "フシギソウ", "language": "Japanese",
      "tcgdex_lang": "ja", "rarity": null, "finish": null
    }]
  }],
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
| `--out FILE` | Where to write the missing cards (default `missing_cards.csv`). |
| `--json FILE` | Also write the missing cards as JSON, grouped by set (see below). |

## How it works

1. Every card in the export is grouped by **set name + print language**, so
   an English and a Japanese collection of the same set are checked
   separately against the right card list.
2. Each set name is matched to a TCGdex set id: first via `set_map.json`,
   then an exact name match across TCGdex's Japanese, English, Chinese and
   Korean datasets, then the binder cover tool's code/name search.
3. The set's card list is fetched from the TCGdex dataset for that print
   language (English for languages TCGdex's datasets here don't cover, such
   as German or French).
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
- Only sets that appear in your RareCandy collection are checked.

## Tests

```bash
python -m unittest discover -s tests -v
```

TCGdex calls are mocked, so the suite runs offline.

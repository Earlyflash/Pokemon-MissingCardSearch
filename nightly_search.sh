#!/bin/sh
# Runs the price search overnight so the cache, offers.csv and
# price_table.html in ~/PokemonData (or $POKEMON_DATA_DIR) are fresh in the
# morning. Schedule it with cron (see "Running it overnight" in README.md).
# --cache-ttl 0 rechecks every page with the shops; unchanged pages cost a
# short "not modified" reply rather than a download.
DATA_DIR="${POKEMON_DATA_DIR:-$HOME/PokemonData}"
mkdir -p "$DATA_DIR" || exit 1
cd "$(dirname "$0")" || exit 1
python3 price_search.py --cache-ttl 0 >> "$DATA_DIR/nightly_search.log" 2>&1

#!/bin/sh
# Runs the price search overnight so the cache, offers.csv and
# price_table.html in ~/PokemonData (or $POKEMON_DATA_DIR) are fresh in the
# morning. Schedule it with cron (see "Running it overnight" in README.md).
# With RARECANDY_PROFILE set, it first re-exports that RareCandy profile and
# rewrites missing_cards.csv (moving arrived cards out of ordered.txt); if
# that fails, the search uses the missing_cards.csv already there.
# --cache-ttl 0 rechecks every page with the shops; unchanged pages cost a
# short "not modified" reply rather than a download.
DATA_DIR="${POKEMON_DATA_DIR:-$HOME/PokemonData}"
mkdir -p "$DATA_DIR" || exit 1
cd "$(dirname "$0")" || exit 1
LOG="$DATA_DIR/nightly_search.log"
echo "=== $(date '+%Y-%m-%d %H:%M') nightly search" >> "$LOG"
if [ -n "$RARECANDY_PROFILE" ]; then
    python3 missing_cards.py --profile "$RARECANDY_PROFILE" >> "$LOG" 2>&1
fi
python3 price_search.py --cache-ttl 0 >> "$LOG" 2>&1

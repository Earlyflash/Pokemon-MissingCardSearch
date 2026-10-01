#!/bin/sh
# Runs the price search overnight so the cache, offers.csv and
# price_table.html are fresh in the morning. Schedule it with cron (see
# "Running it overnight" in README.md). --cache-ttl 0 rechecks every page
# with the shops; unchanged pages cost a short "not modified" reply rather
# than a download.
cd "$(dirname "$0")" || exit 1
python3 price_search.py missing_cards.csv --cache-ttl 0 >> nightly_search.log 2>&1

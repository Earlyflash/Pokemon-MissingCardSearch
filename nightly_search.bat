@echo off
rem Runs the price search overnight so the cache, offers.csv and
rem price_table.html are fresh in the morning. Schedule it with Task
rem Scheduler (see "Running it overnight" in README.md). --cache-ttl 0
rem rechecks every page with the shops; unchanged pages cost a short
rem "not modified" reply rather than a download.
cd /d "%~dp0"
python price_search.py missing_cards.csv --cache-ttl 0 >> nightly_search.log 2>&1

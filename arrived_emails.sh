#!/bin/sh
# Has Claude Code move the emails of orders whose cards have all arrived
# (every line of the order moved from ordered.txt to ordered_arrived.txt by
# missing_cards.py) from your Inbox to ARRIVED_EMAIL_FOLDER (default
# "Inbox/Orders/Pokemon"), using arrived_emails_prompt.txt.
# nightly_search.sh runs it after missing_cards.py. Each order is filed once:
# Claude reports the orders it finished and they're added to
# emails_filed.txt in ~/PokemonData (or $POKEMON_DATA_DIR). With nothing new
# to file, Claude isn't started. Needs the claude CLI, logged in, with your
# email connected (Microsoft 365 by default; set ARRIVED_EMAIL_TOOLS to
# another connector's search, read and move tool names). Claude may only
# search, read and move email and read the data folder. At most
# ARRIVED_EMAIL_MAX_ORDERS (default 25) orders are sent per run, so a
# backlog is worked through over a few nights, and Claude is stopped after
# ORDER_EMAIL_TIMEOUT (default 20m).
REPO="$(cd "$(dirname "$0")" && pwd)" || exit 1
DATA_DIR="${POKEMON_DATA_DIR:-$HOME/PokemonData}"
cd "$DATA_DIR" || exit 1
CLAUDE="${CLAUDE:-$(command -v claude || echo "$HOME/.local/bin/claude")}"
EMAIL_TOOLS="${ARRIVED_EMAIL_TOOLS:-mcp__claude_ai_Microsoft_365__outlook_email_search mcp__claude_ai_Microsoft_365__read_resource mcp__claude_ai_Microsoft_365__outlook_batch_modify_labels}"
FOLDER="${ARRIVED_EMAIL_FOLDER:-Inbox/Orders/Pokemon}"
MAX="${ARRIVED_EMAIL_MAX_ORDERS:-25}"
FILED="$DATA_DIR/emails_filed.txt"
# A leading "//" makes a permission path absolute.
DATA_RULE="/$(pwd -P)"

echo "=== $(date '+%Y-%m-%d %H:%M') filing arrived orders' emails"
# One "<shop> | <order number>" line per order, its cards indented below.
ORDERS="$(python3 "$REPO/ordered.py" arrived-orders "$DATA_DIR/ordered.txt" |
    awk -v max="$MAX" '!/^ / { n++ } n <= max')" || exit 1
if [ -z "$ORDERS" ]; then
    echo "No newly arrived orders."
    exit 0
fi
if [ ! -x "$CLAUDE" ]; then
    echo "No claude CLI at $CLAUDE; not filing emails."
    exit 0
fi

OUT="$(mktemp)" || exit 1
trap 'rm -f "$OUT"' EXIT
PROMPT="$(cat "$REPO/arrived_emails_prompt.txt")

Folder: $FOLDER

Orders:
$ORDERS"
# shellcheck disable=SC2086  # EMAIL_TOOLS is a list
timeout "${ORDER_EMAIL_TIMEOUT:-20m}" "$CLAUDE" -p "$PROMPT" \
    --allowedTools $EMAIL_TOOLS "Read($DATA_RULE/**)" > "$OUT"
status=$?
cat "$OUT"
[ "$status" -eq 124 ] && echo "Stopped after ${ORDER_EMAIL_TIMEOUT:-20m}."
# Record the orders Claude finished, so they aren't sent again.
today="$(date +%Y-%m-%d)"
sed 's/[[:space:]]*$//' "$OUT" | grep -E '^FILED \| .+ \| .+ \| [0-9]+$' |
    sed "s/^FILED | //; s/\$/ emails moved | filed $today/" >> "$FILED"
exit 0

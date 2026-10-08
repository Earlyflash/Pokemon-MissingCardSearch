#!/bin/sh
# Has Claude Code read the last fortnight's order confirmations from your
# email and add their cards to ordered.txt in ~/PokemonData (or
# $POKEMON_DATA_DIR), using order_emails_prompt.txt. Needs the claude CLI,
# logged in, with your email connected (Microsoft 365 by default; set
# ORDER_EMAIL_TOOLS to another connector's tool names). Claude may only read
# email, read the data folder, edit ordered.txt and look cards up on TCGdex.
# Run it before nightly_search.sh (see "Running it overnight" in README.md).
# Claude is stopped after ORDER_EMAIL_TIMEOUT (default 20m) so a hung run
# can't hold up the search, and the log keeps its last LOG_LINES (default
# 5000) lines.
REPO="$(cd "$(dirname "$0")" && pwd)" || exit 1
DATA_DIR="${POKEMON_DATA_DIR:-$HOME/PokemonData}"
mkdir -p "$DATA_DIR" || exit 1
cd "$DATA_DIR" || exit 1
CLAUDE="${CLAUDE:-$(command -v claude || echo "$HOME/.local/bin/claude")}"
EMAIL_TOOLS="${ORDER_EMAIL_TOOLS:-mcp__claude_ai_Microsoft_365__outlook_email_search mcp__claude_ai_Microsoft_365__read_resource}"
# A leading "//" makes a permission path absolute.
DATA_RULE="/$(pwd -P)"
LOG="$DATA_DIR/nightly_orders.log"
if [ -f "$LOG" ]; then
    tail -n "${LOG_LINES:-5000}" "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
{
    echo "=== $(date '+%Y-%m-%d %H:%M') order emails"
    # shellcheck disable=SC2086  # EMAIL_TOOLS is a list
    timeout "${ORDER_EMAIL_TIMEOUT:-20m}" "$CLAUDE" -p "$(cat "$REPO/order_emails_prompt.txt")" \
        --allowedTools $EMAIL_TOOLS "Read($DATA_RULE/**)" \
        "Edit($DATA_RULE/ordered.txt)" \
        "WebFetch(domain:api.tcgdex.net)"
    status=$?
    [ "$status" -eq 124 ] && echo "Stopped after ${ORDER_EMAIL_TIMEOUT:-20m}."
    # Keep it in order date, shop and order number order, new orders included.
    python3 "$REPO/ordered.py" sort "$DATA_DIR/ordered.txt"
} >> "$LOG" 2>&1

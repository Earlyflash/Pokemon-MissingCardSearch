#!/bin/sh
# Emails you the missing cards listed under their Cardmarket trend or
# PulseAPI market price in the last price search (see deals.py), using
# Claude Code and deals_email_prompt.txt. Schedule it for 7am, after
# nightly_search.sh has finished (see "Deals email" in README.md):
#
#   0 7 * * * /path/to/Pokemon-MissingCardSearch/deals_email.sh
#
# It only emails when there's a deal that wasn't in an earlier email (a new
# listing, or the same one cheaper); set DEALS_EVERY_DAY=1 to email every
# day there are any. DEALS_EMAIL_TO is the address to send to (default: your
# own, as Microsoft 365 knows it) and DEALS_MIN_UNDER the percentage a card
# must be under a market price by (default 0). Both can go in the data
# folder's .env. Needs the claude CLI, logged in, with your email connected
# (Microsoft 365 by default; set DEALS_EMAIL_TOOLS to another connector's
# send and "who am I" tool names); Claude may only use those two tools.
# Output goes to nightly_search.log in ~/PokemonData (or $POKEMON_DATA_DIR).
REPO="$(cd "$(dirname "$0")" && pwd)" || exit 1
DATA_DIR="${POKEMON_DATA_DIR:-$HOME/PokemonData}"
cd "$DATA_DIR" || exit 1
LOG="$DATA_DIR/nightly_search.log"
CLAUDE="${CLAUDE:-$(command -v claude || echo "$HOME/.local/bin/claude")}"

# Take settings from .env; variables already set in the environment win.
if [ -f "$DATA_DIR/.env" ]; then
    while IFS='=' read -r key value; do
        case "$key" in
            DEALS_EMAIL_TO|DEALS_MIN_UNDER|DEALS_EVERY_DAY|DEALS_EMAIL_TOOLS)
                eval "[ -n \"\${$key}\" ] || export $key=\"\$value\"" ;;
        esac
    done < "$DATA_DIR/.env"
fi
EMAIL_TOOLS="${DEALS_EMAIL_TOOLS:-mcp__claude_ai_Microsoft_365__outlook_send_mail mcp__claude_ai_Microsoft_365__get_me}"
ONLY_NEW="--only-new"
[ -n "$DEALS_EVERY_DAY" ] && ONLY_NEW=""

DIR="$(mktemp -d)" || exit 1
trap 'rm -rf "$DIR"' EXIT
{
    echo "=== $(date '+%Y-%m-%d %H:%M') deals email"
    # shellcheck disable=SC2086  # ONLY_NEW may be empty
    python3 "$REPO/deals.py" --email-dir "$DIR" $ONLY_NEW || exit 1
    if [ ! -f "$DIR/body.html" ]; then
        exit 0
    fi
    if [ ! -x "$CLAUDE" ]; then
        echo "No claude CLI at $CLAUDE; not emailing the deals."
        exit 0
    fi
    PROMPT="$(cat "$REPO/deals_email_prompt.txt")

To: ${DEALS_EMAIL_TO:-me}
Subject: $(cat "$DIR/subject.txt")
BODY START
$(cat "$DIR/body.html")
BODY END"
    # shellcheck disable=SC2086  # EMAIL_TOOLS is a list
    timeout "${DEALS_EMAIL_TIMEOUT:-10m}" "$CLAUDE" -p "$PROMPT" --allowedTools $EMAIL_TOOLS \
        > "$DIR/out.txt"
    cat "$DIR/out.txt"
    if grep -qx '[[:space:]]*SENT[[:space:]]*' "$DIR/out.txt"; then
        python3 "$REPO/deals.py" --mark-sent
    else
        echo "The deals email wasn't sent; they'll be tried again next time."
    fi
} >> "$LOG" 2>&1

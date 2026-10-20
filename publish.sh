#!/bin/sh
# Uploads missing_cards.html and price_table.html from ~/PokemonData (or
# $POKEMON_DATA_DIR) to Cloudflare Pages, with a small index.html linking
# them, so they can be read from anywhere without opening up this machine.
# Needs CLOUDFLARE_API_TOKEN (a token with "Account > Cloudflare Pages >
# Edit") and CLOUDFLARE_ACCOUNT_ID, from the environment or the data
# folder's .env; CF_PAGES_PROJECT names the Pages project (default
# pokemon-missing-cards), which is created on first use. The pages are
# public unless you put Cloudflare Access in front of them (see "Reading the
# pages online" in README.md). Wrangler 3 is used because Wrangler 4 needs
# Node 22.
DATA_DIR="${POKEMON_DATA_DIR:-$HOME/PokemonData}"
WRANGLER="npx --yes wrangler@3.114.17"

# Take settings from .env; variables already set in the environment win.
if [ -f "$DATA_DIR/.env" ]; then
    while IFS='=' read -r key value; do
        case "$key" in
            CLOUDFLARE_API_TOKEN|CLOUDFLARE_ACCOUNT_ID|CF_PAGES_PROJECT)
                eval "[ -n \"\${$key}\" ] || export $key=\"\$value\"" ;;
        esac
    done < "$DATA_DIR/.env"
fi
PROJECT="${CF_PAGES_PROJECT:-pokemon-missing-cards}"
if [ -z "$CLOUDFLARE_API_TOKEN" ] || [ -z "$CLOUDFLARE_ACCOUNT_ID" ]; then
    echo "publish.sh: set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in $DATA_DIR/.env" >&2
    exit 1
fi

SITE="$(mktemp -d)" || exit 1
trap 'rm -rf "$SITE"' EXIT
found=""
for page in missing_cards.html price_table.html; do
    if [ -f "$DATA_DIR/$page" ]; then
        cp "$DATA_DIR/$page" "$SITE/" && found="$found $page"
    else
        echo "publish.sh: no $DATA_DIR/$page, skipping it" >&2
    fi
done
[ -n "$found" ] || { echo "publish.sh: nothing to publish" >&2; exit 1; }

{
    echo '<!doctype html><meta charset="utf-8">'
    echo '<meta name="viewport" content="width=device-width, initial-scale=1">'
    echo '<title>Pokémon cards</title>'
    echo '<body style="font-family: system-ui, sans-serif; margin: 2em">'
    echo "<p>Updated $(date '+%Y-%m-%d %H:%M')</p><ul>"
    for page in $found; do
        case "$page" in
            missing_cards.html) label="Missing cards" ;;
            price_table.html) label="Prices" ;;
        esac
        echo "<li><a href=\"$page\">$label</a></li>"
    done
    echo '</ul>'
} > "$SITE/index.html"

# Pages won't deploy to a project that doesn't exist, so make it first time.
if ! $WRANGLER pages project list 2>/dev/null | grep -q " $PROJECT "; then
    $WRANGLER pages project create "$PROJECT" --production-branch main || exit 1
fi
$WRANGLER pages deploy "$SITE" --project-name "$PROJECT" --branch main --commit-dirty=true

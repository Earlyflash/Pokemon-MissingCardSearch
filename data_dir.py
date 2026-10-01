"""Where your own files live: the RareCandy export, missing_cards.csv,
ordered.txt, the offers and price table, the cache, .env and so on. They're
kept out of the repo in ~/PokemonData, or the folder named by the
POKEMON_DATA_DIR environment variable."""
import os

DATA_DIR = os.path.expanduser(os.environ.get("POKEMON_DATA_DIR") or "~/PokemonData")


def data_path(*parts):
    return os.path.join(DATA_DIR, *parts)


def ensure_parent(path):
    """Create the folder `path` goes in, so a first run can write to DATA_DIR."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

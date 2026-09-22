"""DeckdHQ plugin tests. The fixtures are real listings captured from
DeckdHQ's API on 2026-09-22, trimmed to the fields the plugin reads, so no
network is needed."""
import json
import os
import sys
import unittest
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import deckdhq  # noqa: E402
from marketplaces.base import MATCH_EXACT, MATCH_LIKELY, MissingCard, SearchContext  # noqa: E402


def load_page(n):
    with open(os.path.join(HERE, "fixtures", f"deckdhq_page{n}.json"), encoding="utf-8") as f:
        return json.load(f)


def card(set_id, set_name, local_id, language="English"):
    lang = {"English": "en", "Japanese": "ja"}[language]
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, "", language, lang)


class FakeContext(SearchContext):
    def __init__(self):
        super().__init__(deckdhq.PLUGIN)
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        page = int(url.rsplit("page=", 1)[1])
        return load_page(page)


def search(*cards):
    return deckdhq.PLUGIN.search_set(list(cards), FakeContext())


class NormalizeTests(unittest.TestCase):
    def test_numbers(self):
        for raw, want in [("073/072", "73"), ("TG05/TG30", "TG5"), ("SVP 176", "SVP176"),
                          ("SV050/SV122", "SV50"), ("94", "94"), (None, "")]:
            self.assertEqual(deckdhq.normalize_number(raw), want, raw)

    def test_text(self):
        self.assertEqual(deckdhq.normalize_text("Pokémon: Scarlet & Violet"),
                         "pokemon scarlet and violet")


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_once_per_run(self):
        ctx = FakeContext()
        deckdhq.PLUGIN.search_set([card("sv04", "Paradox Rift", "046")], ctx)
        deckdhq.PLUGIN.search_set([card("swsh4.5", "Shining Fates", "073")], ctx)
        self.assertEqual([u.rsplit("page=", 1)[1] for u in ctx.urls], ["1", "2"])
        self.assertEqual(len(ctx.state["listings"]), 16)


class MatchingTests(unittest.TestCase):
    def test_structured_listing_matches_exactly(self):
        [offer] = search(card("sv04", "Paradox Rift", "046"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("1.8"))
        self.assertEqual(offer.currency, "GBP")
        self.assertEqual(offer.url, "https://www.deckdhq.com/listing/6091")
        self.assertEqual(offer.condition, "NM")

    def test_ebay_import_matches_set_from_title(self):
        [offer] = search(card("swsh4.5", "Shining Fates", "SV050"))
        self.assertEqual(offer.match, MATCH_LIKELY)
        self.assertEqual(offer.price, Decimal("4.17"))

    def test_japanese_set_from_ebay_title(self):
        offers = search(card("M2a", "MEGA Dream ex", "204", "Japanese"))
        self.assertEqual([(o.url, o.match) for o in offers],
                         [("https://www.deckdhq.com/listing/8706", MATCH_LIKELY)])

    def test_japanese_listing_with_its_own_set_name(self):
        [offer] = search(card("M2a", "MEGA Dream ex", "206", "Japanese"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertIn("graded ACE 9", offer.title)

    def test_set_code_prefix_on_set_name(self):
        [offer] = search(card("M2", "Inferno X", "108", "Japanese"))
        self.assertEqual(offer.url, "https://www.deckdhq.com/listing/6621")
        self.assertEqual(offer.match, MATCH_LIKELY)  # listing has no language

    def test_set_code_prefix_on_card_number(self):
        [offer] = search(card("svp", "Scarlet & Violet Black Star Promos", "216"))
        self.assertEqual(offer.url, "https://www.deckdhq.com/listing/6603")

    def test_language_must_agree(self):
        self.assertEqual(search(card("sv04", "Paradox Rift", "046", "Japanese")), [])
        self.assertEqual(search(card("M2a", "MEGA Dream ex", "206")), [])

    def test_korean_title_beats_japanese_tag(self):
        self.assertEqual(search(card("M3", "Mega Brave", "064", "Japanese")), [])

    def test_wrong_number_or_set_no_match(self):
        self.assertEqual(search(card("sv04", "Paradox Rift", "047")), [])
        self.assertEqual(search(card("sv05", "Temporal Forces", "046")), [])

    def test_several_cards_in_one_call(self):
        offers = search(card("sv04", "Paradox Rift", "244"), card("sv04", "Paradox Rift", "259"))
        self.assertEqual(sorted(o.card_id for o in offers), ["sv04-244", "sv04-259"])


if __name__ == "__main__":
    unittest.main()

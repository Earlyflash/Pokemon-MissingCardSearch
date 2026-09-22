"""Cardmarket plugin tests. The price guide rows and TCGdex pricing blocks
are real ones captured on 2026-09-22, trimmed, so no network is needed."""
import os
import sys
import unittest
import urllib.error
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from marketplaces import cardmarket  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402

PRICE_GUIDE = {"version": 1, "createdAt": "2026-09-22T02:48:57+0200", "priceGuides": [
    {"idProduct": 825955, "idCategory": 51, "avg": 1.04, "low": 0.13, "trend": 0.94},
    {"idProduct": 719448, "idCategory": 51, "avg": 2.1, "low": 0.49, "trend": 1.95},
    {"idProduct": 111111, "idCategory": 51, "avg": None, "low": None, "trend": None},
]}
TCGDEX = {
    "https://api.tcgdex.net/v2/en/cards/sv10-081": {
        "id": "sv10-081", "pricing": {"cardmarket": {"unit": "EUR", "idProduct": 825955}}},
    "https://api.tcgdex.net/v2/ja/cards/SV2a-006": {
        "id": "SV2a-006", "pricing": {"cardmarket": {"unit": "EUR", "idProduct": 719448}}},
    "https://api.tcgdex.net/v2/en/cards/sv10-001": {"id": "sv10-001", "pricing": None},
    "https://api.tcgdex.net/v2/en/cards/sv10-002": {
        "id": "sv10-002", "pricing": {"cardmarket": {"idProduct": 111111}}},
    "https://api.tcgdex.net/v2/en/cards/sv10-003": {
        "id": "sv10-003", "pricing": {"cardmarket": {"idProduct": 999999}}},
}


def card(card_id, lang="en"):
    set_id, local_id = card_id.rsplit("-", 1)
    return MissingCard(card_id, set_id, "", local_id, "", {"en": "English", "ja": "Japanese"}[lang], lang)


class FakeContext(SearchContext):
    def __init__(self, guide_error=None):
        super().__init__(cardmarket.PLUGIN)
        self.urls = []
        self.guide_error = guide_error

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        if url == cardmarket.PRICE_GUIDE_URL:
            if self.guide_error:
                raise self.guide_error
            return PRICE_GUIDE
        if url not in TCGDEX:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return TCGDEX[url]


class CardmarketTests(unittest.TestCase):
    def test_is_a_price_guide(self):
        self.assertTrue(cardmarket.PLUGIN.price_guide)

    def test_prices_english_and_japanese_cards_from_the_guide(self):
        ctx = FakeContext()
        offers = cardmarket.PLUGIN.search_set([card("sv10-081")], ctx)
        offers += cardmarket.PLUGIN.search_set([card("SV2a-006", "ja")], ctx)
        self.assertEqual([(o.card_id, o.price, o.currency, o.match, o.url) for o in offers], [
            ("sv10-081", Decimal("0.13"), "EUR", MATCH_EXACT,
             "https://www.cardmarket.com/en/Pokemon/Products?idProduct=825955"),
            ("SV2a-006", Decimal("0.49"), "EUR", MATCH_EXACT,
             "https://www.cardmarket.com/en/Pokemon/Products?idProduct=719448"),
        ])
        self.assertIn("any language or condition", offers[0].title)
        self.assertIn("trend EUR 0.94", offers[0].title)
        self.assertIsNone(offers[0].condition)

    def test_price_guide_downloaded_once_per_run(self):
        ctx = FakeContext()
        cardmarket.PLUGIN.search_set([card("sv10-081")], ctx)
        cardmarket.PLUGIN.search_set([card("SV2a-006", "ja")], ctx)
        self.assertEqual(ctx.urls.count(cardmarket.PRICE_GUIDE_URL), 1)

    def test_cards_without_a_product_or_a_price_are_skipped(self):
        offers = cardmarket.PLUGIN.search_set(
            [card("sv10-001"), card("sv10-002"), card("sv10-003"), card("sv10-081")], FakeContext())
        self.assertEqual([o.card_id for o in offers], ["sv10-081"])

    def test_card_unknown_to_tcgdex_is_skipped(self):
        offers = cardmarket.PLUGIN.search_set([card("sv10-999"), card("sv10-081")], FakeContext())
        self.assertEqual([o.card_id for o in offers], ["sv10-081"])

    def test_price_guide_failure_fails_the_set_once(self):
        with self.assertRaises(OSError):
            cardmarket.PLUGIN.search_set([card("sv10-081")], FakeContext(OSError("offline")))


if __name__ == "__main__":
    unittest.main()

"""TCG House plugin tests. The fixture is real products captured from the
shop's Shopify JSON on 2026-09-24, trimmed to the fields the plugin reads,
so no network is needed."""
import json
import os
import sys
import unittest
from decimal import Decimal
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import tcghouse  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_fixture():
    with open(os.path.join(HERE, "fixtures", "tcghouse_products.json"), encoding="utf-8") as f:
        return json.load(f)["products"]


def card(set_id, set_name, local_id, lang):
    language = {"en": "English", "ja": "Japanese"}[lang]
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, "", language, lang)


class FakeContext(SearchContext):
    """Serves the fixture a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, products=None):
        super().__init__(tcghouse.PLUGIN)
        self.products = load_fixture() if products is None else products
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        return {"products": self.products[(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return tcghouse.PLUGIN.search_set(list(cards), ctx or FakeContext())


class ParseTests(unittest.TestCase):
    def test_number_at_the_end(self):
        self.assertEqual(tcghouse.parse_title("Tyrunt Common ME03: Perfect Order 044/088 NM"),
                         {"number": "44", "head": "Tyrunt Common ME03: Perfect Order"})

    def test_number_after_a_hash(self):
        self.assertEqual(tcghouse.parse_title("ME02: Phantasmal Flames #079/094 Ambipom"),
                         {"number": "79", "head": "ME02: Phantasmal Flames"})

    def test_promo_number(self):
        self.assertEqual(tcghouse.parse_title("Garchomp & Giratina GX Holo Promo SM Promos SM193")["number"],
                         "SM193")

    def test_no_number(self):
        self.assertIsNone(tcghouse.parse_title("Perfect Order Booster Box"))

    def test_longest_label_wins(self):
        self.assertEqual(tcghouse.find_set("Dusknoir SWSH12: Silver Tempest Trainer Gallery"), ("en", "swsh12tg"))
        self.assertEqual(tcghouse.find_set("Lugia SWSH12: Silver Tempest"), ("en", "swsh12"))

    def test_same_code_in_both_languages(self):
        self.assertEqual(tcghouse.find_set("Zamazenta Holo Art Rare SV10: The Glory of Team Rocket"), ("ja", "SV10"))
        self.assertEqual(tcghouse.find_set("Medicham Reverse Holo Uncommon SV10: Destined Rivals"), ("en", "sv10"))
        self.assertEqual(tcghouse.find_set("Tirtouga Uncommon SV: Black Bolt"), ("en", "sv10.5b"))
        self.assertEqual(tcghouse.find_set("SV11B: Black Bolt"), ("ja", "SV11B"))

    def test_no_set(self):
        self.assertIsNone(tcghouse.find_set("Dusknoir"))
        self.assertIsNone(tcghouse.find_set("Lokix - Rare Deck Exclusives"))


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(tcghouse, "PAGE_SIZE", 4):
            search(card("me03", "Perfect Order", "044", "en"), ctx=ctx)
            search(card("SV10", "Glory of Team Rocket", "107", "ja"), ctx=ctx)
        # 9 products at 4 a page: stops on the short third page.
        self.assertEqual([u.rsplit("page=", 1)[1] for u in ctx.urls], ["1", "2", "3"])
        self.assertEqual(len(ctx.state["products"]), 9)


class MatchingTests(unittest.TestCase):
    def test_english_card(self):
        [offer] = search(card("me03", "Perfect Order", "044", "en"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("0.49"))
        self.assertEqual(offer.currency, "GBP")
        self.assertEqual(offer.condition, "NM")
        self.assertIsNone(offer.grade)
        self.assertEqual(offer.url, "https://tcg-house.co.uk/products/bulk-t3-20?variant=57394265489734")
        self.assertEqual(offer.title, "Tyrunt Common ME03: Perfect Order 044/088 NM")

    def test_japanese_card(self):
        [offer] = search(card("SV10", "Glory of Team Rocket", "107", "ja"))
        self.assertEqual(offer.price, Decimal("3.49"))
        self.assertIn("Zamazenta", offer.title)

    def test_same_code_and_number_in_the_other_language_does_not_match(self):
        [ja] = search(card("SV10", "Glory of Team Rocket", "100", "ja"))
        self.assertIn("Houndoom", ja.title)
        [en] = search(card("sv10", "Destined Rivals", "100", "en"))
        self.assertIn("Medicham", en.title)

    def test_hash_title_with_condition_in_tags(self):
        [offer] = search(card("me02", "Phantasmal Flames", "079", "en"))
        self.assertEqual(offer.price, Decimal("0.79"))
        self.assertEqual(offer.condition, "NM")

    def test_promo(self):
        [offer] = search(card("smp", "SM Black Star Promos", "SM193", "en"))
        self.assertEqual(offer.price, Decimal("79.99"))

    def test_listing_without_a_set_never_matches(self):
        self.assertEqual(search(card("sv08.5", "Prismatic Evolutions", "037", "en")), [])

    def test_sold_out_listing_is_skipped(self):
        self.assertEqual(search(card("SV1V", "Violet ex", "079", "ja")), [])
        self.assertEqual(search(card("M2a", "MEGA Dream ex", "003", "ja")), [])

    def test_unknown_label_matches_on_code_and_set_name(self):
        product = {"id": 1, "title": "Pikachu Holo Art Rare M7: Future Set 101/090 NM", "handle": "p",
                   "tags": [], "variants": [{"id": 2, "title": "Near Mint", "price": "1.00", "available": True}]}
        ctx = FakeContext(products=[product])
        self.assertEqual(len(search(card("M7", "Future Set", "101", "ja"), ctx=ctx)), 1)
        self.assertEqual(search(card("M7", "Other Set", "101", "ja"), ctx=FakeContext(products=[product])), [])
        self.assertEqual(search(card("M8", "Future Set", "101", "ja"), ctx=FakeContext(products=[product])), [])


if __name__ == "__main__":
    unittest.main()

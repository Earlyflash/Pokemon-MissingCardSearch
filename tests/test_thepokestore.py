"""The Poké Store plugin tests. The fixture is real products and collections
captured from the shop's Shopify JSON on 2026-09-23, trimmed to the fields
the plugin reads, so no network is needed."""
import json
import os
import sys
import unittest
from decimal import Decimal
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import thepokestore  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_fixture():
    with open(os.path.join(HERE, "fixtures", "thepokestore_products.json"), encoding="utf-8") as f:
        return json.load(f)


def card(set_id, set_name, local_id):
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, "", "Japanese", "ja")


class FakeContext(SearchContext):
    """Serves the fixture a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, products=None, collections=None):
        super().__init__(thepokestore.PLUGIN)
        fixture = load_fixture()
        self.products = fixture["products"] if products is None else products
        self.collections = fixture["collections"] if collections is None else collections
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        if "/collections.json" in url:
            return {"collections": self.collections[(page - 1) * limit:page * limit]}
        return {"products": self.products[(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return thepokestore.PLUGIN.search_set(list(cards), ctx or FakeContext())


class ParseTests(unittest.TestCase):
    def test_title(self):
        self.assertEqual(thepokestore.parse_title("010/086 Virizion"), {"number": "10", "name": "Virizion"})

    def test_unrecognised_title(self):
        self.assertIsNone(thepokestore.parse_title("Storm Emeralda Booster Box"))

    def test_set_codes_from_collection_titles(self):
        codes = thepokestore.set_codes(load_fixture()["collections"])
        self.assertEqual(codes["black bolt"], "sv11b")
        self.assertEqual(codes["mega dream ex"], "m2a")
        self.assertNotIn("japanese booster boxes", codes)


class CatalogueTests(unittest.TestCase):
    def test_reads_collections_and_every_page_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(thepokestore, "PAGE_SIZE", 4):
            search(card("SV11B", "Black Bolt", "010"), ctx=ctx)
            search(card("M1S", "Mega Symphonia", "001"), ctx=ctx)
        pages = [(u.split("?")[0].rsplit("/", 1)[1], u.rsplit("page=", 1)[1]) for u in ctx.urls]
        # 8 collections and 6 products at 4 a page: both stop on a short page.
        self.assertEqual(pages, [("collections.json", "1"), ("collections.json", "2"), ("collections.json", "3"),
                                 ("products.json", "1"), ("products.json", "2")])
        self.assertEqual(len(ctx.state["products"]), 6)


class MatchingTests(unittest.TestCase):
    def test_tag_code_and_number(self):
        [offer] = search(card("M1S", "メガシンフォニア", "001"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("0.20"))
        self.assertEqual(offer.currency, "GBP")
        self.assertIsNone(offer.condition)
        self.assertIsNone(offer.grade)
        self.assertEqual(offer.url, "https://thepokestore.co.uk/products/001063-tangela?variant=56101558747518")
        self.assertEqual(offer.title, "001/063 Tangela (Non-Holo) [Mega Symphonia]")

    def test_tag_equal_to_set_name_matches_without_a_code(self):
        [offer] = search(card("sv9a-other", "Heat Wave Arena", "001"), ctx=FakeContext(collections=[]))
        self.assertIn("ethans-pinsir", offer.url)

    def test_one_offer_per_print_in_stock(self):
        offers = search(card("SV11W", "White Flare", "010"))
        self.assertEqual(sorted((o.price, o.title) for o in offers),
                         [(Decimal("0.50"), "010/086 Virizion (Holo) [White Flare]"),
                          (Decimal("2.00"), "010/086 Virizion (Poké Ball Holo) [White Flare]")])

    def test_same_number_in_the_sister_set_does_not_match(self):
        offers = search(card("SV11B", "Black Bolt", "010"))
        self.assertTrue(offers)
        self.assertTrue(all("foongus" in o.url for o in offers))

    def test_sold_out_listing_and_print_are_skipped(self):
        self.assertEqual(search(card("M1L", "Mega Brave", "001")), [])
        [offer] = search(card("M2a", "MEGA Dream ex", "002"))
        self.assertEqual(offer.title, "002/193 Yanma (Non-Holo) [Mega Dream ex]")


if __name__ == "__main__":
    unittest.main()

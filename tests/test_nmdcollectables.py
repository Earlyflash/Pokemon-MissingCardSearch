"""NMD Collectables plugin tests. The fixture is real products captured from
the shop's Shopify JSON on 2026-09-24, trimmed to the fields the plugin
reads, so no network is needed."""
import copy
import json
import os
import sys
import unittest
from decimal import Decimal
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import nmdcollectables  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_fixture():
    with open(os.path.join(HERE, "fixtures", "nmdcollectables_products.json"), encoding="utf-8") as f:
        return json.load(f)["products"]


def card(set_id, set_name, local_id):
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, "", "Japanese", "ja")


class FakeContext(SearchContext):
    """Serves the fixture a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, products=None):
        super().__init__(nmdcollectables.PLUGIN)
        self.products = load_fixture() if products is None else products
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        return {"products": self.products[(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return nmdcollectables.PLUGIN.search_set(list(cards), ctx or FakeContext())


def product(title, product_type):
    return {"title": title, "product_type": product_type}


class ParseTests(unittest.TestCase):
    def test_number_and_set_from_product_type(self):
        parsed = nmdcollectables.parse_product(product("Suicune - 026/080 - Rare - m2 - Inferno X", "Inferno X"))
        self.assertEqual(parsed, {"number": "26", "set_id": "m2"})

    def test_shop_spellings_of_set_names(self):
        for title, product_type, set_id in [
            ("Pecharunt ex - 219/187 - Special Art Rare - Terastal Fest EX", "Terastal Festival", "sv8a"),
            ("Kamado - 082/067 - Secret Rare", "Battle Region", "s9a"),
            ("Tandemaus - 064/078 - Common - sv1v - Violet ex", "Violet EX", "sv1v"),
            ("Greedent - 314/190 - Baby Shiny - Shiny Treasure EX", "Shiny Treasure", "sv4a"),
            ("Snorunt - 200/193 - Art Rare - Mega Dream EX", "Mega Dream", "m2a"),
        ]:
            self.assertEqual(nmdcollectables.parse_product(product(title, product_type))["set_id"], set_id, title)

    def test_sealed_and_bundles_have_no_card(self):
        self.assertIsNone(nmdcollectables.parse_product(
            product("Storm Emeralda Booster Box [m6] Japanese Pokemon", "")))
        self.assertIsNone(nmdcollectables.parse_product(
            product("Mega Dream Complete Base Set - 193 Cards", "Bundle")))

    def test_unknown_set_name_has_no_card(self):
        self.assertIsNone(nmdcollectables.parse_product(product("Pikachu - 001/100 - Common", "Some New Set")))


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(nmdcollectables, "PAGE_SIZE", 5):
            search(card("M6", "ストームエメラルダ", "109"), ctx=ctx)
            search(card("M2", "インフェルノX", "026"), ctx=ctx)
        # 14 products at 5 a page: stops on the short third page.
        self.assertEqual([u.rsplit("page=", 1)[1] for u in ctx.urls], ["1", "2", "3"])
        self.assertEqual(len(ctx.state["products"]), 14)


class MatchingTests(unittest.TestCase):
    def test_set_and_number(self):
        [offer] = search(card("M6", "ストームエメラルダ", "109"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("18.99"))
        self.assertEqual(offer.currency, "GBP")
        self.assertIsNone(offer.condition)
        self.assertEqual(offer.title, "Mega Golurk ex - 109/076 - Special Art Rare - Storm Emeralda")
        self.assertTrue(offer.url.startswith("https://www.nmdcollectables.co.uk/products/"))
        self.assertIn("?variant=", offer.url)

    def test_reverse_holo_is_its_own_offer(self):
        offers = search(card("SV11B", "ブラックボルト", "015"))
        self.assertEqual(sorted(o.price for o in offers), [Decimal("0.39"), Decimal("0.99")])
        self.assertTrue(any("Pokeball Reverse" in o.title for o in offers))

    def test_same_number_in_another_set_does_not_match(self):
        self.assertEqual(search(card("M1S", "メガシンフォニア", "088"), card("M2", "インフェルノX", "109")), [])

    def test_sold_out_listing_is_skipped(self):
        self.assertEqual(search(card("M6", "ストームエメラルダ", "070")), [])

    def test_all_sold_out(self):
        products = copy.deepcopy(load_fixture())
        for p in products:
            for v in p["variants"]:
                v["available"] = False
        self.assertEqual(search(card("M6", "ストームエメラルダ", "109"), ctx=FakeContext(products)), [])


if __name__ == "__main__":
    unittest.main()

"""Tyneside TCG plugin tests. The fixture is real products captured from the
shop's Shopify JSON on 2026-09-24, trimmed to the fields the plugin reads,
so no network is needed."""
import copy
import json
import os
import sys
import unittest
from decimal import Decimal
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import tynesidetcg  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_fixture():
    with open(os.path.join(HERE, "fixtures", "tynesidetcg_products.json"), encoding="utf-8") as f:
        return json.load(f)["products"]


def card(set_id, set_name, local_id):
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, "", "Japanese", "ja")


class FakeContext(SearchContext):
    """Serves the fixture a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, products=None):
        super().__init__(tynesidetcg.PLUGIN)
        self.products = load_fixture() if products is None else products
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        return {"products": self.products[(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return tynesidetcg.PLUGIN.search_set(list(cards), ctx or FakeContext())


def product(title, tags=()):
    return {"title": title, "tags": list(tags)}


class ParseTests(unittest.TestCase):
    def test_number_and_set_code_tag(self):
        parsed = tynesidetcg.parse_product(product("Snorunt 200/193 – Mega Dream (Japanese Single)",
                                                   ["AR", "Jap", "JP", "M2A"]))
        self.assertEqual(parsed, {"number": "200", "set_ids": {"m2a"}})

    def test_promo_number_gives_the_set(self):
        parsed = tynesidetcg.parse_product(product("Fuecoco 018/M-P - Mega Evolution Promos (Japanese Single)",
                                                   ["JPPROMO"]))
        self.assertEqual(parsed, {"number": "18", "set_ids": {"mp"}})

    def test_rarity_and_bucket_tags_are_not_set_codes(self):
        parsed = tynesidetcg.parse_product(product("PT1 – 074/096 Slaking 1st Edition DP Holo (Japanese Single)",
                                                   ["JPDP", "AR", "jex", "vstar", "SAR"]))
        self.assertEqual(parsed, {"number": "74", "set_ids": set()})

    def test_pokedex_numbered_titles_have_no_number(self):
        self.assertIsNone(tynesidetcg.parse_product(product("Hitmontop No. 237 - Unnumbered Promos", ["JPPROMO"])))
        self.assertIsNone(tynesidetcg.parse_product(product("DP2 – DPBP #470 Bastiodon", ["JPDP"])))

    def test_condition_from_title(self):
        self.assertEqual(tynesidetcg.condition(product("Armored Mewtwo 365/SM-P (Japanese Single) (LP Condition)")),
                         "LP")
        self.assertIsNone(tynesidetcg.condition(product("Snorunt 200/193 – Mega Dream (Japanese Single)")))


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(tynesidetcg, "PAGE_SIZE", 3):
            search(card("M2a", "MEGAドリームex", "200"), ctx=ctx)
            search(card("SV5a", "クリムゾンヘイズ", "067"), ctx=ctx)
        # 7 products at 3 a page: stops on the short third page.
        self.assertEqual([u.rsplit("page=", 1)[1] for u in ctx.urls], ["1", "2", "3"])
        self.assertEqual(len(ctx.state["products"]), 7)


class MatchingTests(unittest.TestCase):
    def test_tag_code_and_number(self):
        [offer] = search(card("M2a", "MEGAドリームex", "200"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("3.50"))
        self.assertEqual(offer.currency, "GBP")
        self.assertIsNone(offer.condition)
        self.assertEqual(offer.title, "Snorunt 200/193 – Mega Dream (Japanese Single)")
        self.assertTrue(offer.url.startswith(
            "https://www.tynesidetcg.co.uk/products/snorunt-200-193-mega-dream-japanese-single?variant="))

    def test_promo(self):
        [offer] = search(card("M-P", "メガ プロモカード", "018"))
        self.assertIn("fuecoco", offer.url)

    def test_same_number_in_another_set_does_not_match(self):
        self.assertEqual(search(card("SV5M", "サイバージャッジ", "067"), card("M2", "インフェルノX", "200")), [])

    def test_sold_out_listing_is_skipped(self):
        products = copy.deepcopy(load_fixture())
        for p in products:
            for v in p["variants"]:
                v["available"] = False
        self.assertEqual(search(card("M2a", "MEGAドリームex", "200"), ctx=FakeContext(products)), [])


if __name__ == "__main__":
    unittest.main()

"""The Card Attic plugin tests. The fixture is real products captured from
the shop's Shopify JSON on 2026-09-24, trimmed to the fields the plugin
reads, plus a sold-out copy and a non-card product, so no network is
needed."""
import json
import os
import sys
import unittest
from decimal import Decimal
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import cardattic  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_fixture():
    with open(os.path.join(HERE, "fixtures", "cardattic_products.json"), encoding="utf-8") as f:
        return json.load(f)["products"]


def card(set_id, set_name, local_id):
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, "", "Japanese", "ja")


class FakeContext(SearchContext):
    """Serves the fixture a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, products=None):
        super().__init__(cardattic.PLUGIN)
        self.products = load_fixture() if products is None else products
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        return {"products": self.products[(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return cardattic.PLUGIN.search_set(list(cards), ctx or FakeContext())


class ParseTests(unittest.TestCase):
    def test_number_then_coded_set(self):
        self.assertEqual(cardattic.parse_title("Steelix - 033/054 - XY11-Bb: Fever-Burst Fighter"),
                         {"number": "33", "codes": ["xy11bb"], "sets": ["fever burst fighter"]})

    def test_set_then_number(self):
        self.assertEqual(cardattic.parse_title("Hop's Zacian ex - Battle Partners - 069/100"),
                         {"number": "69", "codes": [], "sets": ["battle partners"]})

    def test_unrecognised_title(self):
        self.assertIsNone(cardattic.parse_title("Japanese Booster Pack Bundle"))

    def test_condition_from_description(self):
        products = {p["handle"]: p["body_html"] for p in load_fixture()}
        self.assertEqual(cardattic.parse_condition(products["maractus-101-100-battle-partners"]), "NM")
        self.assertEqual(cardattic.parse_condition(products["mewtwo-ex-062-059-xy8-bb-blue-shock"]), "MP")
        self.assertIsNone(cardattic.parse_condition(products["keldeo-ex-027-086-white-flare"]))


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(cardattic, "PAGE_SIZE", 4):
            search(card("SV9", "Battle Partners", "069"), ctx=ctx)
            search(card("M2a", "MEGA Dream ex", "232"), ctx=ctx)
        # 9 products at 4 a page: stops on the short third page.
        self.assertEqual([u.rsplit("page=", 1)[1] for u in ctx.urls], ["1", "2", "3"])
        self.assertEqual(len(ctx.state["products"]), 9)


class MatchingTests(unittest.TestCase):
    def test_code_prefix_and_number(self):
        [offer] = search(card("SV2a", "ポケモンカード151", "202"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("90.00"))
        self.assertEqual(offer.currency, "GBP")
        self.assertEqual(offer.condition, "NM")
        self.assertIsNone(offer.grade)
        self.assertEqual(offer.url, "https://thecardatticshop.co.uk/products/blastoise-ex-202-165-sv2a-pokemon-card-151")
        self.assertEqual(offer.title, "Blastoise ex - 202/165 - SV2a: Pokemon Card 151")

    def test_xy_code_alias(self):
        [offer] = search(card("XY11a", "爆熱の闘士", "033"))
        self.assertIn("steelix", offer.url)

    def test_english_set_name_without_a_code(self):
        [offer] = search(card("XY8a", "青い衝撃", "062"))
        self.assertIn("mewtwo", offer.url)
        self.assertEqual(offer.condition, "MP")
        [offer] = search(card("SV9", "バトルパートナーズ", "069"))
        self.assertIn("zacian", offer.url)

    def test_card_set_name_equal_to_title_set(self):
        [offer] = search(card("SV-other", "Battle Partners", "101"))
        self.assertIn("maractus", offer.url)

    def test_same_number_in_another_set_does_not_match(self):
        self.assertEqual(search(card("SV8", "Super Electric Breaker", "069")), [])
        self.assertEqual(search(card("XY8b", "赤い閃光", "062")), [])

    def test_sold_out_listing_is_skipped(self):
        self.assertEqual(search(card("M2", "インフェルノX", "029")), [])


if __name__ == "__main__":
    unittest.main()

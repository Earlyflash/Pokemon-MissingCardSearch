"""Midnight Cards plugin tests. The fixture is real products (and the two
variations of one) captured from the shop's WooCommerce Store API on
2026-09-24, trimmed to the fields the plugin reads, so no network is needed."""
import copy
import json
import os
import sys
import unittest
from decimal import Decimal
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import midnightcards  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_fixture():
    with open(os.path.join(HERE, "fixtures", "midnightcards_products.json"), encoding="utf-8") as f:
        return json.load(f)


def card(set_id, set_name, local_id):
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, "", "Japanese", "ja")


class FakeContext(SearchContext):
    """Serves the fixture `per_page` at a time, like the Store API's paging,
    and single variations by id."""

    def __init__(self, products=None):
        super().__init__(midnightcards.PLUGIN)
        fixture = load_fixture()
        self.products = fixture["products"] if products is None else products
        self.variations = fixture["variations"]
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        if "?" not in url:
            return self.variations[url.rsplit("/", 1)[1]]
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["per_page"]), int(query["page"])
        return self.products[(page - 1) * limit:page * limit]


def search(*cards, ctx=None):
    return midnightcards.PLUGIN.search_set(list(cards), ctx or FakeContext())


class ParseTests(unittest.TestCase):
    def test_title_with_em_dash(self):
        self.assertEqual(midnightcards.parse_title("Bouffalant — MEGA Dream ex (M2a) 140/193"),
                         {"name": "Bouffalant", "set_name": "MEGA Dream ex", "code": "m2a", "number": "140"})

    def test_title_with_en_dashes_and_language(self):
        parsed = midnightcards.parse_title("Tangela &#8211; Mega Symphonia (M1S) &#8211; 001/063 &#8211; Japanese")
        self.assertEqual((parsed["name"], parsed["code"], parsed["number"]), ("Tangela", "m1s", "1"))

    def test_title_without_a_total(self):
        self.assertEqual(midnightcards.parse_title("Silvally — Abyss Eye (M5) 93")["number"], "93")

    def test_unrecognised_title(self):
        self.assertIsNone(midnightcards.parse_title("Abyss Eye Booster Box"))


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(midnightcards, "PAGE_SIZE", 3):
            search(card("M5", "Abyss Eye", "060"), ctx=ctx)
            search(card("M1S", "Mega Symphonia", "001"), ctx=ctx)
        # 8 products at 3 a page: stops on the short third page.
        self.assertEqual([u.rsplit("page=", 1)[1] for u in ctx.urls], ["1", "2", "3"])
        self.assertTrue(all("pa_brand" in u and "pokemon-japanese" in u for u in ctx.urls))
        self.assertEqual(len(ctx.state["products"]), 8)


class MatchingTests(unittest.TestCase):
    def test_code_and_number(self):
        [offer] = search(card("M5", "アビスアイ", "060"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("0.20"))
        self.assertEqual(offer.currency, "GBP")
        self.assertIsNone(offer.condition)
        self.assertEqual(offer.quantity, 2)
        self.assertEqual(offer.url, "https://midnightcards.co.uk/product/bastiodon-abyss-eye-m5-060-081/")
        self.assertEqual(offer.title, "Bastiodon — Abyss Eye (M5) 060/081 (Holo)")

    def test_en_dash_title(self):
        [offer] = search(card("M1S", "メガシンフォニア", "001"))
        self.assertEqual(offer.title, "Tangela – Mega Symphonia (M1S) – 001/063 – Japanese (Normal)")
        self.assertEqual(offer.price, Decimal("0.10"))

    def test_stock_level_from_the_analytics_extension(self):
        [offer] = search(card("M5", "Abyss Eye", "078"))
        self.assertEqual(offer.quantity, 3)

    def test_set_name_matches_when_the_code_differs(self):
        self.assertEqual(len(search(card("sv2a-other", "Pokémon Card 151", "134"))), 1)
        self.assertEqual(search(card("sv2a-other", "Something Else", "134")), [])

    def test_same_number_in_another_set_does_not_match(self):
        self.assertEqual(search(card("M4", "Ninja Spinner", "060")), [])

    def test_prints_at_different_prices_are_read_one_by_one(self):
        ctx = FakeContext()
        offers = search(card("SV2a", "Pokémon Card 151", "032"), ctx=ctx)
        self.assertEqual(sorted((o.price, o.title) for o in offers), [
            (Decimal("0.49"), "Nidoran ♂ — Pokémon Card 151 (SV2a) 032/165 (Pokéball Reverse Holo)"),
            (Decimal("7.49"), "Nidoran ♂ — Pokémon Card 151 (SV2a) 032/165 (Master Ball Reverse Holo)")])
        self.assertTrue(all("attribute_pa_card-type=" in o.url for o in offers))
        self.assertEqual(sum(1 for u in ctx.urls if "?" not in u), 2)

    def test_single_print_variable_product_uses_the_product(self):
        ctx = FakeContext()
        [offer] = search(card("M2a", "MEGA Dream ex", "140"), ctx=ctx)
        self.assertEqual(offer.price, Decimal("0.75"))
        self.assertEqual(offer.title, "Bouffalant — MEGA Dream ex (M2a) 140/193 (Energy Reverse Holo)")
        self.assertTrue(all("?" in u for u in ctx.urls))

    def test_out_of_stock_is_skipped(self):
        products = copy.deepcopy(load_fixture()["products"])
        for p in products:
            p["is_in_stock"] = False
        self.assertEqual(search(card("M5", "Abyss Eye", "060"), ctx=FakeContext(products)), [])


if __name__ == "__main__":
    unittest.main()

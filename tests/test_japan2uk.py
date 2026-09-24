"""Japan2UK plugin tests. The fixture is real products captured from
Japan2UK's Japanese singles and graded collections JSON on 2026-09-23,
trimmed to the fields the plugin reads, so no network is needed."""
import copy
import json
import os
import sys
import unittest
from decimal import Decimal
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import japan2uk  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_products():
    with open(os.path.join(HERE, "fixtures", "japan2uk_products.json"), encoding="utf-8") as f:
        return json.load(f)["products"]


def card(set_id, local_id):
    return MissingCard(f"{set_id}-{local_id}", set_id, "", local_id, "", "Japanese", "ja")


class FakeContext(SearchContext):
    """Serves graded products from the graded collection and the rest from
    the singles collection, a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, products=None):
        super().__init__(japan2uk.PLUGIN)
        products = load_products() if products is None else products
        graded = [p for p in products if "Graded" in p["title"]]
        self.collections = {"pokemon-japanese-cards": [p for p in products if p not in graded],
                            "pokemon-japanese-graded-cards": graded}
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        collection = url.split("/collections/", 1)[1].split("/", 1)[0]
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        return {"products": self.collections[collection][(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return japan2uk.PLUGIN.search_set(list(cards), ctx or FakeContext())


class ParseTests(unittest.TestCase):
    def test_title(self):
        self.assertEqual(japan2uk.parse_title("Pokemon Jolteon Reverse Holo Pokemon 151 sv2a 135/165 "
                                              "Japanese Single Card"),
                         {"number": "135", "set_id": "sv2a"})

    def test_promo_number(self):
        self.assertEqual(japan2uk.parse_title("Pokemon Buddy Buddy Poffin Non Holo Gym Promo Card Pack 9 "
                                              "Promo 237/SV-P Japanese Single Card"),
                         {"number": "237", "set_id": "svp"})

    def test_xy_codes_that_differ_from_tcgdex(self):
        for title, set_id in (
                ("Pokemon Volcanion EX SR Fever Burst Fighter xy11 Bb 055/054 Japanese Single Card", "xy11a"),
                ("Pokemon Magearna EX SR Cruel Traitor xy11 Br 055/054 Japanese Single Card", "xy11b"),
                ("Pokemon Zoroark BREAK Blue Shock xy8-Bb 037/059 Japanese Single Card", "xy8a"),
                ("Pokemon Skarmory EX SR Collection X XY1 062/060 Japanese Single Card", "xy1a"),
                ("Pokemon Maxie's Hidden Ball Trick SR Gaia Volcano XY5 078/070 Japanese Single Card", "xy5a")):
            self.assertEqual(japan2uk.parse_title(title)["set_id"], set_id, title)

    def test_graded_card(self):
        self.assertEqual(japan2uk.parse_title("Pokemon Altaria Holo Dragon Storm sm6a 031/053 "
                                              "Japanese Graded Card PSA 10 #153131669"),
                         {"number": "31", "set_id": "sm6a"})

    def test_titles_without_a_card_number(self):
        for title in ("Pokemon Squirtle 007 Base Set Japanese Graded Card PSA 9 #158689547",
                      "Pokemon 25th Anniversary s8a-P Complete Master Set Sequential PSA 10 Japanese Graded Card Set",
                      "Pokemon Mega Evolution Start Deck 100 Battle Collection mC Japanese Deck"):
            self.assertIsNone(japan2uk.parse_title(title), title)


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_of_both_collections_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(japan2uk, "PAGE_SIZE", 3):
            search(card("SV2a", "135"), ctx=ctx)
            search(card("SV-P", "237"), ctx=ctx)
        # 7 singles at 3 a page = 3 pages; 3 graded = a full page, then an empty one.
        self.assertEqual([(u.split("/collections/")[1].split("/")[0], u.rsplit("page=", 1)[1]) for u in ctx.urls],
                         [("pokemon-japanese-cards", "1"), ("pokemon-japanese-cards", "2"),
                          ("pokemon-japanese-cards", "3"), ("pokemon-japanese-graded-cards", "1"),
                          ("pokemon-japanese-graded-cards", "2")])
        self.assertEqual(len(ctx.state["products"]), 10)


class MatchingTests(unittest.TestCase):
    def test_set_code_and_number(self):
        [offer] = search(card("SV2a", "135"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("1.49"))
        self.assertEqual(offer.currency, "GBP")
        self.assertEqual(offer.condition, "NM")
        self.assertIsNone(offer.grade)
        self.assertTrue(offer.url.startswith("https://www.japan2uk.com/products/"
                                             "pokemon-jolteon-reverse-holo-pokemon-151-sv2a-135-165"))
        self.assertIn("?variant=", offer.url)

    def test_same_number_in_another_set_does_not_match(self):
        self.assertEqual(search(card("SV2a", "136")), [])
        self.assertEqual(search(card("SV3", "135")), [])

    def test_sold_out_listing_is_skipped(self):
        self.assertEqual(search(card("SV2a", "103")), [])

    def test_promo_matches_on_promo_code(self):
        [offer] = search(card("SV-P", "237"))
        self.assertEqual(offer.price, Decimal("1.29"))

    def test_xy_alias(self):
        [offer] = search(card("XY11a", "055"))
        self.assertEqual(offer.price, Decimal("59.99"))
        self.assertEqual(search(card("XY11b", "055")), [])

    def test_graded_copy_is_labelled(self):
        products = copy.deepcopy(load_products())
        altaria = next(p for p in products if "Altaria" in p["title"])
        altaria["variants"][0]["available"] = True
        [offer] = search(card("SM6a", "31"), ctx=FakeContext(products))
        self.assertEqual(offer.grade, "PSA 10")
        self.assertIsNone(offer.condition)


if __name__ == "__main__":
    unittest.main()

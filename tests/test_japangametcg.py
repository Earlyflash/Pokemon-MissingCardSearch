"""Japan Game & TCG Market plugin tests. The fixture is real products
captured from the shop's collection JSON on 2026-09-24, trimmed to the
fields the plugin reads, so no network is needed. The whole shop was sold
out that day, so the tests mark products in stock where they need to."""
import copy
import json
import os
import sys
import unittest
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import japangametcg  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_products(in_stock=True):
    with open(os.path.join(HERE, "fixtures", "japangametcg_products.json"), encoding="utf-8") as f:
        products = json.load(f)["products"]
    for product in products:
        for variant in product["variants"]:
            variant["available"] = in_stock
    return products


def card(set_id, local_id, set_name=""):
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, "", "Japanese", "ja")


class FakeContext(SearchContext):
    """Serves the fixture a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, products=None):
        super().__init__(japangametcg.PLUGIN)
        self.products = load_products() if products is None else products
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        return {"products": self.products[(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return japangametcg.PLUGIN.search_set(list(cards), ctx or FakeContext())


class ParseTests(unittest.TestCase):
    def parse(self, title):
        parsed = japangametcg.parse_title(title)
        return parsed and {k: parsed[k] for k in ("number", "set_id", "tag")}

    def test_title(self):
        self.assertEqual(self.parse("【NM】Mega Dragonite ex SAR 246/193 | Mega Dream ex M2a | Japanese"),
                         {"number": "246", "set_id": "m2a", "tag": "NM"})

    def test_slash_separated_and_mixed_case_codes(self):
        self.assertEqual(self.parse("【NM】Zekrom Ex SAR 169/086 / Black Bolt SV11B / Japanese")["set_id"], "sv11b")
        self.assertEqual(self.parse("【LP】Marshadow AR 069/063 / Mega Brave m1L / Japanese")["set_id"], "m1l")
        self.assertEqual(self.parse("【NM】Morpeko ex SAR 115/081 Abyss Eye M5 | Japanese")["set_id"], "m5")

    def test_promo_number(self):
        self.assertEqual(self.parse("【NM】Pikachu 001/SV-P | Scarlet Violet Promo | Japanese"),
                         {"number": "1", "set_id": "svp", "tag": "NM"})

    def test_title_without_a_set_code_keeps_its_set_names(self):
        parsed = japangametcg.parse_title("【MP】Karen's Tyranitar 090/141 1st ED | VS Series | Japanese Pokemon Card")
        self.assertIsNone(parsed["set_id"])
        self.assertIn("vs series", parsed["set_names"])

    def test_titles_that_are_not_one_japanese_card(self):
        for title in ("【LP~NM】Pokemon Card AR Bulk 12 Set | Inferno X M2 | Japanese",
                      "【PSA 10】Charizard 212/172 Mewtwo 221/172 SAR | VSTAR Universe | Japanese",
                      "【PSA 10】Captain Pikachu 0709/09 | Pokemon Exclusive Gem Pack | Chinese",
                      "【LP】Aerodactyl No.142 Holo | Neo Revelation | Japanese Vintage Pokemon",
                      "Pokemon Card Game | Abyss Eye M5 | Booster Box Japanese"):
            self.assertIsNone(japangametcg.parse_title(title), title)

    def test_condition_and_grade(self):
        for tag, cond, grade in (("NM", "NM", None), ("NM-", "LP", None), ("LP~NM", "LP", None),
                                 ("MP", "MP", None), ("Unopened", "NM", None), ("PSA 10", None, "PSA 10")):
            parsed = {"tag": tag}
            self.assertEqual((japangametcg.condition(parsed), japangametcg.grade(parsed)), (cond, grade), tag)


class SearchTests(unittest.TestCase):
    def test_matches_on_set_code_and_number(self):
        offers = search(card("M2a", "246"))
        self.assertEqual(len(offers), 2)
        self.assertEqual({o.condition for o in offers}, {"NM", "LP"})
        o = offers[0]
        self.assertEqual((o.marketplace, o.card_id, o.currency, o.match),
                         ("japangametcg", "M2a-246", "USD", MATCH_EXACT))
        self.assertIsInstance(o.price, Decimal)
        self.assertTrue(o.url.startswith(japangametcg.SHOP + "/products/"))
        self.assertIn("?variant=", o.url)

    def test_other_formats(self):
        self.assertEqual(len(search(card("SV11B", "169"))), 1)
        self.assertEqual(len(search(card("M1L", "069"))), 1)
        self.assertEqual(len(search(card("M5", "115"))), 1)
        self.assertEqual(len(search(card("SV-P", "001"))), 1)

    def test_graded_copy_is_labelled(self):
        [o] = search(card("S12a", "183"))
        self.assertEqual((o.grade, o.condition), ("PSA 10", None))

    def test_set_name_match_when_the_title_has_no_code(self):
        self.assertEqual(len(search(card("VS1", "090", "VS Series"))), 1)
        self.assertEqual(search(card("VS1", "090", "Neo Revelation")), [])

    def test_wrong_set_or_number_doesnt_match(self):
        self.assertEqual(search(card("M2", "246"), card("M2a", "245"), card("S12a", "212")), [])

    def test_sold_out_listings_are_skipped(self):
        self.assertEqual(search(card("M2a", "246"), ctx=FakeContext(load_products(in_stock=False))), [])

    def test_catalogue_is_read_once_and_paged(self):
        products = load_products()
        many = [dict(copy.deepcopy(products[0]), id=i) for i in range(japangametcg.PAGE_SIZE)] + products
        ctx = FakeContext(many)
        search(card("M2a", "246"), ctx=ctx)
        search(card("S12a", "183"), ctx=ctx)
        self.assertEqual(len(ctx.urls), 2)
        self.assertTrue(all("/collections/all/products.json" in u for u in ctx.urls))


if __name__ == "__main__":
    unittest.main()

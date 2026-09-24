"""Cosmic Collectables plugin tests. The fixture is real products captured
from the shop's Japanese singles collection JSON on 2026-09-24, trimmed to
the fields the plugin reads, so no network is needed."""
import copy
import json
import os
import sys
import unittest
from decimal import Decimal
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import cosmiccollectables  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_products():
    with open(os.path.join(HERE, "fixtures", "cosmiccollectables_products.json"), encoding="utf-8") as f:
        return json.load(f)["products"]


def in_stock(*title_parts):
    """The fixture with the products whose titles contain these parts put
    back in stock."""
    products = copy.deepcopy(load_products())
    for product in products:
        if any(part in product["title"] for part in title_parts):
            product["variants"][0]["available"] = True
    return products


def card(set_id, local_id):
    return MissingCard(f"{set_id}-{local_id}", set_id, "", local_id, "", "Japanese", "ja")


class FakeContext(SearchContext):
    """Serves the products a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, products=None):
        super().__init__(cosmiccollectables.PLUGIN)
        self.products = load_products() if products is None else products
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        return {"products": self.products[(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return cosmiccollectables.PLUGIN.search_set(list(cards), ctx or FakeContext())


class ParseTests(unittest.TestCase):
    def parse(self, title):
        return cosmiccollectables.parse_title(title)

    def test_code_in_brackets(self):
        self.assertEqual(self.parse("SWORD AND SHIELD, Shiny Star V (s4a) - 216/190 : Cinderace (Shiny Vault)"),
                         {"number": "216", "set_id": "s4a", "set_name": "shiny star v"})

    def test_bare_code(self):
        self.assertEqual(self.parse("Heat Wave Arena sv9a - 003/063 : Yanmega ex (Half Art) *Japanese*"),
                         {"number": "3", "set_id": "sv9a", "set_name": "heat wave arena"})
        self.assertEqual(self.parse("Mega Brave m1L- 029/063 : Mega Lucario ex (Half Art) *Japanese*")["set_id"],
                         "m1l")

    def test_japanese_prefix_is_not_part_of_the_set_name(self):
        parsed = self.parse("Japanese - SCARLET & VIOLET, Shiny Treasure ex (sv4a) - 135/190 : Noivern ex (Half Art)")
        self.assertEqual(parsed["set_name"], "shiny treasure ex")

    def test_promo_number(self):
        self.assertEqual(self.parse("Japanese - SCARLET & VIOLET - Promos 069/SV-P : Glaceon (Holo)"),
                         {"number": "69", "set_id": "svp", "set_name": None})

    def test_set_name_only(self):
        self.assertEqual(self.parse("PSA - Pokemon - Sword & Shield, Vmax Climax - 232/184 : Sylveon GX (Full Art) "
                                    "- PSA 10"),
                         {"number": "232", "set_id": None, "set_name": "vmax climax"})

    def test_code_as_series(self):
        self.assertEqual(self.parse("PSA - Pokemon - sv5K, Wild Force - 093/071 : Gouging Fire ex (Full Art) - PSA 10"),
                         {"number": "93", "set_id": "sv5k", "set_name": "wild force"})

    def test_pokedex_number_never_parses(self):
        self.assertIsNone(self.parse("SWORD AND SHIELD, Leaders' Stadium - No. 112 : Rhydon (Holo)"))


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(cosmiccollectables, "PAGE_SIZE", 5):
            search(card("S4a", "216"), ctx=ctx)
            search(card("SV9a", "003"), ctx=ctx)
        # 12 products at 5 a page = 3 pages, the last one short.
        self.assertEqual([u.rsplit("page=", 1)[1] for u in ctx.urls], ["1", "2", "3"])
        self.assertEqual(len(ctx.state["products"]), 12)

    def test_set_name_borrows_the_code_other_titles_give_it(self):
        products = dict((p["title"], parsed) for p, parsed in cosmiccollectables.PLUGIN.catalogue(FakeContext()))
        sylveon = next(v for k, v in products.items() if "Sylveon" in k)
        self.assertEqual(sylveon["set_id"], "s8b")

    def test_a_name_with_two_codes_is_not_learned(self):
        codes = cosmiccollectables.learn_codes([{"set_id": "s6h", "set_name": "silver lance"},
                                                {"set_id": "s6k", "set_name": "silver lance"},
                                                {"set_id": "s4a", "set_name": "shiny star v"}])
        self.assertEqual(codes, {"shiny star v": "s4a"})


class MatchingTests(unittest.TestCase):
    def test_set_code_and_number(self):
        [offer] = search(card("S4a", "216"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("6.15"))
        self.assertEqual(offer.currency, "GBP")
        self.assertEqual(offer.condition, "NM")
        self.assertIsNone(offer.grade)
        self.assertTrue(offer.url.startswith("https://cosmiccollectables.co.uk/products/"))
        self.assertIn("?variant=", offer.url)

    def test_same_number_in_another_set_does_not_match(self):
        self.assertEqual(search(card("S4a", "003")), [])
        self.assertEqual(search(card("S8a", "216")), [])

    def test_bare_code(self):
        [offer] = search(card("SV9a", "003"))
        self.assertEqual(offer.price, Decimal("1.25"))

    def test_sold_out_listing_is_skipped(self):
        self.assertEqual(search(card("M1L", "029")), [])
        [offer] = search(card("M1L", "029"), ctx=FakeContext(in_stock("Mega Lucario")))
        self.assertEqual(offer.price, Decimal("1.25"))

    def test_promo_matches_on_promo_code(self):
        [offer] = search(card("SV-P", "069"), ctx=FakeContext(in_stock("Glaceon")))
        self.assertEqual(offer.price, Decimal("18.25"))

    def test_graded_copy_is_labelled(self):
        [offer] = search(card("S8b", "232"), ctx=FakeContext(in_stock("Sylveon")))
        self.assertEqual(offer.grade, "PSA 10")
        self.assertIsNone(offer.condition)

    def test_korean_copies_never_match(self):
        ctx = FakeContext(in_stock("Litleo", "Pikachu"))
        self.assertEqual(search(card("M1S", "066"), ctx=ctx), [])
        self.assertEqual(search(card("SV2a", "173"), ctx=ctx), [])


if __name__ == "__main__":
    unittest.main()

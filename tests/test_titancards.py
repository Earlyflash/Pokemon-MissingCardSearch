"""Titan Cards plugin tests. The fixture is real products captured from the
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
from marketplaces import titancards  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_fixture():
    with open(os.path.join(HERE, "fixtures", "titancards_products.json"), encoding="utf-8") as f:
        return json.load(f)["products"]


def card(set_id, set_name, local_id, lang="en"):
    language = {"en": "English", "ja": "Japanese"}[lang]
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, "", language, lang)


class FakeContext(SearchContext):
    """Serves the fixture a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, products=None):
        super().__init__(titancards.PLUGIN)
        self.products = load_fixture() if products is None else products
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        return {"products": self.products[(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return titancards.PLUGIN.search_set(list(cards), ctx or FakeContext())


class ParseTests(unittest.TestCase):
    def test_title_with_series_prefix(self):
        parsed = titancards.parse_title("Charizard ex 054/091 Double Rare Pokemon Card (SV 4.5 Paldean Fates)")
        self.assertEqual(parsed, {"number": "54", "name": "Charizard ex", "set_id": "sv04.5",
                                  "set_name": "paldean fates", "lang": "en", "promo": False})

    def test_set_aliases(self):
        self.assertEqual(titancards.set_key("Mega Evolution Base Set ME01")[0], "me01")
        self.assertEqual(titancards.set_key("Sword & Shield")[0], "swsh1")
        self.assertEqual(titancards.set_key("SWSH04 Vivid Voltage")[0], "swsh4")
        self.assertEqual(titancards.set_key("Scarlet & Violet White Flare")[0], "sv10.5w")

    def test_unknown_set_keeps_its_name(self):
        self.assertEqual(titancards.set_key("Mega Evolution Some Future Set"), (None, "some future set"))

    def test_promo(self):
        parsed = titancards.parse_title("Spark SWSH226 Full Art Pokemon GO Promo Card (SWSH Promo Series)")
        self.assertEqual((parsed["number"], parsed["set_id"], parsed["promo"]), ("SWSH226", "swshp", True))

    def test_gallery_number_picks_the_gallery_set(self):
        parsed = titancards.parse_title("Kingdra TG05/TG30 Trainer Gallery Pokemon Card (SWSH Brilliant Stars)")
        self.assertEqual((parsed["number"], parsed["set_id"]), ("TG5", "swsh9tg"))

    def test_japanese(self):
        parsed = titancards.parse_title(
            "Donphan 019/025 Ultra Rare Japanese Pokemon Card (Celebrations Classic Collection JP)")
        self.assertEqual((parsed["lang"], parsed["set_id"]), ("ja", "S8a-P"))

    def test_bundle_is_not_a_card(self):
        self.assertIsNone(titancards.parse_title("Pokemon 5x Random ULTRA RARE Cards Bundle (V/VSTAR/VMAX or EX Cards)"))
        self.assertIsNone(titancards.parse_title("Tapu Koko GX 135/145 Full Art Guardians Rising Rare"))


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(titancards, "PAGE_SIZE", 4):
            search(card("me05", "Pitch Black", "119"), ctx=ctx)
            search(card("sv04.5", "Paldean Fates", "054"), ctx=ctx)
        # 9 products at 4 a page: stops on the short third page.
        self.assertEqual([u.rsplit("page=", 1)[1] for u in ctx.urls], ["1", "2", "3"])
        self.assertEqual(len(ctx.state["products"]), 9)


class MatchingTests(unittest.TestCase):
    def test_set_and_number(self):
        [offer] = search(card("me05", "Pitch Black", "119"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("39.99"))
        self.assertEqual(offer.currency, "GBP")
        self.assertEqual(offer.condition, "NM")
        self.assertIsNone(offer.grade)
        self.assertEqual(offer.url, "https://titancards.co.uk/products/"
                                    "gwynn-119-084-special-illustration-rare-pokemon-card-mega-evolution-pitch-black"
                                    "?variant=62926541029706")
        self.assertEqual(offer.title, "Gwynn 119/084 Special Illustration Rare Pokemon Card (Mega Evolution Pitch Black)")

    def test_same_number_in_another_set_does_not_match(self):
        self.assertEqual(search(card("me04", "Chaos Rising", "119")), [])
        # Zacian V is Celebrations 016/025; the Japanese pack's 016 is Claydol.
        [offer] = search(card("cel25", "Celebrations", "016"))
        self.assertIn("zacianv", offer.url)

    def test_base_set_aliases_match(self):
        self.assertTrue(search(card("swsh1", "Sword & Shield", "199")))
        self.assertTrue(search(card("me01", "Mega Evolution", "003")))

    def test_promo_matches_with_or_without_its_code(self):
        self.assertTrue(search(card("swshp", "SWSH Black Star Promos", "SWSH226")))
        self.assertTrue(search(card("swshp", "SWSH Black Star Promos", "226")))

    def test_language_must_match(self):
        self.assertEqual(search(card("S8a-P", "25th Anniversary Promo", "019", lang="en")), [])
        [offer] = search(card("S8a-P", "25th Anniversary Promo", "019", lang="ja"))
        self.assertIn("donphan", offer.url)

    def test_unknown_set_matches_by_name(self):
        products = copy.deepcopy(load_fixture())
        products[0]["title"] = "Gwynn 119/084 Special Illustration Rare Pokemon Card (Mega Evolution Some Future Set)"
        [offer] = search(card("me09", "Some Future Set", "119"), ctx=FakeContext(products))
        self.assertIn("gwynn-119", offer.url)

    def test_sold_out_listing_is_skipped(self):
        products = copy.deepcopy(load_fixture())
        products[0]["variants"][0]["available"] = False
        self.assertEqual(search(card("me05", "Pitch Black", "119"), ctx=FakeContext(products)), [])

    def test_no_condition_without_near_mint(self):
        products = copy.deepcopy(load_fixture())
        products[0]["body_html"] = "<p>Gwynn</p>"
        [offer] = search(card("me05", "Pitch Black", "119"), ctx=FakeContext(products))
        self.assertIsNone(offer.condition)


if __name__ == "__main__":
    unittest.main()

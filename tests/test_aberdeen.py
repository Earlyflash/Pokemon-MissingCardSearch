"""Aberdeen Collectables plugin tests. The fixture is real products captured
from the shop's raw singles and graded cards collections JSON on 2026-09-24,
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
from marketplaces import aberdeen  # noqa: E402
from marketplaces.base import MATCH_EXACT, MATCH_UNCERTAIN, MissingCard, SearchContext  # noqa: E402


def load_collections():
    with open(os.path.join(HERE, "fixtures", "aberdeen_products.json"), encoding="utf-8") as f:
        return json.load(f)


def card(set_id, local_id, lang="ja", set_name="", name="", name_en=None):
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, name, "", lang, name_en=name_en)


class FakeContext(SearchContext):
    """Serves each collection a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, collections=None):
        super().__init__(aberdeen.PLUGIN)
        self.collections = load_collections() if collections is None else collections
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        collection = url.split("/collections/", 1)[1].split("/", 1)[0]
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        return {"products": self.collections[collection][(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return aberdeen.PLUGIN.search_set(list(cards), ctx or FakeContext())


def product(title):
    products = load_collections()
    return next(p for p in products["raw-singles"] + products["graded-cards-1"] if p["title"].startswith(title))


class ParseTests(unittest.TestCase):
    def test_body_code_and_number(self):
        parsed = aberdeen.parse(product("Pikachu ex #044/193"))
        self.assertEqual((parsed["lang"], parsed["numbers"], parsed["set_ids"], parsed["conflict"]),
                         ("ja", {"44"}, {"m2a"}, False))

    def test_promo_code(self):
        self.assertEqual(aberdeen.parse(product("Psyduck #262/SV-P"))["set_ids"], {"svp"})

    def test_english_codes_map_to_tcgdex_ids(self):
        for title, set_id in (("Lugia BREAK", "xy10"), ("Dewgong", "me02"), ("Dubwool V", "swshp"),
                              ("Reuniclus", "svp")):
            self.assertEqual(aberdeen.parse(product(title))["set_ids"], {set_id}, title)

    def test_graded_set_name_without_code(self):
        self.assertEqual(aberdeen.parse(product("Aerodactyl GX"))["set_ids"], {"sm11"})
        self.assertEqual(aberdeen.parse(product("Gyarados GX"))["set_ids"], {"smp"})

    def test_stale_set_tag_is_ignored_when_the_body_names_a_set(self):
        # Tagged "Set: Tag All Stars" and "Set: White Flare", left over from other products.
        self.assertEqual(aberdeen.parse(product("Blaziken EX"))["set_ids"], {"xyp"})
        self.assertEqual(aberdeen.parse(product("Inteleon"))["set_ids"], {"m1s"})

    def test_self_contradicting_listings(self):
        iron_treads = aberdeen.parse(product("Iron Treads ex"))   # title 103/078, body 101/078
        self.assertEqual((iron_treads["numbers"], iron_treads["conflict"]), ({"101", "103"}, True))
        snover = aberdeen.parse(product("Snover"))                 # "MEGA Symphonia (m15)"
        self.assertEqual((snover["set_ids"], snover["conflict"]), ({"m15", "m1s"}, True))

    def test_loose_era_code_is_not_a_conflict(self):
        self.assertFalse(aberdeen.parse(product("Sceptile"))["conflict"])  # "ADV Expansion Pack (ADV)"

    def test_pokedex_number_is_not_a_card_number(self):
        self.assertEqual(aberdeen.parse(product("Ursaring"))["numbers"], set())


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_of_both_collections_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(aberdeen, "PAGE_SIZE", 10):
            search(card("M2a", "044"), ctx=ctx)
            search(card("SV8a", "093"), ctx=ctx)
        # 17 raw singles at 10 a page = 2 pages; 3 graded = 1 page.
        self.assertEqual([(u.split("/collections/")[1].split("/")[0], u.rsplit("page=", 1)[1]) for u in ctx.urls],
                         [("raw-singles", "1"), ("raw-singles", "2"), ("graded-cards-1", "1")])
        self.assertEqual(len(ctx.state["products"]), 20)


class MatchingTests(unittest.TestCase):
    def test_set_code_and_number(self):
        [offer] = search(card("M2a", "044", name_en="Pikachu ex"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("4.00"))
        self.assertEqual(offer.currency, "GBP")
        self.assertEqual(offer.condition, "LP")
        self.assertIsNone(offer.grade)
        self.assertTrue(offer.url.startswith("https://aberdeencollectables.co.uk/products/"
                                             "pikachu-ex-m2a-044-193-japanese-0164?variant="))

    def test_language_must_match(self):
        self.assertEqual(search(card("M2a", "044", lang="en")), [])
        [offer] = search(card("SV2a", "179", lang="ko"))
        self.assertEqual(offer.price, Decimal("10.56"))
        self.assertEqual(search(card("SV2a", "179")), [])

    def test_same_number_in_another_set_does_not_match(self):
        self.assertEqual(search(card("M2", "044")), [])
        self.assertEqual(search(card("M2a", "045")), [])

    def test_english_promo(self):
        [offer] = search(card("swshp", "SWSH049", lang="en", name="Dubwool V"))
        self.assertEqual(offer.match, MATCH_EXACT)

    def test_set_name_equal_to_the_cards_own_matches(self):
        [offer] = search(card("xx9", "044", set_name="MEGA Dream ex"))
        self.assertEqual(offer.price, Decimal("4.00"))

    def test_name_that_disagrees_is_uncertain(self):
        # SV8 110 is Feebas; the shop lists it as Okidogi.
        [offer] = search(card("SV8", "110", name_en="Feebas"))
        self.assertEqual(offer.match, MATCH_UNCERTAIN)
        [offer] = search(card("SV8", "110"))  # no English name to check against
        self.assertEqual(offer.match, MATCH_EXACT)

    def test_self_contradicting_listing_is_uncertain_under_both_numbers(self):
        offers = search(card("SV1V", "101"), card("SV1V", "103"))
        self.assertEqual(sorted((o.card_id, o.match) for o in offers),
                         [("SV1V-101", MATCH_UNCERTAIN), ("SV1V-103", MATCH_UNCERTAIN)])

    def test_finish_other_than_holo_is_in_the_title(self):
        [offer] = search(card("SM10", "076"))
        self.assertTrue(offer.title.endswith("(Reverse Holo)"), offer.title)

    def test_graded_copy_is_labelled(self):
        [offer] = search(card("SV8a", "093"))
        self.assertEqual(offer.grade, "PSA 10")
        self.assertIsNone(offer.condition)
        [offer] = search(card("SM11", "100"))
        self.assertEqual(offer.grade, "GetGraded 9.5")

    def test_sold_out_copy_is_skipped(self):
        collections = copy.deepcopy(load_collections())
        next(p for p in collections["raw-singles"] if p["title"].startswith("Pikachu ex"))["variants"][0]["available"] = False
        self.assertEqual(search(card("M2a", "044"), ctx=FakeContext(collections)), [])


if __name__ == "__main__":
    unittest.main()

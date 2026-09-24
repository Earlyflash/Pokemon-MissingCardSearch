"""Total Cards plugin tests. The fixture is real products captured from the
shop's Japanese singles collection JSON on 2026-09-23, trimmed to the fields
the plugin reads, so no network is needed."""
import json
import os
import sys
import unittest
from decimal import Decimal
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import totalcards  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_products():
    with open(os.path.join(HERE, "fixtures", "totalcards_products.json"), encoding="utf-8") as f:
        return json.load(f)["products"]


def card(set_id, set_name, local_id):
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, "", "Japanese", "ja")


def product(title, *variants, options=("Title",)):
    """A one-off product; each variant is (title, price, available)."""
    return {"id": title, "handle": "p", "title": title, "tags": ["Pokemon"],
            "options": [{"name": n} for n in options],
            "variants": [{"id": i, "title": t, "option1": t.split(" / ")[0],
                          "option2": t.split(" / ")[1] if " / " in t else None,
                          "price": price, "available": available}
                         for i, (t, price, available) in enumerate(variants)]}


class FakeContext(SearchContext):
    """Serves the fixture a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, products=None):
        super().__init__(totalcards.PLUGIN)
        self.products = load_products() if products is None else products
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        return {"products": self.products[(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return totalcards.PLUGIN.search_set(list(cards), ctx or FakeContext())


class ParseTests(unittest.TestCase):
    def test_title(self):
        self.assertEqual(totalcards.parse_title("Pokemon - Pokémon Card 151 - Charizard ex - 201/210"),
                         {"number": "201", "sets": ["pokemon card 151"], "promo": None})

    def test_series_before_set(self):
        parsed = totalcards.parse_title("Pokemon - Mega Evolution - Nihil Zero - Mega Starmie ex - 111/080")
        self.assertEqual(parsed["sets"], ["mega evolution", "nihil zero"])

    def test_number_sharing_the_card_name_segment(self):
        parsed = totalcards.parse_title("Pokemon - Terastal Festival ex - Archaludon 113 (Reverse Holo Master Ball)")
        self.assertEqual((parsed["number"], parsed["sets"]), ("113", ["terastal festival ex"]))

    def test_stray_condition_and_language_words_after_the_number(self):
        for title in ("Pokemon - White Flare - Whimsicott ex - 005/086 Japanese",
                      "Pokemon - Terastal Festival ex - Dragapult ex - 120/187 NM",
                      "Pokemon - Stellar Miracle - Galvantula ex - 033/102 NM NM / Japanese NM / Japanese"):
            self.assertIsNotNone(totalcards.parse_title(title), title)

    def test_promo_number(self):
        parsed = totalcards.parse_title("Pokemon - Sword & Shield Promos - Pikachu 124/S-P")
        self.assertEqual((parsed["number"], parsed["promo"]), ("124", "S-P"))

    def test_unrecognised_title(self):
        self.assertIsNone(totalcards.parse_title("Pokemon - Pokémon GO Enhanced Expansion Pack - Fighting Energy S10b FIG"))


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(totalcards, "PAGE_SIZE", 3):
            search(card("SV2a", "Pokémon Card 151", "201"), ctx=ctx)
            search(card("SV9a", "Heat Wave Arena", "083"), ctx=ctx)
        # 8 products at 3 a page: the third page is short, so paging stops there.
        self.assertEqual([u.rsplit("page=", 1)[1] for u in ctx.urls], ["1", "2", "3"])
        self.assertEqual(len(ctx.state["products"]), 8)


class MatchingTests(unittest.TestCase):
    def test_set_name_and_number(self):
        [offer] = search(card("SV2a", "ポケモンカード151", "201"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("375.95"))
        self.assertEqual(offer.currency, "GBP")
        self.assertIsNone(offer.condition)
        self.assertIsNone(offer.grade)
        self.assertEqual(offer.url, "https://totalcards.net/products/"
                                    "pokemon-pokemon-card-151-charizard-ex-201-211?variant=56019393413501")

    def test_shop_translation_of_a_set_name(self):
        [offer] = search(card("SV9a", "Heat Wave Arena", "083"))
        self.assertIn("hot-air-arena-judge", offer.url)

    def test_series_prefixed_set(self):
        [offer] = search(card("M3", "Nihil Zero", "111"))
        self.assertEqual(offer.price, Decimal("39.95"))

    def test_older_set(self):
        [offer] = search(card("neo2", "遺跡をこえて...", "18"))
        self.assertEqual((offer.condition, offer.title),
                         ("LP", "Pokemon - Crossing the Ruins... - Kabutops - 18/56 (Unlimited / EX)"))

    def test_set_name_equal_to_the_cards_own_matches(self):
        products = [product("Pokemon - Brand New Set - Pikachu - 001/100", ("Default Title", "1.00", True))]
        [offer] = search(card("XX1", "Brand New Set", "001"), ctx=FakeContext(products))
        self.assertEqual(offer.price, Decimal("1.00"))

    def test_only_japanese_copies_in_stock(self):
        [offer] = search(card("SV11W", "White Flare", "005"))
        self.assertEqual((offer.price, offer.condition), (Decimal("1.45"), "NM"))
        self.assertTrue(offer.title.endswith("(Japanese / NM)"))
        # Espeon V is in stock only as a Korean copy.
        self.assertEqual(search(card("S6a", "Eevee Heroes", "081")), [])

    def test_one_offer_per_condition_in_stock(self):
        offers = search(card("SV8a", "Terastal Festival ex", "120"))
        self.assertEqual(sorted((o.condition, o.price) for o in offers),
                         [("MP", Decimal("1.45")), ("NM", Decimal("1.95"))])

    def test_same_number_in_another_set_does_not_match(self):
        self.assertEqual(search(card("SV3a", "Raging Surf", "201")), [])

    def test_promo_matches_on_promo_code(self):
        products = [product("Pokemon - Sword & Shield Promos - Pikachu 124/S-P", ("Default Title", "19.95", True))]
        [offer] = search(card("S-P", "Sword & Shield Promos", "124"), ctx=FakeContext(products))
        self.assertEqual(offer.price, Decimal("19.95"))
        self.assertEqual(search(card("S1W", "Sword", "124"), ctx=FakeContext(products)), [])

    def test_graded_copy_is_labelled(self):
        products = [
            product("Pokemon - Gaia Volcano - Primal Groudon EX 074/070 (PSA 9)", ("Default Title", "119.95", True)),
            product("Pokémon - Cyber Judge - Excadrill 079/071 (ACE Art Label 10 Graded Slab)",
                    ("Default Title", "30.00", True)),
            product("Pokemon - Tag All Stars - Pikachu & Zekrom GX - 162/173", ("PSA / 10", "99.00", True),
                    options=("Grading Company", "Grade")),
        ]
        ctx = FakeContext(products)
        self.assertEqual(search(card("XY5a", "Gaia Volcano", "074"), ctx=ctx)[0].grade, "PSA 9")
        self.assertEqual(search(card("SV5M", "Cyber Judge", "079"), ctx=ctx)[0].grade, "ACE 10")
        self.assertEqual(search(card("SM12a", "Tag All Stars", "162"), ctx=ctx)[0].grade, "PSA 10")

    def test_card_name_is_not_read_as_a_grade(self):
        products = [product("Pokemon - Tag All Stars - TAG TEAM GX 001/173", ("Default Title", "1.00", True))]
        [offer] = search(card("SM12a", "Tag All Stars", "001"), ctx=FakeContext(products))
        self.assertIsNone(offer.grade)


if __name__ == "__main__":
    unittest.main()

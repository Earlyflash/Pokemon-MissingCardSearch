"""CardCargo plugin tests. The fixture is real products captured from
CardCargo's Japanese singles collection JSON on 2026-09-22, trimmed to the
fields the plugin reads, so no network is needed."""
import json
import os
import sys
import unittest
from decimal import Decimal
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import cardcargo  # noqa: E402
from marketplaces.base import MATCH_EXACT, MissingCard, SearchContext  # noqa: E402


def load_products():
    with open(os.path.join(HERE, "fixtures", "cardcargo_products.json"), encoding="utf-8") as f:
        return json.load(f)["products"]


def card(set_id, set_name, local_id):
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, "", "Japanese", "ja")


class FakeContext(SearchContext):
    """Serves the fixture a `limit` at a time, like Shopify's page= paging."""

    def __init__(self, products=None):
        super().__init__(cardcargo.PLUGIN)
        self.products = load_products() if products is None else products
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
        limit, page = int(query["limit"]), int(query["page"])
        return {"products": self.products[(page - 1) * limit:page * limit]}


def search(*cards, ctx=None):
    return cardcargo.PLUGIN.search_set(list(cards), ctx or FakeContext())


class ParseTests(unittest.TestCase):
    def test_title(self):
        self.assertEqual(cardcargo.parse_title("(#173/165) Pikachu - Holo [SV2a: Pokemon Card 151 (JPN)]"),
                         {"number": "173", "promo": None, "set_name": "SV2a: Pokemon Card 151",
                          "set_code": "SV2a", "set_short": "Pokemon Card 151"})

    def test_promo_number(self):
        parsed = cardcargo.parse_title("(#152/S-P) Crobat V - Normal [Sword & Shield (JPN)]")
        self.assertEqual((parsed["number"], parsed["promo"]), ("152", "S-P"))

    def test_pokedex_number_is_not_a_card_number(self):
        self.assertIsNone(cardcargo.parse_title("(#NO. 008) Wartortle - Normal [Southern Islands (JPN)]")["number"])

    def test_unrecognised_title(self):
        self.assertIsNone(cardcargo.parse_title("Booster Box"))


class CatalogueTests(unittest.TestCase):
    def test_reads_every_page_once_per_run(self):
        ctx = FakeContext()
        with mock.patch.object(cardcargo, "PAGE_SIZE", 4):
            search(card("M2a", "MEGA Dream ex", "218"), ctx=ctx)
            search(card("SV7", "Stellar Miracle", "112"), ctx=ctx)
        # 10 products at 4 a page: the third page is short, so paging stops there.
        self.assertEqual([u.rsplit("page=", 1)[1] for u in ctx.urls], ["1", "2", "3"])
        self.assertEqual(len(ctx.state["products"]), 10)


class MatchingTests(unittest.TestCase):
    def test_set_name_and_number(self):
        [offer] = search(card("M2a", "MEGA Dream ex", "218"))
        self.assertEqual(offer.match, MATCH_EXACT)
        self.assertEqual(offer.price, Decimal("1.49"))
        self.assertEqual(offer.currency, "GBP")
        self.assertEqual(offer.condition, "NM")
        self.assertIsNone(offer.grade)
        self.assertEqual(offer.url, "https://cardcargo.com/products/"
                                    "218-193-counter-gain-holo-mega-dream-ex-jpn?variant=54904386847059")

    def test_sold_out_listing_is_skipped(self):
        self.assertEqual(search(card("M2a", "MEGA Dream ex", "240")), [])

    def test_one_offer_per_copy_in_stock(self):
        offers = search(card("SV7", "Stellar Miracle", "112"))
        self.assertEqual(sorted((o.condition, o.price) for o in offers),
                         [("LP", Decimal("3.39")), ("NM", Decimal("3.99"))])
        self.assertEqual(len({o.url for o in offers}), 2)

    def test_set_name_without_or_with_code_prefix(self):
        for set_name in ("Pokemon Card 151", "Pokémon Card 151", "SV2a: Pokemon Card 151"):
            [offer] = search(card("sv2a-other", set_name, "173"))
            self.assertEqual(offer.price, Decimal("25.99"), set_name)

    def test_code_prefix_matches_set_id(self):
        [offer] = search(card("SV11B", "Black Bolt (JP)", "161"))
        self.assertEqual(offer.price, Decimal("4.99"))

    def test_same_number_in_another_set_does_not_match(self):
        offers = search(card("SV4a", "Shiny Treasure ex", "209"))
        self.assertEqual([o.url.split("?")[0].rsplit("/", 1)[1] for o in offers],
                         ["209-190-rellor-holo-shiny-treasure-ex-jpn"])
        self.assertEqual(search(card("SV4a", "Shiny Treasure ex", "210")), [])

    def test_promo_matches_on_promo_code(self):
        [offer] = search(card("S-P", "Sword & Shield Promos", "152"))
        self.assertEqual(offer.price, Decimal("3.99"))
        # ...and not the main Sword & Shield set's card 152.
        self.assertEqual(search(card("S1W", "Sword & Shield", "152")), [])

    def test_pokedex_numbered_listing_never_matches(self):
        self.assertEqual(search(card("si1", "Southern Islands", "8")), [])

    def test_graded_copy_is_labelled(self):
        products = [{"id": 1, "handle": "psa", "title": "(#218/193) Counter Gain - Holo PSA 10 [MEGA Dream ex (JPN)]",
                     "variants": [{"id": 2, "title": "Near mint / NM: #1", "option1": "Near mint",
                                   "price": "20.00", "available": True}]}]
        [offer] = search(card("M2a", "MEGA Dream ex", "218"), ctx=FakeContext(products))
        self.assertEqual(offer.grade, "PSA 10")


if __name__ == "__main__":
    unittest.main()

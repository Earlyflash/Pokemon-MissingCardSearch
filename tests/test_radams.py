"""Radam's Poké Stop plugin tests. The fixtures are real product blocks from
the shop's "shop all" pages and one product page's variant list, captured on
2026-09-23 and trimmed (images and buttons removed), so no network is
needed."""
import os
import sys
import unittest
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from marketplaces import radams  # noqa: E402
from marketplaces.base import MATCH_EXACT, MATCH_UNCERTAIN, MissingCard, SearchContext  # noqa: E402


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return f.read()


def card(set_id, set_name, local_id, name="", lang="ja"):
    return MissingCard(f"{set_id}-{local_id}", set_id, set_name, local_id, name, "", lang)


class FakeContext(SearchContext):
    """Serves page 1, then page 2 via its "next" link; every product page
    is the captured Kakuna page (five conditions, two in stock)."""

    def __init__(self, product_page=None):
        super().__init__(radams.PLUGIN)
        self.product_page = fixture("radams_product.html") if product_page is None else product_page
        self.urls = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.urls.append(url)
        if url == radams.LIST_URL:
            return fixture("radams_page1.html")
        if url == radams.SHOP + "/shop-all?offset=200":
            return fixture("radams_page2.html")
        if isinstance(self.product_page, Exception):
            raise self.product_page
        return self.product_page


def search(*cards, ctx=None):
    return radams.PLUGIN.search_set(list(cards), ctx or FakeContext())


class ParseTests(unittest.TestCase):
    def test_listing_page(self):
        rows, nxt = radams.parse_listing_page(fixture("radams_page1.html"))
        self.assertEqual(nxt, radams.SHOP + "/shop-all?offset=200")
        rapidash = next(r for r in rows if r["title"].startswith("Rapidash"))
        self.assertEqual(rapidash["title"], "Rapidash Poke Ball #078 sv2a 151 JPN")
        self.assertEqual(rapidash["price"], Decimal("1.30"))
        self.assertFalse(rapidash["sold_out"])
        self.assertEqual(rapidash["tags"]["set"], ["sv2a-151-jpn"])
        self.assertEqual(rapidash["tags"]["language"], ["japanese"])
        self.assertTrue(rapidash["url"].startswith(radams.SHOP + "/shop-all/p/rapidash-"))
        self.assertTrue(next(r for r in rows if r["title"].startswith("Pikachu"))["sold_out"])

    def test_last_page_has_no_next(self):
        self.assertIsNone(radams.parse_listing_page(fixture("radams_page2.html"))[1])

    def test_list_price(self):
        self.assertEqual(radams.list_price("Sale Price: £3.50 Original Price: £3.80"), Decimal("3.50"))
        self.assertEqual(radams.list_price("from £1.00"), Decimal("1.00"))
        self.assertEqual(radams.list_price("£1,200.00"), Decimal("1200.00"))
        self.assertIsNone(radams.list_price(""))

    def test_title_number(self):
        self.assertEqual(radams.title_number("Rapidash Poke Ball #078 sv2a 151 JPN"), ("78", None))
        self.assertEqual(radams.title_number("Pikachu 12/30 #034 30th Celebrations 30C"), ("34", None))
        self.assertEqual(radams.title_number("Snorlax GX #001/SM-p JPN Promo"), ("1", "SM-p"))
        self.assertEqual(radams.title_number("Manaphy #SWSH275 Promo"), ("SWSH275", None))
        self.assertEqual(radams.title_number("Iron Treads ex 058/078 sv1v Violet ex JPN"), ("58", None))
        self.assertEqual(radams.title_number("Dedenne Old Maid Playing Card JPN"), (None, None))

    def test_url_numbers(self):
        self.assertEqual(radams.url_numbers(radams.SHOP + "/shop-all/p/pikachu-272/s-p-pokemon-go-promo"),
                         ({"272"}, {"s-p"}))
        self.assertEqual(radams.url_numbers(radams.SHOP + "/shop-all/p/latias-gg20/gg70-crown-zenith"),
                         ({"GG20"}, set()))

    def test_title_set_code_beats_a_wrong_tag(self):
        rows, _ = radams.parse_listing_page(fixture("radams_page2.html"))
        garbodor = radams.prepare(next(r for r in rows if r["title"].startswith("Garbodor")))
        self.assertEqual(garbodor["tags"]["set"], ["sv11b-white-flare-jpn"])
        self.assertEqual(garbodor["codes"], {"sv11w"})

    def test_variants(self):
        found = radams.variants(fixture("radams_product.html"))
        self.assertEqual([(v["condition_text"], v["condition"], v["quantity"], v["price"]) for v in found], [
            ("NM", "NM", 0, Decimal("3.00")), ("EX", "LP", 1, Decimal("2.50")),
            ("LP", "LP", 1, Decimal("2.00")), ("MP", "MP", 0, Decimal("1.50")),
            ("HP", "HP", 0, Decimal("1.00"))])
        self.assertEqual(radams.variants("<html>no product</html>"), [])

    def test_name_agrees_allows_typos(self):
        self.assertTrue(radams.name_agrees("Amoonguss ex", "Amoongus ex #011 Journey Together DR"))
        self.assertTrue(radams.name_agrees("Mew ex", "Mew ex #151 151 DR"))
        self.assertFalse(radams.name_agrees("Hisuian Avalugg", "Galarian Mr Rime V #048 Astral Radiance UR"))


class MatchTests(unittest.TestCase):
    def test_japanese_set_code(self):
        offers = search(card("SV2a", "ポケモンカード151", "078"))
        self.assertEqual(len(offers), 2)  # EX and LP copies in stock
        self.assertEqual({o.match for o in offers}, {MATCH_EXACT})
        self.assertIn("Rapidash", offers[0].title)

    def test_needs_same_language(self):
        self.assertEqual(search(card("SV2a", "151", "078", lang="ko")), [])

    def test_needs_same_number(self):
        self.assertEqual(search(card("SV2a", "ポケモンカード151", "079")), [])

    def test_japanese_promo_code(self):
        offers = search(card("SM-P", "サン＆ムーン プロモ", "001"))
        self.assertEqual([o.title.split(" (")[0] for o in offers], ["Snorlax GX #001/SM-p JPN Promo"] * 2)
        self.assertEqual(search(card("S-P", "ソード＆シールド プロモ", "001")), [])

    def test_traditional_chinese(self):
        self.assertEqual(len(search(card("SC1a", "", "164", lang="zh-tw"))), 2)

    def test_english_set_name(self):
        offers = search(card("sv03.5", "151", "151", "Mew ex", "en"))
        self.assertEqual({o.match for o in offers}, {MATCH_EXACT})
        self.assertEqual(search(card("sv03.5", "Paldea Evolved", "151", "Mew ex", "en")), [])

    def test_english_set_name_with_trailing_plural_or_typo(self):
        self.assertTrue(search(card("me01", "Mega Evolution", "086", "Mega Absol ex", "en")))
        self.assertEqual({o.match for o in search(card("sv09", "Journey Together", "011", "Amoonguss ex", "en"))},
                         {MATCH_EXACT})

    def test_gallery_number_matches_sub_set(self):
        self.assertTrue(search(card("swsh12.5gg", "Crown Zenith Galarian Gallery", "GG20", "Latias", "en")))

    def test_english_promo_prefix(self):
        self.assertTrue(search(card("swshp", "SWSH Black Star Promos", "SWSH275", "Manaphy", "en")))

    def test_english_name_disagreement_is_uncertain(self):
        offers = search(card("sv03.5", "151", "151", "Charizard ex", "en"))
        self.assertEqual({o.match for o in offers}, {MATCH_UNCERTAIN})

    def test_url_number_disagreement_is_uncertain(self):
        offers = search(card("M5", "アビスアイ", "053"))
        self.assertEqual({o.match for o in offers}, {MATCH_UNCERTAIN})

    def test_sold_out_products_are_skipped(self):
        ctx = FakeContext()
        self.assertEqual(search(card("30th", "30th Celebration", "033", "Pikachu", "en"), ctx=ctx), [])
        self.assertEqual(len(ctx.urls), 2)  # never opened the product page

    def test_graded(self):
        offers = search(card("SV10", "ロケット団の栄光", "109"))
        self.assertEqual({o.grade for o in offers}, {"PSA 10"})

    def test_product_page_failure_falls_back_to_list_price(self):
        offers = search(card("SV2a", "151", "078"), ctx=FakeContext(OSError("timeout")))
        self.assertEqual([(o.price, o.condition, o.quantity) for o in offers], [(Decimal("1.30"), None, None)])

    def test_nothing_in_stock_on_product_page(self):
        page = fixture("radams_product.html").replace('"quantity":1', '"quantity":0')
        self.assertEqual(search(card("SV2a", "151", "078"), ctx=FakeContext(page)), [])

    def test_offer_fields(self):
        offer = min(search(card("SV2a", "151", "078")), key=lambda o: o.price)
        self.assertEqual((offer.marketplace, offer.currency, offer.price, offer.condition, offer.quantity),
                         ("radams", "GBP", Decimal("2.00"), "LP", 1))
        self.assertTrue(offer.title.endswith("(Condition: LP)"))
        self.assertTrue(offer.url.startswith(radams.SHOP + "/shop-all/p/"))

    def test_catalogue_is_read_once_per_run(self):
        ctx = FakeContext()
        radams.PLUGIN.search_set([card("SV2a", "151", "078")], ctx)
        radams.PLUGIN.search_set([card("SV10", "", "109")], ctx)
        self.assertEqual(ctx.urls.count(radams.LIST_URL), 1)


if __name__ == "__main__":
    unittest.main()

import os
import sys
import tempfile
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import spares_page  # noqa: E402


def spare(card_id, number, count, name="X", en="", finish="", base="yes", paid="", pic=""):
    return {"Set Name": "Abyss Eye", "TCGdex Set": "M5", "Language": "Japanese",
            "Card Number": number, "Card Name": name, "TCGdex Card ID": card_id,
            "English Name": en, "Finish": finish, "Spares": str(count), "Purchase Price": paid,
            "Base Set": base, "Picture": pic, "Large Picture": ""}


PRICES = {"asof": "24 Nov, 23:34", "currency": "GBP", "cards": {
    "M5-115": ["21.30", "https://pulsetcg.io/card/morpeko", "UK market price: Morpeko ex"],
    "M5-049": ["1.06", "p49", "UK market price"],
    "M5-001": ["0.10", "p1", "UK market price last seen 20 Nov (none sent today)"],
    "M5-090": ["0.50", "p90", "UK market price"],
    "M5-002": ["5.00", "p2", ""],
}}


class SpareRowsTests(unittest.TestCase):
    def test_value_and_bulk(self):
        rows = {r["TCGdex Card ID"] + r["Finish"]: r for r in spares_page.spare_rows([
            spare("M5-115", "115", 1, base=""), spare("M5-049", "049", 9),
            spare("M5-001", "001", 8), spare("M5-090", "090", 2, base=""),
            spare("M5-030", "030", 3), spare("M5-002", "002", 1, finish="Energy Reverse Holofoil"),
        ], PRICES["cards"])}
        self.assertEqual(rows["M5-049"]["value"], Decimal("9.54"))
        # Base cards at £1 or under, or unpriced, are bulk; secret rares never are.
        self.assertEqual({k for k, r in rows.items() if r["bulk"]},
                         {"M5-001", "M5-030", "M5-002Energy Reverse Holofoil"})
        self.assertTrue(rows["M5-001"]["old"])
        # A reverse holo isn't given the standard print's price.
        self.assertIsNone(rows["M5-002Energy Reverse Holofoil"]["each"])


class PageTests(unittest.TestCase):
    def page(self, spares, prices=PRICES, **kw):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "spares.html")
            n = spares_page.write_spares_html(spares, prices, path, **kw)
            with open(path, encoding="utf-8") as f:
                return n, f.read()

    def test_lists_every_spare_most_valuable_first(self):
        n, page = self.page([spare("M5-049", "049", 9, "マーイーカ", "Inkay", paid="0.40"),
                             spare("M5-115", "115", 1, "モルペコex", "Morpeko ex", base="",
                                   pic="https://img/m5-115/medium"),
                             spare("M5-030", "030", 3)], overview="missing_cards.html")
        self.assertEqual(n, 3)
        self.assertLess(page.index('data-name="morpeko ex"'), page.index('data-name="inkay"'))
        self.assertLess(page.index('data-name="inkay"'), page.index('data-name="x"'))
        self.assertIn('<span class="tag secret">Secret rare</span>', page)
        self.assertIn('<tr class="bulk"', page)
        self.assertIn('<td class="num value">£9.54</td><td class="num paid">0.40</td>', page)
        self.assertIn('href="https://pulsetcg.io/card/morpeko"', page)
        self.assertIn('data-line="Morpeko ex (モルペコex) · Abyss Eye M5 #115 · Japanese · ×1 · ~£21.30 each"',
                      page)
        self.assertIn('<img data-src="https://img/m5-115/medium"', page)
        # Totals: everything, and without the bulk.
        self.assertIn("<b>13</b><span>spare copies</span>", page)
        self.assertIn("<b>£30.84</b><span>worth in all</span>", page)
        self.assertIn('<option value="Abyss Eye (Japanese)">', page)
        self.assertIn('<a href="missing_cards.html">Missing cards</a>', page)
        self.assertIn("from 24 Nov, 23:34", page)

    def test_min_price_sets_what_counts_as_bulk(self):
        _, page = self.page([spare("M5-049", "049", 9)], min_price=2)
        self.assertIn('<tr class="bulk"', page)
        self.assertIn("worth £2.00 or less", page)

    def test_no_prices_yet(self):
        _, page = self.page([spare("M5-049", "049", 2)], prices={"cards": {}})
        self.assertIn('<span class="none" title="No PulseAPI price">–</span>', page)
        self.assertIn("1 card(s) have no price", page)

    def test_build_needs_a_spares_csv(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(spares_page.build(os.path.join(d, "none.csv"),
                                                os.path.join(d, "p.csv"), os.path.join(d, "s.html")))


if __name__ == "__main__":
    unittest.main()

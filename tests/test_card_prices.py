import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import card_prices  # noqa: E402

COLS = ["Set Name", "TCGdex Set", "Language", "Card Number", "Card Name", "TCGdex Card ID",
        "Marketplace", "Price (GBP)", "Listed Price", "Listed Currency", "Condition", "Grade",
        "Seller", "Quantity", "Match", "Listing Title", "URL"]


def write_rows(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, COLS, restval="")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def row(card_id, shop, gbp, listed=None, currency="GBP", title="", url="u", **kw):
    return {"TCGdex Card ID": card_id, "Marketplace": shop, "Price (GBP)": gbp,
            "Listed Price": listed or gbp, "Listed Currency": currency, "Listing Title": title,
            "URL": url, **kw}


class ReadCardPricesTests(unittest.TestCase):
    def prices(self, offers, guide):
        with tempfile.TemporaryDirectory() as d:
            o, g = os.path.join(d, "offers.csv"), os.path.join(d, "guide.csv")
            write_rows(o, offers)
            write_rows(g, guide)
            return card_prices.read_card_prices(o, g, names={"nmd": "NMD"})

    def test_pulse_cardmarket_trend_and_lowest_listing(self):
        p = self.prices(
            offers=[row("M2a-236", "nmd", "29.49", url="n1", Condition="NM"),
                    row("M2a-236", "deckd", "31.00", url="d1"),
                    row("M2a-236", "nmd", "12.00", url="slab", Grade="PSA 9")],
            guide=[row("M2a-236", "pulseapi", "19.25", url="p"),
                   row("M2a-236", "cardmarket", "14.62", "17.5", "EUR",
                       "Cheapest copy on Cardmarket (any language or condition); trend EUR 19.97",
                       url="cm")])
        card = p["cards"]["M2a-236"]
        self.assertEqual(card["pulse"], ["19.25", "p"])
        # Trend converted at the run's rate: 19.97 * 14.62 / 17.5.
        self.assertEqual(card["cm"][:2], ["16.68", "cm"])
        self.assertIn("Trend EUR 19.97", card["cm"][2])
        # The graded slab doesn't count as the lowest price.
        self.assertEqual(card["low"], ["29.49", "n1", "NMD · NM · cheapest of 2 listings"])
        self.assertEqual(p["currency"], "GBP")
        self.assertIsNotNone(p["asof"])

    def test_cardmarket_without_trend_uses_from_price(self):
        p = self.prices(offers=[], guide=[row("M1L-064", "cardmarket", "3.34", "4", "EUR", "x")])
        self.assertEqual(p["cards"]["M1L-064"]["cm"][0], "3.34")
        self.assertNotIn("low", p["cards"]["M1L-064"])

    def test_no_csvs(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(card_prices.prices_for(d),
                             {"asof": None, "currency": "GBP", "cards": {}})


class RefreshHtmlTests(unittest.TestCase):
    def test_swaps_only_the_prices_block(self):
        old = {"cards": {"A-1": {"low": ["1.00", "u", ""]}}}
        new = {"cards": {"A-1": {"low": ["0.50", "</script>", ""]}}}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "missing_cards.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"<p>before</p>{card_prices.prices_block(old)}<script>x()</script>")
            self.assertTrue(card_prices.refresh_html(path, new))
            with open(path, encoding="utf-8") as f:
                page = f.read()
            self.assertTrue(page.startswith("<p>before</p>"))
            self.assertTrue(page.endswith("<script>x()</script>"))
            start = page.index('json">') + len('json">')
            self.assertEqual(json.loads(page[start:page.index("</script>", start)]), new)
            self.assertFalse(card_prices.refresh_html(os.path.join(d, "none.html"), new))


if __name__ == "__main__":
    unittest.main()

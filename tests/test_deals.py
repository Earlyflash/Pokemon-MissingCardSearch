import io
import os
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import deals  # noqa: E402

PRICES = {"asof": "16 Nov, 03:40", "currency": "GBP", "cards": {
    # 11% under the Cardmarket trend.
    "M2a-237": {"cm": ["75.32", "cm", ""], "low": ["66.99", "https://cc/237", "CardCargo · NM"]},
    # Under PulseAPI only.
    "M6-108": {"pulse": ["31.79", "p", ""], "cm": ["29.00", "cm", ""],
               "low": ["30.00", "https://n/108", "NMD"]},
    # Over both.
    "M2a-236": {"pulse": ["19.25", "p", ""], "cm": ["16.68", "cm", ""],
                "low": ["24.99", "https://cc/236", "CardCargo"]},
    # Under, but on order.
    "M5-110": {"cm": ["10.00", "cm", ""], "low": ["5.00", "https://x/110", "Shop"]},
    # Under, but no longer missing.
    "M1L-001": {"cm": ["10.00", "cm", ""], "low": ["5.00", "https://x/1", "Shop"]},
}}
MISSING = {cid: {"Set Name": "Set", "Card Number": cid.split("-")[1], "English Name": cid,
                 "Card Name": ""} for cid in ("M2a-237", "M6-108", "M2a-236", "M5-110")}
ORDERED = {"m5-110": ["Cardmarket"]}


class FindDealsTests(unittest.TestCase):
    def test_cheapest_listing_under_a_market_price(self):
        found = deals.find_deals(PRICES, MISSING, ORDERED)
        self.assertEqual([d["card_id"] for d in found], ["M2a-237", "M6-108"])
        mewtwo, raikou = found
        self.assertEqual((mewtwo["price"], mewtwo["shop"]), (Decimal("66.99"), "CardCargo"))
        self.assertAlmostEqual(mewtwo["under_cm"], 11.06, places=1)
        self.assertIsNone(mewtwo["under_pulse"])
        self.assertIsNone(raikou["under_cm"])
        self.assertGreater(raikou["under_pulse"], 5)

    def test_min_under(self):
        found = deals.find_deals(PRICES, MISSING, ORDERED, min_under=10)
        self.assertEqual([d["card_id"] for d in found], ["M2a-237"])


class AlertTests(unittest.TestCase):
    def run_main(self, d, *flags):
        prices = PRICES

        out = io.StringIO()
        with redirect_stdout(out), \
                unittest.mock.patch("deals.read_card_prices", lambda o, g: prices), \
                unittest.mock.patch("deals.read_missing", lambda p: MISSING), \
                unittest.mock.patch("deals.read_ordered_shops", lambda p: ORDERED):
            deals.main(["--alerted", os.path.join(d, "alerted.txt"), *flags])
        return out.getvalue()

    def test_only_new_deals_are_emailed_again(self):
        with tempfile.TemporaryDirectory() as d:
            mail = os.path.join(d, "mail")
            self.run_main(d, "--email-dir", mail, "--only-new")
            with open(os.path.join(mail, "subject.txt"), encoding="utf-8") as f:
                self.assertEqual(f.read(), "Pokémon deals: 2 card(s) under market price")
            with open(os.path.join(mail, "body.html"), encoding="utf-8") as f:
                body = f.read()
            self.assertIn('<a href="https://cc/237"><b>£66.99</b></a>', body)
            self.assertNotIn("<span", body)
            self.run_main(d, "--mark-sent")
            os.remove(os.path.join(mail, "body.html"))
            out = self.run_main(d, "--email-dir", mail, "--only-new")
            self.assertIn("Nothing new to email.", out)
            self.assertFalse(os.path.exists(os.path.join(mail, "body.html")))

    def test_a_cheaper_price_is_new(self):
        d = {"card_id": "M2a-237", "url": "https://cc/237", "price": Decimal("60.00")}
        alerted = {"M2a-237 | 66.99 | https://cc/237": "2026-11-16"}
        self.assertTrue(deals.is_new(d, alerted))
        self.assertFalse(deals.is_new(dict(d, price=Decimal("70.00")), alerted))


if __name__ == "__main__":
    unittest.main()

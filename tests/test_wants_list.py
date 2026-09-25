"""wants_list.py tests: no network, TCGdex and Cardmarket's files are faked."""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import wants_list  # noqa: E402
from marketplaces import cardmarket  # noqa: E402

MISSING = {"sets": [
    {"set_id": "sv10", "set_name": "Destined Rivals", "language": "English", "tcgdex_lang": "en",
     "missing": [
         {"card_id": "sv10-001", "set_id": "sv10", "set_name": "Destined Rivals", "local_id": "001",
          "name": "Dragapult ex", "language": "English", "tcgdex_lang": "en"},
         {"card_id": "sv10-002", "set_id": "sv10", "set_name": "Destined Rivals", "local_id": "002",
          "name": "Switch", "language": "English", "tcgdex_lang": "en"},
         {"card_id": "sv10-003", "set_id": "sv10", "set_name": "Destined Rivals", "local_id": "003",
          "name": "Charizard ex", "language": "English", "tcgdex_lang": "en"},
         {"card_id": "sv10-004", "set_id": "sv10", "set_name": "Destined Rivals", "local_id": "004",
          "name": "Nobody", "language": "English", "tcgdex_lang": "en"},
     ]},
    {"set_id": "M2a", "set_name": "MEGA Dream ex", "language": "Japanese", "tcgdex_lang": "ja",
     "missing": [
         {"card_id": "M2a-001", "set_id": "M2a", "set_name": "MEGA Dream ex", "local_id": "001",
          "name": "スイッチ", "name_en": "Switch", "language": "Japanese", "tcgdex_lang": "ja"},
         {"card_id": "M2a-002", "set_id": "M2a", "set_name": "MEGA Dream ex", "local_id": "002",
          "name": "ピカチュウ", "name_en": "Pikachu", "language": "Japanese", "tcgdex_lang": "ja"},
     ]},
]}

TCGDEX = {
    "sv10-001": 1, "sv10-002": 2, "sv10-003": 3, "M2a-001": 2, "M2a-002": 5,
}
PRODUCTS = {"products": [
    {"idProduct": 1, "name": "Dragapult ex [Jet Headbutt | Phantom Dive]", "idExpansion": 10},
    {"idProduct": 2, "name": "Switch", "idExpansion": 20},
    {"idProduct": 3, "name": "Charizard ex [Infernal Reign | Burning Darkness]", "idExpansion": 10},
    {"idProduct": 4, "name": "Charizard ex [Infernal Reign | Burning Darkness]", "idExpansion": 10},
    {"idProduct": 5, "name": "Pikachu [Thunder Jolt]", "idExpansion": 20},
]}
NONSINGLES = {"products": [
    {"idProduct": 90, "name": "Destined Rivals Booster", "idExpansion": 10},
    {"idProduct": 91, "name": "Destined Rivals Booster Box", "idExpansion": 10},
    {"idProduct": 92, "name": "Destined Rivals Enhanced Booster", "idExpansion": 10},
    {"idProduct": 93, "name": "Some Tin", "idExpansion": 20},
]}
DR, MD = "(Destined Rivals)", "(MEGA Dream ex)"
GUIDE = {"priceGuides": [
    {"idProduct": 1, "low": 1.0, "trend": 5.0},
    {"idProduct": 2, "low": 0.02, "trend": 0.1},
    {"idProduct": 3, "low": 30.0, "trend": 60.0},
    {"idProduct": 5, "low": 2.0, "trend": None},
]}


def fake_fetch(self, url, headers=None, as_json=False, timeout=30):
    if url == wants_list.PRODUCTS_URL:
        return PRODUCTS
    if url == wants_list.NONSINGLES_URL:
        return NONSINGLES
    if url == cardmarket.PRICE_GUIDE_URL:
        return GUIDE
    card_id = url.rsplit("/", 1)[1]
    if card_id in TCGDEX:
        return {"id": card_id, "pricing": {"cardmarket": {"idProduct": TCGDEX[card_id]}}}
    return {"id": card_id, "pricing": None}


class TestHelpers(unittest.TestCase):
    def test_deck_list_name_flattens_attacks(self):
        self.assertEqual(wants_list.deck_list_name("Dragapult ex [Jet Headbutt | Phantom Dive]"),
                         "Dragapult ex Jet Headbutt Phantom Dive")
        self.assertEqual(wants_list.deck_list_name("Lillie's Determination"), "Lillie's Determination")
        self.assertEqual(wants_list.deck_list_name("Magnezone [Magnetic Draw | Lost Burn] Prime"),
                         "Magnezone Magnetic Draw Lost Burn Prime")
        self.assertEqual(wants_list.deck_list_name(None), "")

    def test_expansion_names_from_boosters(self):
        self.assertEqual(wants_list.expansion_names([
            {"name": "Mega Evolution Enhanced Booster", "idExpansion": 1},
            {"name": "Mega Evolution Booster", "idExpansion": 1},
            {"name": "Abyss Eye Booster Box Case", "idExpansion": 2},
            {"name": "Abyss Eye Booster Box", "idExpansion": 2},
            {"name": "Lillie's Support Gift Box", "idExpansion": 3},
        ]), {1: "Mega Evolution", 2: "Abyss Eye"})

    def test_versions_in_product_order(self):
        products = [{"idProduct": 851251, "name": "Mega Absol ex", "idExpansion": 6209},
                    {"idProduct": 851157, "name": "Mega Absol ex", "idExpansion": 6209},
                    {"idProduct": 851232, "name": "Mega Absol ex", "idExpansion": 6209},
                    {"idProduct": 900000, "name": "Mega Absol ex", "idExpansion": 1},
                    {"idProduct": 5, "name": "Switch", "idExpansion": 6209}]
        self.assertEqual(wants_list.versions(products), {851157: 1, 851232: 2, 851251: 3})

    def test_line_name(self):
        product = {"name": "Mega Absol ex [Terminal Period | Claw of Darkness]"}
        self.assertEqual(wants_list.deck_list_line_name(product, 2, "Mega Evolution"),
                         "Mega Absol ex Terminal Period Claw of Darkness (V.2) (Mega Evolution)")
        self.assertEqual(wants_list.deck_list_line_name({"name": "Switch"}, None, "X"), "Switch (X)")
        self.assertIsNone(wants_list.deck_list_line_name(None, None, "X"))

    def test_card_price_converts_and_falls_back_to_low(self):
        rate = Decimal("0.85")
        self.assertEqual(wants_list.card_price({"trend": 10, "low": 1}, "trend", rate), Decimal("8.50"))
        self.assertEqual(wants_list.card_price({"trend": None, "low": 2}, "trend", rate), Decimal("1.70"))
        self.assertIsNone(wants_list.card_price({}, "trend", rate))
        self.assertIsNone(wants_list.card_price({"trend": 1}, "trend", None))

    def test_keep(self):
        row = {"price": Decimal("25")}
        self.assertFalse(wants_list.keep(row, max_price=Decimal("20")))
        self.assertTrue(wants_list.keep(row, min_price=Decimal("20")))
        self.assertTrue(wants_list.keep({"price": None}, max_price=Decimal("20")))
        self.assertFalse(wants_list.keep({"price": None}, skip_unpriced=True))

    def test_lines_merge_identical_names(self):
        rows = [{"line_name": "Switch"}, {"line_name": "Pikachu"}, {"line_name": "Switch"}]
        self.assertEqual(wants_list.deck_list_lines(rows), ["2 Switch", "1 Pikachu"])


@patch.object(wants_list.SearchContext, "fetch", fake_fetch)
@patch.object(wants_list, "exchange_rates", lambda cur, target: {"EUR": Decimal("0.5"), target: 1})
class TestMain(unittest.TestCase):
    def run_main(self, *args):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "missing.json")
            with open(src, "w", encoding="utf-8") as f:
                json.dump(MISSING, f)
            out = os.path.join(d, "wants.txt")
            with redirect_stdout(io.StringIO()) as log:
                wants_list.main([src, "--out", out, "--no-cache", *args])
            with open(out, encoding="utf-8") as f:
                lines = f.read().splitlines()
            with open(os.path.join(d, "wants.csv"), encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            return lines, rows, log.getvalue()

    def test_every_card_with_a_product(self):
        lines, rows, log = self.run_main()
        self.assertEqual(lines, [f"1 Dragapult ex Jet Headbutt Phantom Dive {DR}", f"1 Switch {DR}",
                                 f"1 Charizard ex Infernal Reign Burning Darkness (V.1) {DR}",
                                 f"1 Switch {MD}", f"1 Pikachu Thunder Jolt {MD}"])
        self.assertEqual(len(rows), 6)
        nobody = next(r for r in rows if r["TCGdex Card ID"] == "sv10-004")
        self.assertEqual(nobody["In List"], "no Cardmarket product")
        self.assertIn("Destined Rivals #004 Nobody", log)

    def test_max_price_leaves_out_expensive_cards(self):
        lines, rows, _ = self.run_main("--max-price", "20")
        self.assertFalse([line for line in lines if "Charizard" in line])
        charizard = next(r for r in rows if r["TCGdex Card ID"] == "sv10-003")
        self.assertEqual((charizard["Price (GBP)"], charizard["In List"]), ("30.00", "price filter"))

    def test_low_price_field(self):
        lines, _, _ = self.run_main("--max-price", "1", "--price", "low")
        self.assertEqual(lines, [f"1 Dragapult ex Jet Headbutt Phantom Dive {DR}", f"1 Switch {DR}",
                                 f"1 Switch {MD}", f"1 Pikachu Thunder Jolt {MD}"])

    def test_set_filter(self):
        lines, _, _ = self.run_main("--set", "M2a")
        self.assertEqual(lines, [f"1 Switch {MD}", f"1 Pikachu Thunder Jolt {MD}"])


if __name__ == "__main__":
    unittest.main()

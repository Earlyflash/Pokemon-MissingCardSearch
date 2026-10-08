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
    {"idProduct": 6, "name": "Psyduck [Damp | Ram]", "idExpansion": 20},
    {"idProduct": 8, "name": "Psyduck [Damp | Ram]", "idExpansion": 6409},
    {"idProduct": 7, "name": "Psyduck [Damp | Ram]", "idExpansion": 6409},
]}
# M2a-032 Psyduck: a normal print and two reverse holos, each its own product.
PSYDUCK = {"id": "M2a-032", "pricing": {"cardmarket": {"idProduct": 6}}, "variants_detailed": [
    {"type": "normal", "thirdParty": {"cardmarket": 6}},
    {"type": "reverse", "foil": "energy", "thirdParty": {"cardmarket": 7}},
    {"type": "reverse", "foil": "loveball", "thirdParty": {"cardmarket": 8}},
]}
MISSING_PRINTS = {"sets": [
    {"set_id": "M2a", "set_name": "MEGA Dream ex", "language": "Japanese", "tcgdex_lang": "ja",
     "missing": [
         {"card_id": "M2a-032", "set_id": "M2a", "set_name": "MEGA Dream ex", "local_id": "032",
          "name": "コダック", "name_en": "Psyduck", "language": "Japanese", "tcgdex_lang": "ja",
          "finish": finish} for finish in ("normal", "energy", "ball")]},
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


def fake_fetch(self, url, headers=None, as_json=False, timeout=30, max_age=None):
    if url == wants_list.PRODUCTS_URL:
        return PRODUCTS
    if url == wants_list.NONSINGLES_URL:
        return NONSINGLES
    if url == cardmarket.PRICE_GUIDE_URL:
        return GUIDE
    card_id = url.rsplit("/", 1)[1]
    if card_id == "M2a-032":
        return PSYDUCK
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
        ]), {1: "Mega Evolution", 2: "Abyss Eye", 6409: "MEGA Dream ex: Additionals"})

    def test_versions_in_product_order(self):
        products = [{"idProduct": 851251, "name": "Mega Absol ex", "idExpansion": 6209},
                    {"idProduct": 851157, "name": "Mega Absol ex", "idExpansion": 6209},
                    {"idProduct": 851232, "name": "Mega Absol ex", "idExpansion": 6209},
                    {"idProduct": 900000, "name": "Mega Absol ex", "idExpansion": 1},
                    {"idProduct": 5, "name": "Switch", "idExpansion": 6209}]
        self.assertEqual(wants_list.versions(products), {851157: 1, 851232: 2, 851251: 3})

    def test_m6a_uses_the_japanese_product(self):
        from marketplaces.base import MissingCard
        products = [
            {"idProduct": 907765, "name": "Exeggcute [Hypnosis]", "idExpansion": 6602, "idMetacard": 1},
            {"idProduct": 908179, "name": "Exeggcute [Hypnosis]", "idExpansion": 6603, "idMetacard": 1},
        ]
        nonsingles = [{"name": "30th Celebration JP Booster", "idExpansion": 6602},
                      {"name": "30th Celebration Simplified Chinese Booster", "idExpansion": 6603}]
        c = MissingCard("M6a-001", "M6a", "30th Celebration", "001", "タマタマ", "Japanese", "ja")
        [row] = wants_list.build_rows([({"set_name": "30th Celebration"}, [c])],
                                      {("M6a-001", None): 908179}, products, nonsingles,
                                      {907765: {"trend": 0.08}}, "trend", Decimal("1"))
        self.assertEqual((row["id_product"], row["line_name"], row["price"]),
                         (907765, "Exeggcute Hypnosis (30th Celebration JP)", Decimal("0.08")))

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
        reverse = {"trend": 0, "low": 3, "trend-holo": 4, "low-holo": 3}
        self.assertEqual(wants_list.card_price(reverse, "trend", rate, reverse=True), Decimal("3.40"))
        self.assertEqual(wants_list.card_price(reverse, "avg", rate, reverse=True), Decimal("2.55"))

    def test_keep(self):
        row = {"price": Decimal("25")}
        self.assertFalse(wants_list.keep(row, max_price=Decimal("20")))
        self.assertTrue(wants_list.keep(row, min_price=Decimal("20")))
        self.assertTrue(wants_list.keep({"price": None}, max_price=Decimal("20")))
        self.assertFalse(wants_list.keep({"price": None}, skip_unpriced=True))

    def test_read_ordered(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ordered.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("me01-161\n2 Switch (Black Bolt)\nBlaine's Quiz #1 (Gym Heroes)\n# note\n")
            ids, names = wants_list.read_ordered(path)
            self.assertEqual(wants_list.read_ordered(os.path.join(d, "none.txt")), (set(), {}))
        self.assertEqual(ids, {"me01-161"})
        self.assertEqual(names, {"switch (black bolt)": 2, "blaine's quiz #1 (gym heroes)": 1})

    def test_lines_merge_identical_names(self):
        rows = [{"line_name": "Switch"}, {"line_name": "Pikachu"}, {"line_name": "Switch"},
                {"line_name": "Poké Pad"}, {"line_name": "air Balloon"}]
        self.assertEqual(wants_list.deck_list_lines(rows),
                         ["1 air Balloon", "1 Pikachu", "1 Poké Pad", "2 Switch"])

    def test_set_list_paths(self):
        rows = [{"set_id": "sv10", "language": "English", "set_name": "Destined Rivals"},
                {"set_id": "sv10", "language": "English", "set_name": "Destined Rivals"},
                {"set_id": "me01", "language": "English", "set_name": "Mega Evolution"},
                {"set_id": "me01", "language": "German", "set_name": "Mega Evolution"},
                {"set_id": "M2a", "language": "Japanese", "set_name": "MEGA Dream ex: Pokémon"},
                {"set_id": "SV9", "language": "Japanese", "set_name": "バトルパートナーズ"}]
        self.assertEqual(wants_list.set_list_paths(rows, os.path.join("d", "wants.txt")), {
            ("sv10", "English"): os.path.join("d", "wants_destined-rivals.txt"),
            ("me01", "English"): os.path.join("d", "wants_mega-evolution-english.txt"),
            ("me01", "German"): os.path.join("d", "wants_mega-evolution-german.txt"),
            ("M2a", "Japanese"): os.path.join("d", "wants_mega-dream-ex-pokemon.txt"),
            ("SV9", "Japanese"): os.path.join("d", "wants_sv9.txt"),
        })


@patch.object(wants_list.SearchContext, "fetch", fake_fetch)
@patch.object(wants_list, "exchange_rates", lambda cur, target: {"EUR": Decimal("0.5"), target: 1})
class TestMain(unittest.TestCase):
    def run_main(self, *args, missing=MISSING):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "missing.json")
            with open(src, "w", encoding="utf-8") as f:
                json.dump(missing, f)
            out = os.path.join(d, "wants.txt")
            with redirect_stdout(io.StringIO()) as log:
                wants_list.main([src, "--out", out, "--no-cache",
                                 "--ordered", os.path.join(d, "none.txt"), *args])
            with open(out, encoding="utf-8") as f:
                lines = f.read().splitlines()
            with open(os.path.join(d, "wants.csv"), encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            return lines, rows, log.getvalue()

    def test_every_card_with_a_product(self):
        lines, rows, log = self.run_main()
        self.assertEqual(lines, [f"1 Charizard ex Infernal Reign Burning Darkness (V.1) {DR}",
                                 f"1 Dragapult ex Jet Headbutt Phantom Dive {DR}",
                                 f"1 Pikachu Thunder Jolt {MD}", f"1 Switch {DR}", f"1 Switch {MD}"])
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
        self.assertEqual(lines, [f"1 Dragapult ex Jet Headbutt Phantom Dive {DR}",
                                 f"1 Pikachu Thunder Jolt {MD}", f"1 Switch {DR}", f"1 Switch {MD}"])

    def test_already_ordered_cards_are_left_out(self):
        with tempfile.TemporaryDirectory() as d:
            ordered = os.path.join(d, "ordered.txt")
            with open(ordered, "w", encoding="utf-8") as f:
                f.write("# ordered 2026-09-25\nSV10-001\n\n1x Switch (MEGA Dream ex)\n")
            lines, rows, log = self.run_main("--ordered", ordered)
        self.assertEqual(lines, [f"1 Charizard ex Infernal Reign Burning Darkness (V.1) {DR}",
                                 f"1 Pikachu Thunder Jolt {MD}", f"1 Switch {DR}"])
        self.assertEqual(sorted(r["TCGdex Card ID"] for r in rows if r["In List"] == "already ordered"),
                         ["M2a-001", "sv10-001"])
        self.assertIn("2 left out as already ordered", log)

    def test_each_print_gets_its_own_product(self):
        lines, rows, _ = self.run_main(missing=MISSING_PRINTS)
        self.assertEqual(lines, ["1 Psyduck Damp Ram (MEGA Dream ex)",
                                 "1 Psyduck Damp Ram (V.1) (MEGA Dream ex: Additionals)",
                                 "1 Psyduck Damp Ram (V.2) (MEGA Dream ex: Additionals)"])
        self.assertEqual([r["Finish"] for r in rows], ["normal", "energy", "ball"])
        self.assertTrue(rows[2]["Cardmarket Link"].endswith("idProduct=8"))

    def test_additionals_only(self):
        lines, rows, _ = self.run_main("--additionals-only", missing=MISSING_PRINTS)
        self.assertEqual(lines, ["1 Psyduck Damp Ram (V.1) (MEGA Dream ex: Additionals)",
                                 "1 Psyduck Damp Ram (V.2) (MEGA Dream ex: Additionals)"])
        self.assertEqual([r["Finish"] for r in rows], ["energy", "ball"])
        with self.assertRaises(SystemExit) as e:
            self.run_main("--additionals-only")
        self.assertIn("No missing reverse holo prints", str(e.exception))

    def test_exclude_additionals(self):
        lines, rows, _ = self.run_main("--exclude-additionals", missing=MISSING_PRINTS)
        self.assertEqual(lines, ["1 Psyduck Damp Ram (MEGA Dream ex)"])
        self.assertEqual([r["Finish"] for r in rows], ["normal"])
        with self.assertRaises(SystemExit):
            self.run_main("--exclude-additionals", "--additionals-only")

    def test_ordering_one_print_leaves_the_others(self):
        with tempfile.TemporaryDirectory() as d:
            ordered = os.path.join(d, "ordered.txt")
            with open(ordered, "w", encoding="utf-8") as f:
                f.write("M2a-032 energy | 2026-10-01 | Cardmarket\nM2a-032\n")
            lines, _, _ = self.run_main("--ordered", ordered, missing=MISSING_PRINTS)
        self.assertEqual(lines, ["1 Psyduck Damp Ram (V.2) (MEGA Dream ex: Additionals)"])

    def test_set_filter(self):
        lines, _, _ = self.run_main("--set", "M2a")
        self.assertEqual(lines, [f"1 Pikachu Thunder Jolt {MD}", f"1 Switch {MD}"])

    def test_split_sets(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "missing.json")
            with open(src, "w", encoding="utf-8") as f:
                json.dump(MISSING, f)
            with redirect_stdout(io.StringIO()):
                wants_list.main([src, "--out", os.path.join(d, "wants.txt"), "--no-cache",
                                 "--ordered", os.path.join(d, "none.txt"), "--split-sets"])
            files = {}
            for name in sorted(os.listdir(d)):
                if name.endswith(".txt"):
                    with open(os.path.join(d, name), encoding="utf-8") as f:
                        files[name] = f.read().splitlines()
        self.assertEqual(files, {
            "wants_destined-rivals.txt": [
                f"1 Charizard ex Infernal Reign Burning Darkness (V.1) {DR}",
                f"1 Dragapult ex Jet Headbutt Phantom Dive {DR}", f"1 Switch {DR}"],
            "wants_mega-dream-ex.txt": [f"1 Pikachu Thunder Jolt {MD}", f"1 Switch {MD}"],
        })


if __name__ == "__main__":
    unittest.main()

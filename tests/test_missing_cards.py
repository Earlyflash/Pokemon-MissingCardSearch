import csv
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import missing_cards  # noqa: E402

HEADER = ("Product Name,Set Name,Purchase Date,Card Number,Language,Material/Finish,"
          "Grading Company,Grade,Purchase Price,Quantity,Promo Info,Rarity,Notes\n")

JA_LISTING = [{"id": "M2a", "name": "MEGAドリームex"}, {"id": "M5", "name": "アビスアイ"}]
EN_LISTING = [
    {"id": "me01", "name": "Mega Evolution"},
    {"id": "mep", "name": "Mega Evolution Black Star Promos"},
    {"id": "sv08", "name": "Surging Sparks"},
]

M2A_JA = {"id": "M2a", "name": "MEGAドリームex", "cards": [
    {"id": "M2a-001", "localId": "001", "name": "フシギダネ"},
    {"id": "M2a-002", "localId": "002", "name": "フシギソウ"},
    {"id": "M2a-003", "localId": "003", "name": "メガフシギバナex"},
]}
ME01_EN = {"id": "me01", "name": "Mega Evolution", "cards": [
    {"id": "me01-001", "localId": "001", "name": "Bulbasaur"},
    {"id": "me01-002", "localId": "002", "name": "Ivysaur"},
    {"id": "me01-TG01", "localId": "TG01", "name": "Pikachu"},
]}


def fake_fetch(url, verbose=False):
    routes = {
        "/ja/sets": JA_LISTING,
        "/en/sets": EN_LISTING,
        "/ja/sets/M2a": M2A_JA,
        "/en/sets/me01": ME01_EN,
    }
    for suffix, payload in routes.items():
        if url.endswith(suffix):
            return payload
    return None if "/sets/" in url else []


def write_export(rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    f.write(HEADER)
    for name, set_name, number, lang, qty in rows:
        f.write(f"{name},{set_name},,{number},{lang},,,,1.00,{qty},,C,\n")
    f.close()
    return f.name


class NormalizeNumberTests(unittest.TestCase):
    def test_leading_zeros_hash_and_total_are_ignored(self):
        for raw in ("076", "#76", "76/193", " 0076 "):
            self.assertEqual(missing_cards.normalize_number(raw), "76")

    def test_lettered_numbers(self):
        self.assertEqual(missing_cards.normalize_number("TG01"), "TG1")
        self.assertEqual(missing_cards.normalize_number("tg1"), "TG1")
        self.assertEqual(missing_cards.normalize_number("SV001"), "SV1")

    def test_zero_and_blank(self):
        self.assertEqual(missing_cards.normalize_number("000"), "0")
        self.assertEqual(missing_cards.normalize_number(""), "")
        self.assertEqual(missing_cards.normalize_number(None), "")


class ReadOwnedTests(unittest.TestCase):
    def test_groups_by_set_and_language(self):
        path = write_export([
            ("Bulbasaur", "MEGA Dream ex", "1", "Japanese", 1),
            ("Bulbasaur", "MEGA Dream ex", "001", "Japanese", 2),
            ("Bulbasaur", "Mega Evolution", "1", "English", 1),
            ("Energy", "", "", "English", 1),
        ])
        with redirect_stdout(io.StringIO()):
            owned = missing_cards.read_owned(path)
        os.unlink(path)
        self.assertEqual(owned, {
            ("MEGA Dream ex", "Japanese"): {"1"},
            ("Mega Evolution", "English"): {"1"},
        })


class ResolveSetIdTests(unittest.TestCase):
    def setUp(self):
        missing_cards._LISTING_CACHE.clear()

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_exact_name_beats_ambiguous_substring(self, _):
        # "Mega Evolution" is a substring of the promo set's name too, which
        # binder_cover._find_set_id alone would treat as ambiguous.
        self.assertEqual(missing_cards.resolve_set_id("mega evolution", "English", {}), "me01")

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_set_map_used_for_japanese_names(self, _):
        set_map = {"japanese": {"mega dream ex": "m2a"}}
        self.assertEqual(missing_cards.resolve_set_id("MEGA Dream ex", "Japanese", set_map), "M2a")

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_set_map_is_language_specific(self, _):
        set_map = {"japanese": {"mega dream ex": "m2a"}}
        with redirect_stdout(io.StringIO()):
            self.assertIsNone(missing_cards.resolve_set_id("MEGA Dream ex", "English", set_map))

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_cli_map_wins_over_file(self, _):
        set_map = {"japanese": {"mega dream ex": "M5"}, "cli": {"mega dream ex": "M2a"}}
        self.assertEqual(missing_cards.resolve_set_id("MEGA Dream ex", "Japanese", set_map), "M2a")

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_falls_back_to_binder_substring_search(self, _):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(missing_cards.resolve_set_id("Surging", "English", {}), "sv08")

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_listings_fetched_once(self, mock_fetch):
        missing_cards.resolve_set_id("Mega Evolution", "English", {})
        missing_cards.resolve_set_id("Mega Evolution", "English", {})
        listing_calls = [c for c in mock_fetch.call_args_list if c.args[0].endswith("/sets")]
        self.assertEqual(len(listing_calls), len({c.args[0] for c in listing_calls}))


class FindMissingTests(unittest.TestCase):
    def setUp(self):
        missing_cards._LISTING_CACHE.clear()

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_missing_cards_per_set(self, _):
        owned = {
            ("MEGA Dream ex", "Japanese"): {"1", "3"},
            ("Mega Evolution", "English"): {"2", "99"},
            ("Nonexistent Set", "English"): {"1"},
        }
        set_map = {"japanese": {"mega dream ex": "M2a"}}
        with redirect_stdout(io.StringIO()):
            results, unmatched = missing_cards.find_missing(owned, set_map)

        by_set = {r["set_name"]: r for r in results}
        self.assertEqual([c["id"] for c in by_set["MEGA Dream ex"]["missing"]], ["M2a-002"])
        self.assertEqual(by_set["MEGA Dream ex"]["owned_count"], 2)
        self.assertEqual(by_set["MEGA Dream ex"]["total"], 3)
        self.assertEqual([c["id"] for c in by_set["Mega Evolution"]["missing"]],
                         ["me01-001", "me01-TG01"])
        self.assertEqual(by_set["Mega Evolution"]["unknown_owned"], ["99"])
        self.assertEqual(unmatched, [("Nonexistent Set", "English", "no TCGdex set found")])

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_falls_back_to_other_language_card_list_with_warning(self, _):
        owned = {("Mega Evolution", "Japanese"): {"1"}}
        out = io.StringIO()
        with redirect_stdout(out):
            results, _ = missing_cards.find_missing(owned, {})
        self.assertEqual(results[0]["tcgdex_lang"], "en")
        self.assertIn("isn't in TCGdex's Japanese data", out.getvalue())


class MainTests(unittest.TestCase):
    def setUp(self):
        missing_cards._LISTING_CACHE.clear()

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_end_to_end_csv(self, _):
        export = write_export([
            ("Bulbasaur", "MEGA Dream ex", "001", "Japanese", 1),
            ("Ivysaur", "Mega Evolution", "002", "English", 1),
        ])
        out = tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name
        try:
            with redirect_stdout(io.StringIO()):
                missing_cards.main(["--csv", export, "--out", out, "--min-complete", "0"])
            with open(out, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        finally:
            os.unlink(export)
            os.unlink(out)
        self.assertEqual(
            [(r["Set Name"], r["TCGdex Set"], r["Card Number"]) for r in rows],
            [("MEGA Dream ex", "M2a", "002"), ("MEGA Dream ex", "M2a", "003"),
             ("Mega Evolution", "me01", "001"), ("Mega Evolution", "me01", "TG01")],
        )

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_set_filter_and_map_flag(self, _):
        export = write_export([
            ("Bulbasaur", "MEGA Dream ex", "001", "Japanese", 1),
            ("Ivysaur", "Mega Evolution", "002", "English", 1),
        ])
        out = tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name
        try:
            with redirect_stdout(io.StringIO()):
                missing_cards.main(["--csv", export, "--out", out, "--set", "mega dream ex",
                                    "--min-complete", "0",
                                    "--set-map", "", "--map", "MEGA Dream ex=M2a"])
            with open(out, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        finally:
            os.unlink(export)
            os.unlink(out)
        self.assertEqual({r["Set Name"] for r in rows}, {"MEGA Dream ex"})
        self.assertEqual(len(rows), 2)

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_json_output(self, _):
        import json
        export = write_export([("Bulbasaur", "MEGA Dream ex", "001", "Japanese", 1),
                               ("X", "Mystery", "5", "English", 1)])
        out = tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name
        out_json = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
        try:
            with redirect_stdout(io.StringIO()):
                missing_cards.main(["--csv", export, "--out", out, "--json", out_json,
                                    "--min-complete", "0"])
            with open(out_json, encoding="utf-8") as f:
                data = json.load(f)
        finally:
            for p in (export, out, out_json):
                os.unlink(p)
        [m2a] = data["sets"]
        self.assertEqual((m2a["set_id"], m2a["tcgdex_lang"], m2a["owned"], m2a["total"]),
                         ("M2a", "ja", 1, 3))
        self.assertEqual(m2a["missing"][0], {
            "card_id": "M2a-002", "set_id": "M2a", "set_name": "MEGA Dream ex",
            "local_id": "002", "name": "フシギソウ", "language": "Japanese",
            "tcgdex_lang": "ja", "rarity": None, "finish": None,
        })
        self.assertEqual(data["unmatched"],
                         [{"set_name": "Mystery", "language": "English",
                           "reason": "no TCGdex set found"}])

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_default_threshold_leaves_out_incomplete_sets(self, _):
        # Own 2/3 of M2a (67%) and 3/3 would be complete; the default 75%
        # threshold keeps only sets at or above it.
        export = write_export([
            ("A", "MEGA Dream ex", "001", "Japanese", 1),
            ("B", "MEGA Dream ex", "002", "Japanese", 1),
            ("C", "Mega Evolution", "001", "English", 1),
            ("D", "Mega Evolution", "002", "English", 1),
            ("E", "Mega Evolution", "TG01", "English", 1),
        ])
        out = tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name
        out_json = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
        stdout = io.StringIO()
        try:
            with redirect_stdout(stdout):
                missing_cards.main(["--csv", export, "--out", out, "--json", out_json])
            with open(out_json, encoding="utf-8") as f:
                import json
                data = json.load(f)
        finally:
            for p in (export, out, out_json):
                os.unlink(p)
        self.assertEqual(data["min_complete"], 75)
        self.assertEqual([s["set_id"] for s in data["sets"]], ["me01"])
        self.assertEqual(data["sets"][0]["missing"], [])
        self.assertEqual([(b["set_id"], b["percent_complete"]) for b in data["below_threshold"]],
                         [("M2a", 66.7)])
        self.assertIn("Left out 1 set(s) under 75% complete: MEGA Dream ex (67%)",
                      stdout.getvalue())

    def test_threshold_boundary_is_inclusive(self):
        results = [{"owned_count": 3, "total": 4}, {"owned_count": 2, "total": 4},
                   {"owned_count": 0, "total": 0}]
        kept, below = missing_cards.apply_threshold(results, 75)
        self.assertEqual(kept, [results[0]])
        self.assertEqual(below, results[1:])

    def test_threshold_out_of_range_exits(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                patch("sys.stderr", io.StringIO()):
            missing_cards.main(["--csv", "x.csv", "--min-complete", "150"])

    def test_bad_map_flag_exits(self):
        export = write_export([("Bulbasaur", "MEGA Dream ex", "001", "Japanese", 1)])
        try:
            with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
                missing_cards.main(["--csv", export, "--map", "no-equals-sign"])
        finally:
            os.unlink(export)

    def test_seeded_set_map_loads(self):
        set_map = missing_cards.load_set_map(missing_cards.DEFAULT_SET_MAP)
        self.assertEqual(missing_cards.mapped_code(set_map, "mega dream ex", "Japanese"), "M2a")


if __name__ == "__main__":
    unittest.main()

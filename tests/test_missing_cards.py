import csv
import io
import json
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


CARDMARKET_PRODUCTS = {"products": [
    {"idProduct": 2, "name": "Ivysaur [Leech Seed]", "idExpansion": 9},
    {"idProduct": 3, "name": "Mega Venusaur ex", "idExpansion": 9},
]}


def fake_fetch(url, verbose=False):
    routes = {
        "/ja/sets": JA_LISTING,
        "/en/sets": EN_LISTING,
        "/ja/sets/M2a": M2A_JA,
        "/en/sets/me01": ME01_EN,
        # M2a-001 deliberately has no Cardmarket product.
        "/ja/cards/M2a-001": {"id": "M2a-001", "pricing": {}},
        "/ja/cards/M2a-002": {"id": "M2a-002", "pricing": {"cardmarket": {"idProduct": 2}}},
        "/ja/cards/M2a-003": {"id": "M2a-003", "pricing": {"cardmarket": {"idProduct": 3}}},
        "products_singles_6.json": CARDMARKET_PRODUCTS,
    }
    for suffix, payload in routes.items():
        if url.endswith(suffix):
            return payload
    return None if "/sets/" in url else []


def write_export(rows):
    """rows of (name, set name, number, language, quantity[, finish])."""
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    f.write(HEADER)
    for name, set_name, number, lang, qty, *finish in rows:
        f.write(f"{name},{set_name},,{number},{lang},{''.join(finish)},,,1.00,{qty},,C,\n")
    f.close()
    return f.name


def prints(card_id, ball="pokeball"):
    """A TCGdex card record with a normal print and two reverse holos."""
    return {"id": card_id, "variants_detailed": [
        {"type": "normal", "thirdParty": {"cardmarket": 10}},
        {"type": "reverse", "foil": "energy", "thirdParty": {"cardmarket": 11}},
        {"type": "reverse", "foil": ball, "thirdParty": {"cardmarket": 12}},
    ]}


def fake_fetch_prints(url, verbose=False):
    """fake_fetch, with M2a-001 and -002 in three prints and -003 (an ex) in one."""
    for card_id, ball in (("M2a-001", "loveball"), ("M2a-002", "team-rocket")):
        if url.endswith(f"/ja/cards/{card_id}"):
            return prints(card_id, ball)
    return fake_fetch(url, verbose)


_PRINTS_DIR = tempfile.TemporaryDirectory()


def setUpModule():
    # main() writes missing_prints.csv next to --out; keep it out of the temp
    # folder the tests' --out files share (an absolute name wins in join).
    patcher = patch.object(missing_cards, "PRINTS_NAME",
                           os.path.join(_PRINTS_DIR.name, "missing_prints.csv"))
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    # Nor the real English names cache in ~/PokemonData.
    patcher = patch.object(missing_cards, "DEFAULT_NAMES_CACHE",
                           os.path.join(_PRINTS_DIR.name, "english_names.json"))
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    unittest.addModuleCleanup(_PRINTS_DIR.cleanup)


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


class FinishTests(unittest.TestCase):
    def test_read_owned_collects_finishes_only_where_given(self):
        path = write_export([
            ("Bulbasaur", "MEGA Dream ex", "1", "Japanese", 1, "Normal"),
            ("Bulbasaur", "MEGA Dream ex", "1", "Japanese", 1, "Energy Reverse Holofoil"),
            ("Ivysaur", "MEGA Dream ex", "2", "Japanese", 1, "Poke Ball Reverse Holofoil"),
            ("Venusaur", "MEGA Dream ex", "3", "Japanese", 1),
            ("Bulbasaur", "Mega Evolution", "1", "English", 1),
        ])
        finishes = {}
        with redirect_stdout(io.StringIO()):
            missing_cards.read_owned(path, finishes)
        os.unlink(path)
        self.assertEqual(finishes, {("MEGA Dream ex", "Japanese"): {
            "1": {"normal", "energy"}, "2": {"ball"}, "3": {None}}})

    @patch("binder_cover._fetch_json", side_effect=fake_fetch_prints)
    def test_every_print_of_cards_with_reverse_holos(self, _):
        owned = {("MEGA Dream ex", "Japanese"): {"2", "3"}}
        finishes = {("MEGA Dream ex", "Japanese"): {"2": {"ball"}, "3": {None}}}
        with redirect_stdout(io.StringIO()):
            results, _ = missing_cards.find_missing(
                owned, {"japanese": {"mega dream ex": "M2a"}}, finishes)
        r = results[0]
        # The store search's list is unchanged: only M2a-001 has no copy at all.
        self.assertEqual([(c["id"], c.get("finish")) for c in r["missing"]], [("M2a-001", None)])
        self.assertEqual([(c["id"], c.get("finish"), c.get("finish_name")) for c in r["prints"]], [
            ("M2a-001", "normal", "Normal"),
            ("M2a-001", "energy", "Energy Reverse Holo"),
            ("M2a-001", "ball", "Love Ball Reverse Holo"),
            ("M2a-002", "normal", "Normal"),
            ("M2a-002", "energy", "Energy Reverse Holo"),
        ])
        self.assertEqual((r["owned_count"], r["total"]), (2, 3))
        self.assertIn("m2a-002 ball", r["card_keys"])

    @patch("binder_cover._fetch_json", side_effect=fake_fetch_prints)
    def test_copy_with_no_finish_counts_as_normal(self, _):
        owned = {("MEGA Dream ex", "Japanese"): {"1", "2", "3"}}
        finishes = {("MEGA Dream ex", "Japanese"): {"1": {None}, "2": {"energy"}, "3": {None}}}
        with redirect_stdout(io.StringIO()) as log:
            results, _ = missing_cards.find_missing(
                owned, {"japanese": {"mega dream ex": "M2a"}}, finishes)
        self.assertEqual(results[0]["missing"], [])
        self.assertEqual([(c["id"], c["finish"]) for c in results[0]["prints"]], [
            ("M2a-001", "energy"), ("M2a-001", "ball"),
            ("M2a-002", "normal"), ("M2a-002", "ball")])
        self.assertIn("1 card(s) owned with no finish in the export, counted as normal: 1",
                      log.getvalue())

    @patch("binder_cover._fetch_json", side_effect=fake_fetch_prints)
    def test_arrived_reverse_holo_moves_out_of_ordered_txt(self, _):
        export = write_export([
            ("Bulbasaur", "MEGA Dream ex", "1", "Japanese", 1, "Energy Reverse Holofoil"),
            ("Venusaur", "MEGA Dream ex", "3", "Japanese", 1),
        ])
        with tempfile.TemporaryDirectory() as d:
            ordered = os.path.join(d, "ordered.txt")
            with open(ordered, "w", encoding="utf-8") as f:
                f.write("M2a-001 energy | a\nM2a-001 | b\nM2a-001 ball | c\n")
            out = os.path.join(d, "missing.csv")
            prints_out = os.path.join(d, "prints.csv")
            try:
                with redirect_stdout(io.StringIO()):
                    missing_cards.main(["--csv", export, "--out", out, "--min-complete", "0",
                                        "--prints-out", prints_out,
                                        "--ordered", ordered, "--no-english-names"])
            finally:
                os.unlink(export)
            with open(ordered, encoding="utf-8") as f:
                left = f.read()
            with open(out, newline="", encoding="utf-8-sig") as f:
                cards = list(csv.DictReader(f))
            with open(prints_out, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(left, "M2a-001 | b\nM2a-001 ball | c\n")
        # missing_cards.csv, for the store search, is by card as before.
        self.assertEqual(list(cards[0]), missing_cards.OUT_COLUMNS)
        self.assertEqual([r["Card Number"] for r in cards], ["002"])
        self.assertEqual([(r["Card Number"], r["Finish"]) for r in rows], [
            ("001", "Normal"), ("001", "Love Ball Reverse Holo"),
            ("002", "Normal"), ("002", "Energy Reverse Holo"), ("002", "Team Rocket Reverse Holo")])


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
    def test_never_uses_another_languages_card_list(self, _):
        # Japanese prints of a set TCGdex only has an English list for must not
        # be diffed against the English list: numbering differs between them.
        owned = {("Mega Evolution", "Japanese"): {"1"}}
        with redirect_stdout(io.StringIO()):
            results, unmatched = missing_cards.find_missing(owned, {})
        self.assertEqual(results, [])
        self.assertEqual(unmatched, [(
            "Mega Evolution", "Japanese",
            "matched TCGdex set 'me01', but TCGdex has no Japanese card list for it")])

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_other_western_languages_use_english_list(self, _):
        owned = {("Mega Evolution", "German"): {"1"}}
        with redirect_stdout(io.StringIO()):
            results, _ = missing_cards.find_missing(owned, {})
        self.assertEqual(results[0]["tcgdex_lang"], "en")


class MainTests(unittest.TestCase):
    def setUp(self):
        missing_cards._LISTING_CACHE.clear()
        # Never touch a real ordered.txt next to the script.
        patcher = patch("missing_cards.DEFAULT_ORDERED",
                        os.path.join(tempfile.gettempdir(), "no-such-ordered.txt"))
        patcher.start()
        self.addCleanup(patcher.stop)

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
            "local_id": "002", "name": "フシギソウ", "name_en": "Ivysaur",
            "language": "Japanese",
            "tcgdex_lang": "ja", "rarity": None, "finish": None,
        })
        self.assertEqual(data["unmatched"],
                         [{"set_name": "Mystery", "language": "English",
                           "reason": "no TCGdex set found"}])

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_arrived_orders_move_out_of_ordered_txt(self, _):
        # Own M2a 001 and me01 002. On order: one of each that arrived, one
        # still missing, one from a set not in the collection and a name line.
        export = write_export([("Bulbasaur", "MEGA Dream ex", "001", "Japanese", 1),
                               ("Ivysaur", "Mega Evolution", "002", "English", 1)])
        with tempfile.TemporaryDirectory() as d:
            ordered = os.path.join(d, "ordered.txt")
            with open(ordered, "w", encoding="utf-8") as f:
                f.write("# on order\n"
                        "M2a-001 | 2026-09-20 | Japan2UK | #1001 | Bulbasaur\n"
                        "M2a-003 | 2026-09-20 | Japan2UK | #1001 | Mega Venusaur ex\n"
                        "me01-002 | 2026-09-21 | Cardmarket | 555\n"
                        "sv08-010 | 2026-09-21 | Cardmarket | 555\n"
                        "1 Switch (Black Bolt)\n")
            log = io.StringIO()
            with redirect_stdout(log):
                missing_cards.main(["--csv", export, "--out", os.path.join(d, "out.csv"),
                                    "--min-complete", "90", "--ordered", ordered])
            with open(ordered, encoding="utf-8") as f:
                left = f.read()
            with open(os.path.join(d, "ordered_arrived.txt"), encoding="utf-8") as f:
                arrived = f.read().splitlines()
        os.unlink(export)
        # M2a is only 33% complete, under --min-complete, but still checked.
        self.assertEqual(left, "# on order\n"
                               "M2a-003 | 2026-09-20 | Japan2UK | #1001 | Mega Venusaur ex\n"
                               "sv08-010 | 2026-09-21 | Cardmarket | 555\n"
                               "1 Switch (Black Bolt)\n")
        self.assertEqual([a.rsplit(" | arrived ", 1)[0] for a in arrived],
                         ["M2a-001 | 2026-09-20 | Japan2UK | #1001 | Bulbasaur",
                          "me01-002 | 2026-09-21 | Cardmarket | 555"])
        self.assertIn("2 ordered card(s) now in the collection", log.getvalue())
        self.assertIn("1 line(s)", log.getvalue())

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_keep_ordered_leaves_the_file_alone(self, _):
        export = write_export([("Bulbasaur", "MEGA Dream ex", "001", "Japanese", 1)])
        with tempfile.TemporaryDirectory() as d:
            ordered = os.path.join(d, "ordered.txt")
            with open(ordered, "w", encoding="utf-8") as f:
                f.write("M2a-001\n")
            with redirect_stdout(io.StringIO()):
                missing_cards.main(["--csv", export, "--out", os.path.join(d, "out.csv"),
                                    "--ordered", ordered, "--keep-ordered"])
            with open(ordered, encoding="utf-8") as f:
                self.assertEqual(f.read(), "M2a-001\n")
            self.assertFalse(os.path.exists(os.path.join(d, "ordered_arrived.txt")))
        os.unlink(export)

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

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_english_names_in_csv_and_report(self, _):
        export = write_export([("X", "Mega Evolution", "002", "English", 1)])
        with open(export, "a", encoding="utf-8") as f:
            f.write("Y,MEGA Dream ex,,999,Japanese,,,,1.00,1,,C,\n")
        out = tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name
        stdout = io.StringIO()
        try:
            with redirect_stdout(stdout):
                missing_cards.main(["--csv", export, "--out", out, "--min-complete", "0"])
            with open(out, newline="", encoding="utf-8-sig") as f:
                rows = {r["TCGdex Card ID"]: r["English Name"] for r in csv.DictReader(f)}
        finally:
            os.unlink(export)
            os.unlink(out)
        self.assertEqual(rows, {
            "M2a-001": "",                  # no Cardmarket product on TCGdex
            "M2a-002": "Ivysaur",           # " [Leech Seed]" suffix dropped
            "M2a-003": "Mega Venusaur ex",
            "me01-001": "Bulbasaur",        # English sets use their own name
            "me01-TG01": "Pikachu",
        })
        self.assertIn("#003      メガフシギバナex (Mega Venusaur ex)", stdout.getvalue())
        self.assertIn("#001      Bulbasaur\n", stdout.getvalue())

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_no_english_names_skips_lookups(self, mock_fetch):
        export = write_export([("A", "MEGA Dream ex", "001", "Japanese", 1)])
        out = tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name
        try:
            with redirect_stdout(io.StringIO()):
                missing_cards.main(["--csv", export, "--out", out, "--min-complete", "0",
                                    "--no-english-names"])
        finally:
            os.unlink(export)
            os.unlink(out)
        urls = [c.args[0] for c in mock_fetch.call_args_list]
        self.assertFalse([u for u in urls if "/cards/" in u or "cardmarket" in u])

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_cardmarket_download_failure_leaves_names_blank(self, mock_fetch):
        def no_cardmarket(url, verbose=False):
            return None if "cardmarket" in url else fake_fetch(url)
        mock_fetch.side_effect = no_cardmarket
        results = [{"tcgdex_lang": "ja", "missing": [{"id": "M2a-002", "name": "フシギソウ"}]}]
        with redirect_stdout(io.StringIO()):
            missing_cards.add_english_names(results)
        self.assertIsNone(results[0]["missing"][0]["name_en"])

    @patch("binder_cover._fetch_json", side_effect=fake_fetch)
    def test_names_found_are_cached_and_not_looked_up_again(self, mock_fetch):
        def cards():
            return [{"tcgdex_lang": "ja", "missing": [{"id": "M2a-001", "name": "a"},
                                                     {"id": "M2a-002", "name": "フシギソウ"}]}]
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "names.json")
            with redirect_stdout(io.StringIO()):
                missing_cards.add_english_names(cards(), cache)
            with open(cache, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"ja/M2a-002": "Ivysaur"})
            mock_fetch.reset_mock()
            results = cards()
            with redirect_stdout(io.StringIO()):
                missing_cards.add_english_names(results, cache)
        self.assertEqual([c["name_en"] for c in results[0]["missing"]], [None, "Ivysaur"])
        # Only the card with no name yet is tried again.
        cards_fetched = [c.args[0].rsplit("/", 1)[1] for c in mock_fetch.call_args_list
                         if "/cards/" in c.args[0]]
        self.assertEqual(cards_fetched, ["M2a-001"])

    def test_cardmarket_name_cleanup(self):
        clean = missing_cards._cardmarket_base_name
        self.assertEqual(clean("Tangela [Poison Powder | Hook]"), "Tangela")
        self.assertEqual(clean("AZ's Tranquility"), "AZ's Tranquility")
        self.assertEqual(clean("Basic Fire [Holo] Energy"), "Basic Fire Energy")
        self.assertEqual(clean("Pikachu (Top Deck)"), "Pikachu (Top Deck)")
        self.assertEqual(clean(None), "")

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

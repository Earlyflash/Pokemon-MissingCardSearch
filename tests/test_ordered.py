"""ordered.py tests: reading and pruning ordered.txt."""
import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ordered  # noqa: E402


class ParseLineTests(unittest.TestCase):
    def test_card_id_with_notes(self):
        self.assertEqual(ordered.parse_line("me01-161 | 2026-09-28 | Cardmarket | 123"),
                         ("me01-161", 1, None))

    def test_card_id_with_finish(self):
        self.assertEqual(ordered.parse_line("M2a-032 Energy | 2026-10-01"), ("m2a-032 energy", 1, None))
        self.assertEqual(ordered.parse_line("M2a-032 normal"), ("m2a-032", 1, None))
        self.assertEqual(ordered.card_key("M2a-032", "ball"), "m2a-032 ball")
        self.assertEqual(ordered.card_key("M2a-032", None), "m2a-032")

    def test_wants_list_line_with_notes(self):
        self.assertEqual(ordered.parse_line("2 Switch (Black Bolt) | 2026-09-28 | Cardmarket"),
                         (None, 2, "switch (black bolt)"))

    def test_comments_and_blanks(self):
        self.assertIsNone(ordered.parse_line("# me01-161"))
        self.assertIsNone(ordered.parse_line("   "))
        self.assertEqual(ordered.parse_line("Blaine's Quiz #1 (Gym Heroes)")[2],
                         "blaine's quiz #1 (gym heroes)")

    def test_read_ordered_shops(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ordered.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("me01-161 | 2026-09-28 | Cardmarket | 1\nme01-161 | 2026-09-29 | Deckd | 2\n"
                        "M2a-032 energy | 2026-10-01 | Cardmarket\nme01-001\n# me01-002 | x | Gone\n"
                        "1 Switch (Black Bolt) | 2026-09-28 | Cardmarket\n")
            self.assertEqual(ordered.read_ordered_shops(path),
                             {"me01-161": ["Cardmarket", "Deckd"], "m2a-032 energy": ["Cardmarket"],
                              "me01-001": []})

    def test_read_ordered_ignores_notes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ordered.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("M2a-003 | 2026-09-20 | Japan2UK | #1001\n1 Switch (Black Bolt) | x\n")
            ids, names = ordered.read_ordered(path)
        self.assertEqual(ids, {"m2a-003"})
        self.assertEqual(names, {"switch (black bolt)": 1})


class PruneArrivedTests(unittest.TestCase):
    def write(self, d, text):
        path = os.path.join(d, "ordered.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_moves_arrived_cards_and_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "# note\nme01-001 | a\nme01-002 | b\nsv08-001\n\n1 Switch\n")
            arrived, names = ordered.prune_arrived(
                path, {"me01-001", "me01-002"}, {"me01-002"},
                today=datetime.date(2026, 10, 1))
            with open(path, encoding="utf-8") as f:
                left = f.read()
            with open(os.path.join(d, "ordered_arrived.txt"), encoding="utf-8") as f:
                moved = f.read()
        self.assertEqual(arrived, ["me01-001 | a"])
        self.assertEqual(names, 1)
        self.assertEqual(left, "# note\nme01-002 | b\nsv08-001\n\n1 Switch\n")
        self.assertEqual(moved, "me01-001 | a | arrived 2026-10-01\n")

    def test_appends_to_existing_arrived_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "me01-001\n")
            arrived_path = os.path.join(d, "done.txt")
            with open(arrived_path, "w", encoding="utf-8") as f:
                f.write("old | arrived 2026-09-01\n")
            ordered.prune_arrived(path, {"me01-001"}, set(), arrived_path,
                                  today=datetime.date(2026, 10, 1))
            with open(arrived_path, encoding="utf-8") as f:
                self.assertEqual(f.read().splitlines(),
                                 ["old | arrived 2026-09-01", "me01-001 | arrived 2026-10-01"])

    def test_nothing_arrived_leaves_files_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "me01-001\n")
            self.assertEqual(ordered.prune_arrived(path, set(), set()), ([], 0))
            self.assertEqual(os.listdir(d), ["ordered.txt"])

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ordered.prune_arrived(os.path.join(d, "none.txt"), {"a-1"}, set()),
                             ([], 0))
            self.assertEqual(os.listdir(d), [])


class SortFileTests(unittest.TestCase):
    def test_sorts_by_date_shop_seller(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ordered.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("# card id | date | shop | order | seller | card\n"
                        "M6a-109 | 2026-10-06 | Japan2UK | 182506 | Toxtricity\n"
                        "M3-096 | 2026-10-03 | Cardmarket | 2 | seller zed | Mega Clefable ex\n"
                        "M6-097 | 2026-10-03 | Cardmarket | 2 | seller zed | Adventuring Lantern\n"
                        "\n"
                        "# cancelled M3-108 | 2026-10-03 | Cardmarket | 1 | seller Kard | Jacinthe\n"
                        "M5-082 | 2026-10-03 | Cardmarket | 3 | seller alpha | Fomantis\n"
                        "S10b-028 | 2026-09-30 | Radam's | 00326 | Pikachu\n"
                        "M1S-080 | 2026-10-03 | Cardmarket | 1 | seller Kard | Mega Kangaskhan ex\n")
            ordered.sort_file(path)
            with open(path, encoding="utf-8") as f:
                cards = [line.split(" | ")[0] for line in f.read().splitlines()]
        self.assertEqual(cards, ["# card id", "S10b-028", "M5-082", "# cancelled M3-108", "M1S-080",
                                 "M3-096", "M6-097", "M6a-109"])


if __name__ == "__main__":
    unittest.main()

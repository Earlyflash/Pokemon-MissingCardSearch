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

    def test_wants_list_line_with_notes(self):
        self.assertEqual(ordered.parse_line("2 Switch (Black Bolt) | 2026-09-28 | Cardmarket"),
                         (None, 2, "switch (black bolt)"))

    def test_comments_and_blanks(self):
        self.assertIsNone(ordered.parse_line("# me01-161"))
        self.assertIsNone(ordered.parse_line("   "))
        self.assertEqual(ordered.parse_line("Blaine's Quiz #1 (Gym Heroes)")[2],
                         "blaine's quiz #1 (gym heroes)")

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


if __name__ == "__main__":
    unittest.main()

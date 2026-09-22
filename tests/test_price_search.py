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
import marketplaces  # noqa: E402
import price_search  # noqa: E402
from marketplaces.base import (MATCH_LIKELY, MATCH_UNCERTAIN, Marketplace,  # noqa: E402
                               MissingCard, Offer, SearchContext)

MISSING = {
    "sets": [
        {"set_id": "M2a", "set_name": "MEGA Dream ex", "language": "Japanese", "tcgdex_lang": "ja",
         "total": 3, "owned": 1, "missing": [
             {"card_id": "M2a-002", "set_id": "M2a", "set_name": "MEGA Dream ex", "local_id": "002",
              "name": "フシギソウ", "language": "Japanese", "tcgdex_lang": "ja",
              "rarity": None, "finish": None},
             {"card_id": "M2a-003", "set_id": "M2a", "set_name": "MEGA Dream ex", "local_id": "003",
              "name": "メガフシギバナex", "language": "Japanese", "tcgdex_lang": "ja",
              "rarity": None, "finish": None},
         ]},
        {"set_id": "me01", "set_name": "Mega Evolution", "language": "English", "tcgdex_lang": "en",
         "total": 3, "owned": 2, "missing": [
             {"card_id": "me01-001", "set_id": "me01", "set_name": "Mega Evolution",
              "local_id": "001", "name": "Bulbasaur", "language": "English", "tcgdex_lang": "en",
              "rarity": None, "finish": None},
         ]},
        {"set_id": "sv08", "set_name": "Surging Sparks", "language": "English", "tcgdex_lang": "en",
         "total": 1, "owned": 1, "missing": []},
    ],
    "unmatched": [],
}

# Offers the fake marketplaces return, by card id.
JP_SHOP_STOCK = {
    "M2a-002": [("500", "JPY", "NM"), ("300", "JPY", "LP")],
    "M2a-003": [("9000", "JPY", "NM")],
}
EU_SHOP_STOCK = {
    "M2a-002": [("1.50", "EUR", None)],
    "me01-001": [("0.20", "EUR", "NM")],
}
RATES = {("JPY", "GBP"): Decimal("0.005"), ("EUR", "GBP"): Decimal("0.85")}


def offers_from(stock, card, match="exact"):
    return [Offer(marketplace="", card_id=card.card_id, url=f"https://shop.example/{card.card_id}",
                  price=Decimal(p), currency=cur, condition=cond, match=match,
                  title=f"{card.name} {card.local_id}")
            for p, cur, cond in stock.get(card.card_id, [])]


class JapanShop(Marketplace):
    id, name, languages, min_interval = "jpshop", "Japan Shop", {"ja"}, 0

    def search(self, card, ctx):
        return offers_from(JP_SHOP_STOCK, card)


class EuroShop(Marketplace):
    id, name, min_interval = "euroshop", "Euro Shop", 0

    def search_set(self, cards, ctx):
        return [o for c in cards for o in offers_from(EU_SHOP_STOCK, c)]


class KeyedShop(Marketplace):
    id, name, needs = "keyed", "Keyed Shop", ("KEYED_SHOP_TOKEN",)

    def search(self, card, ctx):
        return []


def write_json(data):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, f, ensure_ascii=False)
    f.close()
    return f.name


def ctx_for(plugin):
    return SearchContext(plugin)


class LoadMissingTests(unittest.TestCase):
    def setUp(self):
        self.path = write_json(MISSING)

    def test_groups_by_set_and_skips_sets_with_nothing_missing(self):
        groups = price_search.load_missing(self.path)
        self.assertEqual([s["set_id"] for s, _ in groups], ["M2a", "me01"])
        self.assertEqual(groups[0][1][0], MissingCard(
            card_id="M2a-002", set_id="M2a", set_name="MEGA Dream ex", local_id="002",
            name="フシギソウ", language="Japanese", tcgdex_lang="ja"))

    def test_set_filter_matches_name_or_id(self):
        self.assertEqual([s["set_id"] for s, _ in price_search.load_missing(self.path, ["me01"])],
                         ["me01"])
        self.assertEqual([s["set_id"] for s, _ in
                          price_search.load_missing(self.path, ["mega dream ex"])], ["M2a"])


class LoadMissingCsvTests(unittest.TestCase):
    def write_csv(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig",
                                        newline="")
        f.write(text)
        f.close()
        return f.name

    def test_missing_cards_csv_loads_like_the_json(self):
        path = self.write_csv(
            "Set Name,TCGdex Set,Language,Card Number,Card Name,TCGdex Card ID\r\n"
            "MEGA Dream ex,M2a,Japanese,002,フシギソウ,M2a-002\r\n"
            "MEGA Dream ex,M2a,Japanese,003,メガフシギバナex,M2a-003\r\n"
            "Mega Evolution,me01,English,001,Bulbasaur,me01-001\r\n")
        from_csv = price_search.load_missing(path)
        from_json = price_search.load_missing(write_json(MISSING))
        self.assertEqual([cards for _, cards in from_csv], [cards for _, cards in from_json])
        self.assertEqual([(s["set_id"], s["language"]) for s, _ in from_csv],
                         [("M2a", "Japanese"), ("me01", "English")])

    def test_other_csv_gives_a_clear_error(self):
        path = self.write_csv("Product Name,Set Name\r\nPikachu,Base Set\r\n")
        with self.assertRaises(SystemExit) as e:
            price_search.load_missing(path)
        self.assertIn("missing_cards.csv", str(e.exception))


class SelectPluginsTests(unittest.TestCase):
    available = {"jpshop": JapanShop(), "keyed": KeyedShop()}

    def test_plugins_missing_settings_are_skipped(self):
        chosen, skipped = price_search.select_plugins(self.available, [], environ={})
        self.assertEqual([p.id for p in chosen], ["jpshop"])
        self.assertIn("KEYED_SHOP_TOKEN", skipped["keyed"])

    def test_plugins_with_settings_run(self):
        chosen, _ = price_search.select_plugins(self.available, ["keyed"],
                                                environ={"KEYED_SHOP_TOKEN": "x"})
        self.assertEqual([p.id for p in chosen], ["keyed"])

    def test_unknown_marketplace_exits(self):
        with self.assertRaises(SystemExit):
            price_search.select_plugins(self.available, ["nope"], environ={})


class RunPluginTests(unittest.TestCase):
    def setUp(self):
        self.groups = price_search.load_missing(write_json(MISSING))

    def test_cards_only_go_to_plugins_that_sell_their_language(self):
        seen = []

        class Recorder(JapanShop):
            def search(self, card, ctx):
                seen.append(card.card_id)
                return []

        price_search.run_plugin(Recorder(), self.groups, ctx_for(Recorder()))
        self.assertEqual(seen, ["M2a-002", "M2a-003"])

    def test_one_card_failing_keeps_the_rest(self):
        class Flaky(JapanShop):
            def search(self, card, ctx):
                if card.card_id == "M2a-002":
                    raise RuntimeError("boom")
                return super().search(card, ctx)

        with redirect_stdout(io.StringIO()):
            offers, failures = price_search.run_plugin(Flaky(), self.groups, ctx_for(Flaky()))
        self.assertEqual([o.card_id for _, o in offers], ["M2a-003"])
        self.assertEqual(failures, 0)

    def test_a_failing_set_is_counted_and_others_still_searched(self):
        class Broken(EuroShop):
            def search_set(self, cards, ctx):
                if cards[0].set_id == "M2a":
                    raise RuntimeError("site down")
                return super().search_set(cards, ctx)

        with redirect_stdout(io.StringIO()):
            offers, failures = price_search.run_plugin(Broken(), self.groups, ctx_for(Broken()))
        self.assertEqual([o.card_id for _, o in offers], ["me01-001"])
        self.assertEqual(failures, 1)

    def test_offers_for_cards_not_asked_about_are_dropped(self):
        class Stray(EuroShop):
            def search_set(self, cards, ctx):
                return [Offer("", "zzz-999", "u", Decimal(1), "GBP"),
                        Offer("", cards[0].card_id, "u", Decimal(1), "GBP", match="maybe")]

        with redirect_stdout(io.StringIO()):
            offers, _ = price_search.run_plugin(Stray(), self.groups, ctx_for(Stray()))
        self.assertEqual(offers, [])

    def test_marketplace_id_is_stamped_on_offers(self):
        offers, _ = price_search.run_plugin(EuroShop(), self.groups, ctx_for(EuroShop()))
        self.assertEqual({o.marketplace for _, o in offers}, {"euroshop"})


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.groups = price_search.load_missing(write_json(MISSING))

    def rank(self, offers, rates=None, **kw):
        rates = rates if rates is not None else {"GBP": Decimal(1), "JPY": Decimal("0.005"),
                                                 "EUR": Decimal("0.85")}
        return price_search.rank_offers(self.groups, offers, rates, **kw)

    def test_cheapest_first_in_one_currency(self):
        card = self.groups[0][1][0]
        offers = [(0, o) for o in offers_from(JP_SHOP_STOCK, card) + offers_from(EU_SHOP_STOCK, card)]
        ranked = self.rank(offers)[(0, "M2a-002")]
        self.assertEqual([p for p, _ in ranked], [Decimal("1.28"), Decimal("1.50"), Decimal("2.50")])

    def test_uncertain_matches_left_out_unless_asked_for(self):
        card = self.groups[1][1][0]
        offers = [(1, o) for o in offers_from(EU_SHOP_STOCK, card, match=MATCH_UNCERTAIN)]
        self.assertEqual(self.rank(offers)[(1, "me01-001")], [])
        self.assertEqual(len(self.rank(offers, include_uncertain=True)[(1, "me01-001")]), 1)
        likely = [(1, o) for o in offers_from(EU_SHOP_STOCK, card, match=MATCH_LIKELY)]
        self.assertEqual(len(self.rank(likely)[(1, "me01-001")]), 1)

    def test_unconvertible_currency_is_listed_last(self):
        card = self.groups[0][1][0]
        offers = [(0, o) for o in offers_from(JP_SHOP_STOCK, card) + offers_from(EU_SHOP_STOCK, card)]
        ranked = self.rank(offers, rates={"GBP": Decimal(1), "JPY": Decimal("0.005")})[(0, "M2a-002")]
        self.assertEqual(ranked[-1][0], None)
        self.assertEqual(ranked[-1][1].currency, "EUR")

    def test_same_card_id_in_two_collections_stays_separate(self):
        card = self.groups[1][1][0]
        german = dict(MISSING["sets"][1], language="German")
        groups = self.groups + [(german, [card])]
        offer = offers_from(EU_SHOP_STOCK, card)[0]
        ranked = price_search.rank_offers(groups, [(2, offer)], {"EUR": Decimal("0.85")})
        self.assertEqual(ranked[(1, "me01-001")], [])
        self.assertEqual(len(ranked[(2, "me01-001")]), 1)

    def test_graded_copies_compete_on_price(self):
        card = self.groups[1][1][0]
        raw = Offer("x", card.card_id, "u1", Decimal("5.00"), "GBP")
        slab = Offer("x", card.card_id, "u2", Decimal("2.00"), "GBP", grade="PSA 9")
        ranked = self.rank([(1, raw), (1, slab)])[(1, "me01-001")]
        self.assertEqual([o.url for _, o in ranked], ["u2", "u1"])

    def test_rate_failures_are_reported_not_fatal(self):
        def fetch(src, dst):
            if src == "EUR":
                raise OSError("offline")
            return RATES[(src, dst)]

        with redirect_stdout(io.StringIO()) as out:
            rates = price_search.exchange_rates({"JPY", "EUR", "GBP"}, "GBP", fetch=fetch)
        self.assertEqual(rates, {"GBP": Decimal(1), "JPY": Decimal("0.005")})
        self.assertIn("EUR", out.getvalue())


class MainTests(unittest.TestCase):
    def run_main(self, *extra):
        out_csv = tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name
        plugins = {"jpshop": JapanShop(), "euroshop": EuroShop(), "keyed": KeyedShop()}
        with patch.object(marketplaces, "discover", return_value=plugins), \
                patch.object(price_search, "fetch_rate", side_effect=lambda s, d: RATES[(s, d)]), \
                patch.dict(os.environ, {}, clear=False), redirect_stdout(io.StringIO()) as out:
            os.environ.pop("KEYED_SHOP_TOKEN", None)
            price_search.main([write_json(MISSING), "--out", out_csv, "--no-cache", *extra])
        with open(out_csv, encoding="utf-8") as f:
            return list(csv.DictReader(f)), out.getvalue()

    def test_writes_every_offer_cheapest_first(self):
        rows, out = self.run_main()
        self.assertEqual([(r["TCGdex Card ID"], r["Marketplace"], r["Price (GBP)"]) for r in rows], [
            ("M2a-002", "euroshop", "1.28"),
            ("M2a-002", "jpshop", "1.50"),
            ("M2a-002", "jpshop", "2.50"),
            ("M2a-003", "jpshop", "45.00"),
            ("me01-001", "euroshop", "0.17"),
        ])
        self.assertIn("3/3 missing card(s) found for sale", out)
        self.assertIn("GBP 46.45", out)
        self.assertIn("keyed: skipped (needs KEYED_SHOP_TOKEN set)", out)

    def test_cheapest_only(self):
        rows, _ = self.run_main("--cheapest-only")
        self.assertEqual([(r["TCGdex Card ID"], r["Price (GBP)"], r["Listed Price"],
                           r["Listed Currency"]) for r in rows],
                         [("M2a-002", "1.28", "1.50", "EUR"), ("M2a-003", "45.00", "9000", "JPY"),
                          ("me01-001", "0.17", "0.20", "EUR")])

    def test_one_marketplace_only(self):
        rows, out = self.run_main("--marketplace", "euroshop")
        self.assertEqual({r["Marketplace"] for r in rows}, {"euroshop"})
        self.assertIn("Not found for sale: #003", out)


class SearchContextTests(unittest.TestCase):
    class FakeResponse(io.BytesIO):
        headers = type("H", (), {"get_content_charset": staticmethod(lambda: "utf-8")})()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_fetch_caches_on_disk_and_paces_requests(self):
        plugin = EuroShop()
        plugin.min_interval = 2.0
        sleeps, now = [], [100.0]
        ctx = SearchContext(plugin, cache_dir=tempfile.mkdtemp(), sleep=sleeps.append,
                            clock=lambda: now[0])
        calls = []

        def urlopen(req, timeout):
            calls.append(req.full_url)
            return self.FakeResponse(b'{"ok": true}')

        with patch("urllib.request.urlopen", urlopen):
            self.assertEqual(ctx.fetch("https://a.example/1", as_json=True), {"ok": True})
            self.assertEqual(ctx.fetch("https://a.example/1", as_json=True), {"ok": True})
            now[0] += 0.5
            ctx.fetch("https://a.example/2")
        self.assertEqual(calls, ["https://a.example/1", "https://a.example/2"])
        self.assertEqual(sleeps, [1.5])


class DiscoverTests(unittest.TestCase):
    def test_discover_returns_plugins_by_id(self):
        plugins = marketplaces.discover()
        for pid, plugin in plugins.items():
            self.assertEqual(plugin.id, pid)
            self.assertIsInstance(plugin, Marketplace)


if __name__ == "__main__":
    unittest.main()

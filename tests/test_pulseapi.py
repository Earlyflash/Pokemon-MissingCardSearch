"""PulseAPI plugin tests. Responses are built from the Card objects in
PulseAPI's documentation (fetched 2026-09-24), trimmed, so no network or API
key is needed."""
import io
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from decimal import Decimal
from email.message import Message

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from marketplaces import pulseapi  # noqa: E402
from marketplaces.base import MATCH_EXACT, MATCH_LIKELY, MissingCard, SearchContext  # noqa: E402


def hit(set_id, number, name, price, material=None, graded_by=None, grade=None, condition=None,
        language="English", global_price=None, set_name="Scarlet & Violet 151"):
    slots = [set_id, number, material or "null", "null", graded_by or "null", grade or "null"]
    if condition:
        slots.append(condition)
    return {"product_id": "card:" + "|".join(slots), "card_name": name, "card_number": number,
            "set_id": set_id, "set_name": set_name, "material": material, "promo_info": None,
            "graded_by": graded_by, "grade": grade, "language": language,
            "slug": f"{name.lower().replace(' ', '-')}-{number.replace('/', '-')}",
            "prices": {"market_price": price, "market_price_global": global_price}}


SV151 = [
    hit("sv3pt5", "199/165", "Charizard ex", 269.16),
    hit("sv3pt5", "199/165", "Charizard ex", 1500, graded_by="PSA", grade="10"),
    hit("sv3pt5", "199/165", "Charizard ex", 190.41, condition="hp"),
    hit("sv3pt5", "001/165", "Bulbasaur", 0.2),
    hit("sv3pt5", "001/165", "Bulbasaur", 0.9, material="Reverse Holo"),
    hit("sv3pt5", "002/165", "Ivysaur", None, global_price=0.35),
    hit("sv3pt5", "003/165", "Venusaur", None),
]
JP = [
    hit("SV2a", "006/165", "Charmander", 0.5, language="Japanese", set_name="Pokemon Card 151"),
    hit("SV2a", "201/165", "Charizard ex", 120, language="Japanese", set_name="Pokemon Card 151"),
    hit("SV2a", "201/165", "Charizard ex", 110, material="Holo", language="Japanese",
        set_name="Pokemon Card 151"),
]


def card(card_id, name="", lang="en", set_name="151", name_en=None):
    set_id, local_id = card_id.rsplit("-", 1)
    return MissingCard(card_id, set_id, set_name, local_id, name,
                       {"en": "English", "ja": "Japanese"}[lang], lang, name_en=name_en)


class FakeContext(SearchContext):
    """Answers PulseAPI searches from lists of cards, `page_size` a page."""

    def __init__(self, catalogue, page_size=100, rate_limited=0, status=None, retry_after="7",
                 max_limit=500, cache_dir=None):
        super().__init__(pulseapi.PLUGIN, config={"PULSEAPI_KEY": "pk_test"}, cache_dir=cache_dir)
        self.retry_after = retry_after
        self.max_limit = max_limit
        self.catalogue = catalogue
        self.page_size = page_size
        self.rate_limited = rate_limited
        self.status = status
        self.queries = []
        self.headers = []

    def fetch(self, url, headers=None, as_json=False, timeout=30):
        self.headers.append(headers)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.queries.append(q)
        if self.status:
            raise urllib.error.HTTPError(url, self.status, "", Message(), io.BytesIO())
        if self.rate_limited:
            self.rate_limited -= 1
            h = Message()
            h["Retry-After"] = self.retry_after
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", h, io.BytesIO())
        if int(q.get("limit", 20)) > self.max_limit:
            raise urllib.error.HTTPError(url, 400, "Bad Request", Message(), io.BytesIO())
        rows = [h for h in self.catalogue if h["language"] == q["language"]]
        if q.get("exclude_graded") == "true":
            rows = [h for h in rows if not h["graded_by"]]
        if q.get("exclude_conditioned") == "true":
            rows = [h for h in rows if len(h["product_id"].split("|")) == 6]
        if "set_id" in q:
            rows = [h for h in rows if h["set_id"] == q["set_id"]]
        if "q" in q:
            rows = [h for h in rows if q["q"].lower() in h["card_name"].lower()]
        page = int(q.get("page", 1))
        size = min(int(q.get("limit", 20)), self.page_size)
        pages = max(1, -(-len(rows) // size))
        return {"success": True, "data": rows[(page - 1) * size:page * size],
                "meta": {"total": len(rows), "page": page, "limit": size, "total_pages": pages}}


class PulseAPITests(unittest.TestCase):
    def setUp(self):
        self.slept = []
        self.plugin = pulseapi.PulseAPI(sleep=self.slept.append)

    def test_is_a_gbp_market_price_guide_needing_a_key(self):
        self.assertTrue(pulseapi.PLUGIN.price_guide)
        self.assertEqual(pulseapi.PLUGIN.needs, ("PULSEAPI_KEY",))
        self.assertEqual(pulseapi.PLUGIN.guide_label, "market price")

    def test_numbers_and_set_ids(self):
        self.assertEqual(pulseapi.norm_number("199/165"), "199")
        self.assertEqual(pulseapi.norm_number("002"), "2")
        self.assertEqual(pulseapi.norm_number("TG05/TG30"), "TG5")
        self.assertEqual(pulseapi.set_id_guesses("sv03.5"), ["sv03.5", "sv3pt5"])
        self.assertEqual(pulseapi.set_id_guesses("SV2a"), ["SV2a", "sv2a"])
        self.assertEqual(pulseapi.set_id_guesses("swsh7"), ["swsh7"])

    def test_prices_an_english_set_found_by_its_pokemontcg_style_id(self):
        ctx = FakeContext(SV151)
        offers = self.plugin.search_set([card("sv03.5-199"), card("sv03.5-001"),
                                         card("sv03.5-002"), card("sv03.5-003")], ctx)
        got = [(o.card_id, o.price, o.currency, o.match, o.url) for o in offers]
        self.assertEqual(got, [
            ("sv03.5-199", Decimal("269.16"), "GBP", MATCH_EXACT,
             "https://pulsetcg.io/card/charizard-ex-199-165"),
            # The standard print, not the reverse holo, when the finish isn't known.
            ("sv03.5-001", Decimal("0.2"), "GBP", MATCH_EXACT,
             "https://pulsetcg.io/card/bulbasaur-001-165"),
            ("sv03.5-002", Decimal("0.35"), "GBP", MATCH_EXACT,
             "https://pulsetcg.io/card/ivysaur-002-165"),
        ])
        self.assertTrue(offers[0].title.startswith("UK market price: Charizard ex 199/165"))
        self.assertTrue(offers[2].title.startswith("UK+US market price"))
        self.assertEqual([q.get("set_id") for q in ctx.queries], ["sv03.5", "sv3pt5"])
        self.assertTrue(all(h == {"x-api-key": "pk_test"} for h in ctx.headers))

    def test_japanese_set_is_filtered_by_language(self):
        ctx = FakeContext(SV151 + JP)
        offers = self.plugin.search_set([card("SV2a-006", "ヒトカゲ", "ja")], ctx)
        self.assertEqual([(o.card_id, o.price) for o in offers], [("SV2a-006", Decimal("0.5"))])
        self.assertEqual(ctx.queries[0]["language"], "Japanese")

    def test_only_variants_of_a_number_are_offered_as_likely(self):
        catalogue = [dict(h, material=h["material"] or "Holo") for h in JP]
        catalogue[2] = dict(catalogue[2], material="Reverse Holo")
        offers = self.plugin.search_set([card("SV2a-201", "リザードンex", "ja")], FakeContext(catalogue))
        self.assertEqual(sorted((o.price, o.match) for o in offers),
                         [(Decimal("110"), MATCH_LIKELY), (Decimal("120"), MATCH_LIKELY)])

    def test_falls_back_to_a_name_search_when_set_id_guesses_miss(self):
        catalogue = [dict(h, set_id="PC151") for h in JP]
        ctx = FakeContext(catalogue)
        offers = self.plugin.search_set(
            [card("SV2a-006", "ヒトカゲ", "ja", "Pokemon Card 151", name_en="Charmander")], ctx)
        self.assertEqual([(o.card_id, o.price) for o in offers], [("SV2a-006", Decimal("0.5"))])
        self.assertEqual(ctx.queries[2].get("q"), "Charmander")
        self.assertEqual(ctx.queries[-1].get("set_id"), "PC151")

    def test_reads_every_page_of_a_set(self):
        ctx = FakeContext(SV151, page_size=2)
        offers = self.plugin.search_set([card("sv03.5-003"), card("sv03.5-002")], ctx)
        self.assertEqual([o.card_id for o in offers], ["sv03.5-002"])
        self.assertEqual([q.get("page") for q in ctx.queries if q.get("set_id") == "sv3pt5"],
                         [None, "2", "3"])

    def test_unknown_set_gives_no_prices(self):
        ctx = FakeContext(SV151)
        self.assertEqual(self.plugin.search_set([card("zz1-001", "Nobody")], ctx), [])

    def test_waits_out_a_rate_limit_and_retries(self):
        ctx = FakeContext(SV151, rate_limited=1)
        offers = self.plugin.search_set([card("sv03.5-199")], ctx)
        self.assertEqual(len(offers), 1)
        self.assertEqual(self.slept, [7])

    def test_a_used_up_daily_quota_stops_instead_of_waiting(self):
        ctx = FakeContext(SV151, rate_limited=1, retry_after="7200")
        with self.assertRaisesRegex(RuntimeError, "quota is used up; it resets in 120"):
            self.plugin.search_set([card("sv03.5-199")], ctx)
        self.assertEqual(self.slept, [])

    def test_asks_for_500_a_page_then_falls_back_to_the_free_tiers_100(self):
        ctx = FakeContext(SV151, max_limit=100)
        offers = self.plugin.search_set([card("sv03.5-199")], ctx)
        self.assertEqual(len(offers), 1)
        self.assertEqual([q["limit"] for q in ctx.queries], ["500", "100", "100"])
        self.plugin.search_set([card("sv03.5-001")], ctx)
        self.assertEqual(ctx.queries[-1]["limit"], "100")

    def test_remembers_which_set_it_found_for_the_next_run(self):
        cache = tempfile.mkdtemp()
        self.plugin.search_set([card("sv03.5-199")], FakeContext(SV151, cache_dir=cache))
        self.plugin.search_set([card("zz1-001", "Nobody")], FakeContext(SV151, cache_dir=cache))
        ctx = FakeContext(SV151, cache_dir=cache)
        offers = self.plugin.search_set([card("sv03.5-199")], ctx)
        self.assertEqual(len(offers), 1)
        self.assertEqual([q.get("set_id") for q in ctx.queries], ["sv3pt5"])
        ctx = FakeContext(SV151, cache_dir=cache)
        self.assertEqual(self.plugin.search_set([card("zz1-001", "Nobody")], ctx), [])
        self.assertEqual(ctx.queries, [])

    def test_forgets_a_set_it_couldnt_find_after_a_week(self):
        cache, now = tempfile.mkdtemp(), [1000.0]
        plugin = pulseapi.PulseAPI(sleep=self.slept.append, clock=lambda: now[0])
        plugin.search_set([card("zz1-001", "Nobody")], FakeContext(SV151, cache_dir=cache))
        now[0] += 8 * 86400
        ctx = FakeContext(SV151, cache_dir=cache)
        plugin.search_set([card("zz1-001", "Nobody")], ctx)
        self.assertTrue(ctx.queries)

    def test_skips_languages_pulsetcg_has_no_sets_for(self):
        german = MissingCard("sv03.5-199", "sv03.5", "151", "199", "", "German", "en")
        self.assertFalse(pulseapi.PLUGIN.handles(german))
        self.assertTrue(pulseapi.PLUGIN.handles(card("SV2a-006", lang="ja")))

    def test_a_refused_key_says_so(self):
        with self.assertRaisesRegex(RuntimeError, "PULSEAPI_KEY"):
            self.plugin.search_set([card("sv03.5-199")], FakeContext(SV151, status=401))


if __name__ == "__main__":
    unittest.main()

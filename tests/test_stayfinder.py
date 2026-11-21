"""Offline tests. Run: python -m unittest discover -s tests"""
import copy
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stayfinder import airbnb, areas, config, page, photos, pipeline  # noqa: E402
from stayfinder.rules import apply_rules, compile_rules  # noqa: E402
import fixtures as fx  # noqa: E402

TODAY = dt.date(2030, 1, 1)
SQUARE = [[0.0, 0.0], [0.0, 0.01], [0.01, 0.01], [0.01, 0.0], [0.0, 0.0]]  # about 1.1 km a side
EAST = [[0.0, 0.01], [0.0, 0.02], [0.01, 0.02], [0.01, 0.01], [0.0, 0.01]]


def base_cfg(**over):
    c = {"title": "Test stay", "areas": ["Old Town, Example City, Nowhere"], "checkin": "2030-02-01",
         "checkout": "2030-02-08", "adults": 2, "currency": "EUR", "max_total": 600, "output": "out/test.html"}
    c.update(over)
    return c


class SearchPageTest(unittest.TestCase):
    def test_parses_results_and_skips_ads(self):
        st = fx.search_state([fx.search_result(111, "Sunny flat", 1.5, 2.5, "€1,234"),
                              {"__typename": "ExploreAd"},
                              fx.search_result(222, "Cosy loft", 1.6, 2.6, "€500", discounted="€450")],
                             count=2, cursors=["a", "b"])
        p = airbnb.parse_search_page(st, 7)
        self.assertEqual(p["count"], 2)
        self.assertEqual(p["cursors"], ["a", "b"])
        self.assertEqual([i["id"] for i in p["items"]], ["111", "222"])
        self.assertEqual(p["items"][0]["total"], 1234.0)
        self.assertIsNone(p["items"][0]["total_before_discount"])
        self.assertEqual(p["items"][1]["total"], 450.0)
        self.assertEqual(p["items"][1]["total_before_discount"], 500.0)
        self.assertEqual((p["items"][0]["lat"], p["items"][0]["lng"]), (1.5, 2.5))

    def test_missing_search_block_raises(self):
        with self.assertRaises(airbnb.AirbnbFormatError):
            airbnb.parse_search_page({"niobeClientData": []}, 7)

    def test_missing_count_raises(self):
        st = fx.search_state([fx.search_result(1, "x", 0, 0)])
        del st["niobeClientData"][0][1]["data"]["presentation"]["staysSearch"]["results"]["filters"]
        with self.assertRaisesRegex(airbnb.AirbnbFormatError, "resultCount"):
            airbnb.parse_search_page(st, 7)

    def test_bad_listing_id_raises(self):
        r = fx.search_result(1, "x", 0, 0)
        r["demandStayListing"]["id"] = "U29tZXRoaW5nOjE="  # "Something:1"
        with self.assertRaises(airbnb.AirbnbFormatError):
            airbnb.parse_search_page(fx.search_state([r]), 7)


class PriceTest(unittest.TestCase):
    def test_total(self):
        self.assertEqual(airbnb.parse_price({"primaryLine": {"price": "€2,345", "qualifier": "for 7 nights"}}, 7),
                         (2345.0, None))
        self.assertEqual(airbnb.parse_price({"primaryLine": {"price": "$99", "qualifier": "for 1 night"}}, 1),
                         (99.0, None))

    def test_discount(self):
        spd = {"primaryLine": {"discountedPrice": "€380", "originalPrice": "€420", "qualifier": "for 7 nights"}}
        self.assertEqual(airbnb.parse_price(spd, 7), (380.0, 420.0))

    def test_per_night_or_wrong_nights_raises(self):
        with self.assertRaises(airbnb.AirbnbFormatError):
            airbnb.parse_price({"primaryLine": {"price": "€60", "qualifier": "night"}}, 7)
        with self.assertRaises(airbnb.AirbnbFormatError):
            airbnb.parse_price({"primaryLine": {"price": "€600", "qualifier": "for 5 nights"}}, 7)

    def test_missing_or_unreadable_raises(self):
        with self.assertRaises(airbnb.AirbnbFormatError):
            airbnb.parse_price({}, 7)
        with self.assertRaises(airbnb.AirbnbFormatError):
            airbnb.parse_price({"primaryLine": {"price": "free", "qualifier": "for 7 nights"}}, 7)


class SearchTileTest(unittest.TestCase):
    """search_tile against a fake Airbnb that returns a fixed set of pins, 2 per page."""

    def setUp(self):
        self.pins = [(f"{i}", 0.001 + 0.0024 * (i % 4), 0.001 + 0.0024 * (i // 4)) for i in range(16)]

    def fake(self, per_page=2, max_pages=3, dup_page=False):
        cfg = {"checkin": "2030-02-01", "checkout": "2030-02-08", "adults": 1, "currency": "EUR",
               "max_total": 600, "entire_place": False, "amenity_ids": []}
        calls = []

        def fetch(url):
            calls.append(url)
            q = dict(re.findall(r"([a-z_]+)=([^&]+)", url))
            s, w, n, e = (float(q[k]) for k in ("sw_lat", "sw_lng", "ne_lat", "ne_lng"))
            inside = [p for p in self.pins if s <= p[1] < n and w <= p[2] < e]
            pages = [inside[i:i + per_page] for i in range(0, len(inside), per_page)][:max_pages] or [[]]
            idx = int(q.get("cursor", "p0")[1:])
            page_items = pages[idx] if idx < len(pages) else []
            if dup_page and idx == 1:
                page_items = pages[0]  # Airbnb repeating a page
            res = [fx.search_result(int(i), "n" + i, la, lo) for i, la, lo in page_items]
            return fx.search_state(res, count=len(inside), cursors=[f"p{k}" for k in range(len(pages))] if inside else [])
        return cfg, fetch, calls

    def test_finds_everything_by_splitting(self):
        cfg, fetch, calls = self.fake()
        found, short = airbnb.search(cfg, 7, [0, 0, 0.01, 0.01], fetch, log=lambda *_: None)
        self.assertEqual(sorted(found, key=int), [p[0] for p in self.pins])
        self.assertEqual(short, 0)

    def test_repeated_pages_trigger_a_split(self):
        cfg, fetch, calls = self.fake(per_page=2, max_pages=10, dup_page=True)
        found, short = airbnb.search(cfg, 7, [0, 0, 0.01, 0.01], fetch, log=lambda *_: None)
        self.assertEqual(len(found), 16)

    def test_reports_shortfall_it_cannot_recover(self):
        cfg, fetch, _ = self.fake(per_page=10)

        def lying(url):  # count one more than exists, on single-page boxes
            st = fetch(url)
            panel = st["niobeClientData"][0][1]["data"]["presentation"]["staysSearch"]["results"]["filters"]["filterPanel"]
            if panel["resultCount"]:
                panel["resultCount"] += 1
            return st
        found, short = airbnb.search(cfg, 7, [0, 0, 0.0048, 0.0048], lying, log=lambda *_: None)
        self.assertEqual(short, 1)
        self.assertEqual(len(found), 4)


class ListingTest(unittest.TestCase):
    def test_parse_listing(self):
        d = airbnb.parse_listing(fx.listing_state(original="Piso tranquilo."), "42")
        self.assertEqual(d["kind"], "Entire rental unit")
        self.assertEqual(d["overview"], ["1 bedroom", "2 beds", "1 bath"])
        self.assertEqual(d["rating"], 4.87)
        self.assertEqual(d["reviews"], 12)
        self.assertTrue(d["guest_favourite"])
        self.assertEqual(d["category_ratings"], {"CLEANLINESS": "4.9", "VALUE": "4.5"})
        self.assertEqual(len(d["photos"]), 3)
        self.assertEqual(d["rooms"], ["Living room", "Bedroom"])
        self.assertEqual(d["description"], "A quiet flat with a small balcony.")
        self.assertIn("Additional rules:\nNo parties. Pets are welcome.", d["house_rules"])
        self.assertIn("Up to 4 guests (max)", d["house_rules"])
        self.assertIn("Piso tranquilo.", d["original_text"])
        self.assertEqual(d["amenities"], ["Wifi", "Kitchen", "Free washer – In unit"])  # "Not included" dropped

    def test_unrated(self):
        d = airbnb.parse_listing(fx.listing_state(reviews=0), "42")
        self.assertIsNone(d["rating"])

    def test_no_photos_raises(self):
        with self.assertRaisesRegex(airbnb.AirbnbFormatError, "no photos"):
            airbnb.parse_listing(fx.listing_state(photos=[]), "42")

    def test_changed_format_raises_clearly(self):
        st = fx.listing_state()
        del st["niobeClientData"][0][1]["data"]["node"]["pdpPresentation"]["quality"]
        with self.assertRaisesRegex(airbnb.AirbnbFormatError, "listing 42: page data is missing 'quality'"):
            airbnb.parse_listing(st, "42")
        with self.assertRaisesRegex(airbnb.AirbnbFormatError, "no pdpPresentation"):
            airbnb.parse_listing({"niobeClientData": []}, "42")

    def test_state_from_html(self):
        body = '<html><script id="data-deferred-state-0" type="application/json">{"a": 1}</script></html>'
        self.assertEqual(airbnb.state_from_html(body, "u"), {"a": 1})
        with self.assertRaises(airbnb.AirbnbFormatError):
            airbnb.state_from_html("<html>captcha</html>", "u")


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.rules = compile_rules([
            {"label": "No pets", "action": "flag", "pattern": r"\bno pets\b"},
            {"label": "Pets welcome", "action": "highlight", "pattern": r"\bpets? (are )?welcome\b",
             "unless": r"\bnot\b"},
            {"label": "Shared bathroom", "action": "exclude", "pattern": r"shared bathroom"},
        ])

    def test_actions_and_quotes(self):
        hits = apply_rules(self.rules, ["Lovely flat. Pets are welcome!", "Note: SHARED BATHROOM on the landing."])
        self.assertEqual([h["label"] for h in hits["highlight"]], ["Pets welcome"])
        self.assertEqual(hits["highlight"][0]["quote"], "Pets are welcome!")
        self.assertEqual([h["label"] for h in hits["exclude"]], ["Shared bathroom"])
        self.assertEqual(hits["flag"], [])

    def test_unless_cancels_in_same_sentence_only(self):
        hits = apply_rules(self.rules, ["Pets welcome is not true here."])
        self.assertEqual(hits["highlight"], [])
        hits = apply_rules(self.rules, ["Pets welcome. We are not on a busy street."])
        self.assertEqual(len(hits["highlight"]), 1)

    def test_bad_rules_raise(self):
        for bad in ([{"label": "x", "pattern": "a", "action": "drop"}],
                    [{"label": "", "pattern": "a", "action": "flag"}],
                    [{"label": "x", "pattern": "(", "action": "flag"}],
                    [{"label": "x", "pattern": "a", "action": "flag", "typo": 1}],
                    [{"label": "x", "pattern": "a", "action": "flag"}, {"label": "x", "pattern": "b", "action": "flag"}]):
            with self.assertRaises(ValueError):
                compile_rules(bad)


class AreaTest(unittest.TestCase):
    def setUp(self):
        self.areas = {"West": [SQUARE], "East": [EAST]}

    def test_inside(self):
        self.assertEqual(areas.area_of(0.005, 0.005, self.areas, 0.1), "West")
        self.assertEqual(areas.area_of(0.005, 0.015, self.areas, 0.1), "East")

    def test_border_tolerance(self):
        # about 0.11 km north of West's top edge
        self.assertEqual(areas.area_of(0.011, 0.005, self.areas, 0.2), "West")
        self.assertIsNone(areas.area_of(0.011, 0.005, self.areas, 0.05))
        self.assertIsNone(areas.area_of(0.05, 0.05, self.areas, 0.5))

    def test_parse_geocode(self):
        res = [{"display_name": "A point", "geojson": {"type": "Point", "coordinates": [1, 2]}},
               {"display_name": "Old Town", "geojson": {"type": "Polygon",
                                                        "coordinates": [[[2, 1], [3, 1], [3, 2], [2, 1]]]}}]
        name, rings = areas.parse_geocode(res, "Old Town")
        self.assertEqual(name, "Old Town")
        self.assertEqual(rings, [[[1, 2], [1, 3], [2, 3], [1, 2]]])  # [lat, lng]
        with self.assertRaisesRegex(RuntimeError, "no area outline"):
            areas.parse_geocode(res[:1], "Nowhere")

    def test_pad_bbox(self):
        s, w, n, e = areas.pad_bbox([0, 0, 0.01, 0.01], 1.1057)
        self.assertAlmostEqual(s, -0.01, places=4)
        self.assertAlmostEqual(n, 0.02, places=4)


class ConfigTest(unittest.TestCase):
    def test_valid_with_defaults(self):
        c = config.validate(base_cfg(), TODAY)
        self.assertEqual(c["max_listings"], 300)
        self.assertEqual(c["rules"], [])
        self.assertEqual(config.nights(c), 7)

    def test_example_config_is_valid(self):
        raw = json.loads((ROOT / "config.example.json").read_text())
        config.validate(raw, dt.date.fromisoformat(raw["checkin"]) - dt.timedelta(days=1))

    def test_all_problems_reported(self):
        bad = base_cfg(checkout="2030-01-20", adults=0, currency="euro", max_total=-1, colour="red",
                       bbox=[0, 0, 1, 1], rules=[{"label": "x", "pattern": "(", "action": "flag"}])
        with self.assertRaises(config.ConfigError) as cm:
            config.validate(bad, TODAY)
        msg = str(cm.exception)
        for needle in ("checkout: must be after checkin", "adults", "currency", "max_total", "colour: unknown key",
                       "exactly one of 'areas'", "rules:"):
            self.assertIn(needle, msg)

    def test_past_checkin(self):
        with self.assertRaisesRegex(config.ConfigError, "in the past"):
            config.validate(base_cfg(checkin="2029-12-01", checkout="2029-12-05"), TODAY)
        config.validate(base_cfg(checkin="2029-12-01", checkout="2029-12-05"), TODAY, allow_past=True)

    def test_judgements_key(self):
        self.assertIsNone(config.validate(base_cfg(), TODAY)["judgements"])
        self.assertEqual(config.validate(base_cfg(judgements="j.json"), TODAY)["judgements"], "j.json")
        config.validate(base_cfg(judgements=["a.json", "b.json"]), TODAY)
        for bad in ("j.txt", 3, [], ["a.json", 1]):
            with self.assertRaisesRegex(config.ConfigError, "judgements:"):
                config.validate(base_cfg(judgements=bad), TODAY)

    def test_area_labels_must_differ(self):
        with self.assertRaisesRegex(config.ConfigError, "same label"):
            config.validate(base_cfg(areas=["Centro, A, X", "Centro, B, Y"]), TODAY)


def make_records(cfg):
    """Records as pipeline.build makes them, from synthetic listing pages."""
    rules = compile_rules(cfg["rules"])
    studio = re.compile(cfg["studio_pattern"], re.I)
    specs = [
        ("101", "Sunny flat with balcony", 0.004, 0.004, 320, "West", fx.listing_state()),
        ("102", "Studio near the square", 0.006, 0.006, 410, "West",
         fx.listing_state(description="Compact studio.", rules_text="No pets please.", reviews=0)),
        ("103", "Quiet loft </script><b>x</b>", 0.004, 0.014, 590, "East",
         fx.listing_state(description="Big windows.", rules_text="Shared bathroom with one other guest.")),
        ("104", "Family home", 0.006, 0.016, 480, "East",
         fx.listing_state(description="Two floors, terrace.", rules_text="Quiet hours.", rating=4.6, reviews=40,
                          amenities=["Wifi", "Kitchen"])),
    ]
    out = []
    for lid, name, la, lo, total, area, st in specs:
        cand = {"id": lid, "name": name, "lat": la, "lng": lo, "total": total, "total_before_discount": None,
                "area": area}
        out.append(pipeline.listing_record(cfg, cand, airbnb.parse_listing(st, lid), rules, studio))
    return out


PAGE_CFG = base_cfg(
    areas=["West, Example City, Nowhere", "East, Example City, Nowhere"],
    studio_pattern=r"\b(studio|loft)\b", amenity_filters=["Washer"],
    rules=[{"label": "No pets", "action": "flag", "pattern": r"\bno pets\b"},
           {"label": "Pets welcome", "action": "highlight", "pattern": r"\bpets? (are )?welcome\b"},
           {"label": "Terrace", "action": "highlight", "pattern": r"\bterrace\b"},
           {"label": "Shared bathroom", "action": "exclude", "pattern": r"\bshared bathroom\b"}])


class PageTest(unittest.TestCase):
    def build(self):
        cfg = config.validate(copy.deepcopy(PAGE_CFG), TODAY)
        recs = make_records(cfg)
        stats = {"search_results": 9, "over_budget": 1, "outside_areas": 2, "not_returned": 0,
                 "below_min_rating": 0, "listed": len(recs), "excluded": 1}
        return recs, page.build_page(cfg, recs, {"West": [SQUARE], "East": [EAST]}, [0, 0, 0.01, 0.02], stats,
                                     "2030-01-01T10:00", storage_key="test.html")

    def test_records(self):
        recs = {r["id"]: r for r in self.build()[0]}
        self.assertEqual(recs["102"]["studio"], "Studio near the square")
        self.assertEqual([h["label"] for h in recs["102"]["flag"]], ["No pets"])
        self.assertEqual([h["label"] for h in recs["101"]["highlight"]], ["Pets welcome"])
        self.assertEqual([h["label"] for h in recs["103"]["exclude"]], ["Shared bathroom"])
        self.assertTrue(recs["101"]["am"]["Washer"])
        self.assertFalse(recs["104"]["am"]["Washer"])

    def test_html_is_self_contained_and_safe(self):
        _, html = self.build()
        self.assertEqual(html.count("</script>"), 1)  # the listing name's </script> did not leak
        self.assertIn("<title>Test stay</title>", html)
        self.assertIn('id="fArea1"', html)
        self.assertIn('id="fFlag0"', html)
        self.assertIn('id="fHl1"', html)
        self.assertIn('id="fAm0"', html)
        self.assertIn('id="fStudio"', html)
        self.assertNotIn("{{", html)
        app = json.loads(re.search(r"const APP = (.*?);\n", html).group(1))
        self.assertEqual(app["key"], "stayfinder:test.html")
        self.assertEqual([l[2] for l in app["labels"]], ["West", "East"])
        self.assertAlmostEqual(app["labels"][0][0], 0.005, places=6)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_page_script_in_node(self):
        _, html = self.build()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "page.html"
            p.write_text(html, encoding="utf-8")
            harness = str(Path(__file__).with_name("page_harness.js"))
            r = subprocess.run(["node", harness, str(p)], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["steps"]["initial"], 3)  # 103 is excluded
            self.assertEqual(out["steps"]["excluded"], 1)
            self.assertEqual(out["steps"]["area_off_East"], 2)
            self.assertEqual(out["steps"]["no_studios"], 2)
            self.assertEqual(out["steps"]["hide_No pets"], 2)
            self.assertEqual(out["steps"]["must_Terrace"], 1)
            self.assertEqual(out["steps"]["amenity_Washer"], 2)
            self.assertFalse(out["storeWarnVisible"])
            blocked = subprocess.run(["node", harness, str(p), "--block-storage"], capture_output=True, text=True,
                                     timeout=60)
            self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
            self.assertTrue(json.loads(blocked.stdout)["storeWarnVisible"])


JUDGED = {"101": {"verdict": "great", "note": "Bright living room with two big windows."},
          "102": {"verdict": "no", "note": "Dark room facing a wall, a studio with a curtain."},
          "103": {"verdict": "ok", "note": "Excluded by rules but judged anyway."}}  # 104 is not checked


class JudgementsTest(unittest.TestCase):
    IDS = {"101", "102", "103", "104"}

    def load(self, *files):
        with tempfile.TemporaryDirectory() as d:
            names = []
            for i, data in enumerate(files):
                (Path(d) / f"j{i}.json").write_text(data if isinstance(data, str) else json.dumps(data))
                names.append(f"j{i}.json")
            return photos.load_judgements(names[0] if len(names) == 1 else names, d, self.IDS)

    def test_valid(self):
        self.assertEqual(self.load(JUDGED)["101"], JUDGED["101"])
        both = self.load({"101": JUDGED["101"]}, {"102": JUDGED["102"]})
        self.assertEqual(sorted(both), ["101", "102"])

    def test_every_problem_is_reported(self):
        bad = {"999": JUDGED["101"],
               "101": {"verdict": "nice", "note": "x"},
               "102": {"verdict": "ok", "note": "  "},
               "103": {"verdict": "ok"},
               "104": {"verdict": "ok", "note": "x", "score": 3}}
        with self.assertRaises(photos.JudgementError) as cm:
            self.load(bad)
        msg = str(cm.exception)
        for needle in ("'999' is not on the page", "listing 101: verdict must be one of", "listing 102: note must be",
                       "listing 103: missing ['note']", "listing 104: unknown keys ['score']"):
            self.assertIn(needle, msg)

    def test_bad_files(self):
        with self.assertRaisesRegex(photos.JudgementError, "not valid JSON"):
            self.load("{oops")
        with self.assertRaisesRegex(photos.JudgementError, "must be a JSON object"):
            self.load([1, 2])
        with self.assertRaisesRegex(photos.JudgementError, "judged twice"):
            self.load({"101": JUDGED["101"]}, {"101": JUDGED["102"]})
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(photos.JudgementError, "not found"):
            photos.load_judgements("missing.json", d, self.IDS)


class JudgedPageTest(unittest.TestCase):
    def build(self, judged):
        cfg = config.validate(copy.deepcopy(PAGE_CFG), TODAY)
        recs = make_records(cfg)
        if judged:
            for r in recs:
                r["photo"] = JUDGED.get(r["id"])
        stats = {"search_results": 9, "over_budget": 1, "outside_areas": 2, "not_returned": 0,
                 "below_min_rating": 0, "listed": len(recs), "excluded": 1}
        return page.build_page(cfg, recs, {"West": [SQUARE], "East": [EAST]}, [0, 0, 0.01, 0.02], stats,
                               "2030-01-01T10:00", storage_key="test.html", judged=judged)

    def test_controls_only_with_judgements(self):
        plain, judged = self.build(False), self.build(True)
        self.assertNotIn('id="fPhoto"', plain)
        self.assertNotIn('value="photos"', plain)
        self.assertIn('id="fPhoto"', judged)
        self.assertIn('<option value="photos">best photos first</option>', judged)
        app = json.loads(re.search(r"const APP = (.*?);\n", judged).group(1))
        self.assertTrue(app["judged"])
        by = {d["id"]: d for d in app["listings"]}
        self.assertEqual(by["101"]["photo"], JUDGED["101"])
        self.assertIsNone(by["104"]["photo"])
        self.assertFalse(json.loads(re.search(r"const APP = (.*?);\n", plain).group(1))["judged"])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_photo_filter_and_sort_in_node(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "page.html"
            p.write_text(self.build(True), encoding="utf-8")
            r = subprocess.run(["node", str(Path(__file__).with_name("page_harness.js")), str(p)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["steps"]["initial"], 2)       # 103 excluded by rules, 102 judged "no"
            self.assertEqual(out["steps"]["photo_ok"], 2)      # 101 great, 104 not checked
            self.assertEqual(out["steps"]["photo_great"], 1)
            self.assertEqual(out["steps"]["photo_all"], 3)
            self.assertEqual(out["steps"]["photo_order"], ["101", "104", "102"])
            self.assertEqual(out["steps"]["no_studios"], 2)   # the other filters still count every listing
            self.assertIn("photos: not checked", out["legend"])
            self.assertIn("first photos of each listing, which the host chose", out["header"]["notes"])


def cached_trip(d, **over):
    """A config, a cached run and cached listing pages in folder d, as `run` leaves them."""
    raw = copy.deepcopy(PAGE_CFG) | over
    cfg_path = Path(d) / "trip.json"
    cfg_path.write_text(json.dumps(raw))
    cfg = config.load(cfg_path, TODAY, allow_past=True)
    cache = pipeline.Cache(Path(d) / ".cache")
    recs = make_records(cfg)
    photo_sets = {"101": [("Living room", "https://img.example/a1.jpg"), ("Living room", "https://img.example/a2.jpg"),
                          ("Rooftop / Pool!", "https://img.example/a3.jpg")],
                  "104": [("", "https://img.example/d1.jpg")]}
    for r in recs:
        cache.get(airbnb.listing_url(cfg, r["id"]),
                  lambda url, r=r: fx.listing_state(rules_text="Shared bathroom." if r["id"] == "103" else "Ok.",
                                                    photos=photo_sets.get(r["id"])))
    cands = [{k: r[k] for k in ("id", "name", "lat", "lng", "total", "area")} | {"total_before_discount": None}
             for r in recs]
    cache.run_file(cfg_path).write_text(json.dumps({
        "search": config.search_fingerprint(cfg), "searched_at": "2030-01-01T10:00",
        "areas": {"West": [SQUARE], "East": [EAST]}, "bbox": [0, 0, 0.01, 0.02],
        "counts": {"search_results": 4, "over_budget": 0, "outside_areas": 0, "not_returned": 0},
        "candidates": cands}))
    return raw, cfg_path


JPEG = b"\xff\xd8\xff\xe0fake jpeg"


class PhotosTest(unittest.TestCase):
    def test_download_paths_and_index(self):
        with tempfile.TemporaryDirectory() as d:
            _, cfg_path = cached_trip(d)
            got = []
            s = pipeline.photos(cfg_path, max_n=2, log=lambda *_: None, download=lambda u: got.append(u) or JPEG)
            folder = Path(d).resolve() / ".cache" / "photos"
            self.assertEqual(s["folder"], folder)
            self.assertEqual((s["listings"], s["photos"], s["downloaded"]), (3, 5, 5))  # 103 is excluded by rules
            self.assertIn("https://img.example/a1.jpg?im_w=480", got)
            self.assertNotIn("https://img.example/a3.jpg?im_w=480", got)  # beyond --max 2
            self.assertEqual(sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.jpg")),
                             ["101/01-living-room.jpg", "101/02-living-room.jpg", "102/01-living-room.jpg",
                              "102/02-living-room.jpg", "104/01-unlabelled.jpg"])
            self.assertEqual((folder / "101" / "01-living-room.jpg").read_bytes(), JPEG)
            idx = json.loads((folder / "index.json").read_text())
            self.assertEqual(sorted(idx["listings"]), ["101", "102", "104"])
            e = idx["listings"]["101"]
            self.assertEqual((e["name"], e["area"], e["price"]), ("Sunny flat with balcony", "West", 320))
            self.assertEqual(e["overview"], ["1 bedroom", "2 beds", "1 bath"])
            self.assertEqual(e["photos_in_tour"], 3)
            self.assertEqual(e["photos"][0], {"file": "101/01-living-room.jpg", "room": "Living room",
                                              "url": "https://img.example/a1.jpg?im_w=480"})

            # second run: everything is there, nothing is downloaded
            again = pipeline.photos(cfg_path, max_n=2, log=lambda *_: None,
                                    download=lambda u: self.fail("downloaded again: " + u))
            self.assertEqual(again["downloaded"], 0)
            # more photos per listing: only the new third photos are fetched, with their room names slugged
            got.clear()
            pipeline.photos(cfg_path, max_n=3, log=lambda *_: None, download=lambda u: got.append(u) or JPEG)
            self.assertEqual(sorted(got), ["https://img.example/3.jpg?im_w=480", "https://img.example/a3.jpg?im_w=480"])
            self.assertTrue((folder / "101" / "03-rooftop-pool.jpg").exists())

    def test_same_file_name_with_another_photo_is_fetched_again(self):
        with tempfile.TemporaryDirectory() as d:
            _, cfg_path = cached_trip(d)
            pipeline.photos(cfg_path, max_n=1, log=lambda *_: None, download=lambda u: JPEG)
            index = Path(d) / ".cache" / "photos" / "index.json"
            idx = json.loads(index.read_text())
            idx["listings"]["101"]["photos"][0]["url"] = "https://img.example/old.jpg?im_w=480"
            index.write_text(json.dumps(idx))
            got = []
            pipeline.photos(cfg_path, max_n=1, log=lambda *_: None, download=lambda u: got.append(u) or JPEG)
            self.assertEqual(got, ["https://img.example/a1.jpg?im_w=480"])

    def test_errors_name_the_listing(self):
        with tempfile.TemporaryDirectory() as d:
            _, cfg_path = cached_trip(d)

            def broken(url):
                if "d1" in url:
                    raise RuntimeError("HTTP 404")
                return JPEG
            with self.assertRaisesRegex(RuntimeError, r"listing 104: photo 1 could not be downloaded .*HTTP 404"):
                pipeline.photos(cfg_path, log=lambda *_: None, download=broken)
            with self.assertRaisesRegex(RuntimeError, r"listing \d+: photo \d+ is not a JPEG"):
                pipeline.photos(cfg_path, log=lambda *_: None, download=lambda u: b"<html>blocked</html>")
            with self.assertRaisesRegex(ValueError, "--max"):
                pipeline.photos(cfg_path, max_n=0, log=lambda *_: None, download=lambda u: JPEG)

    def test_slug(self):
        self.assertEqual(photos.slug("Full bathroom 2"), "full-bathroom-2")
        self.assertEqual(photos.slug("Común / Área"), "comun-area")
        self.assertEqual(photos.slug(""), "unlabelled")


class CacheBuildTest(unittest.TestCase):
    """pipeline.build reads only the cache, and refuses a cache made with other search settings."""

    def test_build_from_cache(self):
        with tempfile.TemporaryDirectory() as d:
            raw = copy.deepcopy(PAGE_CFG)
            cfg_path = Path(d) / "trip.json"
            cfg_path.write_text(json.dumps(raw))
            cfg = config.load(cfg_path, TODAY, allow_past=True)
            cache = pipeline.Cache(Path(d) / ".cache")
            recs = make_records(cfg)
            for r in recs:
                cache.get(airbnb.listing_url(cfg, r["id"]),
                          lambda url, r=r: fx.listing_state(rules_text="Shared bathroom." if r["id"] == "103" else "Ok."))
            cands = [{k: r[k] for k in ("id", "name", "lat", "lng", "total", "area")} | {"total_before_discount": None}
                     for r in recs]
            cache.run_file(cfg_path).write_text(json.dumps({
                "search": config.search_fingerprint(cfg), "searched_at": "2030-01-01T10:00",
                "areas": {"West": [SQUARE], "East": [EAST]}, "bbox": [0, 0, 0.01, 0.02],
                "counts": {"search_results": 4, "over_budget": 0, "outside_areas": 0, "not_returned": 0},
                "candidates": cands}))
            out = pipeline.build(cfg_path, log=lambda *_: None)
            self.assertTrue(out.exists())
            self.assertEqual(out, (Path(d) / "out" / "test.html").resolve())

            raw["max_total"] = 999
            cfg_path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(RuntimeError, "max_total"):
                pipeline.build(cfg_path, log=lambda *_: None)

    def test_build_merges_judgements(self):
        with tempfile.TemporaryDirectory() as d:
            raw, cfg_path = cached_trip(d, judgements="verdicts/photos.json")
            (Path(d) / "verdicts").mkdir()
            (Path(d) / "verdicts" / "photos.json").write_text(json.dumps(JUDGED))
            out = pipeline.build(cfg_path, log=lambda *_: None)
            app = json.loads(re.search(r"const APP = (.*?);\n", out.read_text()).group(1))
            self.assertTrue(app["judged"])
            self.assertEqual({d["id"]: d["photo"] and d["photo"]["verdict"] for d in app["listings"]},
                             {"101": "great", "102": "no", "103": "ok", "104": None})

            (Path(d) / "verdicts" / "photos.json").write_text(json.dumps({"555": JUDGED["101"]}))
            with self.assertRaisesRegex(photos.JudgementError, "'555' is not on the page"):
                pipeline.build(cfg_path, log=lambda *_: None)


if __name__ == "__main__":
    unittest.main()

"""The map's two scripts, run in node (2026-10-06), as the bell's is in
``toto.notify.tests_bell``: what is sent, when, and under which ``op``.

    manage.py test toto.geography.tests_map_js
"""

import json
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

_NODE = shutil.which("node")

#: A page around the controller: a recording fetch whose answers the script
#: under test queues, and storage, history and location that record any touch.
HARNESS = r"""
const touched = [];
function spy(name) {
  return new Proxy({}, {
    get(_t, key) { touched.push(name + "." + String(key)); return () => null; },
    set(_t, key) { touched.push(name + "." + String(key) + "="); return true; },
  });
}
globalThis.localStorage = spy("localStorage");
globalThis.sessionStorage = spy("sessionStorage");
globalThis.history = spy("history");
globalThis.location = spy("location");
const calls = [];
const queue = [];
function fetchStub(url, init) {
  calls.push({url: url, method: init.method, body: JSON.parse(init.body), headers: init.headers});
  const next = queue.length ? queue.shift() : {status: 200, data: {}};
  if (next.network) { return Promise.reject(new Error("offline")); }
  return Promise.resolve({ok: next.status >= 200 && next.status < 300, status: next.status,
                          json: () => Promise.resolve(next.data)});
}
const GeographyMap = require(process.argv[1]);
const GeographyZone = require(process.argv[2]);
const urls = {search: "/geography/api/search/", route: "/geography/api/route/",
              savePoint: "/geography/me/address/", clearPoint: "/geography/me/address/clear/",
              saveZone: "/geography/communities/g/zone/", clearZone: "/geography/communities/g/zone/clear/"};
const saved = [{kind: "address", id: 41, pk: 41, lat: 52.2297, lng: 21.0122, label: "Home"}];
const controller = GeographyMap.createController({fetch: fetchStub, urls: urls, csrf: "tok", saved: saved});
const HITS = {results: [{label: "Warszawa", lat: 52.23, lng: 21.01}, {label: "Warszawa, USA", lat: 40.1, lng: -75.2}]};
(async () => {
  const out = {};
  __BODY__
  out.touched = touched;
  out.calls = calls;
  console.log(JSON.stringify(out));
})().catch((error) => { console.error(error); process.exit(1); });
"""

UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


@skipUnless(_NODE, "node is not installed")
class MapScriptTests(SimpleTestCase):
    def run_js(self, body):
        map_js, zone_js = finders.find("geography/map.js"), finders.find("geography/zone_draw.js")
        self.assertTrue(map_js and zone_js)
        done = subprocess.run([_NODE, "-e", HARNESS.replace("__BODY__", body), map_js, zone_js],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_a_temporary_point_makes_no_request_and_writes_no_storage(self):
        out = self.run_js("""
          const a = controller.placeTemporary(52.123456789, 21.5);
          const b = controller.placeTemporary(50.06, 19.94, "Kraków");
          controller.removeTemporary(a.id);
          out.temporary = controller.state.temporary;
          out.ends = controller.ends().length;
        """)
        self.assertEqual(out["calls"], [])
        self.assertEqual(out["touched"], [])
        self.assertEqual(out["temporary"], [{"id": "t2", "kind": "temporary", "lat": 50.06,
                                             "lng": 19.94, "label": "Kraków"}])
        self.assertEqual(out["ends"], 2)

    def test_a_second_press_of_search_mints_a_new_op(self):
        out = self.run_js("""
          queue.push({status: 200, data: HITS}, {status: 200, data: HITS});
          await controller.search("  Warsaw ");
          await controller.search("Warsaw");
          out.hits = controller.state.hits;
        """)
        first, second = out["calls"]
        self.assertEqual((first["url"], first["method"]), ("/geography/api/search/", "POST"))
        self.assertEqual(first["body"]["q"], "Warsaw")
        self.assertRegex(first["body"]["op"], UUID)
        self.assertRegex(second["body"]["op"], UUID)
        self.assertNotEqual(first["body"]["op"], second["body"]["op"])
        self.assertEqual(set(first["body"]), {"q", "op"})
        self.assertEqual(first["headers"]["X-CSRFToken"], "tok")
        self.assertEqual([hit["label"] for hit in out["hits"]], ["Warszawa", "Warszawa, USA"])
        self.assertEqual(out["touched"], [], "the hits live in the page only")

    def test_a_retry_repeats_the_op(self):
        out = self.run_js("""
          queue.push({network: true}, {status: 503, data: {error: "down"}},
                     {status: 429, data: {}}, {status: 200, data: HITS},
                     {status: 200, data: HITS});
          for (let i = 0; i < 5; i++) { await controller.search("Warsaw"); }
        """)
        ops = [call["body"]["op"] for call in out["calls"]]
        self.assertEqual(len(set(ops[:4])), 1, "unanswered: the same op again")
        self.assertNotEqual(ops[4], ops[3], "answered: the next press is a new one")

    def test_another_query_after_a_failure_is_a_new_op(self):
        out = self.run_js("""
          queue.push({network: true}, {status: 200, data: HITS});
          await controller.search("Warsaw");
          await controller.search("Gdansk");
        """)
        ops = [call["body"]["op"] for call in out["calls"]]
        self.assertNotEqual(ops[0], ops[1])

    def test_a_refusal_ends_the_op(self):
        out = self.run_js("""
          queue.push({status: 402, data: {error: "no mana"}}, {status: 409, data: {}},
                     {status: 200, data: HITS});
          for (let i = 0; i < 3; i++) { await controller.search("Warsaw"); }
        """)
        ops = [call["body"]["op"] for call in out["calls"]]
        self.assertEqual(len(set(ops)), 3)

    def test_the_route_request_holds_two_coordinate_pairs_and_no_id(self):
        out = self.run_js("""
          queue.push({status: 200, data: HITS},
                     {status: 200, data: {line: {type: "LineString", coordinates: [[21, 52], [19, 50]]},
                                          distance_km: 290.1, duration_min: 200.5, charged: true}});
          await controller.search("Warsaw");
          const spot = controller.placeTemporary(50.0614, 19.9366);
          const ends = controller.ends();
          out.kinds = ends.map((end) => end.kind);
          await controller.route(ends[0], spot, "bicycle");
          out.route = controller.state.route;
        """)
        self.assertEqual(out["kinds"], ["address", "hit", "hit", "temporary"])
        route = out["calls"][1]
        self.assertEqual(route["url"], "/geography/api/route/")
        self.assertEqual(set(route["body"]), {"from", "to", "mode", "op"})
        self.assertEqual(route["body"]["from"], {"lat": 52.2297, "lng": 21.0122})
        self.assertEqual(route["body"]["to"], {"lat": 50.0614, "lng": 19.9366})
        self.assertEqual(route["body"]["mode"], "bicycle")
        self.assertRegex(route["body"]["op"], UUID)
        text = json.dumps(route["body"])
        for word in ("41", "Home", "id", "pk", "kind", "label", "address"):
            self.assertNotIn(word, text.replace(route["body"]["op"], ""))
        self.assertEqual(out["route"]["distance_km"], 290.1)
        self.assertEqual(out["touched"], [], "the route lives in the page only")

    def test_no_route_and_a_refusal_leave_no_line(self):
        out = self.run_js("""
          queue.push({status: 200, data: {line: null, distance_km: null, duration_min: null, charged: false}},
                     {status: 402, data: {error: "no mana"}});
          const a = controller.placeTemporary(1, 1), b = controller.placeTemporary(2, 2);
          await controller.route(a, b, "car");
          out.first = controller.state.route;
          await controller.route(a, b, "car");
          out.second = controller.state.route;
        """)
        self.assertIsNone(out["first"])
        self.assertIsNone(out["second"])

    def test_saving_a_point_and_a_zone_carry_an_op_and_removing_does_not(self):
        out = self.run_js("""
          await controller.savePoint({lat: 52.22970012, lng: 21.0122, name: "Home", note: ""});
          await controller.saveZone({name: "Area", description: "", outline: [[52, 21], [52, 21.1], [52.1, 21.1]]});
          await controller.clearPoint();
          await controller.clearZone();
        """)
        point, zone, clear_point, clear_zone = out["calls"]
        self.assertEqual(set(point["body"]), {"lat", "lng", "name", "note", "op"})
        self.assertEqual(point["body"]["lat"], 52.2297)
        self.assertEqual(set(zone["body"]), {"name", "description", "outline", "op"})
        self.assertEqual(zone["body"]["outline"], [[52, 21], [52, 21.1], [52.1, 21.1]])
        self.assertEqual(clear_point["body"], {})
        self.assertEqual(clear_zone["body"], {})
        self.assertEqual(clear_point["url"], "/geography/me/address/clear/")

    def test_the_outline_editor_keeps_one_open_ring(self):
        out = self.run_js("""
          const outline = GeographyZone.createOutline();
          out.steps = [outline.add(52, 21), outline.add(52, 21), outline.add(52, 21.1),
                       outline.complete(), outline.add(52.1, 21.1), outline.complete()];
          outline.move(0, 51.9, 20.9);
          out.moved = outline.list();
          outline.remove(1);
          out.count = outline.count();
          outline.undo(); outline.reset();
          out.empty = outline.list();
          const full = GeographyZone.createOutline();
          for (let i = 0; i < 500; i++) { full.add(10 + i / 1000, 20); }
          out.full = [full.count(), full.add(50, 50), GeographyZone.MAX_CORNERS];
        """)
        self.assertEqual(out["steps"], [True, False, True, False, True, True])
        self.assertEqual(out["moved"], [[51.9, 20.9], [52, 21.1], [52.1, 21.1]])
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["empty"], [])
        self.assertEqual(out["full"], [500, False, 500])
        self.assertEqual(out["calls"], [])


class MapScriptSourceTests(SimpleTestCase):
    """What the two files may not hold, read as text (no node needed)."""

    def sources(self):
        return {name: Path(finders.find(f"geography/{name}")).read_text(encoding="utf-8")
                for name in ("map.js", "zone_draw.js", "pin.js")}

    def test_they_use_no_storage_no_address_bar_and_no_timer_loop(self):
        for name, source in self.sources().items():
            for word in ("localStorage", "sessionStorage", "indexedDB", "document.cookie",
                         "pushState", "replaceState", "location.hash", "location.search",
                         "setInterval", "sendBeacon", "XMLHttpRequest("):
                code = "\n".join(line for line in source.splitlines()
                                 if not line.strip().startswith(("*", "/*", "//")))
                self.assertNotIn(word, code, f"{name}: {word}")

    def test_they_name_no_outside_host(self):
        for name, source in self.sources().items():
            self.assertNotIn("http://www.w3.org/1999", source)
            hosts = [word for word in source.split() if "://" in word
                     and "www.w3.org/2000/svg" not in word]
            self.assertEqual(hosts, [], name)

    def test_the_pin_is_a_copy_under_this_app_s_own_path(self):
        pin = Path(finders.find("geography/pin.js"))
        self.assertIn("window.classicPin = function", pin.read_text(encoding="utf-8"))
        original = pin.parents[3] / "locations" / "static" / "oya" / "pin.js"
        if original.exists():
            self.assertEqual(pin.read_bytes(), original.read_bytes())

    def test_a_search_never_runs_while_typing(self):
        source = self.sources()["map.js"]
        self.assertIn('event.key === "Enter"', source)
        for event in ('"input"', '"keyup"', '"change"'):
            self.assertNotIn(f"addEventListener({event}", source)

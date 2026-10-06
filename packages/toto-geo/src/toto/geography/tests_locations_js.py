"""The Locations page's script, its half with no page in it, run in node
(stage 64, 2026-10-06): what is near what, what the filter lets through, and
one press of a charged control. "Search nearby" is a function of the rows the
page holds: it takes no fetch at all, so a centre cannot be sent.

    manage.py test toto.geography.tests_locations_js
"""

import json
import shutil
import subprocess
from unittest import skipUnless

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

_NODE = shutil.which("node")

HARNESS = r"""
const touched = [];
globalThis.fetch = () => { touched.push("fetch"); return Promise.reject(new Error("no")); };
const G = require(process.argv[1]);
const calls = [];
const queue = [];
function fetchStub(url, init) {
  calls.push({url: url, body: JSON.parse(init.body), headers: init.headers});
  const next = queue.length ? queue.shift() : {status: 200, data: {}};
  if (next.network) { return Promise.reject(new Error("offline")); }
  return Promise.resolve({ok: next.status >= 200 && next.status < 300, status: next.status,
                          json: () => Promise.resolve(next.data)});
}
let minted = 0;
const press = G.createPresser({fetch: fetchStub, csrf: "tok", mint: () => "op-" + (++minted)});
const WARSAW = {lat: 52.2297, lng: 21.0122};
const ROWS = [
  {id: "pin:a", kind: "pin", name: "Well", note: "open", lat: 52.2297, lng: 21.0122,
   community: {slug: "guild", name: "Guild"}},
  {id: "pin:b", kind: "pin", name: "Mill", note: "", lat: 52.2400, lng: 21.0122,
   community: {slug: "other", name: "Other"}},
  {id: "person:x", kind: "person", name: "Opal", note: "", lat: 52.4069, lng: 16.9299},
  {id: "zone:c", kind: "zone", name: "Meadow", note: "by the river",
   outline: [[52.30, 21.00], [52.30, 21.10], [52.40, 21.10], [52.40, 21.00]],
   community: {slug: "guild", name: "Guild"}},
];
(async () => {
  const out = {};
  __BODY__
  out.touched = touched;
  out.calls = calls;
  console.log(JSON.stringify(out));
})().catch((error) => { console.error(error); process.exit(1); });
"""


@skipUnless(_NODE, "node is not installed")
class LocationsScriptTests(SimpleTestCase):
    def run_js(self, body):
        script = finders.find("geography/locations.js")
        done = subprocess.run([_NODE, "-e", HARNESS.replace("__BODY__", body), script],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_nearby_inside_outside_and_the_edge(self):
        out = self.run_js("""
          out.one = G.nearby(ROWS, WARSAW, 1).map((x) => x.row.id);
          out.five = G.nearby(ROWS, WARSAW, 5).map((x) => x.row.id);
          out.ten = G.nearby(ROWS, WARSAW, 10).map((x) => [x.row.id, Math.round(x.km * 10) / 10]);
          out.far = G.nearby(ROWS, WARSAW, 300).map((x) => x.row.id);
          const d = G.haversineKm(WARSAW, ROWS[1]);
          out.edge = [G.nearby([ROWS[1]], WARSAW, d).length, G.nearby([ROWS[1]], WARSAW, d - 0.001).length];
          out.km = Math.round(d * 1000) / 1000;
        """)
        self.assertEqual(out["one"], ["pin:a"])
        self.assertEqual(out["five"], ["pin:a", "pin:b"])
        self.assertEqual([row for row, _km in out["ten"]], ["pin:a", "pin:b", "zone:c"])
        self.assertEqual(out["ten"][0][1], 0)
        self.assertEqual(out["far"], ["pin:a", "pin:b", "zone:c", "person:x"])
        self.assertEqual(out["edge"], [1, 0])           # at the radius: in; beyond: out
        self.assertAlmostEqual(out["km"], 1.145, delta=0.01)
        self.assertEqual(out["touched"], [])            # no request, whatever the centre
        self.assertEqual(out["calls"], [])

    def test_a_zone_is_near_when_the_centre_is_inside_or_an_edge_is_within(self):
        out = self.run_js("""
          const outline = ROWS[3].outline;
          out.inside = G.zoneKm({lat: 52.35, lng: 21.05}, outline);
          out.south = Math.round(G.zoneKm({lat: 52.25, lng: 21.05}, outline) * 10) / 10;
          out.corner = G.zoneKm({lat: 52.20, lng: 20.90}, outline) > 10;
          out.overlap = G.nearby([ROWS[3]], {lat: 52.25, lng: 21.05}, 10).length;
          out.miss = G.nearby([ROWS[3]], {lat: 52.25, lng: 21.05}, 5).length;
        """)
        self.assertEqual(out["inside"], 0)
        self.assertAlmostEqual(out["south"], 5.6, delta=0.2)
        self.assertTrue(out["corner"])
        self.assertEqual((out["overlap"], out["miss"]), (1, 0))

    def test_the_filter(self):
        out = self.run_js("""
          const ids = (f) => ROWS.filter((r) => G.passes(r, f)).map((r) => r.id);
          out.all = ids({});
          out.text = ids({text: "  RIVER "});
          out.kinds = ids({kinds: {pin: false}});
          out.community = ids({community: "guild"});
          out.both = ids({community: "guild", kinds: {zone: false}, text: "well"});
          out.byCommunityName = ids({text: "other"});
        """)
        self.assertEqual(len(out["all"]), 4)
        self.assertEqual(out["text"], ["zone:c"])
        self.assertEqual(out["kinds"], ["person:x", "zone:c"])
        self.assertEqual(out["community"], ["pin:a", "zone:c"])    # no person's point
        self.assertEqual(out["both"], ["pin:a"])
        self.assertEqual(out["byCommunityName"], ["pin:b"])

    def test_a_press_mints_once_and_repeats_only_what_was_never_answered(self):
        out = self.run_js("""
          queue.push({network: true}, {status: 503, data: {}}, {status: 200, data: {pin: {}}},
                     {status: 402, data: {error: "no mana"}}, {status: 200, data: {}});
          const body = {lat: 1, lng: 2, name: "A"};
          const a = await press("pin", "/p/", body);
          const b = await press("pin", "/p/", body);
          const c = await press("pin", "/p/", body);
          const d = await press("pin", "/p/", body);
          const e = await press("pin", "/p/", body);
          out.ops = [a.op, b.op, c.op, d.op, e.op];
          out.sent = calls.map((call) => call.body.op);
          queue.push({network: true});
          const f = await press("pin", "/p/", body);
          const g = await press("pin", "/p/", {lat: 1, lng: 2, name: "B"});
          const h = await press("pin", "/other/", {lat: 1, lng: 2, name: "B"});
          out.changed = [f.op === e.op, g.op === f.op, h.op === g.op];
          out.error = d.data.error;
        """)
        # Offline and a 5xx repeat the op; an answer (200, 402) ends it.
        self.assertEqual(out["ops"], ["op-1", "op-1", "op-1", "op-2", "op-3"])
        self.assertEqual(out["sent"], out["ops"])
        self.assertEqual(out["changed"], [False, False, False])
        self.assertEqual(out["error"], "no mana")
        call = out["calls"][0]
        self.assertEqual(call["headers"]["X-CSRFToken"], "tok")
        self.assertEqual(set(call["body"]), {"lat", "lng", "name", "op"})

    def test_longitudes_come_home(self):
        out = self.run_js("""
          out.wrap = [G.wrapLng(381), G.wrapLng(-339), G.wrapLng(21), G.wrapLng(180)];
          out.ring = G.homeRing([[52, 381], [52, 381.1], [52.1, 381.1]]);
          out.centre = G.centreOf(ROWS[3]);
        """)
        self.assertEqual(out["wrap"], [21, 21, 21, 180])
        self.assertEqual(out["ring"], [[52, 21], [52, 21.1], [52.1, 21.1]])
        self.assertAlmostEqual(out["centre"]["lat"], 52.35)

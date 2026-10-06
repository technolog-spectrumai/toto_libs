"""The map's two scripts, run in node (2026-10-06), as the bell's is in
``toto.notify.tests_bell``: what is sent, when, and under which ``op``.

Two harnesses. ``HARNESS`` runs the controller, the half with rules and no
page in it. ``PAGE`` runs ``mount()``, the half that draws: on a page faked
just far enough (the elements ``_map.html`` really has, read from the
template's own text) and a Leaflet that records what it is handed. No
browser draws anything here: what a member sees is the owner's by-hand note.

    manage.py test toto.geography.tests_map_js
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.contrib.staticfiles import finders
from django.template.loader import get_template
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

#: A page around ``mount()``: the elements the template has (argv[3]: their
#: names, tags and which start hidden), the page's config (argv[4]), a fetch
#: that records, storage that records any touch, and a Leaflet that keeps
#: what it is handed. ``innerHTML`` throws.
PAGE = r"""
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
globalThis.fetch = function (url, init) {
  calls.push({url: url, method: init.method, body: JSON.parse(init.body)});
  const next = queue.length ? queue.shift() : {status: 200, data: {}};
  if (next.network) { return Promise.reject(new Error("offline")); }
  return Promise.resolve({ok: next.status >= 200 && next.status < 300, status: next.status,
                          json: () => Promise.resolve(next.data)});
};
class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.dataset = {}; this.handlers = {};
    this._text = ""; this.value = ""; this.disabled = false; this.title = "";
    const names = new Set();
    this.classList = {
      contains: (name) => names.has(name), add: (name) => { names.add(name); },
      remove: (name) => { names.delete(name); },
      toggle: (name, on) => { const want = on === undefined ? !names.has(name) : !!on;
                              if (want) { names.add(name); } else { names.delete(name); } return want; }};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set innerHTML(_value) { throw new Error("innerHTML is never used"); }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); }
  fire(name, event) {
    (this.handlers[name] || []).forEach((fn) => fn(event || {preventDefault() {}, stopPropagation() {}}));
  }
  querySelector(selector) {
    const named = /^\[data-geo="([\w-]+)"\]$/.exec(selector);
    if (!named) { throw new Error("an unexpected selector: " + selector); }
    asked.add(named[1]);
    return this.parts[named[1]] || null;
  }
}
const asked = new Set();
const tips = [];
const page = {map: null};
function layer(kind, more) {
  return Object.assign({
    kind: kind, handlers: {},
    on(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); return this; },
    fire(name, event) { (this.handlers[name] || []).forEach((fn) => fn(event || {})); },
    addTo(target) { target.items.push(this); return this; },
    bindTooltip(content) { tips.push(content); this.tip = content; return this; },
    getBounds() { return {}; },
  }, more || {});
}
globalThis.L = {
  map(el, options) {
    page.map = layer("map", {
      el: el, options: options || {}, items: [], view: null,
      setView(center, zoom) { this.view = {center: [center[0], center[1]], zoom: zoom}; return this; },
      fitBounds() { return this; }, invalidateSize() {},
      removeLayer(gone) { this.items = this.items.filter((item) => item !== gone); },
      getCenter() { return {lat: this.view.center[0], lng: this.view.center[1]}; },
      getZoom() { return this.view.zoom; }});
    return page.map;
  },
  layerGroup() { return layer("group", {items: [], clearLayers() { this.items = []; }}); },
  marker(at, options) {
    return layer("marker", {at: [at[0], at[1]], options: options || {},
                            setLatLng(to) { this.at = [to[0], to[1]]; },
                            getLatLng() { return {lat: this.at[0], lng: this.at[1]}; }});
  },
  circleMarker(at) { return layer("circle", {at: [at[0], at[1]]}); },
  polygon(list) { return layer("polygon", {list: list}); },
  polyline(list) { return layer("polyline", {list: list}); },
  geoJSON(feature) { return layer("geojson", {feature: feature}); },
  divIcon(options) { return options; },
  DomEvent: {stopPropagation() {}},
};
globalThis.totoTileLayer = () => null;
globalThis.classicPin = () => ({});
const template = JSON.parse(process.argv[3]);
const config = JSON.parse(process.argv[4]);
globalThis.document = {
  getElementById: () => ({textContent: JSON.stringify(config)}),
  createElement: (tag) => new El(tag),
};
const GeographyMap = require(process.argv[1]);
require(process.argv[2]);
const box = new El("div");
box.dataset = {config: "the-config", csrf: "tok"};
box.parts = {};
template.forEach((part) => {
  const el = new El(part.tag);
  if (part.hidden) { el.classList.add("hidden"); }
  el.textContent = part.text || "";
  box.parts[part.name] = el;
});
const part = (name) => box.parts[name];
const press = (name) => part(name).fire("click");
const clickMap = (lat, lng) => page.map.fire("click", {latlng: {lat: lat, lng: lng}});
const options = (name) => part(name).children.map((o) => [o.value, o.textContent, !!o.disabled]);
const drawn = (group) => group.items.map((item) => item.kind);
const settle = async () => { for (let i = 0; i < 8; i++) { await new Promise((r) => setImmediate(r)); } };
const HITS5 = {results: [1, 2, 3, 4, 5].map((n) => ({label: "Hit " + n, lat: 50 + n, lng: 20 + n}))};
const HITS3 = {results: [6, 7, 8].map((n) => ({label: "Hit " + n, lat: 50 + n, lng: 20 + n}))};
const HITS6 = {results: [1, 2, 3, 4, 5, 6].map((n) => ({label: "Other " + n, lat: 40 + n, lng: 10 + n}))};
const ROUTE = {line: {type: "LineString", coordinates: [[21, 52], [19, 50]]},
               distance_km: 290.1, duration_min: 200.5, charged: true};
async function search(text, answer) {
  queue.push({status: 200, data: answer});
  part("q").value = text;
  press("search-go");
  await settle();
}
function choose(name, id) { part(name).value = id; part(name).fire("change"); }
(async () => {
  const out = {};
  const mounted = GeographyMap.mount(box);
  const controller = mounted.controller;
  __BODY__
  out.touched = touched;
  out.calls = calls;
  out.asked = Array.from(asked).sort();
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch((error) => { console.error(error); process.exit(1); });
"""

TEXTS = {"failed": "FAILED", "nothing_found": "NOTHING", "no_route": "NO ROUTE",
         "route_summary": "%(km)s km, about %(min)s min", "temporary": "Temporary point",
         "place_first": "PLACE FIRST", "corners": "%(n)s corners",
         "choose_end": "CHOOSE", "end_gone": "GONE", "choose_again": "CHOOSE AGAIN"}

URLS = {"search": "/geography/api/search/", "route": "/geography/api/route/",
        "savePoint": "/geography/communities/g/headquarters/",
        "clearPoint": "/geography/communities/g/headquarters/clear/",
        "saveZone": "/geography/communities/g/zone/",
        "clearZone": "/geography/communities/g/zone/clear/"}

#: The elements only whoever may set the point, or the zone, is given.
POINT_FORM = {"open-point", "edit-point", "point-name", "point-note-text", "save-point",
              "clear-point", "point-note"}
ZONE_FORM = {"draw-zone", "edit-zone", "zone-undo", "zone-restart", "zone-count", "zone-name",
             "zone-description", "save-zone", "clear-zone", "zone-note"}
ROUTE_PANEL = {"route", "from", "to", "mode", "route-go", "route-go-label", "route-note"}


def template_parts():
    """Every ``data-geo`` element of ``_map.html``, from the template's own
    text: its name, its tag, whether it starts hidden and, for the one label
    the script reads back, its text."""
    source = Path(get_template("geography/_map.html").origin.name).read_text(encoding="utf-8")
    parts = []
    for match in re.finditer(r'<(\w+)\b[^>]*?data-geo="([\w-]+)"[^>]*>', source, re.S):
        classes = re.search(r'\sclass="([^"]*)"', match.group(0))
        parts.append({"tag": match.group(1), "name": match.group(2),
                      "hidden": bool(classes and "hidden" in classes.group(1).split()),
                      "text": "Find route" if match.group(2) == "route-go-label" else ""})
    return parts


def page_config(**changes):
    config = {"center": None, "zoom": 13, "points": [], "zone": None, "urls": dict(URLS),
              "edit_kind": "headquarters", "can_edit_point": True, "can_edit_zone": True,
              "texts": dict(TEXTS)}
    config.update(changes)
    return config


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


class PageCase(SimpleTestCase):
    """``mount()`` run on the faked page."""

    def run_page(self, body, *, without=(), **config):
        """Run ``body`` after ``mount()``. ``without``: the element names
        this viewer's page does not have."""
        map_js, zone_js = finders.find("geography/map.js"), finders.find("geography/zone_draw.js")
        parts = [part for part in template_parts() if part["name"] not in set(without)]
        done = subprocess.run(
            [_NODE, "-e", PAGE.replace("__BODY__", body), map_js, zone_js, json.dumps(parts),
             json.dumps(page_config(**config))],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])


@skipUnless(_NODE, "node is not installed")
class RouteEndTests(PageCase):
    """Each end of a route is the point the member chose, whatever is added
    to the map or taken from it afterwards (2026-10-06). They used to be
    remembered by their place in the list of points, so a new search or a
    removed temporary point changed, without a word, which two points the
    charged route ran between."""

    #: Two clicks, From and To chosen as the two temporary points, in that
    #: order, after a search put five hits before them in the list.
    START = """
      await search("first", HITS5);
      clickMap(52.1, 21.1); clickMap(50.2, 19.2);
      const [first, second] = controller.state.temporary.map((p) => p.id);
      choose("from", first); choose("to", second);
    """
    FIND = """
      queue.push({status: 200, data: ROUTE});
      press("route-go"); await settle();
      out.route = calls[calls.length - 1];
    """
    BETWEEN_THE_TWO = {"from": {"lat": 52.1, "lng": 21.1}, "to": {"lat": 50.2, "lng": 19.2}}

    def ends_of(self, out):
        self.assertEqual(out["route"]["url"], URLS["route"])
        return {key: out["route"]["body"][key] for key in ("from", "to")}

    def test_a_search_with_fewer_hits_leaves_both_ends_where_they_were(self):
        out = self.run_page(self.START + """
          await search("second", HITS3);
          out.values = [part("from").value, part("to").value, first, second];
          out.count = options("from").length;
        """ + self.FIND)
        self.assertEqual(out["values"][:2], out["values"][2:])
        self.assertEqual(out["count"], 5)
        self.assertEqual(self.ends_of(out), self.BETWEEN_THE_TWO)

    def test_a_search_with_more_hits_leaves_both_ends_where_they_were(self):
        out = self.run_page(self.START + """
          await search("second", HITS6);
          out.values = [part("from").value, part("to").value, first, second];
        """ + self.FIND)
        self.assertEqual(out["values"][:2], out["values"][2:])
        self.assertEqual(self.ends_of(out), self.BETWEEN_THE_TWO)

    def test_a_removed_earlier_temporary_point_does_not_swap_the_ends(self):
        out = self.run_page("""
          [[1, 1], [2, 2], [3, 3], [4, 4]].forEach((at) => clickMap(at[0], at[1]));
          const ids = controller.state.temporary.map((p) => p.id);
          choose("from", ids[2]); choose("to", ids[3]);
          const circles = page.map.items.filter((item) => item.kind === "group")[2].items;
          circles[0].fire("click", {});
          out.left = controller.state.temporary.length;
          out.values = [part("from").value, part("to").value, ids[2], ids[3]];
        """ + self.FIND)
        self.assertEqual(out["left"], 3)
        self.assertEqual(out["values"][:2], out["values"][2:])
        self.assertEqual(self.ends_of(out), {"from": {"lat": 3, "lng": 3},
                                             "to": {"lat": 4, "lng": 4}})

    def test_an_end_whose_point_is_gone_is_cleared_and_the_button_says_so(self):
        out = self.run_page("""
          await search("first", HITS5);
          clickMap(52.1, 21.1);
          const hit = controller.state.hits[1].id, spot = controller.state.temporary[0].id;
          choose("from", hit); choose("to", spot);
          out.ready = [part("route-go").disabled, part("route-go-label").textContent];
          await search("second", HITS3);
          out.from = options("from")[0];
          out.values = [part("from").value, part("to").value === spot];
          out.waiting = [part("route-go").disabled, part("route-go-label").textContent];
          const before = calls.length;
          press("route-go"); await settle();
          out.sentWhileCleared = calls.length - before;
          out.hitIds = [hit, controller.state.hits.map((h) => h.id)];
          choose("from", controller.state.hits[0].id);
          out.again = [part("route-go").disabled, part("route-go-label").textContent,
                       options("from")[0][2]];
        """ + self.FIND)
        self.assertEqual(out["ready"], [False, "Find route"])
        self.assertEqual(out["from"], ["", "GONE", True])
        self.assertEqual(out["values"], ["", True])
        self.assertEqual(out["waiting"], [True, "CHOOSE AGAIN"])
        self.assertEqual(out["sentWhileCleared"], 0, "no route is asked for while an end is cleared")
        self.assertNotIn(out["hitIds"][0], out["hitIds"][1], "a new search's hits take new ids")
        self.assertEqual(out["again"], [False, "Find route", False])
        self.assertEqual(self.ends_of(out), {"from": {"lat": 56, "lng": 26},
                                             "to": {"lat": 52.1, "lng": 21.1}})

    def test_a_removed_temporary_point_clears_the_end_that_meant_it(self):
        out = self.run_page("""
          clickMap(1, 1); clickMap(2, 2); clickMap(3, 3);
          out.prefilled = [part("from").value, part("to").value,
                           controller.state.temporary[0].id, controller.state.temporary[1].id];
          const circles = page.map.items.filter((item) => item.kind === "group")[2].items;
          circles[1].fire("click", {});
          out.to = options("to")[0];
          out.kept = part("from").value === out.prefilled[0];
          out.waiting = [part("route-go").disabled, part("route-go-label").textContent];
          clickMap(4, 4);
          out.stillWaiting = [part("to").value, part("route-go").disabled];
        """)
        self.assertEqual(out["prefilled"][:2], out["prefilled"][2:], "the first two points pre-fill")
        self.assertEqual(out["to"], ["", "GONE", True])
        self.assertTrue(out["kept"])
        self.assertEqual(out["waiting"], [True, "CHOOSE AGAIN"])
        self.assertEqual(out["stillWaiting"], ["", True], "no other point steps into its place")
        self.assertEqual(out["calls"], [])

    def test_what_the_page_showed_as_chosen_stays_chosen_when_hits_arrive(self):
        """Nobody touched the selects: the first two points were shown as
        From and To. A search then puts hits before them in the list."""
        out = self.run_page("""
          clickMap(1, 1); clickMap(2, 2);
          await search("first", HITS5);
        """ + self.FIND, points=[])
        self.assertEqual(self.ends_of(out), {"from": {"lat": 1, "lng": 1},
                                             "to": {"lat": 2, "lng": 2}})

    def test_one_point_alone_is_no_route(self):
        out = self.run_page("""
          clickMap(1, 1);
          out.to = options("to")[0];
          out.go = part("route-go").disabled;
          press("route-go"); await settle();
        """)
        self.assertEqual(out["to"], ["", "CHOOSE", True])
        self.assertTrue(out["go"])
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

    def test_a_name_reaches_the_map_as_text_never_as_markup(self):
        """Leaflet draws a string tooltip as HTML. A point's name is typed by
        a member and a hit's label comes from outside, so both go in as a
        text node; nothing else is handed to a tooltip but the page's own
        sentence, and nothing is written with innerHTML."""
        import re

        source = self.sources()["map.js"]
        self.assertIn("tip.textContent = point.label;", source)
        self.assertIn("made.bindTooltip(tip);", source)
        bound = re.findall(r"bindTooltip\(([^)]*)\)", source)
        self.assertEqual(sorted(bound), ['texts.temporary || ""', "tip"])
        for name, text in self.sources().items():
            self.assertNotIn("innerHTML", text, name)
            self.assertNotIn("bindPopup", text, name)

    def test_a_search_never_runs_while_typing(self):
        """As text: no script listens for typing anywhere. (The route's two
        selects listen for ``change``; that the search box listens for
        nothing but a key press is run in ``PageTests``.)"""
        source = self.sources()["map.js"]
        self.assertIn('event.key === "Enter"', source)
        for event in ('"input"', '"keyup"', '"keypress"'):
            self.assertNotIn(f"addEventListener({event}", source)
        self.assertEqual(source.count('addEventListener("change"'), 1)

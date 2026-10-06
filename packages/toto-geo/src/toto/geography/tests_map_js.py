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
  dispatchEvent(event) { this.fire(event.type, event); return true; }
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
const groups = () => page.map.items.filter((item) => item.kind === "group");
const drawn = (index) => groups()[index].items.map((item) => item.kind);
const SAVED = 0, HITS = 1, TEMPORARY = 2, ROUTE_LINE = 3, ZONE = 4;
const pin = () => page.map.items.filter((item) => item.kind === "marker" && item.options.draggable)[0] || null;
const hidden = (name) => part(name).classList.contains("hidden");
// A dialog as the script left it: open or not (Alpine draws it in a browser).
const shown = (name) => part(name).dataset.open === "1";
// Leaving a dialog by Escape, the backdrop or the X: the template's event.
const dismiss = (name) => part(name).fire("geography-dismiss", {type: "geography-dismiss"});
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
POINT_FORM = {"open-point", "edit-point", "point-continue", "clear-point", "point-status",
              "point-dialog", "point-name", "point-note-text", "save-point", "point-note",
              "point-dialog-cancel"}
ZONE_FORM = {"draw-zone", "edit-zone", "zone-undo", "zone-restart", "zone-count",
             "zone-continue", "clear-zone", "zone-status", "zone-dialog", "zone-name",
             "zone-description", "save-zone", "zone-note", "zone-dialog-cancel"}
#: Where the words are typed: in the two dialogs, and nowhere else.
WORDS = {"point-name", "point-note-text", "zone-name", "zone-description"}
ROUTE_PANEL = {"route", "from", "to", "mode", "route-go", "route-go-label", "route-note"}
#: The sentences only a page with route search is given.
ROUTE_TEXTS = {"no_route", "route_summary", "choose_end", "end_gone", "choose_again"}


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

    def test_a_longitude_from_a_copy_of_the_world_is_brought_home(self):
        """Leaflet reports 381 for a click on Poland on the copy of the world
        to the right. Every door refuses a longitude outside -180..180, so
        none leaves the page."""
        out = self.run_js("""
          const a = controller.placeTemporary(52, 381.0122);
          const b = controller.placeTemporary(50.06, -340.06);
          out.kept = [a.lng, b.lng];
          await controller.route(a, {lat: 1, lng: 721.5}, "car");
          await controller.savePoint({lat: 52.2297, lng: -338.9878, name: "", note: ""});
          out.wrap = [-180, 180, 21.0122, 180.5, -180.5, 540, 381].map(GeographyMap.wrapLng);
        """)
        self.assertEqual(out["kept"], [21.0122, 19.94])
        route, point = out["calls"]
        self.assertEqual(route["body"]["from"], {"lat": 52, "lng": 21.0122})
        self.assertEqual(route["body"]["to"], {"lat": 1, "lng": 1.5})
        self.assertEqual(point["body"]["lng"], 21.0122)
        self.assertEqual(out["wrap"][:3], [-180, 180, 21.0122], "what is in range stays as it is")
        self.assertEqual([round(value, 6) for value in out["wrap"][3:]], [-179.5, 179.5, -180, 21])

    def test_a_zone_drawn_on_a_copy_is_moved_home_as_one_ring(self):
        out = self.run_js("""
          await controller.saveZone({name: "", description: "",
                                     outline: [[52, 381], [52, 381.1], [52.1, 381.1]]});
          await controller.saveZone({name: "", description: "",
                                     outline: [[10, 179], [10, 181], [11, 180]]});
          await controller.saveZone({name: "", description: "", outline: []});
        """)
        home, across, empty = (call["body"]["outline"] for call in out["calls"])
        self.assertEqual(home, [[52, 21], [52, 21.1], [52.1, 21.1]])
        self.assertEqual(across, [[10, 179], [10, 181], [11, 180]],
                         "a ring across the date line is not torn: the server refuses it")
        self.assertEqual(empty, [])

    def test_a_page_with_no_route_door_sends_no_route_request(self):
        """Route search is the Locations page's alone (2026-10-06): a page
        that was not given the door asks nothing of it, whatever is called."""
        out = self.run_js("""
          const quiet = GeographyMap.createController({
            fetch: fetchStub, csrf: "tok", saved: saved,
            urls: {search: urls.search, savePoint: urls.savePoint}});
          const a = quiet.placeTemporary(1, 1), b = quiet.placeTemporary(2, 2);
          out.selection = [quiet.selection().from.id, quiet.selection().to.id];
          out.chosen = quiet.routeChosen("car");
          const answer = await quiet.route(a, b, "car");
          out.answer = [answer.ok, answer.status, quiet.state.route];
        """)
        self.assertIsNone(out["chosen"])
        self.assertEqual(out["answer"], [False, 0, None])
        self.assertEqual(out["calls"], [])

    def test_the_outline_editor_keeps_its_ring_on_one_copy_of_the_world(self):
        out = self.run_js("""
          const here = GeographyZone.createOutline();
          here.add(52, 21); here.add(52, 381.1); here.add(52.1, -338.9);
          here.move(1, 52, 381.2);
          out.here = here.list();
          const there = GeographyZone.createOutline();
          there.add(52, 381); there.add(52, 21.1);
          there.move(0, 52, 21);
          out.there = there.list();
          const line = GeographyZone.createOutline();
          line.add(10, 179); line.add(10, 181);
          out.line = line.list();
        """)
        self.assertEqual(out["here"], [[52, 21], [52, 21.2], [52.1, 21.1]])
        self.assertEqual(out["there"], [[52, 21], [52, 381.1]],
                         "the first corner is where it was put; the others follow it")
        self.assertEqual(out["line"], [[10, 179], [10, 181]])

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
          groups()[TEMPORARY].items[0].fire("click", {});
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
          groups()[TEMPORARY].items[1].fire("click", {});
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


HEADQUARTERS = {"kind": "headquarters", "lat": 54.352, "lng": 18.6466, "label": "Harbour house"}
SQUARE = [[54.3, 18.6], [54.3, 18.7], [54.4, 18.7], [54.4, 18.6]]


@skipUnless(_NODE, "node is not installed")
class MapBehaviourTests(PageCase):
    """What a click means (2026-10-06): one thing at a time."""

    def test_a_picked_hit_moves_no_pin_while_the_point_form_is_closed(self):
        """The head searches to look at a place and picks a hit, the form
        closed. No pin moves and the saved one stays drawn; the form then
        opens on the saved point, and only now does a picked hit move the
        pin, which Save sends."""
        out = self.run_page("""
          await search("first", HITS5);
          groups()[HITS].items[2].fire("click", {});
          part("results").children[3].children[0].fire("click");
          out.closed = [pin() === null, drawn(SAVED), page.map.view.center];
          press("open-point");
          out.opened = [pin().at, drawn(SAVED)];
          groups()[HITS].items[2].fire("click", {});
          out.picked = pin().at;
          press("point-continue"); press("save-point"); await settle();
          out.sent = calls[calls.length - 1];
        """, points=[HEADQUARTERS], center=[54.352, 18.6466])
        self.assertEqual(out["closed"], [True, ["marker"], [54, 24]])
        self.assertEqual(out["opened"], [[54.352, 18.6466], []])
        self.assertEqual(out["picked"], [53, 23])
        self.assertEqual(out["sent"]["url"], URLS["savePoint"])
        self.assertEqual((out["sent"]["body"]["lat"], out["sent"]["body"]["lng"]), (53, 23))

    def test_the_zone_form_gives_the_click_back(self):
        """Once the zone editor had started, every click was a corner for
        good: no headquarters pin and no temporary point could be placed."""
        out = self.run_page("""
          press("draw-zone");
          clickMap(54.5, 18.5);
          out.drawing = [hidden("edit-zone"), hidden("edit-point"), part("zone-count").textContent,
                         controller.state.temporary.length, pin() === null, drawn(ZONE)];
          press("open-point");
          out.point = [hidden("edit-zone"), hidden("edit-point"), drawn(ZONE)];
          clickMap(54.6, 18.8);
          out.pin = pin().at;
          press("close-point");
          out.closed = [hidden("edit-point"), pin() === null, drawn(SAVED),
                        part("point-continue").disabled];
          clickMap(54.7, 18.9);
          out.temporary = controller.state.temporary.map((p) => [p.lat, p.lng]);
          press("draw-zone");
          out.again = [hidden("edit-zone"), hidden("edit-point"), part("zone-count").textContent];
          clickMap(54.5, 18.5);
          press("close-zone");
          out.cancelled = [hidden("edit-zone"), drawn(ZONE)];
          clickMap(54.8, 19);
          out.after = controller.state.temporary.length;
          press("draw-zone"); press("zone-continue"); press("save-zone"); await settle();
          out.sent = calls[calls.length - 1].body.outline;
        """, points=[HEADQUARTERS], center=[54.352, 18.6466], zone={"outline": SQUARE})
        self.assertEqual(out["drawing"], [False, True, "5 corners", 0, True, []])
        self.assertEqual(out["point"], [True, False, ["polygon"]],
                         "the zone form closes and the saved zone is drawn again")
        self.assertEqual(out["pin"], [54.6, 18.8], "the click moved the pin, and made no corner")
        self.assertEqual(out["closed"], [True, True, ["marker"], True],
                         "no pin on the map: Continue waits for one")
        self.assertEqual(out["temporary"], [[54.7, 18.9]])
        self.assertEqual(out["again"], [False, True, "4 corners"],
                         "what was drawn and not saved is gone: the saved outline again")
        self.assertEqual(out["cancelled"], [True, ["polygon"]])
        self.assertEqual(out["after"], 2)
        self.assertEqual(out["sent"], SQUARE)
        self.assertEqual([call["url"] for call in out["calls"]], [URLS["saveZone"]])

    def test_a_click_on_a_copy_of_the_world_places_the_point_at_home(self):
        out = self.run_page("""
          page.map.setView([52, 381], 2);
          clickMap(52.5, 381.5);
          out.temporary = controller.state.temporary.map((p) => [p.lat, p.lng]);
          out.circle = groups()[TEMPORARY].items[0].at;
          out.view = [page.map.view.center, page.map.view.zoom];
          clickMap(52.6, 21.6);
          out.viewKept = page.map.view.center;
          press("open-point");
          page.map.setView([52, -339], 2);
          clickMap(52.7, -338.3);
          out.pin = pin().at.map((n) => Math.round(n * 1e6) / 1e6);
          out.viewAfterPin = page.map.view.center.map((n) => Math.round(n * 1e6) / 1e6);
          pin().setLatLng([52.7, 381.7]);
          press("point-continue"); press("save-point"); await settle();
          out.sent = calls[calls.length - 1].body;
        """)
        self.assertEqual(out["temporary"], [[52.5, 21.5]])
        self.assertEqual(out["circle"], [52.5, 21.5])
        self.assertEqual(out["view"], [[52, 21], 2], "the view goes back to the world it draws on")
        self.assertEqual(out["viewKept"], [52, 21])
        self.assertEqual(out["pin"], [52.7, 21.7])
        self.assertEqual(out["viewAfterPin"], [52, 21])
        self.assertEqual((out["sent"]["lat"], out["sent"]["lng"]), (52.7, 21.7),
                         "a pin dragged onto a copy is saved at home too")

    def test_an_answer_that_cannot_be_read_never_says_nothing_was_charged(self):
        out = self.run_page("""
          clickMap(1, 1); clickMap(2, 2);
          queue.push({status: 200, data: {line: null, distance_km: null, duration_min: null, charged: false}},
                     {status: 200, data: {}}, {status: 200, data: ROUTE});
          out.notes = [];
          for (let i = 0; i < 3; i++) {
            press("route-go"); await settle();
            out.notes.push([part("route-note").textContent, drawn(ROUTE_LINE)]);
          }
        """)
        self.assertEqual(out["notes"], [["NO ROUTE", []], ["FAILED", []],
                                        ["290.1 km, about 200.5 min", ["geojson"]]])


@skipUnless(_NODE, "node is not installed")
class PageTests(PageCase):
    """The page half, run: what the controller's tests could only say of
    the controller."""

    def test_a_click_on_the_map_makes_no_request_and_writes_no_storage(self):
        out = self.run_page("""
          clickMap(52.1, 21.1); clickMap(50.2, 19.2);
          groups()[TEMPORARY].items[0].fire("click", {});
          out.temporary = controller.state.temporary.length;
          out.circles = drawn(TEMPORARY);
        """, points=[HEADQUARTERS], center=[54.352, 18.6466])
        self.assertEqual((out["temporary"], out["circles"]), (1, ["circle"]))
        self.assertEqual(out["calls"], [])
        self.assertEqual(out["touched"], [])

    def test_no_name_reaches_a_tooltip_as_a_string(self):
        """Leaflet draws a string as HTML. A saved point's name, a hit's
        label and the page's own sentence each go in as an element whose
        text they are; the page's ``innerHTML`` throws."""
        markup = '<img src=x onerror=alert(1)>'
        out = self.run_page("""
          queue.push({status: 200, data: {results: [{label: config.points[0].label, lat: 1, lng: 2}]}});
          part("q").value = "x"; press("search-go"); await settle();
          clickMap(3, 4);
          out.tips = tips.map((tip) => [typeof tip, tip.tagName, tip.textContent, tip.children.length]);
          out.listed = part("results").children[0].children[0].textContent;
          out.option = options("from").map((o) => o[1]);
        """, points=[{**HEADQUARTERS, "label": markup}], center=[54.352, 18.6466])
        self.assertEqual(out["tips"], [["object", "span", markup, 0], ["object", "span", markup, 0],
                                       ["object", "span", "Temporary point", 0]])
        self.assertEqual(out["listed"], markup)
        self.assertEqual(out["option"][:2], [markup, markup])

    def test_the_search_box_listens_for_a_key_press_and_nothing_else(self):
        out = self.run_page("""
          out.box = Object.keys(part("q").handlers);
          out.button = Object.keys(part("search-go").handlers);
          part("q").value = "Warsaw";
          part("q").fire("keydown", {key: "W", preventDefault() {}});
          out.typed = calls.length;
          queue.push({status: 200, data: HITS3});
          part("q").fire("keydown", {key: "Enter", preventDefault() {}}); await settle();
          out.entered = calls.map((call) => [call.url, call.body.q]);
        """)
        self.assertEqual((out["box"], out["button"]), (["keydown"], ["click"]))
        self.assertEqual(out["typed"], 0)
        self.assertEqual(out["entered"], [[URLS["search"], "Warsaw"]])

    def test_the_script_asks_for_the_elements_the_template_has(self):
        """The script finds its elements by name. Every name it asks for is
        one the template has, and the template has none the script never
        asks for but the two boxes that only hold others."""
        names = {part["name"] for part in template_parts()}
        self.assertEqual(len(names), len(template_parts()), "a name is used once")
        self.assertTrue(POINT_FORM | ZONE_FORM | ROUTE_PANEL | {"close-point", "close-zone"} <= names)
        out = self.run_page("""
          await search("first", HITS3);
          clickMap(2, 2); clickMap(3, 3);
          queue.push({status: 200, data: ROUTE}); press("route-go"); await settle();
          press("open-point"); clickMap(1, 1); press("point-continue");
          press("save-point"); await settle();
          press("close-point");
          press("draw-zone"); clickMap(1, 1); clickMap(1, 2); clickMap(2, 2);
          press("zone-continue"); press("save-zone"); await settle();
          press("close-zone");
          out.sent = calls.map((call) => call.url);
        """)
        self.assertEqual(out["sent"], [URLS["search"], URLS["route"], URLS["savePoint"],
                                       URLS["saveZone"]])
        self.assertEqual(set(out["asked"]) - names, set())
        self.assertEqual(names - set(out["asked"]), {"search", "route"})

    def test_a_page_without_route_search_wires_nothing_for_routes(self):
        """The profile's map and the community's: no panel, no route door in
        the data. Clicks and a search go on as they did; nothing listens
        for a route, and nothing reaches the route door."""
        out = self.run_page("""
          clickMap(1, 1); clickMap(2, 2);
          await search("first", HITS3);
          groups()[HITS].items[0].fire("click", {});
          out.temporary = controller.state.temporary.length;
          out.route = controller.routeChosen("car");
          out.chosen = controller.state.chosen;
          out.sent = calls.map((call) => call.url);
        """, without=ROUTE_PANEL, points=[HEADQUARTERS], center=[54.352, 18.6466],
            urls={key: value for key, value in URLS.items() if key != "route"},
            texts={key: value for key, value in TEXTS.items() if key not in ROUTE_TEXTS})
        self.assertEqual(out["temporary"], 2)
        self.assertEqual(out["chosen"], {"from": {"id": None, "gone": False},
                                         "to": {"id": None, "gone": False}})
        self.assertIsNone(out["route"])
        self.assertEqual(out["sent"], [URLS["search"]])

    def test_a_panel_with_no_door_behind_it_is_left_alone(self):
        """The panel's elements without the door in the data (it cannot
        happen from ``map_context``; the script does not lean on that)."""
        out = self.run_page("""
          clickMap(1, 1); clickMap(2, 2);
          out.options = options("from").length;
          out.listens = [Object.keys(part("route-go").handlers), Object.keys(part("from").handlers)];
          press("route-go"); await settle();
        """, urls={key: value for key, value in URLS.items() if key != "route"})
        self.assertEqual(out["options"], 0)
        self.assertEqual(out["listens"], [[], []])
        self.assertEqual(out["calls"], [])

    def test_a_viewer_s_page_has_no_form_and_the_script_wires_none(self):
        out = self.run_page("""
          clickMap(1, 1);
          out.temporary = controller.state.temporary.length;
          out.zone = drawn(ZONE);
          out.pin = pin() === null;
        """, without=POINT_FORM | ZONE_FORM | {"close-point", "close-zone"},
            points=[HEADQUARTERS], center=[54.352, 18.6466], zone={"outline": SQUARE},
            can_edit_point=False, can_edit_zone=False,
            urls={key: URLS[key] for key in ("search", "route")})
        self.assertEqual((out["temporary"], out["zone"], out["pin"]), (1, ["polygon"], True))
        self.assertEqual(out["calls"], [])


@skipUnless(_NODE, "node is not installed")
class DialogTests(PageCase):
    """The words of a point and of a zone are typed in a dialog and nowhere
    else (the owner, 2026-10-06: "the information like name etc should be
    inputed via modal and modal only"). Beside the map: the geometry."""

    def test_the_point_is_placed_on_the_map_and_named_in_the_dialog(self):
        out = self.run_page("""
          out.start = [hidden("edit-point"), shown("point-dialog"), part("point-continue").disabled];
          press("open-point");
          out.tool = [hidden("edit-point"), shown("point-dialog"), part("point-continue").disabled];
          press("point-continue");
          out.unplaced = [shown("point-dialog"), part("point-status").textContent];
          clickMap(54.6, 18.8);
          out.placed = [pin().at, part("point-continue").disabled, shown("point-dialog")];
          press("point-continue");
          out.opened = [shown("point-dialog"), hidden("edit-point"), part("point-status").textContent];
          part("point-name").value = "Harbour house";
          part("point-note-text").value = "ring twice";
          press("save-point"); await settle();
          out.sent = calls[0];
          out.reloads = touched.filter((name) => name === "location.reload").length;
        """)
        self.assertEqual(out["start"], [True, False, True])
        self.assertEqual(out["tool"], [False, False, True], "no pin yet: Continue waits")
        self.assertEqual(out["unplaced"], [False, "PLACE FIRST"])
        self.assertEqual(out["placed"], [[54.6, 18.8], False, False],
                         "placing the pin opens nothing by itself")
        self.assertEqual(out["opened"], [True, False, ""])
        self.assertEqual(out["sent"]["url"], URLS["savePoint"])
        body = out["sent"]["body"]
        # What the form beside the map sent, to the last key.
        self.assertEqual(set(body), {"lat", "lng", "name", "note", "op"})
        self.assertEqual((body["lat"], body["lng"], body["name"], body["note"]),
                         (54.6, 18.8, "Harbour house", "ring twice"))
        self.assertRegex(body["op"], UUID)
        self.assertEqual(out["reloads"], 1)

    def test_a_refused_point_keeps_the_dialog_open_with_what_was_typed(self):
        out = self.run_page("""
          press("open-point"); clickMap(54.6, 18.8); press("point-continue");
          part("point-name").value = "Harbour house";
          part("point-note-text").value = "ring twice";
          queue.push({status: 402, data: {error: "Not enough storage mana."}},
                     {status: 503, data: {}}, {status: 200, data: {}});
          press("save-point"); await settle();
          out.refused = [shown("point-dialog"), part("point-note").textContent,
                         hidden("point-note"), part("point-name").value,
                         part("point-note-text").value, pin().at, hidden("edit-point"),
                         part("save-point").disabled, part("point-status").textContent];
          press("save-point"); await settle();
          out.unanswered = [shown("point-dialog"), part("point-note").textContent];
          press("save-point"); await settle();
          out.ops = calls.map((call) => call.body.op);
          out.bodies = calls.map((call) => [call.url, call.body.name, call.body.note]);
          out.reloads = touched.filter((name) => name === "location.reload").length;
        """)
        self.assertEqual(out["refused"], [True, "Not enough storage mana.", False, "Harbour house",
                                          "ring twice", [54.6, 18.8], False, False, ""])
        self.assertEqual(out["unanswered"], [True, "FAILED"])
        first, second, third = out["ops"]
        self.assertNotEqual(first, second, "an answered refusal ends its op")
        self.assertEqual(second, third, "a press the server never answered repeats its op")
        self.assertEqual(out["bodies"], [[URLS["savePoint"], "Harbour house", "ring twice"]] * 3)
        self.assertEqual(out["reloads"], 1)

    def test_cancel_in_the_point_dialog_keeps_the_pin(self):
        out = self.run_page("""
          press("open-point");
          out.saved = pin().at;
          clickMap(54.6, 18.8); press("point-continue");
          part("point-name").value = "Typed";
          press("point-dialog-cancel");
          out.cancelled = [shown("point-dialog"), pin().at, hidden("edit-point"),
                           part("point-continue").disabled, part("point-name").value];
          pin().setLatLng([54.7, 18.9]);
          press("point-continue");
          dismiss("point-dialog");
          out.dismissed = [shown("point-dialog"), pin().at, hidden("edit-point")];
          dismiss("point-dialog");
          press("point-continue"); press("close-point");
          out.closed = [shown("point-dialog"), pin() === null, hidden("edit-point"), drawn(SAVED)];
        """, points=[HEADQUARTERS], center=[54.352, 18.6466])
        self.assertEqual(out["saved"], [54.352, 18.6466], "a saved point is edited from where it is")
        self.assertEqual(out["cancelled"], [False, [54.6, 18.8], False, False, "Typed"])
        self.assertEqual(out["dismissed"], [False, [54.7, 18.9], False],
                         "Escape, the backdrop and the X leave the pin too")
        self.assertEqual(out["closed"], [False, True, True, ["marker"]],
                         "Cancel beside the map closes the tool, its dialog with it")
        self.assertEqual(out["calls"], [])

    def test_the_zone_is_drawn_on_the_map_and_named_in_the_dialog(self):
        out = self.run_page("""
          press("draw-zone");
          clickMap(1, 1); clickMap(1, 2);
          out.two = [part("zone-continue").disabled, shown("zone-dialog")];
          press("zone-continue");
          out.early = shown("zone-dialog");
          clickMap(2, 2);
          out.three = [part("zone-continue").disabled, shown("zone-dialog")];
          press("zone-continue");
          out.opened = [shown("zone-dialog"), hidden("edit-zone")];
          part("zone-name").value = "Meadow";
          part("zone-description").value = "by the river";
          press("zone-dialog-cancel");
          out.cancelled = [shown("zone-dialog"), hidden("edit-zone"), part("zone-count").textContent,
                           part("zone-name").value];
          press("zone-continue");
          queue.push({status: 400, data: {error: "The outline crosses itself."}},
                     {status: 200, data: {}});
          press("save-zone"); await settle();
          out.refused = [shown("zone-dialog"), part("zone-note").textContent,
                         part("zone-name").value, part("zone-description").value,
                         part("zone-count").textContent];
          press("save-zone"); await settle();
          out.sent = calls.map((call) => [call.url, call.body.name, call.body.description,
                                          call.body.outline]);
          out.keys = Object.keys(calls[1].body).sort();
          out.ops = calls.map((call) => call.body.op);
          out.reloads = touched.filter((name) => name === "location.reload").length;
        """)
        self.assertEqual(out["two"], [True, False])
        self.assertFalse(out["early"], "two corners are no zone: Continue opens nothing")
        self.assertEqual(out["three"], [False, False])
        self.assertEqual(out["opened"], [True, False])
        self.assertEqual(out["cancelled"], [False, False, "3 corners", "Meadow"],
                         "back to the map, the corners where they were")
        self.assertEqual(out["refused"], [True, "The outline crosses itself.", "Meadow",
                                          "by the river", "3 corners"])
        ring = [[1, 1], [1, 2], [2, 2]]
        self.assertEqual(out["sent"], [[URLS["saveZone"], "Meadow", "by the river", ring]] * 2)
        self.assertEqual(out["keys"], ["description", "name", "op", "outline"])
        self.assertNotEqual(out["ops"][0], out["ops"][1])
        self.assertEqual(out["reloads"], 1)

    def test_remove_stays_beside_the_map_and_says_its_refusal_there(self):
        out = self.run_page("""
          press("open-point");
          queue.push({status: 403, data: {error: "Not yours."}});
          press("clear-point"); await settle();
          out.point = [part("point-status").textContent, shown("point-dialog")];
          press("draw-zone");
          queue.push({status: 403, data: {error: "Not yours either."}});
          press("clear-zone"); await settle();
          out.zone = [part("zone-status").textContent, shown("zone-dialog")];
          out.sent = calls.map((call) => [call.url, Object.keys(call.body)]);
        """, points=[HEADQUARTERS], center=[54.352, 18.6466], zone={"outline": SQUARE})
        self.assertEqual(out["point"], ["Not yours.", False])
        self.assertEqual(out["zone"], ["Not yours either.", False])
        self.assertEqual(out["sent"], [[URLS["clearPoint"], []], [URLS["clearZone"], []]])

    def test_the_template_keeps_every_word_s_field_inside_a_dialog(self):
        """As text, from the template itself: the name, the note and the
        description are fields of the two dialogs and of nothing else, and
        each dialog is the library's modal."""
        from toto.geography.testing import tree_of, walk

        source = Path(get_template("geography/_map.html").origin.name).read_text(encoding="utf-8")
        tree = tree_of(re.sub(r"{%.*?%}|{#.*?#}", "", source, flags=re.S))
        typed, dialogs = {}, []
        for node, above in walk(tree):
            if node["attrs"].get("role") == "dialog":
                dialogs.append(node["attrs"])
            if node["tag"] in ("input", "textarea", "select"):
                inside = [up["attrs"].get("data-geo") for up in above
                          if up["attrs"].get("role") == "dialog"]
                typed[node["attrs"].get("data-geo")] = inside
        self.assertEqual({name for name, inside in typed.items() if inside}, WORDS)
        self.assertEqual({name: inside[0] for name, inside in typed.items() if inside},
                         {"point-name": "point-dialog", "point-note-text": "point-dialog",
                          "zone-name": "zone-dialog", "zone-description": "zone-dialog"})
        # What is left beside the map takes no words: the search box and
        # the route's three choices.
        self.assertEqual({name for name, inside in typed.items() if not inside},
                         {"q", "from", "to", "mode"})
        self.assertEqual([attrs.get("data-geo") for attrs in dialogs],
                         ["point-dialog", "zone-dialog"])
        for attrs in dialogs:
            self.assertEqual((attrs["aria-modal"], attrs["x-show"], attrs["x-trap"]),
                             ("true", "open", "open"))
            self.assertIn("aria-labelledby", attrs)
            self.assertIn("@keydown.escape.window", attrs)


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
        source = self.sources()["map.js"]
        self.assertIn("tip.textContent = text;", source)
        bound = re.findall(r"bindTooltip\((.*)\);", source)
        self.assertEqual(sorted(bound), ["asText(point.label)", 'asText(texts.temporary || "")',
                                         "asText(zone.label)"])
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

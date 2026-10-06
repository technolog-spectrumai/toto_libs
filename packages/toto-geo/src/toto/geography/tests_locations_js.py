"""The Locations page's script, run in node (stage 64, 2026-10-06).

``HARNESS`` runs its half with no page in it: what is near what, what the
filter lets through, one press of a charged control, the address a reload
keeps. "Search nearby" is a function of the rows the page holds: it takes no
fetch at all, so a centre cannot be sent.

``PAGE`` runs ``mount()``, the half that draws, on the page the server
really answers a member with: its HTML is read into a tree here and rebuilt
in node as elements faked just far enough (attributes, classes, values,
listeners, simple selectors), with a Leaflet that keeps what it is handed
and a fetch, an address and a history that record. That is where the tabs,
the community checkboxes, the dialogs, "Keep on the map" beside a search
result and the advanced filter are pressed. No browser draws anything: how
it looks is the owner's by-hand note.

    manage.py test toto.geography.tests_locations_js
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, override_settings

from toto.geography import saves
from toto.geography.locations_testing import LocationsCase
from toto.geography.testing import client_of, element, op, post, tree_of

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
          out.centre = G.centreOf(ROWS[3]);
        """)
        self.assertEqual(out["wrap"], [21, 21, 21, 180])
        self.assertAlmostEqual(out["centre"]["lat"], 52.35)

    def test_the_community_filter_takes_several_one_or_none(self):
        """None chosen lets every row through. One or more keep the rows of
        those communities and hide every other row, a person's point
        included (it belongs to no community): the rule the single choice
        had. Search nearby is narrowed by it, and nothing is sent."""
        out = self.run_js("""
          const ids = (f) => ROWS.filter((r) => G.passes(r, f)).map((r) => r.id);
          out.none = ids({communities: []});
          out.one = ids({communities: ["guild"]});
          out.two = ids({communities: ["other", "guild"]});
          out.unknown = ids({communities: ["no-such"]});
          out.single = ids({community: "other"});
          out.withKinds = ids({communities: ["guild", "other"], kinds: {zone: false}, text: "m"});
          const near = (f) => G.nearby(ROWS.filter((r) => G.passes(r, f)), WARSAW, 300)
            .map((x) => x.row.id);
          out.near = [near({}), near({communities: ["other"]}), near({communities: ["guild"]}),
                      near({communities: ["guild", "other"]})];
        """)
        self.assertEqual(out["none"], ["pin:a", "pin:b", "person:x", "zone:c"])
        self.assertEqual(out["one"], ["pin:a", "zone:c"])
        self.assertEqual(out["two"], ["pin:a", "pin:b", "zone:c"])      # no person's point
        self.assertEqual(out["unknown"], [])
        self.assertEqual(out["single"], ["pin:b"])
        self.assertEqual(out["withKinds"], ["pin:b"])
        self.assertEqual(out["near"], [["pin:a", "pin:b", "zone:c", "person:x"], ["pin:b"],
                                       ["pin:a", "zone:c"], ["pin:a", "pin:b", "zone:c"]])
        self.assertEqual((out["touched"], out["calls"]), ([], []))

    def test_the_advanced_filter_hides_rows_by_id_with_the_other_three(self):
        """The owner, 2026-10-07: "you can select items to display". The
        rows unticked one by one are the filter's ``hidden``: a row shows
        only when the text, the kinds, the communities and it all let it
        through; nearby is narrowed by it, and nothing is sent."""
        out = self.run_js("""
          const ids = (f) => ROWS.filter((r) => G.passes(r, f)).map((r) => r.id);
          const none = Object.create(null);
          const two = Object.create(null); two["pin:a"] = true; two["zone:c"] = true;
          out.none = ids({hidden: none});
          out.two = ids({hidden: two});
          out.plain = ids({hidden: {"person:x": true}});
          out.unknown = ids({hidden: {"pin:zzz": true, "toString": true}});
          out.inherited = ids({hidden: {}});
          out.withKinds = ids({hidden: two, kinds: {person: false}});
          out.withCommunity = ids({hidden: two, communities: ["guild"]});
          out.withText = [ids({hidden: two, text: "mill"}), ids({hidden: two, text: "well"})];
          out.all = ids({hidden: two, kinds: {pin: false}, communities: ["other"], text: "m"});
          out.near = G.nearby(ROWS.filter((r) => G.passes(r, {hidden: two})), WARSAW, 300)
            .map((x) => x.row.id);
          out.order = G.byKindThenName(ROWS.concat([
            {id: "pin:z", kind: "pin", name: "apple"}, {id: "odd:1", kind: "odd", name: "Aaa"},
            {id: "pin:y", kind: "pin", name: ""}, {id: "area:g", kind: "area", name: "Guild"},
            {id: "headquarters:g", kind: "headquarters", name: "House"}])).map((r) => r.id);
          out.kept = ROWS.map((r) => r.id);
          const T = {advanced: "Advanced", advanced_hidden: "Advanced · %(n)s hidden"};
          out.labels = [G.advancedLabel(none, T), G.advancedLabel(two, T),
                        G.advancedLabel({"pin:a": true}, T), G.advancedLabel(undefined, T)];
        """)
        everything = ["pin:a", "pin:b", "person:x", "zone:c"]
        self.assertEqual(out["none"], everything)
        self.assertEqual(out["two"], ["pin:b", "person:x"])
        self.assertEqual(out["plain"], ["pin:a", "pin:b", "zone:c"])
        self.assertEqual(out["unknown"], everything)
        self.assertEqual(out["inherited"], everything, "a method's name hides no row")
        self.assertEqual(out["withKinds"], ["pin:b"])
        self.assertEqual(out["withCommunity"], [])
        self.assertEqual(out["withText"], [["pin:b"], []])
        self.assertEqual(out["all"], [])
        self.assertEqual(out["near"], ["pin:b", "person:x"])
        # By type in the order of the "Show" boxes, then by name; an unknown
        # kind last. The page's own order is left as it was.
        self.assertEqual(out["order"], ["person:x", "headquarters:g", "area:g", "pin:y", "pin:z",
                                        "pin:b", "pin:a", "zone:c", "odd:1"])
        self.assertEqual(out["kept"], everything)
        self.assertEqual(out["labels"], ["Advanced", "Advanced · 2 hidden", "Advanced · 1 hidden",
                                         "Advanced"])
        self.assertEqual((out["touched"], out["calls"]), ([], []))

    def test_what_the_filter_s_button_says_and_what_the_address_ticks(self):
        out = self.run_js("""
          const NAMES = {guild: "Guild", other: "Other <b>", third: "Third"};
          const T = {all_communities: "All communities", n_communities: "%(n)s communities"};
          out.labels = [G.communityLabel([], NAMES, T), G.communityLabel(["other"], NAMES, T),
                        G.communityLabel(["guild", "other"], NAMES, T),
                        G.communityLabel(["guild", "other", "third"], NAMES, T),
                        G.communityLabel(undefined, NAMES, T)];
          out.known = [G.knownCommunities("guild", NAMES),
                       G.knownCommunities(["other", "no-such", "guild", "other"], NAMES),
                       G.knownCommunities("", NAMES), G.knownCommunities(undefined, NAMES),
                       G.knownCommunities(["toString", "constructor"], NAMES)];
        """)
        self.assertEqual(out["labels"], ["All communities", "Other <b>", "2 communities",
                                         "3 communities", "All communities"])
        self.assertEqual(out["known"], [["guild"], ["other", "guild"], [], [], []])

    def test_the_address_keeps_the_tab_the_communities_and_the_opened_row(self):
        out = self.run_js("""
          const at = "https://zenobia.test/geography/";
          out.tool = [G.address(at, {tool: "nearby"}), G.address(at + "?tool=route", {tool: "index"}),
                      G.address(at + "?tool=route&open=pin:1", {tool: "nearby"})];
          out.communities = [
            G.address(at + "?community=old", {communities: ["a", "b"]}),
            G.address(at + "?community=old&tool=route", {communities: []}),
            G.address(at, {communities: ["a b&c"]})];
          out.open = [G.address(at + "?open=pin:1&tool=route&community=a", {open: "zone:2"}),
                      G.address(at + "?open=pin:1&community=a", {open: ""})];
          out.all = G.address(at + "?other=1", {tool: "route", communities: ["a"], open: "pin:9"});
          out.untouched = G.address(at + "?tool=route&community=a&open=pin:1", {});
          out.steps = [G.stepTab(["index", "nearby", "route"], "index", "ArrowRight"),
                       G.stepTab(["index", "nearby", "route"], "route", "ArrowRight"),
                       G.stepTab(["index", "nearby", "route"], "index", "ArrowLeft"),
                       G.stepTab(["index", "nearby", "route"], "nearby", "Home"),
                       G.stepTab(["index", "nearby", "route"], "nearby", "End"),
                       G.stepTab(["index", "nearby", "route"], "nearby", "Enter"),
                       G.stepTab(["index", "nearby", "route"], "nearby", "ArrowDown"),
                       G.stepTab(["index", "nearby"], "nearby", "ArrowRight"),
                       G.stepTab([], "nearby", "ArrowRight")];
        """)
        at = "https://zenobia.test/geography/"
        self.assertEqual(out["tool"], [at + "?tool=nearby", at, at + "?open=pin%3A1&tool=nearby"])
        self.assertEqual(out["communities"], [at + "?community=a&community=b", at + "?tool=route",
                                              at + "?community=a+b%26c"])
        self.assertEqual(out["open"], [at + "?tool=route&community=a&open=zone%3A2",
                                       at + "?community=a"])
        self.assertEqual(out["all"], at + "?other=1&tool=route&community=a&open=pin%3A9")
        self.assertEqual(out["untouched"], at + "?tool=route&community=a&open=pin:1")
        self.assertEqual(out["steps"], ["nearby", "index", "route", "index", "route", None, None,
                                        "index", None])
        self.assertEqual((out["touched"], out["calls"]), ([], []))


#: The page around ``mount()``. argv: locations.js, map.js and
#: a file with ``{tree, config, href, fragments, extra}``.
PAGE = r"""
const DATA = JSON.parse(require("fs").readFileSync(process.argv[3], "utf8"));
const X = DATA.extra;
const touched = [];
function spy(name) {
  return new Proxy({}, {
    get(_t, key) { touched.push(name + "." + String(key)); return () => null; },
    set(_t, key) { touched.push(name + "." + String(key) + "="); return true; },
  });
}
globalThis.localStorage = spy("localStorage");
globalThis.sessionStorage = spy("sessionStorage");
const calls = [], queue = [], replaced = [], assigned = [];
globalThis.location = {href: DATA.href, assign(url) { assigned.push(String(url)); },
                       reload() { assigned.push("reload"); }};
globalThis.history = {
  state: null,
  replaceState(_state, _title, url) { replaced.push(String(url)); location.href = String(url); },
  pushState() { throw new Error("the page adds no entry to the history"); },
};
globalThis.confirm = () => true;
globalThis.fetch = function (url, init) {
  init = init || {};
  calls.push({url: url, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : null});
  const next = queue.length ? queue.shift() : {status: 200, data: {}};
  if (next.network) { return Promise.reject(new Error("offline")); }
  return Promise.resolve({ok: next.status >= 200 && next.status < 300, status: next.status,
                          json: () => Promise.resolve(next.data || {}),
                          text: () => Promise.resolve(next.text || "")});
};

const camel = (name) => name.replace(/-([a-z])/g, (_m, c) => c.toUpperCase());
function parseSelector(selector) {
  return selector.trim().split(/\s+/).map((part) => {
    const m = /^([a-zA-Z]*)((?:\[[^\]]+\])*)$/.exec(part);
    if (!m) { throw new Error("an unexpected selector: " + selector); }
    const tests = (m[2].match(/\[[^\]]+\]/g) || []).map((t) => {
      const mm = /^\[([\w-]+)(?:=(?:"([^"]*)"|([^\]]*)))?\]$/.exec(t);
      return {name: mm[1], value: mm[2] !== undefined ? mm[2] : mm[3]};
    });
    return {tag: m[1].toUpperCase(), tests: tests};
  });
}
class Txt { constructor(text) { this.textContent = text; } }
class El {
  constructor(tag, attrs) {
    this.tagName = String(tag).toUpperCase();
    this.attrs = Object.assign({}, attrs || {});
    this.children = []; this.parentNode = null; this.handlers = {}; this.dataset = {};
    this.style = {}; this._text = ""; this.title = ""; this.href = "";
    this.type = this.attrs.type || "";
    this.disabled = "disabled" in this.attrs;
    this.checked = "checked" in this.attrs;
    this._value = this.attrs.value !== undefined ? this.attrs.value : "";
    Object.keys(this.attrs).forEach((key) => {
      if (key.startsWith("data-")) { this.dataset[camel(key.slice(5))] = this.attrs[key]; }
    });
    const names = new Set(String(this.attrs.class || "").split(/\s+/).filter(Boolean));
    this._classes = names;
    this.classList = {
      contains: (name) => names.has(name), add: (name) => { names.add(name); },
      remove: (name) => { names.delete(name); },
      toggle: (name, on) => { const want = on === undefined ? !names.has(name) : !!on;
                              if (want) { names.add(name); } else { names.delete(name); } return want; }};
  }
  get className() { return Array.from(this._classes).join(" "); }
  set className(value) {
    this._classes.clear();
    String(value).split(/\s+/).filter(Boolean).forEach((name) => this._classes.add(name));
  }
  elements() { return this.children.filter((child) => child instanceof El); }
  all() {
    const out = [];
    const walk = (node) => node.elements().forEach((child) => { out.push(child); walk(child); });
    walk(this);
    return out;
  }
  get value() {
    if (this.tagName !== "SELECT") { return this._value; }
    const options = this.all().filter((o) => o.tagName === "OPTION");
    if (options.some((o) => o.value === this._value)) { return this._value; }
    const picked = options.find((o) => "selected" in o.attrs) || options[0];
    return picked ? picked.value : "";
  }
  set value(value) { this._value = String(value); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.children = []; }
  set innerHTML(value) {
    const made = DATA.fragments[String(value)];
    if (!made) { throw new Error("innerHTML takes only what the server drew"); }
    this._text = ""; this.children = [];
    made.forEach((node) => this.appendChild(build(node)));
  }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  addEventListener(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); }
  fire(name, more) {
    const event = Object.assign({type: name, target: this, defaultPrevented: false,
                                 preventDefault() { this.defaultPrevented = true; },
                                 stopPropagation() {}}, more || {});
    (this.handlers[name] || []).forEach((fn) => fn(event));
    return event;
  }
  dispatchEvent(event) { this.fire(event.type, {detail: event.detail}); return true; }
  focus() { document.activeElement = this; }
  contains(other) { for (let n = other; n; n = n.parentNode) { if (n === this) { return true; } } return false; }
  getAttribute(name) {
    if (name.startsWith("data-")) {
      const value = this.dataset[camel(name.slice(5))];
      return value === undefined ? null : String(value);
    }
    if (name === "type" && this.type) { return this.type; }
    return name in this.attrs ? this.attrs[name] : null;
  }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name.startsWith("data-")) { this.dataset[camel(name.slice(5))] = String(value); }
  }
  hasAttribute(name) { return this.getAttribute(name) !== null; }
  querySelectorAll(selector) {
    const parts = parseSelector(selector);
    const fits = (el, part) => (!part.tag || el.tagName === part.tag) && part.tests.every((t) => {
      const value = el.getAttribute(t.name);
      return value !== null && (t.value === undefined || value === t.value);
    });
    return this.all().filter((el) => {
      if (!fits(el, parts[parts.length - 1])) { return false; }
      let at = parts.length - 2;
      for (let up = el.parentNode; up && up !== this.parentNode && at >= 0; up = up.parentNode) {
        if (fits(up, parts[at])) { at -= 1; }
      }
      return at < 0;
    });
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function build(node) {
  if (node.text !== undefined) { return new Txt(node.text); }
  const el = new El(node.tag, node.attrs);
  if (node.tag === "textarea") {
    el._value = node.children.map((child) => child.text || "").join("");
    return el;
  }
  node.children.forEach((child) => el.appendChild(build(child)));
  return el;
}

const page = {map: null};
function layer(kind, more) {
  return Object.assign({
    kind: kind, handlers: {},
    on(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); return this; },
    fire(name, event) { (this.handlers[name] || []).forEach((fn) => fn(event || {})); },
    addTo(target) { target.items.push(this); return this; },
    bindTooltip(content) { this.tip = content; return this; },
    getBounds() { return {}; },
  }, more || {});
}
globalThis.L = {
  map(el) {
    page.map = layer("map", {
      el: el, items: [], view: null, fitted: 0,
      setView(center, zoom) { this.view = {center: [center[0], center[1]], zoom: zoom}; return this; },
      fitBounds() { this.fitted += 1; return this; }, invalidateSize() {},
      removeLayer(gone) { this.items = this.items.filter((item) => item !== gone); },
      getCenter() { return {lat: this.view.center[0], lng: this.view.center[1]}; },
      getZoom() { return this.view.zoom; }});
    return page.map;
  },
  layerGroup() {
    return layer("group", {items: [], clearLayers() { this.items = []; },
                           eachLayer(fn) { this.items.forEach(fn); }});
  },
  featureGroup(list) { return layer("feature", {list: list}); },
  marker(at, options) {
    return layer("marker", {at: [at[0], at[1]], options: options || {},
                            setLatLng(to) { this.at = [to[0], to[1]]; },
                            getLatLng() { return {lat: this.at[0], lng: this.at[1]}; }});
  },
  circleMarker(at) { return layer("circle", {at: [at[0], at[1]]}); },
  circle(at, options) { return layer("ring", {at: [at[0], at[1]], options: options}); },
  polygon(list) { return layer("polygon", {list: list}); },
  polyline(list) { return layer("polyline", {list: list}); },
  geoJSON(feature) { return layer("geojson", {feature: feature}); },
  divIcon(options) { return options; },
  DomEvent: {stopPropagation() {}},
};
globalThis.totoTileLayer = () => null;
globalThis.classicPin = () => ({});

const box = build(DATA.tree);
const docHandlers = {};
globalThis.document = {
  readyState: "loading", activeElement: null,
  getElementById: (id) => (id === box.dataset.config ? {textContent: JSON.stringify(DATA.config)} : null),
  createElement: (tag) => new El(tag),
  addEventListener(name, fn) { (docHandlers[name] = docHandlers[name] || []).push(fn); },
  fire(name, event) { (docHandlers[name] || []).forEach((fn) => fn(event)); },
  querySelectorAll: () => [],
};
require(process.argv[2]);
const G = require(process.argv[1]);

const part = (name) => box.querySelector('[data-geo="' + name + '"]');
const press = (name) => part(name).fire("click");
const hidden = (name) => part(name).classList.contains("hidden");
// A dialog as the script left it: open or not (Alpine draws it in a browser).
const shown = (name) => part(name).dataset.open === "1";
// Leaving a dialog by Escape, the backdrop or the X: the template's event.
const dismiss = (name) => part(name).fire("geography-dismiss");
const mode = (name) => part(name).querySelectorAll("[data-geo-mode]")
  .filter((node) => !node.classList.contains("hidden")).map((node) => node.dataset.geoMode);
const clickMap = (lat, lng) => page.map.fire("click", {latlng: {lat: lat, lng: lng}});
const tab = (name) => box.querySelector('[data-geo-tab="' + name + '"]');
const tabsNow = () => box.querySelectorAll("[data-geo-tab]")
  .map((t) => [t.dataset.geoTab, t.getAttribute("aria-selected"), t.getAttribute("tabindex")]);
const panelsNow = () => box.querySelectorAll("[data-geo-panel]")
  .filter((p) => !p.classList.contains("hidden")).map((p) => p.dataset.geoPanel);
const drawerOpen = () => !hidden("drawer") && part("drawer").classList.contains("flex");
const names = (list) => part(list).elements().map((line) => {
  const button = line.elements()[0];
  return button ? button.elements()[0].textContent : line.textContent;
});
const rowButton = (id) => part("index").querySelectorAll("button").find((b) => b.dataset.row === id);
const ticks = () => part("community-boxes").querySelectorAll("input");
const ticked = () => ticks().filter((t) => t.checked).map((t) => t.value);
const tick = (slug, on) => {
  const one = ticks().find((t) => t.value === slug);
  one.checked = on;
  one.fire("change");
};
const chosen = () => part("community-chosen").textContent;
const options = (name) => part(name).all().filter((o) => o.tagName === "OPTION")
  .map((o) => [o.value, o.textContent]);
const groups = () => page.map.items.filter((item) => item.kind === "group");
const LAYER = {rows: 0, hits: 1, temporary: 2, route: 3, nearby: 4, draft: 5};
const drawn = (name) => groups()[LAYER[name]].items.map((item) => item.kind);
const draft = () => groups()[LAYER.draft].items[0] || null;
const settle = async () => { for (let i = 0; i < 8; i++) { await new Promise((r) => setImmediate(r)); } };
const ROUTE = {line: {type: "LineString", coordinates: [[21, 52], [19, 50]]},
               distance_km: 290.1, duration_min: 200.5, charged: true};
(async () => {
  const out = {};
  const mounted = G.mount(box);
  __BODY__
  out.touched = touched;
  out.calls = calls;
  out.replaced = replaced;
  out.assigned = assigned;
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch((error) => { console.error(error); process.exit(1); });
"""

CONFIG = r'<script id="geography-locations-config" type="application/json">(.*?)</script>'
SITE = "https://zenobia.test"
ROUTING = {"enabled": True, "timeout": 10,
           "endpoints": {"car": "https://router.example/routed-car/route/v1/driving"}}
INDEX_OPEN = [["index", "true", "0"], ["nearby", "false", "-1"], ["route", "false", "-1"]]
#: The index with nothing filtered: mia's own point (under her name), then
#: the pins, the newest first. A nearby search lists the nearest first.
EVERYTHING = ["Mia", "Mill", "Well"]


@override_settings(LOCATIONS_GEOCODING={"enabled": True}, GEOGRAPHY_ROUTING=ROUTING)
class PageCase(LocationsCase):
    """``mount()`` run on the page the server answers mia with. She belongs
    to Guild and to Other. On the map: her own point (far away), her pin in
    Guild (Well) and olga's pin in Other (Mill, a kilometre north)."""

    def setUp(self):
        super().setUp()
        if not _NODE:
            self.skipTest("node is not installed")
        self.member.communities.add(self.other)
        self.well = self.pin()
        self.mill = self.pin(self.other_user, self.other, name="Mill", lat=52.24, lng=21.0122,
                             postal_address="", note="")
        saves.save_person_point(self.member_user, self.member, lat=50.0, lng=19.9, name="Home",
                                note="", op=op())
        self.guild_slug, self.other_slug = self.guild.slug, self.other.slug

    def run_page(self, body, *, user=None, query="", fragments=None):
        import re

        client = client_of(user or self.member_user)
        response = client.get(self.page_url + query)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        data = {
            "tree": element(tree_of(html), "data-geography-locations"),
            "config": json.loads(re.search(CONFIG, html, re.S).group(1)),
            "href": SITE + self.page_url + query,
            "fragments": {name: tree_of(client.get(url).content.decode())
                          for name, url in (fragments or {}).items()},
            "extra": {"guild": self.guild_slug, "other": self.other_slug,
                      "well": f"pin:{self.well.uid}", "mill": f"pin:{self.mill.uid}",
                      "at": SITE + self.page_url},
        }
        scripts = [finders.find(f"geography/{name}")
                   for name in ("locations.js", "map.js")]
        with tempfile.TemporaryDirectory() as folder:
            handed = Path(folder) / "page.json"
            handed.write_text(json.dumps(data), encoding="utf-8")
            done = subprocess.run([_NODE, "-e", PAGE.replace("__BODY__", body), *scripts,
                                   str(handed)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])


class TabScriptTests(PageCase):
    """The owner, 2026-10-06: "Search nearby and Route in locations should
    be different tabs"."""

    def test_one_tab_is_open_at_a_time_and_the_arrow_keys_move_between_them(self):
        out = self.run_page("""
          out.start = [tabsNow(), panelsNow(), replaced.length];
          tab("nearby").fire("click");
          out.nearby = [tabsNow(), panelsNow(), replaced.slice()];
          tab("nearby").fire("click");
          out.again = replaced.length;
          const key = (name, k) => tab(name).fire("keydown", {key: k});
          let event = key("nearby", "ArrowRight");
          out.right = [panelsNow(), document.activeElement === tab("route"), event.defaultPrevented];
          key("route", "ArrowRight"); out.round = [panelsNow(), document.activeElement === tab("index")];
          key("index", "ArrowLeft"); out.left = panelsNow();
          key("route", "Home"); out.home = panelsNow();
          key("index", "End"); out.end = [panelsNow(), tabsNow()];
          event = key("route", "Enter");
          out.other = [panelsNow(), event.defaultPrevented];
          tab("index").fire("click");
          out.back = [location.href, panelsNow()];
          out.addresses = replaced.slice();
        """)
        at = SITE + self.page_url
        self.assertEqual(out["start"], [INDEX_OPEN, ["index"], 0],
                         "the page starts as the server drew it and rewrites nothing")
        self.assertEqual(out["nearby"], [[["index", "false", "-1"], ["nearby", "true", "0"],
                                          ["route", "false", "-1"]], ["nearby"],
                                         [at + "?tool=nearby"]])
        self.assertEqual(out["again"], 1, "the open tab pressed again changes nothing")
        self.assertEqual(out["right"], [["route"], True, True])
        self.assertEqual(out["round"], [["index"], True])
        self.assertEqual(out["left"], ["route"])
        self.assertEqual(out["home"], ["index"])
        self.assertEqual(out["end"], [["route"], [["index", "false", "-1"], ["nearby", "false", "-1"],
                                                  ["route", "true", "0"]]])
        self.assertEqual(out["other"], [["route"], False], "any other key is the browser's")
        self.assertEqual(out["back"], [at, ["index"]], "the index is the address without a tab")
        self.assertEqual(out["addresses"][-2:], [at + "?tool=route", at])
        # Switching tabs asks the server nothing and writes no storage.
        self.assertEqual((out["calls"], out["touched"], out["assigned"]), ([], [], []))

    def test_the_tab_the_address_names_is_open_after_a_reload(self):
        out = self.run_page("""
          out.start = [panelsNow(), tabsNow()[2], replaced.length];
          tab("nearby").fire("click");
          out.then = [panelsNow(), location.href];
        """, query=f"?tool=route&community={self.guild_slug}")
        at = SITE + self.page_url
        self.assertEqual(out["start"], [["route"], ["route", "true", "0"], 0])
        self.assertEqual(out["then"], [["nearby"],
                                       f"{at}?tool=nearby&community={self.guild_slug}"])
        self.assertEqual(out["calls"], [])

    def test_the_map_keeps_what_is_on_it_when_the_tab_changes(self):
        """A temporary point, a nearby search with its ring and its rows, a
        kept search hit and a drawn route: all still there after going
        round the tabs, and going round asked for nothing."""
        out = self.run_page("""
          clickMap(52.3, 21.2); press("click-temporary");
          clickMap(52.2297, 21.0122); press("click-centre");
          out.centre = [panelsNow(), part("nearby-centre").textContent, drawerOpen(), location.href];
          part("nearby-radius").value = "5"; press("nearby-go");
          out.found = [names("nearby-results"), names("index"), drawn("nearby"),
                       part("nearby-note").textContent];
          queue.push({status: 200, data: {results: [{label: "Cafe", lat: 52.25, lng: 21.05}]}});
          part("q").value = "cafe"; press("search-go"); await settle();
          tab("route").fire("click");
          queue.push({status: 200, data: ROUTE}); press("route-go"); await settle();
          const snapshot = () => JSON.stringify({
            layers: Object.keys(LAYER).map((name) => [name, drawn(name)]),
            temporary: mounted.controller.state.temporary.length,
            hits: mounted.controller.state.hits.length, near: mounted.state.near,
            results: names("nearby-results"), index: names("index"),
            route: part("route-note").textContent, centre: part("nearby-centre").textContent,
            from: part("from").value, to: part("to").value});
          const before = snapshot();
          const asked = calls.length, fitted = page.map.fitted;
          ["index", "nearby", "route", "nearby", "index"].forEach((name) => tab(name).fire("click"));
          tab("index").fire("keydown", {key: "ArrowRight"});
          out.same = snapshot() === before;
          out.before = JSON.parse(before);
          out.quiet = [calls.length - asked, page.map.fitted - fitted];
          out.sent = calls.map((call) => call.url);
        """)
        self.assertEqual(out["centre"][:3], [["nearby"], "Centre: The point you clicked", True])
        self.assertTrue(out["centre"][3].endswith("?tool=nearby"))
        self.assertEqual(out["found"], [["Well", "Mill"], ["Well", "Mill"], ["ring"],
                                        "2 within 5 km"])
        self.assertTrue(out["same"])
        before = out["before"]
        self.assertEqual(dict(before["layers"]), {
            "rows": ["marker", "marker"], "hits": ["marker"], "temporary": ["circle"],
            "route": ["geojson"], "nearby": ["ring"], "draft": []})
        self.assertEqual((before["temporary"], before["hits"], before["results"]),
                         (1, 1, ["Well", "Mill"]))
        self.assertEqual(before["route"], "290.1 km, about 200.5 min")
        self.assertEqual(out["quiet"], [0, 0], "no request and no move of the map")
        config_urls = [reverse_("search"), reverse_("route")]
        self.assertEqual(out["sent"], config_urls)

    def test_a_phone_opens_the_drawer_at_each_tab(self):
        out = self.run_page("""
          const opener = (name) => box.querySelector('[data-geo-open="' + name + '"]');
          out.start = drawerOpen();
          opener("route").fire("click");
          out.route = [drawerOpen(), panelsNow()];
          press("drawer-close");
          out.closed = [drawerOpen(), panelsNow()];
          opener("nearby").fire("click");
          out.nearby = [drawerOpen(), panelsNow()];
          opener("index").fire("click");
          out.index = [drawerOpen(), panelsNow()];
          rowButton(X.mill).fire("click");
          out.row = [drawerOpen(), panelsNow()];
        """)
        self.assertFalse(out["start"])
        self.assertEqual(out["route"], [True, ["route"]])
        self.assertEqual(out["closed"], [False, ["route"]], "closing the drawer keeps its tab")
        self.assertEqual(out["nearby"], [True, ["nearby"]])
        self.assertEqual(out["index"], [True, ["index"]])
        self.assertEqual(out["row"], [False, ["index"]], "a chosen row shows the map again")
        self.assertEqual([call["method"] for call in out["calls"]], ["GET"])   # the row's details


def reverse_(name):
    from django.urls import reverse

    return reverse(f"geography:{name}")


class CommunityFilterScriptTests(PageCase):
    """The owner, 2026-10-06: "istead of community choice in locations app
    there should be dropdown with checkboxes filter by community"."""

    def test_several_one_and_none_ticked(self):
        out = self.run_page("""
          out.start = [chosen(), ticks().map((t) => [t.value, t.type, t.parentNode.tagName,
                                                     t.parentNode.textContent]),
                       ticked(), names("index"), drawn("rows").length];
          tick(X.guild, true);
          out.one = [chosen(), ticked(), names("index"), drawn("rows").length, location.href,
                     part("shown").textContent];
          tick(X.other, true);
          out.two = [chosen(), ticked(), names("index"), drawn("rows").length, location.href];
          tick(X.guild, false);
          out.other = [chosen(), names("index"), location.href];
          press("community-none");
          out.none = [chosen(), ticked(), names("index"), location.href];
          press("community-all");
          out.all = [chosen(), ticked(), names("index")];
          press("reset");
          out.reset = [chosen(), ticked(), names("index"), location.href];
        """)
        at = SITE + self.page_url
        guild, other = self.guild_slug, self.other_slug
        self.assertEqual(out["start"], ["All communities",
                                        [[guild, "checkbox", "LABEL", "Guild"],
                                         [other, "checkbox", "LABEL", "Other"]],
                                        [], EVERYTHING, 3])
        self.assertEqual(out["one"], ["Guild", [guild], ["Well"], 1, f"{at}?community={guild}",
                                      "1 shown"])
        self.assertEqual(out["two"], ["2 communities", [guild, other], ["Mill", "Well"], 2,
                                      f"{at}?community={guild}&community={other}"])
        self.assertEqual(out["other"], ["Other", ["Mill"], f"{at}?community={other}"])
        self.assertEqual(out["none"], ["All communities", [], EVERYTHING, at])
        # Every community ticked is "the rows of these communities": a
        # person's point belongs to none, as under the single choice.
        self.assertEqual(out["all"], ["2 communities", [guild, other], ["Mill", "Well"]])
        self.assertEqual(out["reset"], ["All communities", [], EVERYTHING, at])
        # Ticking sends nothing: no request, no storage, no reload.
        self.assertEqual((out["calls"], out["touched"], out["assigned"]), ([], [], []))

    def test_the_address_ticks_them_at_loading(self):
        guild, other = self.guild_slug, self.other_slug
        for query, label, expected, rows in (
                (f"?community={guild}", "Guild", [guild], ["Well"]),
                (f"?community={other}&community={guild}", "2 communities", [guild, other],
                 ["Mill", "Well"]),
                (f"?community=no-such&community=toString&community={other}", "Other", [other],
                 ["Mill"]),
                ("?community=no-such", "All communities", [], EVERYTHING)):
            with self.subTest(query=query):
                out = self.run_page("""
                  out.now = [chosen(), ticked(), names("index")];
                  out.rewritten = replaced.length;
                """, query=query)
                self.assertEqual(out["now"], [label, expected, rows])
                self.assertEqual((out["rewritten"], out["calls"]), (0, []))

    def test_search_nearby_is_narrowed_by_it(self):
        out = self.run_page("""
          clickMap(52.2297, 21.0122); press("click-centre");
          part("nearby-radius").value = "5"; press("nearby-go");
          out.everything = [names("nearby-results"), part("nearby-note").textContent];
          tick(X.other, true);
          out.other = [names("nearby-results"), names("index"), part("nearby-note").textContent,
                       drawn("rows").length, drawn("nearby")];
          tick(X.guild, true); tick(X.other, false);
          out.guild = names("nearby-results");
          part("nearby-radius").value = "1"; press("nearby-go");
          out.near = [names("nearby-results"), part("shown").textContent];
          press("community-none");
          out.none = names("nearby-results");
          press("nearby-clear");
          out.cleared = [names("nearby-results"), names("index"), drawn("nearby")];
        """)
        self.assertEqual(out["everything"], [["Well", "Mill"], "2 within 5 km"])
        self.assertEqual(out["other"], [["Mill"], ["Mill"], "1 within 5 km", 1, ["ring"]])
        self.assertEqual(out["guild"], ["Well"])
        self.assertEqual(out["near"], [["Well"], "1 within 1 km"])
        self.assertEqual(out["none"], ["Well"])
        self.assertEqual(out["cleared"], [[], EVERYTHING, []])
        self.assertEqual((out["calls"], out["touched"]), ([], []))

    def test_the_list_opens_and_closes_from_the_keyboard_and_on_a_click_outside(self):
        out = self.run_page("""
          const state = () => [hidden("community-list"),
                               part("community-toggle").getAttribute("aria-expanded")];
          out.start = state();
          press("community-toggle"); out.opened = state();
          press("community-toggle"); out.toggled = state();
          let event = part("community-toggle").fire("keydown", {key: "ArrowDown"});
          out.down = [state(), document.activeElement === ticks()[0], event.defaultPrevented];
          part("community-filter").fire("keydown", {key: "Escape"});
          out.escape = [state(), document.activeElement === part("community-toggle")];
          press("community-toggle");
          document.fire("click", {target: ticks()[1]});
          out.inside = state();
          document.fire("click", {target: part("map")});
          out.outside = state();
          press("community-toggle");
          part("community-filter").fire("focusout", {relatedTarget: ticks()[1]});
          out.within = state();
          part("community-filter").fire("focusout", {relatedTarget: part("filter")});
          out.away = state();
        """)
        closed, opened = [True, "false"], [False, "true"]
        self.assertEqual((out["start"], out["opened"], out["toggled"]), (closed, opened, closed))
        self.assertEqual(out["down"], [opened, True, True])
        self.assertEqual(out["escape"], [closed, True])
        self.assertEqual((out["inside"], out["outside"]), (opened, closed))
        self.assertEqual((out["within"], out["away"]), (opened, closed))
        self.assertEqual(out["calls"], [])

    def test_a_new_pin_still_belongs_to_one_community(self):
        """The save dialog offers the member's communities as a single
        choice, and starts at the one the filter shows when it shows one."""
        out = self.run_page("""
          tick(X.other, true);
          clickMap(52.5, 21.5); press("click-pin"); press("pin-continue");
          out.pin = [part("pin-community").tagName, options("pin-community"),
                     part("pin-community").value];
        """)
        self.assertEqual(out["pin"], ["SELECT", [[self.guild_slug, "Guild"],
                                                 [self.other_slug, "Other"]], self.other_slug])


class DialogScriptTests(PageCase):
    """The owner, 2026-10-06: "the information like name etc should be
    inputed via modal and modal only"."""

    def test_a_pin_is_placed_on_the_map_and_named_in_the_dialog(self):
        out = self.run_page("""
          clickMap(52.5, 21.5);
          out.menu = [hidden("click-menu"), shown("pin-dialog")];
          press("click-pin");
          out.tool = [hidden("pin-tool"), drawn("draft"), draft().options.draggable,
                      shown("pin-dialog"), part("pin-continue").disabled, hidden("click-menu")];
          draft().setLatLng([52.6, 381.6]);
          press("pin-continue");
          out.opened = [shown("pin-dialog"), mode("pin-dialog"), part("pin-community").value];
          part("pin-community").value = X.other;
          part("pin-name").value = "Mill pond";
          part("pin-postal").value = "Mill lane 2";
          part("pin-note").value = "deep";
          queue.push({status: 402, data: {error: "Not enough storage mana."}}, {network: true},
                     {status: 200, data: {pin: {uid: "abc"}}});
          press("pin-save"); await settle();
          out.refused = [shown("pin-dialog"), part("pin-status").textContent, hidden("pin-status"),
                         part("pin-name").value, part("pin-postal").value, part("pin-note").value,
                         part("pin-community").value, drawn("draft"), part("pin-save").disabled,
                         assigned.length];
          press("pin-save"); await settle();
          out.offline = [shown("pin-dialog"), part("pin-status").textContent, assigned.length];
          press("pin-save"); await settle();
          out.sent = calls.map((call) => [call.url, call.method]);
          out.bodies = calls.map((call) => call.body);
        """)
        self.assertEqual(out["menu"], [False, False])
        self.assertEqual(out["tool"], [False, ["marker"], True, False, False, True],
                         "the pin is on the map; nothing is asked yet")
        self.assertEqual(out["opened"], [True, ["new", "new", "new"], self.guild_slug])
        self.assertEqual(out["refused"], [True, "Not enough storage mana.", False, "Mill pond",
                                          "Mill lane 2", "deep", self.other_slug, ["marker"],
                                          False, 0])
        self.assertEqual(out["offline"], [True, "That did not work. Try again.", 0])
        door = self.url("pin_create", community=self.other)
        self.assertEqual(out["sent"], [[door, "POST"]] * 3)
        first, second, third = out["bodies"]
        # What the form beside the map sent, to the last key.
        self.assertEqual({key: value for key, value in third.items() if key != "op"},
                         {"lat": 52.6, "lng": 21.6, "name": "Mill pond",
                          "postal_address": "Mill lane 2", "note": "deep"})
        self.assertNotEqual(first["op"], second["op"], "an answered refusal ends its op")
        self.assertEqual(second["op"], third["op"], "an unanswered press repeats its op")
        self.assertEqual(out["assigned"], [f"{SITE}{self.page_url}?open=pin%3Aabc"])

    def test_cancel_in_the_pin_dialog_keeps_the_pin_on_the_map(self):
        out = self.run_page("""
          clickMap(52.5, 21.5); press("click-pin"); press("pin-continue");
          part("pin-name").value = "Typed";
          press("pin-dialog-cancel");
          out.cancelled = [shown("pin-dialog"), hidden("pin-tool"), drawn("draft"), draft().at];
          clickMap(52.7, 21.7);
          out.moved = [draft().at, hidden("click-menu")];
          press("pin-continue");
          out.again = [shown("pin-dialog"), part("pin-name").value];
          dismiss("pin-dialog");
          out.dismissed = [shown("pin-dialog"), drawn("draft"), hidden("pin-tool")];
          press("pin-cancel");
          out.dropped = [hidden("pin-tool"), drawn("draft"), part("pin-name").value];
          clickMap(52.8, 21.8);
          out.after = hidden("click-menu");
        """)
        self.assertEqual(out["cancelled"], [False, False, ["marker"], [52.5, 21.5]])
        self.assertEqual(out["moved"], [[52.7, 21.7], True], "a click moves the pin being placed")
        self.assertEqual(out["again"], [True, "Typed"], "what was typed is still there")
        self.assertEqual(out["dismissed"], [False, ["marker"], False],
                         "Escape, the backdrop and the X leave the pin too")
        self.assertEqual(out["dropped"], [True, [], ""], "Cancel beside the map drops the pin")
        self.assertFalse(out["after"], "and a click offers its choices again")
        self.assertEqual(out["calls"], [])

    def test_no_zone_is_drawn_on_this_page(self):
        """The owner, 2026-10-06: "remove 'draw community zone' from general
        locations tab". The page has no control for it and the script holds
        no editor: a click on the map offers a point, as before."""
        out = self.run_page("""
          out.parts = ["zone-open", "zone-tool", "zone-continue", "zone-cancel",
                       "zone-community"].map((name) => part(name) === null);
          out.state = ["zoneEditor" in mounted.state, typeof globalThis.GeographyZone];
          out.api = [typeof G.homeRing];
          clickMap(52.5, 21.5);
          out.menu = hidden("click-menu");
        """)
        self.assertEqual(out["parts"], [True] * 5)
        self.assertEqual(out["state"], [False, "undefined"])
        self.assertEqual(out["api"], ["undefined"])
        self.assertFalse(out["menu"])
        self.assertEqual(out["calls"], [])

    def test_the_author_s_change_opens_the_same_dialog_filled_in(self):
        changed = self.pin(name='A "well" <b>', note="line one\nline two")
        url = self.url("pin_detail", changed)
        out = self.run_page("""
          queue.push({status: 200, text: "PIN"});
          rowButton(X.row).fire("click"); await settle();
          const change = part("details").querySelector("[data-geo-change]");
          out.details = [hidden("details"), change.tagName,
                         part("details").querySelectorAll("input").length
                         + part("details").querySelectorAll("textarea").length
                         - part("details").querySelectorAll("[data-geo-thread] input").length
                         - part("details").querySelectorAll("[data-geo-thread] textarea").length];
          change.fire("click");
          out.opened = [shown("pin-dialog"), mode("pin-dialog"), part("pin-name").value,
                        part("pin-postal").value, part("pin-note").value,
                        part("pin-community-fixed").textContent, drawn("draft"), hidden("pin-tool")];
          part("pin-name").value = "Old well";
          queue.push({status: 409, data: {error: "This request was already used."}},
                     {status: 200, data: {pin: {}}});
          press("pin-save"); await settle();
          out.refused = [shown("pin-dialog"), part("pin-status").textContent,
                         part("pin-name").value, part("pin-note").value, assigned.length];
          press("pin-save"); await settle();
          out.sent = calls.slice(1).map((call) => [call.url, call.method]);
          out.bodies = calls.slice(1).map((call) => call.body);
          out.first = [calls[0].url, calls[0].method];
        """.replace("X.row", json.dumps(f"pin:{changed.uid}")), fragments={"PIN": url})
        self.assertEqual(out["first"], [url, "GET"])
        self.assertEqual(out["details"], [False, "BUTTON", 0],
                         "the details hold the button, and no field for the words")
        self.assertEqual(out["opened"], [True, ["change", "change", "change"], 'A "well" <b>',
                                         "Rynek 1", "line one\nline two", "Guild", [], True],
                         "filled in from what is saved; nothing is put on the map")
        self.assertEqual(out["refused"], [True, "This request was already used.", "Old well",
                                          "line one\nline two", 0])
        self.assertEqual(out["sent"], [[url, "POST"]] * 2)
        first, second = out["bodies"]
        # What the details' form sent: the words, and no coordinate.
        self.assertEqual({key: value for key, value in second.items() if key != "op"},
                         {"name": "Old well", "postal_address": "Rynek 1",
                          "note": "line one\nline two"})
        self.assertNotEqual(first["op"], second["op"])
        self.assertEqual(out["assigned"],
                         [f"{SITE}{self.page_url}?open=pin%3A{changed.uid}"])

    def test_a_zone_s_change_opens_its_dialog_filled_in(self):
        area = self.zone()
        url = self.url("zone_detail", area)
        out = self.run_page("""
          queue.push({status: 200, text: "ZONE"});
          rowButton(X.row).fire("click"); await settle();
          part("details").querySelector("[data-geo-change]").fire("click");
          out.opened = [shown("zone-dialog"), part("zone-name").value,
                        part("zone-description").value, part("zone-community-fixed").textContent,
                        shown("pin-dialog"), drawn("draft")];
          part("zone-description").value = "wider";
          press("zone-dialog-cancel");
          out.cancelled = [shown("zone-dialog"), calls.length];
          part("details").querySelector("[data-geo-change]").fire("click");
          out.again = [shown("zone-dialog"), part("zone-description").value];
          part("zone-description").value = "wider";
          queue.push({status: 409, data: {error: "This request was already used."}},
                     {status: 200, data: {zone: {}}});
          press("zone-save"); await settle();
          out.refused = [shown("zone-dialog"), part("zone-status").textContent,
                         part("zone-description").value, assigned.length];
          press("zone-save"); await settle();
          out.sent = calls.slice(1).map((call) => [call.url, call.method,
                                                   Object.keys(call.body).sort(),
                                                   call.body.name, call.body.description]);
        """.replace("X.row", json.dumps(f"zone:{area.uid}")), fragments={"ZONE": url})
        self.assertEqual(out["opened"], [True, "Meadow", "between the river and the road",
                                         "Guild", False, []],
                         "filled in from what is saved; nothing is put on the map")
        self.assertEqual(out["cancelled"], [False, 1], "Cancel sends nothing")
        self.assertEqual(out["again"], [True, "between the river and the road"],
                         "a change always starts from the saved words")
        self.assertEqual(out["refused"], [True, "This request was already used.", "wider", 0])
        # The words, and no outline: a saved zone is not redrawn here.
        self.assertEqual(out["sent"], [[url, "POST", ["description", "name", "op"], "Meadow",
                                        "wider"]] * 2)
        self.assertEqual(out["assigned"],
                         [f"{SITE}{self.page_url}?open=zone%3A{area.uid}"])


class KeepScriptTests(PageCase):
    """The owner, 2026-10-07: "if I add something to the map after search it
    and I press "keep" it should be added yto my pins". The pins a member
    saves on this page are a community's, so "Keep on the map" beside a
    search result puts the pin to drag at the result and opens the pin's
    dialog at once, with the result's words filled in. Save is the door a
    clicked pin uses; nothing is sent before it."""

    KRAKOW = {"label": "Kraków, województwo małopolskie, Polska", "lat": 50.0619474,
              "lng": 19.9368564}
    SEARCH = """
      queue.push({status: 200, data: {results: HITS}});
      part("q").value = "kraków"; press("search-go"); await settle();
      const keeps = () => part("results").elements().map((line) => line.elements()[1]);
      const asked = calls.length;
    """

    def run_search(self, body, hits=None, **more):
        return self.run_page((self.SEARCH + body).replace(
            "HITS", json.dumps(hits or [self.KRAKOW])), **more)

    def test_keep_opens_the_pin_s_dialog_filled_in_and_save_sends_it(self):
        out = self.run_search("""
          out.listed = [keeps().length, keeps()[0].tagName, keeps()[0].textContent,
                        part("results").elements()[0].elements().length];
          keeps()[0].fire("click");
          out.kept = [shown("pin-dialog"), mode("pin-dialog"), hidden("pin-tool"), drawn("draft"),
                      draft().at, draft().options.draggable, part("pin-continue").disabled];
          out.words = [part("pin-name").value, part("pin-postal").value, part("pin-note").value,
                       part("pin-community").value, options("pin-community")];
          out.quiet = [calls.length - asked, mounted.controller.state.temporary.length,
                       drawn("temporary"), assigned.length, hidden("search-note")];
          // Still an end of a route while it is listed.
          out.end = mounted.controller.ends().filter((end) => end.kind === "hit")
            .map((end) => end.label);
          part("pin-note").value = "the old town";
          queue.push({status: 402, data: {error: "Not enough storage mana."}},
                     {status: 200, data: {pin: {uid: "abc"}}});
          press("pin-save"); await settle();
          out.refused = [shown("pin-dialog"), part("pin-status").textContent,
                         part("pin-name").value, part("pin-postal").value, part("pin-note").value,
                         drawn("draft"), part("pin-save").disabled, assigned.length];
          press("pin-save"); await settle();
          out.sent = calls.slice(asked).map((call) => [call.url, call.method]);
          out.bodies = calls.slice(asked).map((call) => call.body);
        """)
        self.assertEqual(out["listed"], [1, "BUTTON", "Keep on the map", 2],
                         "one button beside a result, and no second one")
        self.assertEqual(out["kept"], [True, ["new", "new", "new"], False, ["marker"],
                                       [50.061947, 19.936856], True, False])
        self.assertEqual(out["words"], ["Kraków", "Kraków, województwo małopolskie, Polska", "",
                                        self.guild_slug, [[self.guild_slug, "Guild"],
                                                          [self.other_slug, "Other"]]])
        self.assertEqual(out["quiet"], [0, 0, [], 0, True],
                         "nothing is sent, and no temporary point is made, by Keep")
        self.assertEqual(out["end"], [self.KRAKOW["label"]])
        self.assertEqual(out["refused"], [True, "Not enough storage mana.", "Kraków",
                                          "Kraków, województwo małopolskie, Polska",
                                          "the old town", ["marker"], False, 0])
        door = self.url("pin_create", community=self.guild)
        self.assertEqual(out["sent"], [[door, "POST"]] * 2, "one request for each press of Save")
        first, second = out["bodies"]
        self.assertEqual({key: value for key, value in second.items() if key != "op"},
                         {"lat": 50.061947, "lng": 19.936856, "name": "Kraków",
                          "postal_address": "Kraków, województwo małopolskie, Polska",
                          "note": "the old town"})
        self.assertTrue(first["op"] and second["op"])
        self.assertNotEqual(first["op"], second["op"], "an answered refusal ends its op")
        self.assertEqual(out["assigned"], [f"{SITE}{self.page_url}?open=pin%3Aabc"])
        self.assertEqual(out["touched"], [])

    def test_the_words_are_cut_to_what_the_fields_take(self):
        label = "N" * 250 + " , " + "x" * 2100
        out = self.run_search("""
          keeps()[0].fire("click");
          out.words = [part("pin-name").value, part("pin-postal").value];
          out.most = [part("pin-name").getAttribute("maxlength"),
                      part("pin-postal").getAttribute("maxlength")];
          keeps()[1].fire("click");
          out.plain = [part("pin-name").value, part("pin-postal").value, drawn("draft"), draft().at];
        """, hits=[{"label": label, "lat": 50.1, "lng": 20.1},
                   {"label": "  Wawel  ", "lat": 50.054, "lng": 19.935}])
        self.assertEqual(out["most"], ["200", "2000"])
        self.assertEqual(out["words"], ["N" * 200, label[:2000]])
        # A label with no comma is the name whole; the second Keep moves the
        # one pin to its own result.
        self.assertEqual(out["plain"], ["Wawel", "  Wawel  ", ["marker"], [50.054, 19.935]])
        self.assertEqual(len(out["calls"]), 1, "the search, and nothing else")

    def test_leaving_the_dialog_leaves_the_pin_and_cancel_beside_the_map_drops_it(self):
        out = self.run_search("""
          tick(X.other, true);
          keeps()[0].fire("click");
          out.community = part("pin-community").value;
          part("pin-name").value = "Old town";
          dismiss("pin-dialog");
          out.left = [shown("pin-dialog"), hidden("pin-tool"), drawn("draft"), draft().at];
          clickMap(50.07, 19.95);
          out.moved = [draft().at, hidden("click-menu")];
          press("pin-continue");
          out.again = [shown("pin-dialog"), part("pin-name").value, part("pin-postal").value];
          press("pin-dialog-cancel");
          out.cancelled = [shown("pin-dialog"), drawn("draft"), calls.length - asked];
          press("pin-continue");
          queue.push({status: 200, data: {pin: {uid: "abc"}}});
          press("pin-save"); await settle();
          out.sent = calls.slice(asked).map((call) => [call.url, call.body.lat, call.body.lng,
                                                       call.body.name, call.body.postal_address]);
        """)
        self.assertEqual(out["community"], self.other_slug, "the one community the filter shows")
        self.assertEqual(out["left"], [False, False, ["marker"], [50.061947, 19.936856]])
        self.assertEqual(out["moved"], [[50.07, 19.95], True])
        self.assertEqual(out["again"], [True, "Old town", self.KRAKOW["label"]])
        self.assertEqual(out["cancelled"], [False, ["marker"], 0])
        self.assertEqual(out["sent"], [[self.url("pin_create", community=self.other), 50.07, 19.95,
                                        "Old town", self.KRAKOW["label"]]])

    def test_cancel_beside_the_map_drops_the_kept_result_s_pin_and_its_words(self):
        out = self.run_search("""
          keeps()[0].fire("click");
          dismiss("pin-dialog");
          press("pin-cancel");
          out.dropped = [hidden("pin-tool"), drawn("draft"), part("pin-name").value,
                         part("pin-postal").value, calls.length - asked];
          // Words typed for a clicked pin that was not saved are replaced.
          clickMap(52.5, 21.5); press("click-pin"); press("pin-continue");
          part("pin-name").value = "Typed"; part("pin-note").value = "a note";
          press("pin-dialog-cancel");
          keeps()[0].fire("click");
          out.replaced_words = [shown("pin-dialog"), part("pin-name").value, part("pin-note").value,
                                drawn("draft"), draft().at, hidden("click-menu")];
        """)
        self.assertEqual(out["dropped"], [True, [], "", "", 0])
        self.assertEqual(out["replaced_words"], [True, "Kraków", "", ["marker"],
                                                 [50.061947, 19.936856], True])
        self.assertEqual(len(out["calls"]), 1, "the search, and nothing else")

    def test_with_no_community_to_save_into_keep_makes_a_temporary_point(self):
        out = self.run_search("""
          keeps()[0].fire("click");
          out.kept = [shown("pin-dialog"), hidden("pin-tool"), drawn("draft"), drawn("temporary"),
                      mounted.controller.state.temporary.map((p) => [p.lat, p.lng, p.label]),
                      part("search-note").textContent, hidden("search-note"),
                      calls.length - asked];
        """, user=self.staff_user)
        self.assertEqual(out["kept"], [
            False, True, [], ["circle"], [[50.061947, 19.936856, self.KRAKOW["label"]]],
            "You belong to no community yet, so there is nowhere to save a pin.", False, 0])
        self.assertEqual(len(out["calls"]), 1, "the search, and nothing else")
        self.assertEqual((out["assigned"], out["touched"]), ([], []))

    def test_a_result_s_name_with_markup_is_a_value_not_markup(self):
        name = "<img src=x onerror=alert(1)>"
        label = name + ", <b>Rynek</b>"
        out = self.run_search("""
          const line = part("results").elements()[0];
          out.listed = [line.elements()[0].textContent, line.all().length];
          keeps()[0].fire("click");
          out.words = [part("pin-name").value, part("pin-postal").value,
                       part("pin-name").children.length, part("pin-postal").children.length,
                       part("pin-dialog").querySelectorAll("img").length
                       + part("pin-dialog").querySelectorAll("b").length];
        """, hits=[{"label": label, "lat": 50.1, "lng": 20.1}])
        self.assertEqual(out["listed"], [label, 2], "the label is the button's text")
        self.assertEqual(out["words"], [name, label, 0, 0, 0])


class AdvancedFilterScriptTests(PageCase):
    """The owner, 2026-10-07: "also when filtering we need advanced butotn
    that opens a modal and you can select items to display, items go with
    name and type and checkbox, the list is scroollable. add advanced
    locations filter modal." On the page: mia's own point, Well (Guild),
    Mill (Other) and the zone Meadow (Guild)."""

    #: The dialog's lines, read as a member reads them.
    TOOLS = """
      const lines = () => part("advanced-list").elements().filter((li) => li.elements().length);
      const listed = () => lines().filter((li) => !li.classList.contains("hidden"));
      const parts = (li) => {
        const label = li.elements()[0];
        const [box, words] = label.elements();
        const [name, under] = words.elements();
        return {label: label, box: box, name: name.textContent, under: under.textContent};
      };
      const read = (list) => list.map((li) => { const p = parts(li);
        return [p.label.tagName, p.box.type, p.box.checked, p.name, p.under]; });
      const said = (list) => list.map((li) => parts(li).name);
      const chosenNow = () => lines().filter((li) => parts(li).box.checked).map((li) => parts(li).name);
      const set = (name, on) => {
        const box = parts(lines().find((li) => parts(li).name === name)).box;
        box.checked = on; box.fire("change");
      };
      const type = (text) => { part("advanced-filter").value = text; part("advanced-filter").fire("input"); };
      const button = () => part("advanced-label").textContent;
      const count = () => part("advanced-count").textContent;
      const rowsDrawn = () => drawn("rows").length;
    """
    #: As the index lists them (a person, the pins newest first, the zone),
    #: which for these four is also the dialog's order: by type, then name.
    ALL = ["Mia", "Mill", "Well", "Meadow"]
    LISTED = ALL

    def setUp(self):
        super().setUp()
        self.meadow = self.zone()

    def run_advanced(self, body, **more):
        return self.run_page(self.TOOLS + body, **more)

    def test_the_dialog_lists_every_row_with_its_name_its_type_and_a_ticked_box(self):
        self.pin(name="Zen", note="")       # the newest pin: first of the pins in the index
        out = self.run_advanced("""
          out.start = [shown("advanced-dialog"), button(), names("index"),
                       part("advanced-open").tagName];
          // Whatever the other filters say, every row is listed.
          part("filter").value = "well"; part("filter").fire("input");
          tick(X.other, true);
          out.filtered = names("index");
          press("advanced-open");
          out.opened = [shown("advanced-dialog"), read(lines()), count(), said(listed()),
                        part("advanced-list").tagName, part("advanced-filter").value];
          out.quiet = [calls.length, replaced.length];
        """)
        self.assertEqual(out["start"], [False, "Advanced",
                                        ["Mia", "Zen", "Mill", "Well", "Meadow"], "BUTTON"])
        self.assertEqual(out["filtered"], ["Nothing matches."])
        # By type, then by name: not the index's order.
        self.assertEqual(out["opened"], [True, [
            ["LABEL", "checkbox", True, "Mia", "People"],
            ["LABEL", "checkbox", True, "Mill", "Community pins · Other"],
            ["LABEL", "checkbox", True, "Well", "Community pins · Guild"],
            ["LABEL", "checkbox", True, "Zen", "Community pins · Guild"],
            ["LABEL", "checkbox", True, "Meadow", "Community zones · Guild"],
        ], "5 of 5 chosen", ["Mia", "Mill", "Well", "Zen", "Meadow"], "UL", ""])
        # Opening it sends nothing; the address holds the ticked community
        # (one rewrite) and nothing of the dialog.
        self.assertEqual(out["quiet"], [0, 1])
        self.assertEqual(out["calls"], [])

    def test_two_unticked_and_show_chosen_hides_both_everywhere(self):
        out = self.run_advanced("""
          const counts = () => box.querySelectorAll("[data-count]").map((n) => n.textContent);
          out.before = [names("index"), rowsDrawn(), part("shown").textContent, counts()];
          clickMap(52.2297, 21.0122); press("click-centre");
          part("nearby-radius").value = "25"; press("nearby-go");
          out.near = [names("nearby-results"), part("nearby-note").textContent];
          press("nearby-clear"); tab("index").fire("click");
          const rewritten = replaced.length, address = location.href;
          press("advanced-open");
          set("Mill", false); set("Meadow", false);
          out.picking = [count(), chosenNow(), names("index"), rowsDrawn(), button()];
          press("advanced-apply");
          out.applied = [shown("advanced-dialog"), names("index"), rowsDrawn(), drawn("rows"),
                         part("shown").textContent, button(), counts()];
          out.state = Object.keys(mounted.state.filter.hidden).sort();
          tab("nearby").fire("click"); press("nearby-go");
          out.nearAfter = [names("nearby-results"), part("nearby-note").textContent,
                           names("index")];
          press("nearby-clear"); tab("index").fire("click");
          // Reopened: it remembers what is hidden, and one may come back.
          press("advanced-open");
          out.reopened = [chosenNow(), count()];
          set("Mill", true); press("advanced-apply");
          out.one = [names("index"), button(), rowsDrawn()];
          out.quiet = [location.href === address, replaced.slice(rewritten)];
        """)
        five = ["1", "0", "0", "2", "1"]
        self.assertEqual(out["before"], [self.ALL, 4, "4 shown", five])
        self.assertEqual(out["near"], [["Well", "Mill", "Meadow"], "3 within 25 km"])
        self.assertEqual(out["picking"], ["2 of 4 chosen", ["Mia", "Well"], self.ALL, 4,
                                          "Advanced"], "unticking changes nothing by itself")
        self.assertEqual(out["applied"], [False, ["Mia", "Well"], 2, ["circle", "marker"],
                                          "2 shown", "Advanced · 2 hidden", five],
                         "the header counts what the page was handed; the index what is shown")
        self.assertEqual(out["state"], sorted([f"pin:{self.mill.uid}",
                                               f"zone:{self.meadow.uid}"]))
        self.assertEqual(out["nearAfter"], [["Well"], "1 within 25 km", ["Well"]])
        self.assertEqual(out["reopened"], [["Mia", "Well"], "2 of 4 chosen"])
        self.assertEqual(out["one"], [["Mia", "Mill", "Well"], "Advanced · 1 hidden", 3])
        # The tabs wrote the address (?tool=); the filter wrote nothing.
        self.assertTrue(out["quiet"][0])
        self.assertEqual({url.split("?")[-1] for url in out["quiet"][1] if "?" in url},
                         {"tool=nearby"})
        self.assertFalse([url for url in out["replaced"] if "hidden" in url or "advanced" in url])
        self.assertEqual((out["calls"], out["assigned"], out["touched"]), ([], [], []))

    def test_cancel_escape_and_the_backdrop_discard_what_was_changed(self):
        out = self.run_advanced("""
          press("advanced-open"); set("Well", false); set("Mia", false);
          press("advanced-cancel");
          out.cancelled = [shown("advanced-dialog"), names("index"), button(), rowsDrawn(),
                           Object.keys(mounted.state.filter.hidden)];
          press("advanced-open");
          out.again = [chosenNow(), count()];
          set("Mill", false); press("advanced-none");
          dismiss("advanced-dialog");
          out.dismissed = [shown("advanced-dialog"), names("index"), button()];
          // A change of a box after the dialog is left changes nothing.
          set("Well", false); press("advanced-all"); press("advanced-apply");
          out.closed = [names("index"), button(), shown("advanced-dialog")];
          press("advanced-open"); set("Well", false); press("advanced-apply");
          press("advanced-open"); set("Well", true); set("Mill", false);
          press("advanced-cancel");
          out.kept = [names("index"), button()];
        """)
        self.assertEqual(out["cancelled"], [False, self.ALL, "Advanced", 4, []])
        self.assertEqual(out["again"], [self.LISTED, "4 of 4 chosen"])
        self.assertEqual(out["dismissed"], [False, self.ALL, "Advanced"])
        self.assertEqual(out["closed"], [self.ALL, "Advanced", False])
        self.assertEqual(out["kept"], [["Mia", "Mill", "Meadow"], "Advanced · 1 hidden"],
                         "Cancel goes back to what was applied before")
        self.assertEqual((out["calls"], out["replaced"], out["touched"]), ([], [], []))

    def test_the_field_narrows_the_lines_and_all_and_none_act_on_the_listed_ones(self):
        out = self.run_advanced("""
          press("advanced-open");
          type("  M ");
          out.narrowed = [said(listed()), chosenNow(), count()];
          press("advanced-none");
          out.none = [chosenNow(), count(), said(listed())];
          type("mi");
          out.mi = said(listed());
          press("advanced-all");
          out.all = [chosenNow(), count()];
          type("no such name");
          out.nothing = [said(listed()), part("advanced-list").elements()
                           .filter((li) => !li.classList.contains("hidden"))
                           .map((li) => li.textContent)];
          press("advanced-none"); press("advanced-all");
          out.untouched = chosenNow();
          type("");
          out.cleared = [said(listed()), chosenNow(), names("index")];
          press("advanced-apply");
          out.applied = [names("index"), button()];
          // The field is the dialog's: it is empty at the next opening, and
          // it never was the index's text filter.
          type("we"); press("advanced-cancel"); press("advanced-open");
          out.reopened = [part("advanced-filter").value, said(listed()), part("filter").value,
                          mounted.state.filter.text];
        """)
        self.assertEqual(out["narrowed"], [["Mia", "Mill", "Meadow"], self.LISTED,
                                           "4 of 4 chosen"], "it changes what is listed only")
        self.assertEqual(out["none"], [["Well"], "1 of 4 chosen", ["Mia", "Mill", "Meadow"]])
        self.assertEqual(out["mi"], ["Mia", "Mill"])
        self.assertEqual(out["all"], [["Mia", "Mill", "Well"], "3 of 4 chosen"])
        self.assertEqual(out["nothing"], [[], ["Nothing matches."]])
        self.assertEqual(out["untouched"], ["Mia", "Mill", "Well"])
        self.assertEqual(out["cleared"], [self.LISTED, ["Mia", "Mill", "Well"], self.ALL])
        self.assertEqual(out["applied"], [["Mia", "Mill", "Well"], "Advanced · 1 hidden"])
        self.assertEqual(out["reopened"], ["", self.LISTED, "", ""])
        self.assertEqual((out["calls"], out["replaced"], out["touched"]), ([], [], []))

    def test_it_combines_with_a_kind_a_community_and_the_text_and_reset_clears_it(self):
        out = self.run_advanced("""
          const kind = (name, on) => {
            const check = box.querySelector('[data-geo-kind="' + name + '"]');
            check.checked = on; check.fire("change");
          };
          press("advanced-open"); set("Well", false); press("advanced-apply");
          out.hidden = names("index");
          kind("person", false);
          out.kind = [names("index"), part("shown").textContent];
          tick(X.guild, true);
          out.community = [names("index"), rowsDrawn()];
          tick(X.other, true);
          out.two = names("index");
          part("filter").value = "mea"; part("filter").fire("input");
          out.text = names("index");
          part("filter").value = "well"; part("filter").fire("input");
          out.hiddenByAll = names("index");
          // The dialog still lists everything, with what it hides unticked.
          press("advanced-open");
          out.dialog = [said(listed()), chosenNow()];
          press("advanced-cancel");
          press("reset");
          out.reset = [names("index"), button(), rowsDrawn(), part("shown").textContent,
                       Object.keys(mounted.state.filter.hidden), location.href];
          press("advanced-open");
          out.after = [chosenNow(), count()];
        """)
        self.assertEqual(out["hidden"], ["Mia", "Mill", "Meadow"])
        self.assertEqual(out["kind"], [["Mill", "Meadow"], "2 shown"])
        self.assertEqual(out["community"], [["Meadow"], 1])
        self.assertEqual(out["two"], ["Mill", "Meadow"])
        self.assertEqual(out["text"], ["Meadow"])
        self.assertEqual(out["hiddenByAll"], ["Nothing matches."])
        self.assertEqual(out["dialog"], [self.LISTED, ["Mia", "Mill", "Meadow"]])
        self.assertEqual(out["reset"], [self.ALL, "Advanced", 4, "4 shown", [],
                                        SITE + self.page_url])
        self.assertEqual(out["after"], [self.LISTED, "4 of 4 chosen"])
        self.assertEqual((out["calls"], out["assigned"], out["touched"]), ([], [], []))
        # The address held the ticked communities and never the hidden rows.
        self.assertFalse([url for url in out["replaced"] if "pin" in url or "hidden" in url])

    def test_the_opened_row_s_details_close_when_it_is_hidden(self):
        out = self.run_advanced("""
          const mia = DATA.config.rows.find((row) => row.kind === "person");
          rowButton(mia.id).fire("click");
          out.opened = [hidden("details"), mounted.state.open === mia.id,
                        part("details").textContent.includes("Mia")];
          press("advanced-open"); set("Well", false); press("advanced-apply");
          out.other = [hidden("details"), mounted.state.open === mia.id];
          press("advanced-open"); set("Mia", false); press("advanced-apply");
          out.closed = [hidden("details"), mounted.state.open, part("details").textContent,
                        names("index")];
        """)
        self.assertEqual(out["opened"], [False, True, True])
        self.assertEqual(out["other"], [False, True], "another row hidden leaves it open")
        self.assertEqual(out["closed"], [True, None, "", ["Mill", "Meadow"]])
        self.assertEqual(out["calls"], [], "a person's details are the page's own")

    def test_a_name_with_markup_is_text_and_a_hidden_row_is_marked(self):
        name = "<img src=x onerror=alert(1)>"
        marked = self.pin(name=name, note="<b>x</b>")
        post(client_of(self.head_user), self.url("pin_hide", marked), {})
        out = self.run_advanced("""
          press("advanced-open");
          const line = lines().find((li) => parts(li).name.startsWith("<img"));
          out.line = [parts(line).name, parts(line).under, line.all().map((n) => n.tagName),
                      part("advanced-list").querySelectorAll("img").length
                      + part("advanced-list").querySelectorAll("b").length];
          out.index = names("index").filter((n) => n.startsWith("<img"));
          set(parts(line).name, false); press("advanced-apply");
          out.gone = [names("index").filter((n) => n.startsWith("<img")), button()];
        """)
        self.assertEqual(out["line"], [name, "Community pins · Guild · Hidden by a moderator",
                                       ["LABEL", "INPUT", "SPAN", "SPAN", "SPAN"], 0])
        self.assertEqual(out["index"], [name])
        self.assertEqual(out["gone"], [[], "Advanced · 1 hidden"])
        self.assertEqual(out["calls"], [])


class LocationsScriptSourceTests(SimpleTestCase):
    """What the script may not hold, read as text (no node needed)."""

    def test_the_address_is_rewritten_in_place_and_nothing_is_kept_in_storage(self):
        source = Path(finders.find("geography/locations.js")).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith(("*", "/*", "//")))
        self.assertEqual(code.count("replaceState("), 1)
        for word in ("pushState", "localStorage", "sessionStorage", "indexedDB",
                     "document.cookie", "setInterval", "sendBeacon", "XMLHttpRequest("):
            self.assertNotIn(word, code, word)
        # The form that stood in a row's details is gone: its words are the
        # dialog's now.
        self.assertNotIn("data-geo-edit", source)
        self.assertEqual(source.count(".innerHTML"), 1)

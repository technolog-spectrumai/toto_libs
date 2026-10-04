"""The bell in the app bar (2026-10-04): an icon and a count, no words; on
the desktop bar and in the phone menu; nothing written into a script.

    manage.py test toto.notify.tests_bell
"""

import re
import shutil
import subprocess
import json
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from toto import notify
from toto.core.models import Platform
from toto.notify.testing import MEMORY_LAYER

User = get_user_model()
BELL = re.compile(r'<div class="[^"]*" data-notify-bell (.*?)>\s*(.*?)</button>', re.S)
_NODE = shutil.which("node")


@override_settings(CHANNEL_LAYERS=MEMORY_LAYER)
class BellTests(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.ada = User.objects.create_user("ada", password="pw")

    def header(self, user=None, language="en"):
        request = RequestFactory().get("/somewhere/")
        request.user = user or self.ada
        with translation.override(language):
            return render_to_string("oya/header.html", {"header_nav_items": []}, request=request)

    def test_one_bell_on_each_bar_an_icon_and_a_count_and_no_words(self):
        notify.send(self.ada, "vault.uploaded", title="a.txt", bucket="Work")
        notify.send(self.ada, "vault.trashed", title="b.txt", bucket="Work")
        html = self.header()
        bells = BELL.findall(html)
        self.assertEqual(len(bells), 2)
        self.assertEqual(sorted(re.findall(r'data-variant="(\w+)"', html)), ["bar", "mobile"])
        for _attributes, button in bells:
            seen = " ".join(re.sub(r"<[^>]+>", " ", button).split())
            self.assertEqual(seen, "2")
            self.assertIn("fa-bell", button)
            self.assertIn('title="Notifications"', button)
            self.assertIn('aria-label="Notifications"', button)
        self.assertIn('class="relative hidden sm:block" data-notify-bell data-variant="bar"', html)

    def test_no_count_is_shown_at_zero_and_the_name_is_translated(self):
        html = self.header(language="pl")
        self.assertEqual(html.count("data-notify-count"), 2)
        self.assertEqual(len(re.findall(r"data-notify-count[^>]* hidden>", html)), 2)
        with translation.override("pl"):
            name = translation.gettext("Notifications")
        self.assertEqual(html.count(f'aria-label="{name}"'), 2)

    def test_the_addresses_are_data_and_the_script_is_a_file(self):
        html = self.header()
        for name in ("notify:api_list", "notify:api_read", "notify:api_read_all"):
            self.assertIn(f'="{reverse(name)}"', html)
        self.assertEqual(html.count('data-ws-path="/ws/live/"'), 2)
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S)
        mine = [(attrs, body) for attrs, body in scripts if "notify" in attrs]
        self.assertEqual(len(mine), 1)
        self.assertIn("defer", mine[0][0])
        self.assertEqual(mine[0][1].strip(), "")
        self.assertIsNotNone(finders.find("notify/live.js"))

    @override_settings(CHANNEL_LAYERS={})
    def test_a_host_with_no_channel_layer_opens_no_socket(self):
        self.assertEqual(self.header().count('data-ws-path=""'), 2)

    def test_a_visitor_has_no_bell(self):
        self.assertNotIn("data-notify-bell", self.header(user=AnonymousUser()))

_HARNESS = r"""
const {createLive, createBell, BACKOFF, POLL_MS} = require(process.argv[1]);
const out = {};
const timers = [];
const listeners = {};
const sockets = [];
class Sock { constructor(url) { this.url = url; this.sent = []; sockets.push(this); }
  send(text) { this.sent.push(JSON.parse(text)); } close() {} }
const events = [];
const env = {WebSocket: Sock, location: {protocol: "https:", host: "portal.example.org"},
  path: "/ws/live/", setTimeout: (fn, ms) => { timers.push({fn, ms}); },
  dispatch: (name, detail) => { events.push([name, detail]); (listeners[name] || []).forEach(f => f({detail})); }};
const live = createLive(env);
live.watch(7);
live.connect();
out.url = sockets[0].url;
out.sentBeforeOpen = sockets[0].sent.length;
sockets[0].onopen();
out.watchAfterOpen = sockets[0].sent.slice();
live.watch(7); live.watch("x"); live.watch(9);
out.watches = sockets[0].sent.map(m => m.directory);
sockets[0].onmessage({data: JSON.stringify({type: "notification", text: "<b>x</b>"})});
sockets[0].onmessage({data: JSON.stringify({type: "folder", kind: "added", file: 3, directory: 7, name: "n"})});
sockets[0].onmessage({data: "not json"});
out.events = events.slice();
sockets[0].onclose();
out.openAfterClose = live.isOpen();
const delays = [];
for (let i = 0; i < 8; i++) { const t = timers.pop(); delays.push(t.ms); t.fn(); sockets[sockets.length - 1].onclose(); }
out.delays = delays;
timers.pop().fn(); sockets[sockets.length - 1].onopen();
out.rewatched = sockets[sockets.length - 1].sent.map(m => m.directory);
out.backoff = BACKOFF; out.poll = POLL_MS;

// The bell, on a page faked just far enough.
class El { constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.attrs = {};
    this.hidden = false; this.className = ""; this._text = ""; this.handlers = {}; }
  set textContent(v) { this._text = String(v); } get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  set innerHTML(v) { throw new Error("innerHTML is never used"); }
  appendChild(c) { this.children.push(c); c.parent = this; return c; }
  removeChild(c) { this.children = this.children.filter(x => x !== c); }
  get firstChild() { return this.children[0] || null; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); }
  contains() { return false; }
  querySelector(sel) { return this.parts[sel] || null; } }
const mk = () => { const root = new El("div");
  root.dataset = {listUrl: "/notify/api/", readUrl: "/notify/api/read/", readAllUrl: "/notify/api/read-all/"};
  const token = new El("input"); token.value = "tok";
  root.parts = {"input[name=csrfmiddlewaretoken]": token, "[data-notify-count]": new El("span"),
                "[data-notify-list]": new El("ul"), "[data-notify-empty]": new El("p"),
                "[data-notify-read-all]": new El("button"), "[data-notify-panel]": new El("div"),
                "[data-notify-toggle]": new El("button")};
  root.parts["[data-notify-panel]"].hidden = true; return root; };
const roots = [mk(), mk()];
const doc = new El("document"); doc.body = new El("body"); doc.createElement = (tag) => new El(tag);
const calls = [];
let answer = {unread: 1, items: [{id: 1, created: "a", text: "<img src=x onerror=alert(1)>", actor: "Ada", when: "now", link: "/vault/", read: false, icon: "fa-solid fa-upload"}]};
const intervals = [], later = [], gone = [];
let open = false;
const bell = createBell({roots, document: doc, location: {assign: (u) => gone.push(u)},
  live: {isOpen: () => open}, visible: () => true,
  fetch: (url, options) => { calls.push([url, (options || {}).method || "GET", (options || {}).headers || {}, (options || {}).body]);
    return Promise.resolve({ok: true, json: () => Promise.resolve(JSON.parse(JSON.stringify(answer)))}); },
  setTimeout: (fn, ms) => { later.push({fn, ms}); return later.length; },
  setInterval: (fn, ms) => { intervals.push({fn, ms}); }});
const tick = () => new Promise(r => setImmediate(r));
(async () => {
  bell.start();
  later.shift().fn(); await tick(); await tick();
  out.firstList = calls.map(c => c[0] + " " + c[1]);
  out.count = roots.map(r => r.parts["[data-notify-count]"].textContent + (r.parts["[data-notify-count]"].hidden ? " hidden" : ""));
  out.rowText = roots[0].parts["[data-notify-list]"].textContent;
  out.toastsAtFirst = doc.body.children.length ? doc.body.children[0].children.length : 0;
  // No socket: the minute's question, and what is new is toasted.
  out.pollMs = intervals[0].ms;
  answer = {unread: 2, items: [{id: 2, created: "b", text: "second", actor: "", when: "now", link: "//evil.example.com/", read: false}].concat(answer.items)};
  intervals[0].fn(); await tick(); await tick();
  out.polled = calls.length;
  out.toasts = doc.body.children[0].children.map(c => c.textContent);
  open = true; intervals[0].fn(); await tick();
  out.notPolledWhenOpen = calls.length;
  // The socket's poke: one question after a burst.
  doc.handlers["toto:notification"].forEach(f => { f({}); f({}); f({}); });
  out.settles = later.filter(t => t.ms === 400).length;
  // A click marks read with the token, then follows only a path of this platform.
  const row = roots[0].parts["[data-notify-list]"].children[1].children[0];
  row.handlers.click[0](); await tick(); await tick();
  const post = calls.filter(c => c[1] === "POST")[0];
  out.post = [post[0], post[2]["X-CSRFToken"], post[3]];
  const evil = roots[0].parts["[data-notify-list]"].children[0].children[0];
  evil.handlers.click[0](); await tick(); await tick();
  out.gone = gone;
  console.log(JSON.stringify(out));
})();
"""


@skipUnless(_NODE, "node is not installed")
class LiveScriptTests(SimpleTestCase):
    """notify/live.js as it ships, run in node against a faked page."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        script = finders.find("notify/live.js")
        done = subprocess.run([_NODE, "-e", _HARNESS, script], capture_output=True, text=True,
                              timeout=60)
        if done.returncode != 0:
            raise AssertionError(f"node failed: {done.stderr}")
        cls.out = json.loads(done.stdout.strip().splitlines()[-1])

    def test_one_socket_to_the_platforms_own_address(self):
        self.assertEqual(self.out["url"], "wss://portal.example.org/ws/live/")

    def test_a_watch_asked_early_is_sent_once_the_socket_opens_and_never_twice(self):
        self.assertEqual(self.out["sentBeforeOpen"], 0)
        self.assertEqual(self.out["watchAfterOpen"], [{"type": "watch", "directory": 7}])
        self.assertEqual(self.out["watches"], [7, 9])

    def test_what_the_server_says_becomes_events_with_ids_only(self):
        self.assertEqual(self.out["events"], [
            ["toto:live-open", {}], ["toto:notification", {}],
            ["toto:folder", {"kind": "added", "file": 3, "directory": 7}]])

    def test_it_reconnects_waiting_longer_each_time_and_watches_again(self):
        self.assertFalse(self.out["openAfterClose"])
        self.assertEqual(self.out["delays"], [1000, 2000, 5000, 10000, 30000, 60000, 60000, 60000])
        self.assertEqual(self.out["rewatched"], [7, 9])

    def test_the_bell_draws_its_doors_answer_as_text(self):
        self.assertEqual(self.out["firstList"], ["/notify/api/ GET"])
        self.assertEqual(self.out["count"], ["1", "1"])
        self.assertIn("<img src=x onerror=alert(1)>", self.out["rowText"])
        self.assertEqual(self.out["toastsAtFirst"], 0)

    def test_without_a_socket_it_asks_every_minute_and_toasts_what_is_new(self):
        self.assertEqual(self.out["pollMs"], 60000)
        self.assertEqual(self.out["polled"], 2)
        self.assertEqual(self.out["toasts"], ["second"])
        self.assertEqual(self.out["notPolledWhenOpen"], 2)
        self.assertEqual(self.out["settles"], 1)

    def test_a_click_marks_read_with_the_token_and_follows_only_this_platforms_paths(self):
        self.assertEqual(self.out["post"], ["/notify/api/read/", "tok", "id=1"])
        self.assertEqual(self.out["gone"], ["/vault/"])

    def test_the_script_writes_no_markup(self):
        source = Path(finders.find("notify/live.js")).read_text(encoding="utf-8")
        for word in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(word, source)

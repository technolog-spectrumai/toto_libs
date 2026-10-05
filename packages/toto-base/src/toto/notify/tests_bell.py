"""The bell in the app bar (2026-10-04): an icon and a count, no words; on
the desktop bar and in the phone menu; nothing written into a script. And
when it asks (2026-10-06): at a page load, on a return to the tab, after a
read — never on a timer, and never a request that is kept open.

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
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import translation

from toto import notify
from toto.core.models import Platform

User = get_user_model()
BELL = re.compile(r'<div class="[^"]*" data-notify-bell (.*?)>\s*(.*?)</button>', re.S)
_NODE = shutil.which("node")


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
        for gone in ("data-wait-url", "api/wait", "data-ws-path", "ws/live", "data-msg-signed"):
            self.assertNotIn(gone, html)
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S)
        mine = [(attrs, body) for attrs, body in scripts if "notify" in attrs]
        self.assertEqual(len(mine), 1)
        self.assertIn("defer", mine[0][0])
        self.assertEqual(mine[0][1].strip(), "")
        self.assertIn("notify/bell.js", mine[0][0])
        self.assertIsNotNone(finders.find("notify/bell.js"))
        self.assertIsNone(finders.find("notify/live.js"))

    def test_a_visitor_has_no_bell(self):
        self.assertNotIn("data-notify-bell", self.header(user=AnonymousUser()))

_HARNESS = r"""
const {createBell, MIN_GAP_MS, MAX_TOASTS} = require(process.argv[1]);
const out = {};
const tick = () => new Promise(r => setImmediate(r));
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };

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
const row = (id, text, more) => Object.assign({id, created: "c" + id, text, actor: "", when: "now", link: "", read: false}, more || {});

(async () => {
  const roots = [mk(), mk()];
  const doc = new El("document"); doc.body = new El("body"); doc.createElement = (tag) => new El(tag);
  const calls = [], later = [], gone = [];
  let clock = 1000, shown = true;
  let listed = {unread: 1, items: [row(1, "<img src=x onerror=alert(1)>", {actor: "Ada", link: "/vault/", icon: "fa-solid fa-upload"})]};
  // No setInterval, no clearTimeout, no AbortController is handed over: a
  // script that reached for one would throw here.
  const bell = createBell({roots, document: doc, location: {assign: (u) => gone.push(u)},
    now: () => clock, visible: () => shown,
    fetch: (url, options) => { calls.push([url, (options || {}).method || "GET", (options || {}).headers || {}, (options || {}).body]);
      return Promise.resolve({ok: true, json: () => Promise.resolve(JSON.parse(JSON.stringify(listed)))}); },
    setTimeout: (fn, ms) => { later.push({fn, ms}); return later.length; }});
  const gets = () => calls.filter(c => c[1] === "GET").length;
  const toasts = () => doc.body.children.length ? doc.body.children[0].children.map(c => c.textContent) : [];

  // The page has loaded: one question, at once, and no timer set.
  bell.start();
  out.askedAtStart = calls.map(c => c[0] + " " + c[1]);
  await settle();
  out.timersAfterStart = later.length;
  out.count = roots.map(r => r.parts["[data-notify-count]"].textContent + (r.parts["[data-notify-count]"].hidden ? " hidden" : ""));
  out.rowText = roots[0].parts["[data-notify-list]"].textContent;
  out.toastsAtFirst = toasts().length;
  out.bellListens = Object.keys(doc.handlers).sort();

  // An idle page: however long it stays open, nothing more is asked.
  clock += 3600 * 1000; await settle();
  out.askedWhileIdle = gets();

  // Back to the tab: asked once; again within the gap: not asked.
  listed = {unread: 2, items: [row(2, "second", {link: "//evil.example.com/"})].concat(listed.items)};
  out.cameBack = bell.comeBack(); await settle();
  out.askedOnReturn = gets();
  out.toasts = toasts();
  out.toastTimers = later.map(t => t.ms);
  clock += MIN_GAP_MS - 1;
  out.tooSoon = bell.comeBack(); await settle();
  out.askedTooSoon = gets();
  // A hidden tab asks nothing, however long ago it asked.
  clock += 10 * MIN_GAP_MS; shown = false;
  out.hidden = bell.comeBack(); await settle();
  out.askedHidden = gets();
  // Looked at again after the gap, and nothing arrived: asked, no toast.
  shown = true;
  out.later = bell.comeBack(); await settle();
  out.askedLater = gets();
  out.toastsUnchanged = toasts().length;
  // Many arrived while away: at most MAX_TOASTS more.
  clock += MIN_GAP_MS;
  listed = {unread: 7, items: [7, 6, 5, 4, 3].map(n => row(n, "n" + n)).concat(listed.items)};
  bell.comeBack(); await settle();
  out.manyToasts = toasts().length - out.toastsUnchanged;
  out.maxToasts = MAX_TOASTS; out.gap = MIN_GAP_MS;
  // The count fell (read in another tab): no toast.
  clock += MIN_GAP_MS;
  listed = {unread: 1, items: [row(8, "eighth")].concat(listed.items.map(i => Object.assign({}, i, {read: true})))};
  const before = toasts().length;
  bell.comeBack(); await settle();
  out.toastsWhenCountFell = toasts().length - before;

  // Opening the panel asks (the person did), whatever the gap.
  const asked = gets();
  roots[0].parts["[data-notify-toggle]"].handlers.click[0]({stopPropagation() {}}); await settle();
  out.askedOnOpen = gets() - asked;
  // A click marks read with the token, then follows only a path of this platform.
  const list = roots[0].parts["[data-notify-list]"];
  const find = (text) => list.children.filter(li => li.textContent.indexOf(text) !== -1)[0].children[0];
  find("eighth").handlers.click[0](); await settle();
  const post = calls.filter(c => c[1] === "POST")[0];
  out.post = [post[0], post[2]["X-CSRFToken"], post[3]];
  out.askedAfterRead = calls[calls.length - 1].slice(0, 2);
  listed.items.push(row(9, "ninth", {link: "/vault/"}), row(10, "tenth", {link: "//evil.example.com/"}));
  await bell.refresh(false); await settle();
  find("ninth").handlers.click[0](); await settle();
  find("tenth").handlers.click[0](); await settle();
  out.gone = gone;
  // Mark all as read: one post, then the list once more.
  const n = calls.length;
  roots[0].parts["[data-notify-read-all]"].handlers.click[0](); await settle();
  out.readAll = calls.slice(n).map(c => c[0] + " " + c[1]);
  out.timerKinds = Array.from(new Set(later.map(t => t.ms)));
  console.log(JSON.stringify(out));
})();
"""


@skipUnless(_NODE, "node is not installed")
class BellScriptTests(SimpleTestCase):
    """notify/bell.js as it ships, run in node against a faked page."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        script = finders.find("notify/bell.js")
        done = subprocess.run([_NODE, "-e", _HARNESS, script], capture_output=True, text=True,
                              timeout=60)
        if done.returncode != 0:
            raise AssertionError(f"node failed: {done.stderr}")
        cls.out = json.loads(done.stdout.strip().splitlines()[-1])

    def test_it_asks_once_when_the_page_has_loaded_and_sets_no_timer(self):
        self.assertEqual(self.out["askedAtStart"], ["/notify/api/ GET"])
        self.assertEqual(self.out["timersAfterStart"], 0)
        self.assertEqual(self.out["askedWhileIdle"], 1)

    def test_the_bell_draws_its_doors_answer_as_text(self):
        self.assertEqual(self.out["count"], ["1", "1"])
        self.assertIn("<img src=x onerror=alert(1)>", self.out["rowText"])
        self.assertEqual(self.out["toastsAtFirst"], 0)

    def test_a_return_to_the_tab_asks_again_but_not_within_thirty_seconds(self):
        self.assertEqual(self.out["gap"], 30000)
        self.assertTrue(self.out["cameBack"])
        self.assertEqual(self.out["askedOnReturn"], 2)
        self.assertFalse(self.out["tooSoon"])
        self.assertEqual(self.out["askedTooSoon"], 2)
        self.assertTrue(self.out["later"])
        self.assertEqual(self.out["askedLater"], 3)

    def test_a_hidden_tab_asks_nothing(self):
        self.assertFalse(self.out["hidden"])
        self.assertEqual(self.out["askedHidden"], 2)

    def test_a_toast_only_when_the_count_grew_between_two_questions(self):
        self.assertEqual(self.out["toasts"], ["second"])
        self.assertEqual(self.out["toastsUnchanged"], 1)
        self.assertEqual((self.out["manyToasts"], self.out["maxToasts"]), (3, 3))
        self.assertEqual(self.out["toastsWhenCountFell"], 0)

    def test_the_only_timer_takes_a_toast_away(self):
        self.assertEqual(self.out["toastTimers"], [6000])
        self.assertEqual(self.out["timerKinds"], [6000])

    def test_it_listens_for_nothing_the_server_would_have_to_say(self):
        self.assertEqual(self.out["bellListens"], ["click", "keydown"])

    def test_opening_the_panel_and_reading_ask_again(self):
        self.assertEqual(self.out["askedOnOpen"], 1)
        self.assertEqual(self.out["askedAfterRead"], ["/notify/api/", "GET"])
        self.assertEqual(self.out["readAll"], ["/notify/api/read-all/ POST", "/notify/api/ GET"])

    def test_a_click_marks_read_with_the_token_and_follows_only_this_platforms_paths(self):
        self.assertEqual(self.out["post"], ["/notify/api/read/", "tok", "id=8"])
        self.assertEqual(self.out["gone"], ["/vault/"])


class BellSourceTests(SimpleTestCase):
    """The script as text: what it must never hold (it runs without node)."""

    def source(self):
        return Path(finders.find("notify/bell.js")).read_text(encoding="utf-8")

    def code(self):
        """The script without its comments."""
        source = re.sub(r"/\*.*?\*/", "", self.source(), flags=re.S)
        return "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("//"))

    def test_the_script_writes_no_markup(self):
        for word in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(word, self.source())

    def test_the_script_opens_no_socket(self):
        for word in ("new WebSocket", "env.WebSocket", "wss://", "ws://", "toto:presence",
                     "signed in", "EventSource"):
            self.assertNotIn(word, self.source())

    def test_nothing_is_asked_on_a_timer_and_no_request_is_held(self):
        code = self.code()
        for word in ("setInterval", "requestAnimationFrame", "AbortController", "cursor",
                     "waitUrl", "api/wait", "BACKOFF", "POLL_MS", "totoLive", "toto:folder",
                     "toto:notification", "keepalive", "sendBeacon"):
            with self.subTest(word=word):
                self.assertNotIn(word, code)
        # One timer in the whole script, and it is the toast's.
        self.assertEqual(re.findall(r"env\.setTimeout\(([^,]+),\s*(\w+)\)", code),
                         [("drop", "TOAST_MS")])
        self.assertEqual(code.count("setTimeout("), 2)      # that call, and boot's env
        # Every question goes to one of the three doors the element names.
        self.assertEqual(sorted(set(re.findall(r'address\("(\w+)"\)', code))),
                         ["listUrl", "readAllUrl", "readUrl"])

    def test_it_asks_again_when_the_tab_is_looked_at(self):
        code = self.code()
        for word in ('"visibilitychange"', '"focus"', '"pageshow"', "MIN_GAP_MS = 30000"):
            self.assertIn(word, code)

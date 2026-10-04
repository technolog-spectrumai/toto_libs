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
        self.assertEqual(html.count(f'data-wait-url="{reverse("notify:api_wait")}"'), 2)
        for gone in ("data-ws-path", "ws/live", "data-msg-signed"):
            self.assertNotIn(gone, html)
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S)
        mine = [(attrs, body) for attrs, body in scripts if "notify" in attrs]
        self.assertEqual(len(mine), 1)
        self.assertIn("defer", mine[0][0])
        self.assertEqual(mine[0][1].strip(), "")
        self.assertIsNotNone(finders.find("notify/live.js"))

    def test_a_visitor_has_no_bell(self):
        self.assertNotIn("data-notify-bell", self.header(user=AnonymousUser()))

_HARNESS = r"""
const {createLive, createBell, BACKOFF, POLL_MS, MIN_GAP_MS} = require(process.argv[1]);
const out = {};
const tick = () => new Promise(r => setImmediate(r));
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };

// The long poll, against a door faked just far enough.
const timers = [];          // {fn, ms, cleared}
const asked = [];           // every request: {url, options, resolve, reject, aborted}
const events = [];
let clock = 0, shown = true;
class Abort { constructor() { this.signal = {owner: this}; this.aborted = false; }
  abort() { this.aborted = true; const call = asked.find(c => c.options.signal === this.signal);
            if (call) { call.aborted = true; call.reject(new Error("AbortError")); } } }
const env = {url: "/notify/api/wait/", AbortController: Abort, now: () => clock, visible: () => shown,
  setTimeout: (fn, ms) => { const t = {fn, ms, cleared: false}; timers.push(t); return t; },
  clearTimeout: (t) => { if (t) t.cleared = true; },
  fetch: (url, options) => new Promise((resolve, reject) => { asked.push({url, options, resolve, reject}); }),
  dispatch: (name, detail) => { events.push([name, detail]); }};
const answer = (call, data, status) => call.resolve({status: status || 200, ok: !status || status < 300,
                                                     json: () => Promise.resolve(data)});
const due = () => timers.filter(t => !t.cleared && !t.done);
const fire = () => { const t = due()[0]; t.done = true; t.fn(); return t.ms; };

(async () => {
  const live = createLive(env);
  live.watch(7);
  live.start(); live.start();
  out.firstUrl = asked[0].url;
  out.firstOptions = [asked[0].options.credentials, asked[0].options.headers.Accept, asked[0].options.method || "GET"];
  out.oneAtATime = asked.length;
  out.openBeforeAnswer = live.isOpen();
  // An answer at once: the cursor is kept, and the next question waits its second.
  clock = 20;
  answer(asked[0], {cursor: "c1", notifications: false, folders: [7], files: {"7": {"3": "aa"}}});
  await settle();
  out.openAfterAnswer = live.isOpen();
  out.gapAfterQuickAnswer = fire();
  out.secondUrl = asked[1].url;
  // A held request answered after 25 s: asked again without delay.
  clock += 25000;
  answer(asked[1], {cursor: "c2", notifications: true, folders: [], name: "<b>x</b>"});
  await settle();
  out.gapAfterHeldAnswer = fire();
  out.events = events.slice();
  // A folder opened while a request is held: that request is dropped (no
  // failure counted) and the next names both folders, each once.
  live.watch(7); live.watch("x"); live.watch(9);
  await settle();
  out.droppedForWatch = asked[2].aborted === true;
  out.reaskDelay = fire();
  out.thirdUrl = asked[3].url;
  // A hidden tab stops asking; looked at again, it asks with the cursor it had.
  shown = false; live.pause();
  await settle();
  out.droppedWhenHidden = asked[3].aborted === true;
  out.timersWhenHidden = due().length;
  live.start();
  out.askedWhileHidden = asked.length;
  shown = true; live.start();
  out.resumedUrl = asked[4].url;
  // Failures: waiting longer each time, and "closed" said once.
  const delays = [];
  let n = 4;
  for (let i = 0; i < 8; i++) { answer(asked[n], null, 500); await settle(); delays.push(fire()); n += 1; }
  out.delays = delays;
  out.closedEvents = events.filter(e => e[0] === "toto:live-closed").length;
  out.openAfterFailures = live.isOpen();
  // Sent home by the cap: it stays away as long as it was told.
  answer(asked[n], {cursor: "c3", notifications: false, folders: [], retry: 25}); await settle();
  out.retryDelay = fire(); n += 1;
  out.opened = events.filter(e => e[0] === "toto:live-open").length;
  // The session ended: it asks no more.
  answer(asked[n], {}, 401); await settle();
  out.timersAfter401 = due().length;
  live.start();
  out.askedAfter401 = asked.length - (n + 1);
  out.backoff = BACKOFF; out.poll = POLL_MS; out.gap = MIN_GAP_MS;

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
    root.dataset = {listUrl: "/notify/api/", readUrl: "/notify/api/read/", readAllUrl: "/notify/api/read-all/",
                    waitUrl: "/notify/api/wait/"};
    const token = new El("input"); token.value = "tok";
    root.parts = {"input[name=csrfmiddlewaretoken]": token, "[data-notify-count]": new El("span"),
                  "[data-notify-list]": new El("ul"), "[data-notify-empty]": new El("p"),
                  "[data-notify-read-all]": new El("button"), "[data-notify-panel]": new El("div"),
                  "[data-notify-toggle]": new El("button")};
    root.parts["[data-notify-panel]"].hidden = true; return root; };
  const roots = [mk(), mk()];
  const doc = new El("document"); doc.body = new El("body"); doc.createElement = (tag) => new El(tag);
  const calls = [];
  let listed = {unread: 1, items: [{id: 1, created: "a", text: "<img src=x onerror=alert(1)>", actor: "Ada", when: "now", link: "/vault/", read: false, icon: "fa-solid fa-upload"}]};
  const intervals = [], later = [], gone = [];
  let open = false;
  const bell = createBell({roots, document: doc, location: {assign: (u) => gone.push(u)},
    live: {isOpen: () => open}, visible: () => true,
    fetch: (url, options) => { calls.push([url, (options || {}).method || "GET", (options || {}).headers || {}, (options || {}).body]);
      return Promise.resolve({ok: true, json: () => Promise.resolve(JSON.parse(JSON.stringify(listed)))}); },
    setTimeout: (fn, ms) => { later.push({fn, ms}); return later.length; },
    setInterval: (fn, ms) => { intervals.push({fn, ms}); }});
  bell.start();
  later.shift().fn(); await tick(); await tick();
  out.firstList = calls.map(c => c[0] + " " + c[1]);
  out.count = roots.map(r => r.parts["[data-notify-count]"].textContent + (r.parts["[data-notify-count]"].hidden ? " hidden" : ""));
  out.rowText = roots[0].parts["[data-notify-list]"].textContent;
  out.toastsAtFirst = doc.body.children.length ? doc.body.children[0].children.length : 0;
  // The long poll is not working: the minute's question, and what is new is toasted.
  out.pollMs = intervals[0].ms;
  listed = {unread: 2, items: [{id: 2, created: "b", text: "second", actor: "", when: "now", link: "//evil.example.com/", read: false}].concat(listed.items)};
  intervals[0].fn(); await tick(); await tick();
  out.polled = calls.length;
  out.toasts = doc.body.children[0].children.map(c => c.textContent);
  open = true; intervals[0].fn(); await tick();
  out.notPolledWhenOpen = calls.length;
  // The long poll's word: one question after a burst.
  doc.handlers["toto:notification"].forEach(f => { f({}); f({}); f({}); });
  out.settles = later.filter(t => t.ms === 400).length;
  out.bellListens = Object.keys(doc.handlers).sort();
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

    def test_one_request_at_a_time_to_the_platforms_own_door(self):
        self.assertEqual(self.out["firstUrl"], "/notify/api/wait/?folders=7")
        self.assertEqual(self.out["firstOptions"], ["same-origin", "application/json", "GET"])
        self.assertEqual(self.out["oneAtATime"], 1)
        self.assertFalse(self.out["openBeforeAnswer"])
        self.assertTrue(self.out["openAfterAnswer"])

    def test_it_asks_again_with_the_cursor_it_was_given(self):
        self.assertEqual(self.out["secondUrl"], "/notify/api/wait/?cursor=c1&folders=7")
        # Never more than one question a second; none of that after a held one.
        self.assertEqual(self.out["gap"], 1000)
        self.assertEqual(self.out["gapAfterQuickAnswer"], 980)
        self.assertEqual(self.out["gapAfterHeldAnswer"], 0)

    def test_what_the_answer_says_becomes_events_with_ids_only(self):
        self.assertEqual(self.out["events"], [
            ["toto:live-open", {}],
            ["toto:folder", {"directory": 7, "files": {"3": "aa"}}],
            ["toto:notification", {}]])

    def test_a_folder_opened_later_is_asked_about_at_once_and_never_twice(self):
        self.assertTrue(self.out["droppedForWatch"])
        self.assertEqual(self.out["reaskDelay"], 100)
        self.assertEqual(self.out["thirdUrl"], "/notify/api/wait/?cursor=c2&folders=7,9")

    def test_a_hidden_tab_stops_asking_and_resumes_with_its_cursor(self):
        self.assertTrue(self.out["droppedWhenHidden"])
        self.assertEqual(self.out["timersWhenHidden"], 0)
        self.assertEqual(self.out["askedWhileHidden"], 4)
        self.assertEqual(self.out["resumedUrl"], "/notify/api/wait/?cursor=c2&folders=7,9")

    def test_a_failure_is_tried_again_waiting_longer_each_time(self):
        self.assertEqual(self.out["delays"], [1000, 2000, 5000, 10000, 30000, 60000, 60000, 60000])
        self.assertEqual(self.out["closedEvents"], 1)
        self.assertFalse(self.out["openAfterFailures"])
        self.assertEqual(self.out["opened"], 2)

    def test_it_stays_away_when_told_to_and_stops_when_the_session_ended(self):
        self.assertEqual(self.out["retryDelay"], 25000)
        self.assertEqual(self.out["timersAfter401"], 0)
        self.assertEqual(self.out["askedAfter401"], 0)

    def test_the_bell_draws_its_doors_answer_as_text(self):
        self.assertEqual(self.out["firstList"], ["/notify/api/ GET"])
        self.assertEqual(self.out["count"], ["1", "1"])
        self.assertIn("<img src=x onerror=alert(1)>", self.out["rowText"])
        self.assertEqual(self.out["toastsAtFirst"], 0)

    def test_without_the_long_poll_it_asks_every_minute_and_toasts_what_is_new(self):
        self.assertEqual(self.out["pollMs"], 60000)
        self.assertEqual(self.out["polled"], 2)
        self.assertEqual(self.out["toasts"], ["second"])
        self.assertEqual(self.out["notPolledWhenOpen"], 2)
        self.assertEqual(self.out["settles"], 1)

    def test_nobody_signing_in_or_out_is_listened_for(self):
        self.assertEqual(self.out["bellListens"], ["click", "keydown", "toto:notification"])

    def test_a_click_marks_read_with_the_token_and_follows_only_this_platforms_paths(self):
        self.assertEqual(self.out["post"], ["/notify/api/read/", "tok", "id=1"])
        self.assertEqual(self.out["gone"], ["/vault/"])

    def test_the_script_writes_no_markup(self):
        source = Path(finders.find("notify/live.js")).read_text(encoding="utf-8")
        for word in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(word, source)

    def test_the_script_opens_no_socket(self):
        source = Path(finders.find("notify/live.js")).read_text(encoding="utf-8")
        for word in ("new WebSocket", "env.WebSocket", "wss://", "ws://", "toto:presence",
                     "signed in"):
            self.assertNotIn(word, source)
        self.assertIn("visibilitychange", source)

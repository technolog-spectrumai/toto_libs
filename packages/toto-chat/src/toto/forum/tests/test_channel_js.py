"""The channel page's script, run in node (stage 70, 2026-10-07).

``HARNESS`` runs the half with no page in it: what one answer of the feed
does to what a page holds, the timer that asks (its interval, a hidden tab,
the return, ``more``, the back-off), the wait before an estimate is asked,
the rule that makes a link.

``PAGE`` runs ``mount()``, the half that draws, on the page the server
really answers a member with: its HTML is read into a tree here and rebuilt
in node as elements faked just far enough (attributes, classes, values,
listeners, children, simple selectors), with a clock the test moves and a
``fetch`` that records and answers from a queue. That is where a message
is added once, a poll's card is filled again, a removal becomes a quiet
line, a cleanup takes nodes out, a post is not drawn twice, the estimate is
shown and Send is switched off. No browser draws anything: how it looks is
the owner's by-hand note.

    manage.py test toto.forum.tests.test_channel_js
"""

import json
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from unittest import skipUnless

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

from toto.forum.testing import ForumCase, client_of

_NODE = shutil.which("node")

CONFIG = re.compile(
    r'<script id="forum-channel-config" type="application/json">(.*?)</script>', re.S)

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}


def tree_of(html: str) -> list:
    """The elements of ``html`` as nested dicts: ``{"tag", "attrs",
    "children"}``, a text as ``{"text"}``. Far enough for a page this app
    draws; no browser's repairs."""
    root = {"tag": "", "attrs": {}, "children": []}
    stack = [root]

    class Build(HTMLParser):
        def handle_starttag(self, tag, attrs):
            node = {"tag": tag, "attrs": {name: "" if value is None else value
                                          for name, value in attrs}, "children": []}
            stack[-1]["children"].append(node)
            if tag not in _VOID:
                stack.append(node)

        def handle_startendtag(self, tag, attrs):
            stack[-1]["children"].append(
                {"tag": tag, "attrs": {name: "" if value is None else value
                                       for name, value in attrs}, "children": []})

        def handle_endtag(self, tag):
            for index in range(len(stack) - 1, 0, -1):
                if stack[index]["tag"] == tag:
                    del stack[index:]
                    break

        def handle_data(self, data):
            if data.strip():
                stack[-1]["children"].append({"text": data})

    parser = Build(convert_charrefs=True)
    parser.feed(html)
    parser.close()
    return root["children"]


def element(tree, attr):
    """The first element that carries ``attr``, or None."""
    for node in tree:
        if "tag" not in node:
            continue
        if attr in node["attrs"]:
            return node
        found = element(node["children"], attr)
        if found is not None:
            return found
    return None


#: A clock the test moves: timers run only when ``advance`` reaches them.
CLOCK = r"""
const timers = [];
let clock = 0, timerIds = 0;
const setTimer = (fn, ms) => { const id = ++timerIds; timers.push({id: id, at: clock + ms, ms: ms, fn: fn}); return id; };
const clearTimer = (id) => { const at = timers.findIndex((t) => t.id === id); if (at >= 0) { timers.splice(at, 1); } };
const settle = async () => { for (let i = 0; i < 12; i++) { await new Promise((r) => setImmediate(r)); } };
async function advance(ms) {
  const until = clock + ms;
  for (;;) {
    const due = timers.filter((t) => t.at <= until).sort((a, b) => a.at - b.at)[0];
    if (!due) { break; }
    timers.splice(timers.indexOf(due), 1);
    clock = due.at;
    due.fn();
    await settle();
  }
  clock = until;
  await settle();
}
const waits = () => timers.map((t) => t.ms);
"""

HARNESS = CLOCK + r"""
const touched = [];
globalThis.fetch = () => { touched.push("fetch"); return Promise.reject(new Error("no")); };
const F = require(process.argv[1]);
(async () => {
  const out = {};
  __BODY__
  out.touched = touched;
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch((error) => { console.error(error); process.exit(1); });
"""


@skipUnless(_NODE, "node is not installed")
class ScriptCase(SimpleTestCase):
    def run_js(self, body):
        script = finders.find("forum/channel.js")
        done = subprocess.run([_NODE, "-e", HARNESS.replace("__BODY__", body), script],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])


class FeedStateTests(ScriptCase):
    """``applyFeed``: rows by id, replaced only by a newer ``seq``."""

    def test_a_new_row_is_added_and_the_same_row_again_changes_nothing(self):
        out = self.run_js("""
          const s = F.newState();
          const row = {id: "a", number: 1, seq: 1, text: "one", created_at: "2026-10-07T10:00:00Z"};
          out.first = F.applyFeed(s, {cursor: 1, messages: [row], polls: []}).messages.length;
          out.again = F.applyFeed(s, {cursor: 1, messages: [Object.assign({}, row)], polls: []}).messages.length;
          out.older = F.applyFeed(s, {messages: [Object.assign({}, row, {seq: 0, text: "stale"})]}).messages.length;
          out.text = s.messages.a.text;
          out.newer = F.applyFeed(s, {cursor: 4, messages: [Object.assign({}, row, {seq: 4, text: "new"})]}).messages.length;
          out.after = [s.messages.a.text, s.cursor, Object.keys(s.messages).length];
        """)
        self.assertEqual((out["first"], out["again"], out["older"]), (1, 0, 0))
        self.assertEqual(out["text"], "one")
        self.assertEqual(out["newer"], 1)
        self.assertEqual(out["after"], ["new", 4, 1])

    def test_what_the_members_own_door_answered_is_not_added_again_by_the_feed(self):
        out = self.run_js("""
          const s = F.newState();
          F.applyFeed(s, {cursor: 3, messages: [], polls: []});
          const mine = {id: "m", number: 4, seq: 4, text: "hello", created_at: "2026-10-07T10:00:00Z"};
          out.own = F.applyFeed(s, {messages: [mine]}).messages.length;
          out.cursorAfterOwn = s.cursor;          // an own answer moves no cursor
          const fed = F.applyFeed(s, {cursor: 4, messages: [Object.assign({}, mine)], polls: []});
          out.fed = fed.messages.length;
          out.cursor = s.cursor;
          out.held = Object.keys(s.messages);
        """)
        self.assertEqual((out["own"], out["cursorAfterOwn"]), (1, 3))
        self.assertEqual((out["fed"], out["cursor"], out["held"]), (0, 4, ["m"]))

    def test_a_tombstone_takes_the_row_and_a_late_answer_cannot_bring_it_back(self):
        out = self.run_js("""
          const s = F.newState();
          const row = {id: "a", number: 1, seq: 1, text: "one", created_at: "2026-10-07T10:00:00Z"};
          const poll = {id: "p", number: 2, seq: 2, title: "Q", created_at: "2026-10-07T10:00:00Z"};
          F.applyFeed(s, {cursor: 2, messages: [row], polls: [poll]});
          const gone = F.applyFeed(s, {cursor: 4, messages: [{id: "a", number: 1, seq: 3, removed: true}],
                                       polls: [{id: "p", number: 2, seq: 4, removed: true}]});
          out.gone = [gone.removedMessages, gone.removedPolls, gone.purgedMessages];
          out.late = F.applyFeed(s, {messages: [row], polls: [poll]});
          out.unknown = F.applyFeed(s, {cursor: 5, messages: [{id: "z", number: 9, seq: 5, removed: true}]})
            .removedMessages;
          out.held = [Object.keys(s.messages), Object.keys(s.polls)];
        """)
        self.assertEqual(out["gone"], [["a"], ["p"], []])
        self.assertEqual((out["late"]["messages"], out["late"]["polls"]), ([], []))
        self.assertEqual(out["unknown"], [])
        self.assertEqual(out["held"], [[], []])

    def test_a_cleanup_drops_everything_from_before_its_edge(self):
        out = self.run_js("""
          const s = F.newState();
          const at = (h) => "2026-10-07T" + h + ":00:00Z";
          F.applyFeed(s, {cursor: 4, messages: [
            {id: "old", number: 1, seq: 1, created_at: at("08")},
            {id: "new", number: 3, seq: 3, created_at: at("12")}],
            polls: [{id: "oldpoll", number: 2, seq: 2, created_at: at("09")},
                    {id: "newpoll", number: 4, seq: 4, created_at: at("13")}]});
          const changed = F.applyFeed(s, {cursor: 4, messages: [], polls: [], purged_before: at("10")});
          out.changed = [changed.removedMessages, changed.purgedMessages, changed.removedPolls,
                         changed.purgedPolls, changed.edge === Date.parse(at("10"))];
          out.held = [Object.keys(s.messages), Object.keys(s.polls), s.purgedBefore];
          out.same = F.applyFeed(s, {cursor: 4, messages: [], polls: [], purged_before: at("10")}).edge;
          out.bad = F.applyFeed(s, {purged_before: "not a date"}).edge;
        """)
        self.assertEqual(out["changed"], [["old"], ["old"], ["oldpoll"], ["oldpoll"], True])
        self.assertEqual(out["held"], [["new"], ["newpoll"], "2026-10-07T10:00:00Z"])
        self.assertEqual((out["same"], out["bad"]), (None, None))

    def test_older_history_moves_oldest_only(self):
        out = self.run_js("""
          const s = F.newState();
          F.applyFeed(s, {cursor: 60, messages: [{id: "b", number: 50, seq: 50, created_at: ""}],
                          polls: [], more: true, oldest: 50});
          out.first = [s.cursor, s.oldest, s.more];
          F.applyFeed(s, {messages: [{id: "a", number: 10, seq: 10, created_at: ""}], more: false, oldest: 10});
          out.then = [s.cursor, s.oldest, s.more, F.byNumber(s.messages).map((r) => r.id)];
          // What changed after a cursor says `more` of ITSELF: the history's flag stays.
          F.applyFeed(s, {cursor: 61, messages: [], polls: [], more: true});
          out.live = [s.cursor, s.more];
        """)
        self.assertEqual(out["first"], [60, 50, True])
        self.assertEqual(out["then"], [60, 10, False, ["a", "b"]])
        self.assertEqual(out["live"], [61, False])

    def test_the_length_a_post_is_priced_by_is_utf8_bytes(self):
        out = self.run_js("""
          out.sizes = ["", "abc", "zażółć", "€", "😀", "a\\nb"].map(F.utf8Length);
          out.none = [F.utf8Length(null), F.utf8Length(undefined)];
        """)
        self.assertEqual(out["sizes"], [0, 3, len("zażółć".encode()), 3, 4, 3])
        self.assertEqual(out["none"], [0, 0])


class RingTests(ScriptCase):
    """What a poll's ring is drawn from (the owner, 2026-10-07: "poll results
    should also have pie chart")."""

    def test_a_ring_is_the_counts_in_the_options_order_each_with_its_colour(self):
        out = self.run_js("""
          const row = (total, ballots) => ({total: total,
            choices: ballots.map((b, i) => ({id: i + 1, label: "o" + i, text: "", ballots: b}))});
          out.counted = F.ringData(row(4, [3, 0, 1]));
          out.none = [F.ringData(row(0, [0, 0])), F.ringData(row(null, [null, null])),
                      F.ringData(row(2, [2, null])), F.ringData(null), F.ringData({total: 3, choices: []}),
                      F.ringData({total: 3})];
          out.eleven = F.ringData(row(11, new Array(11).fill(1))).colours;
          out.slices = [F.SLICES.length, new Set(F.SLICES).size, F.SLICES.every((c) => /^#[0-9a-f]{6}$/.test(c))];
          const item = (data, at, label) => ({chart: {data: {datasets: [{data: data}]}}, datasetIndex: 0,
                                              dataIndex: at, label: label});
          out.tips = [F.ringTip(item([3, 1], 0, "Soup")), F.ringTip(item([3, 1], 1, "<b>Salad</b>")),
                      F.ringTip(item([1, 1, 1], 2, "c")), F.ringTip(item([0, 0], 0, "Soup"))];
        """)
        self.assertEqual(out["counted"], {"labels": ["o0", "o1", "o2"], "values": [3, 0, 1],
                                          "colours": ["#3b82f6", "#ef4444", "#10b981"]})
        # No answer, a count that is withheld (wholly or in part), no poll, no option: no ring.
        self.assertEqual(out["none"], [None] * 6)
        # Ten colours, each its own: as many as a poll has options at the most.
        self.assertEqual(out["slices"], [10, 10, True])
        self.assertEqual(out["eleven"][10], out["eleven"][0])
        self.assertEqual(out["tips"], ["Soup: 3 (75%)", "<b>Salad</b>: 1 (25%)", "c: 1 (33%)", "Soup: 0 (0%)"])


class PollerTests(ScriptCase):
    """``createPoller``: the timer, with a clock the test moves."""

    SETUP = """
      const asked = [];
      const answers = [];
      let hidden = false;
      const states = [];
      const poller = F.createPoller({
        seconds: SECONDS, setTimer: setTimer, clearTimer: clearTimer,
        hidden: () => hidden, onState: (s) => states.push([s.ok, s.failures, s.wait]),
        ask: () => {
          asked.push(clock);
          const next = answers.length ? answers.shift() : {ok: true};
          if (next.hold) { return new Promise((resolve) => { next.release = () => resolve(next.then || {ok: true}); held.push(next); }); }
          return next.fail ? Promise.reject(new Error("offline")) : Promise.resolve(next);
        }});
      const held = [];
    """

    def poller(self, body, seconds=5):
        return self.run_js(self.SETUP.replace("SECONDS", json.dumps(seconds)) + body)

    def test_the_interval_is_the_page_datas_and_each_timer_follows_an_answer(self):
        out = self.poller("""
          poller.start();
          out.first = waits();
          await advance(6999);
          out.before = asked.length;
          await advance(1);
          out.at = asked.slice();
          out.next = waits();
          await advance(7000);
          out.second = asked.slice();
          out.bounds = [F.waitOf(0.2), F.waitOf(5), F.waitOf("9"), F.waitOf(99999), F.waitOf(null),
                        F.waitOf("x"), F.waitOf(-3)];
        """, seconds=7)
        self.assertEqual(out["first"], [7000])
        self.assertEqual(out["before"], 0)
        self.assertEqual(out["at"], [7000])
        self.assertEqual(out["next"], [7000])
        self.assertEqual(out["second"], [7000, 14000])
        self.assertEqual(out["bounds"], [1000, 5000, 9000, 3600000, 5000, 5000, 5000])

    def test_no_timer_is_set_while_a_request_is_out(self):
        out = self.poller("""
          answers.push({hold: true});
          poller.start();
          await advance(5000);
          out.asked = asked.length;
          out.whileOut = [waits(), poller.flying(), poller.waiting()];
          await advance(60000);                 // a slow server is not asked twice
          out.still = asked.length;
          held[0].release();
          await settle();
          out.after = [waits(), poller.flying()];
        """)
        self.assertEqual(out["asked"], 1)
        self.assertEqual(out["whileOut"], [[], True, False])
        self.assertEqual(out["still"], 1)
        self.assertEqual(out["after"], [[5000], False])

    def test_a_hidden_tab_asks_nothing_and_the_return_asks_once_at_once(self):
        out = self.poller("""
          poller.start();
          await advance(5000);
          out.visible = asked.length;
          hidden = true;
          poller.visibility();
          out.hiddenTimers = waits();
          await advance(600000);
          out.whileHidden = asked.length;
          hidden = false;
          poller.visibility();
          await settle();
          out.onReturn = asked.slice(1);
          out.then = waits();
          await advance(4999);
          out.notYet = asked.length;
          await advance(1);
          out.next = asked.length;
        """)
        self.assertEqual(out["visible"], 1)
        self.assertEqual(out["hiddenTimers"], [])
        self.assertEqual(out["whileHidden"], 1)
        self.assertEqual(out["onReturn"], [605000])       # at once, and once
        self.assertEqual(out["then"], [5000])
        self.assertEqual((out["notYet"], out["next"]), (2, 3))

    def test_a_timer_that_fires_in_a_hidden_tab_asks_nothing(self):
        out = self.poller("""
          poller.start();
          hidden = true;                 // hidden, and no event told the page
          await advance(20000);
          out.asked = asked.length;
          out.timers = waits();
        """)
        self.assertEqual((out["asked"], out["timers"]), (0, []))

    def test_an_answer_that_lands_in_a_hidden_tab_sets_no_timer(self):
        out = self.poller("""
          answers.push({hold: true});
          poller.start();
          await advance(5000);
          hidden = true;
          poller.visibility();
          held[0].release();
          await settle();
          out.timers = waits();
          await advance(60000);
          out.asked = asked.length;
        """)
        self.assertEqual((out["timers"], out["asked"]), ([], 1))

    def test_more_asks_again_at_once_until_there_is_no_more(self):
        out = self.poller("""
          answers.push({ok: true, more: true}, {ok: true, more: true}, {ok: true, more: false});
          poller.start();
          await advance(5000);
          out.asked = asked.slice();
          out.timers = waits();
        """)
        self.assertEqual(out["asked"], [5000, 5000, 5000])
        self.assertEqual(out["timers"], [5000])

    def test_a_failure_doubles_the_wait_up_to_a_minute_and_an_answer_brings_it_back(self):
        out = self.poller("""
          for (let i = 0; i < 6; i++) { answers.push(i % 2 ? {ok: false} : {fail: true}); }
          poller.start();
          out.waits = [];
          for (let i = 0; i < 6; i++) { await advance(poller.wait()); out.waits.push(waits()[0]); }
          out.asked = asked.slice();
          await advance(60000);                   // the seventh is answered
          out.back = [waits(), poller.wait()];
          out.states = states.map((s) => s[0] + ":" + s[1] + ":" + s[2]);
        """)
        self.assertEqual(out["waits"], [10000, 20000, 40000, 60000, 60000, 60000])
        self.assertEqual(out["asked"], [5000, 15000, 35000, 75000, 135000, 195000])
        self.assertEqual(out["back"], [[5000], 5000])
        self.assertEqual(out["states"][0], "false:1:10000")
        self.assertEqual(out["states"][-1], "true:0:5000")

    def test_a_long_interval_is_never_shortened_by_the_back_off(self):
        out = self.poller("""
          answers.push({fail: true}, {fail: true});
          poller.start();
          await advance(120000);
          out.one = waits();
          await advance(120000);
          out.two = waits();
        """, seconds=120)
        self.assertEqual((out["one"], out["two"]), ([120000], [120000]))

    def test_now_asks_at_once_and_during_a_request_once_more_after_it(self):
        out = self.poller("""
          poller.start();
          poller.now();
          await settle();
          out.atOnce = asked.slice();
          out.timers = waits();                    // the old timer is gone: one timer
          answers.push({hold: true});
          poller.now(); poller.now(); poller.now();
          await settle();
          out.out = asked.length;                  // one request, however often it is pressed
          held[0].release();
          await settle();
          out.after = asked.length;                // and one more, as it was asked for
          out.timersAfter = waits();
        """)
        self.assertEqual(out["atOnce"], [0])
        self.assertEqual(out["timers"], [5000])
        self.assertEqual(out["out"], 2)
        self.assertEqual(out["after"], 3)
        self.assertEqual(out["timersAfter"], [5000])

    def test_stop_ends_it(self):
        out = self.poller("""
          poller.start();
          poller.stop();
          out.timers = waits();
          await advance(60000);
          out.asked = asked.length;
          out.visibility = poller.visibility();
          await settle();
          out.still = asked.length;
        """)
        self.assertEqual((out["timers"], out["asked"], out["visibility"], out["still"]),
                         ([], 0, None, 0))


class EstimatorTests(ScriptCase):
    """``createEstimator``: asked a moment after the last change."""

    SETUP = """
      const asked = [], shown = [];
      const answers = [];
      const estimator = F.createEstimator({
        setTimer: setTimer, clearTimer: clearTimer, show: (data) => shown.push(data),
        ask: (text, image) => {
          asked.push([clock, text, image]);
          const next = answers.length ? answers.shift() : {ok: true, data: {display: "1 mana"}};
          if (next.hold) { return new Promise((resolve) => { next.release = () => resolve(next.then); held.push(next); }); }
          return next.fail ? Promise.reject(new Error("offline")) : Promise.resolve(next);
        }});
      const held = [];
    """

    def test_it_waits_for_the_typing_to_stop(self):
        out = self.run_js(self.SETUP + """
          out.wait = F.ESTIMATE_WAIT;
          estimator.request(1, 0); await advance(200);
          estimator.request(2, 0); await advance(200);
          estimator.request(3, 0); await advance(399);
          out.before = asked.length;
          await advance(1);
          out.asked = asked.slice();
          out.shown = shown.slice();
        """)
        self.assertEqual(out["wait"], 400)
        self.assertEqual(out["before"], 0)
        self.assertEqual(out["asked"], [[800, 3, 0]])
        self.assertEqual(out["shown"], [{"display": "1 mana"}])

    def test_a_chosen_picture_is_asked_at_once_and_an_empty_post_asks_nothing(self):
        out = self.run_js(self.SETUP + """
          estimator.request(0, 2048, true);
          await settle();
          out.atOnce = asked.slice();
          estimator.request(0, 0);
          await advance(1000);
          out.empty = [asked.length, shown[shown.length - 1]];
        """)
        self.assertEqual(out["atOnce"], [[0, 0, 2048]])
        self.assertEqual(out["empty"], [1, None])

    def test_an_overtaken_answer_is_dropped_and_a_failure_shows_nothing(self):
        out = self.run_js(self.SETUP + """
          answers.push({hold: true, then: {ok: true, data: {display: "old"}}},
                       {ok: true, data: {display: "new"}}, {fail: true}, {ok: false, data: {error: "no"}});
          estimator.request(5, 0, true);
          estimator.request(6, 0, true);
          await settle();
          held[0].release();
          await settle();
          out.shown = shown.map((d) => d && d.display);
          estimator.request(7, 0, true); await settle();
          estimator.request(8, 0, true); await settle();
          out.after = shown.slice(1);
        """)
        self.assertEqual(out["shown"], ["new"])
        self.assertEqual(out["after"], [None, None])


class LinkTests(ScriptCase):
    """A link only to this platform; everything else stays text."""

    def test_only_this_platforms_addresses_become_links(self):
        out = self.run_js("""
          const hosts = ["forum.example", "WWW.Other.Example"];
          const cut = (text) => F.splitLinks(text, hosts, "https://forum.example");
          out.own = cut("see https://forum.example/vault/x?a=1#b, please");
          out.other = cut("see https://evil.example/a and http://forum.example.evil.test/");
          out.second = cut("www.other.example/forum/guild/ is ours too");
          out.user = cut("https://forum.example@evil.example/ and https://x@forum.example/");
          out.scheme = cut("javascript:alert(1) data:text/html,x ftp://forum.example/x");
          out.glued = cut("xhttps://forum.example/a mail@www.forum.example");
          out.plain = cut("<b>bold</b> & <script>x</script>");
          out.joined = ["see https://forum.example/a.", "(https://forum.example/a)", ""].map(
            (text) => cut(text).map((part) => part.text).join("") === text);
        """)
        self.assertEqual(out["own"], [
            {"text": "see "},
            {"text": "https://forum.example/vault/x?a=1#b", "href": "https://forum.example/vault/x?a=1#b"},
            {"text": ", please"}])
        self.assertEqual([part.get("href") for part in out["other"]], [None])
        # Another of the platform's names leads to THIS origin, same path.
        self.assertEqual(out["second"][0], {"text": "www.other.example/forum/guild/",
                                            "href": "https://forum.example/forum/guild/"})
        for name in ("user", "scheme", "glued", "plain"):
            self.assertEqual([part.get("href") for part in out[name]], [None], name)
        self.assertEqual(out["plain"], [{"text": "<b>bold</b> & <script>x</script>"}])
        self.assertEqual(out["joined"], [True, True, True])


PAGE = CLOCK + r"""
const DATA = JSON.parse(require("fs").readFileSync(process.argv[2], "utf8"));
const config = DATA.config;
// The estimate door is the tests' own to add: without it the page asks none.
delete config.urls.estimate;
const touched = [];
function spy(name) {
  return new Proxy({}, {
    get(_t, key) { touched.push(name + "." + String(key)); return () => null; },
    set(_t, key) { touched.push(name + "." + String(key) + "="); return true; },
  });
}
globalThis.localStorage = spy("localStorage");
globalThis.sessionStorage = spy("sessionStorage");
globalThis.WebSocket = function () { touched.push("WebSocket"); };
globalThis.EventSource = function () { touched.push("EventSource"); };
globalThis.setInterval = () => { touched.push("setInterval"); return 0; };
globalThis.setTimeout = setTimer;
globalThis.clearTimeout = clearTimer;
globalThis.location = {hostname: "forum.example", origin: "https://forum.example"};
let confirms = 0;
globalThis.confirm = () => { confirms += 1; return true; };

const calls = [], queue = [], held = [];
let flying = 0, mostFlying = 0;
function bodyOf(body) {
  if (body === undefined || body === null) { return null; }
  if (typeof body === "string") { try { return JSON.parse(body); } catch (e) { return body; } }
  if (body instanceof URLSearchParams) { return Object.fromEntries(body.entries()); }
  if (body instanceof FormData) {
    const out = {};
    for (const [key, value] of body.entries()) {
      out[key] = typeof value === "string" ? value : {name: value.name, size: value.size};
    }
    return out;
  }
  return String(body);
}
globalThis.fetch = function (url, init) {
  init = init || {};
  calls.push({url: String(url), method: init.method || "GET", body: bodyOf(init.body),
              headers: init.headers || {}, at: clock});
  const next = queue.length ? queue.shift() : {status: 200, data: {}};
  flying += 1; mostFlying = Math.max(mostFlying, flying);
  const answer = () => {
    flying -= 1;
    if (next.network) { return Promise.reject(new Error("offline")); }
    return Promise.resolve({ok: next.status >= 200 && next.status < 300, status: next.status,
      json: () => (next.notJson ? Promise.reject(new Error("not json")) : Promise.resolve(next.data || {}))});
  };
  if (next.hold) { return new Promise((resolve, reject) => { held.push(() => answer().then(resolve, reject)); }); }
  return answer();
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
let made = 0;
class Txt {
  constructor(text) { this.textContent = String(text); this.parentNode = null; }
}
class El {
  constructor(tag, attrs) {
    this.tagName = String(tag).toUpperCase();
    this.serial = ++made;                       // which node this is: identity across redraws
    this.attrs = Object.assign({}, attrs || {});
    this.childNodes = []; this.parentNode = null; this.handlers = {}; this.dataset = {};
    this.style = {}; this._text = "";
    this.type = this.attrs.type || "";
    this.disabled = "disabled" in this.attrs;
    this._value = this.attrs.value !== undefined ? this.attrs.value : "";
    this.files = [];
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
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
  get children() { return this.childNodes.filter((child) => child instanceof El); }
  all() {
    const out = [];
    const walk = (node) => node.children.forEach((child) => { out.push(child); walk(child); });
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
  set value(value) { this._value = String(value); if (this.type === "file" && !this._value) { this.files = []; } }
  get textContent() { return this._text + this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(value) {
    this.childNodes.forEach((child) => { child.parentNode = null; });
    this._text = String(value); this.childNodes = [];
  }
  set innerHTML(_value) { throw new Error("the page is never written as markup"); }
  set outerHTML(_value) { throw new Error("the page is never written as markup"); }
  insertAdjacentHTML() { throw new Error("the page is never written as markup"); }
  _take(child) {
    if (child.parentNode) { child.parentNode.removeChild(child); }
    child.parentNode = this;
    return child;
  }
  appendChild(child) { this.childNodes.push(this._take(child)); return child; }
  insertBefore(child, before) {
    this._take(child);
    const at = before ? this.childNodes.indexOf(before) : -1;
    if (before && at < 0) { throw new Error("insertBefore: not a child"); }
    if (at < 0) { this.childNodes.push(child); } else { this.childNodes.splice(at, 0, child); }
    return child;
  }
  removeChild(child) {
    const at = this.childNodes.indexOf(child);
    if (at < 0) { throw new Error("removeChild: not a child"); }
    this.childNodes.splice(at, 1); child.parentNode = null;
    return child;
  }
  replaceChild(fresh, old) {
    const at = this.childNodes.indexOf(old);
    if (at < 0) { throw new Error("replaceChild: not a child"); }
    this._take(fresh);
    this.childNodes.splice(this.childNodes.indexOf(old), 1, fresh); old.parentNode = null;
    return old;
  }
  addEventListener(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); }
  // What the page tells an element (a dialog: open or close) is kept, and heard.
  dispatchEvent(event) {
    (this.sent = this.sent || []).push({type: event.type, open: event.detail ? event.detail.open : undefined});
    this.fire(event.type, {detail: event.detail});
    return true;
  }
  fire(name, more) {
    const event = Object.assign({type: name, target: this, defaultPrevented: false,
                                 preventDefault() { this.defaultPrevented = true; },
                                 stopPropagation() {}}, more || {});
    (this.handlers[name] || []).forEach((fn) => fn(event));
    return event;
  }
  click() { return this.fire("click"); }
  focus() { document.activeElement = this; }
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
  removeAttribute(name) { delete this.attrs[name]; }
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

const box = build(DATA.tree);
const docHandlers = {};
globalThis.document = {
  readyState: "loading", activeElement: null, visibilityState: "visible", hidden: false,
  documentElement: {lang: DATA.lang},
  getElementById: (id) => (id === "forum-channel-config" ? {textContent: JSON.stringify(config)} : null),
  createElement: (tag) => new El(tag),
  createTextNode: (text) => new Txt(text),
  addEventListener(name, fn) { (docHandlers[name] = docHandlers[name] || []).push(fn); },
  fire(name, event) { (docHandlers[name] || []).forEach((fn) => fn(event || {type: name})); },
  querySelectorAll: () => [],
};
const F = require(process.argv[1]);

const part = (name) => box.querySelector("[data-forum-" + name + "]");
const list = part("messages"), polls = part("polls"), scroll = part("scroll");
const T0 = Date.now() - 6 * 3600 * 1000;
const at = (n) => new Date(T0 + n * 600000).toISOString();
const msg = (n, more) => Object.assign({
  id: "m" + n, number: n, seq: n, kind: "text", sender: "Ann", sender_id: 1, mine: false,
  avatar: "", created_at: at(n), text: "text " + n, image: null, may_remove: false}, more || {});
const pollRow = (n, more) => Object.assign({
  id: "p" + n, number: n, seq: n, title: "Lunch?", status: "open", open: true, closes_at: null,
  revisability: "final", visibility: "live", created_at: at(n), opener: "Ann", mine: false,
  may_manage: false,
  choices: [{id: 1, label: "Soup", text: "", ballots: 0}, {id: 2, label: "Salad", text: "with bread", ballots: 0}],
  total: 0, my_choice: null, results_visible: true}, more || {});
const feedOf = (cursor, messages, pollRows, more) => Object.assign(
  {cursor: cursor, messages: messages || [], polls: pollRows || [], more: false, purged_before: null},
  more || {});
// What stands in the conversation: a message's id, "?" and the poll's id for a
// question, "gone" for the quiet line of a removed message.
const ids = () => list.children.map((n) => n.dataset.messageId ||
  (n.dataset.questionId ? "?" + n.dataset.questionId : "gone"));
const asked = (id) => list.querySelector('[data-question-id="' + id + '"]');
const radios = (id) => asked(id).querySelectorAll("input");
const node = (id) => list.querySelector('[data-message-id="' + id + '"]');
const card = (id) => polls.querySelector('[data-poll-id="' + id + '"]');
const bodyText = (id) => { const p = node(id).querySelector("[data-forum-body]"); return p ? p.textContent : null; };
const bars = (id) => card(id).querySelectorAll("[data-forum-bar]").map((b) => [b.dataset.forumBar, b.style.width]);
const ballots = (id) => card(id).querySelectorAll("[data-forum-ballots]").map((b) => b.dataset.forumBallots);
const status = () => [part("status").textContent, part("status").classList.contains("hidden")];
const estimate = () => [part("estimate").textContent, part("estimate").classList.contains("hidden")];
const feeds = () => calls.filter((c) => c.url.indexOf("/feed/") >= 0).map((c) => c.url.split("/feed/")[1]);
const hide = async () => { document.visibilityState = "hidden"; document.hidden = true; document.fire("visibilitychange"); await settle(); };
const show = async () => { document.visibilityState = "visible"; document.hidden = false; document.fire("visibilitychange"); await settle(); };
const type = async (text) => { part("text").value = text; part("text").fire("input"); await settle(); };
const choose = async (name, size, kind) => {
  const input = part("image");
  input.files = [new File([new Uint8Array(size)], name, {type: kind || "image/png"})];
  input.fire("change");
  await settle();
};
const submit = async () => { const e = part("post").fire("submit"); await settle(); return e; };
(async () => {
  const out = {};
  __BEFORE__
  const mounted = F.mount(box);
  await settle();
  __BODY__
  out.touched = touched;
  out.calls = calls;
  out.mostFlying = mostFlying;
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch((error) => { console.error(error); process.exit(1); });
"""


class PageCase(ForumCase):
    """``mount()`` run on the page the server answers a member with."""

    def setUp(self):
        super().setUp()
        if not _NODE:
            self.skipTest("node is not installed")

    def run_page(self, body, *, before="", user=None):
        response = client_of(user or self.member).get(self.url("channel_detail"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        data = {"tree": element(tree_of(html), "data-forum-channel"),
                "config": json.loads(CONFIG.search(html).group(1)), "lang": "en"}
        self.assertIsNotNone(data["tree"])
        with tempfile.TemporaryDirectory() as folder:
            handed = Path(folder) / "page.json"
            handed.write_text(json.dumps(data), encoding="utf-8")
            source = PAGE.replace("__BEFORE__", before).replace("__BODY__", body)
            done = subprocess.run([_NODE, "-e", source, finders.find("forum/channel.js"),
                                   str(handed)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])


class PagePollingTests(PageCase):
    def test_the_real_page_mounts_and_its_interval_is_the_settings(self):
        from toto.forum.models import ForumSettings

        self.say(self.second, "the first word")
        self.open_poll(self.second)
        settings = ForumSettings.current()
        settings.refresh_seconds = 9
        settings.save()
        out = self.run_page("""
          out.rows = list.querySelectorAll("[data-forum-body]").map((n) => n.textContent);
          out.conversation = list.children.map((n) => (n.dataset.messageId ? "message" : n.dataset.state));
          out.options = list.querySelectorAll("input").map((n) => n.type);
          out.polls = polls.children.length;
          out.count = box.querySelectorAll("[data-forum-poll-count]").map((n) => n.textContent);
          out.waits = waits();
          out.cursor = mounted.state.cursor;
          out.asked = calls.length;
          await advance(9000);
          out.feeds = feeds();
          out.live = [part("live").textContent, part("live").dataset.state];
        """)
        self.assertEqual(out["rows"], ["the first word"])
        # The poll's question stands in the conversation, after the message
        # it was opened after, with its two options to choose from.
        self.assertEqual(out["conversation"], ["message", "asked"])
        self.assertEqual(out["options"], ["radio", "radio"])
        self.assertEqual(out["polls"], 1)
        self.assertEqual(out["count"], ["1"])
        self.assertEqual(out["waits"], [9000])          # one timer, the Settings' interval
        self.assertEqual(out["asked"], 0)               # the page's own feed came with it
        self.assertEqual(out["feeds"], [f"?after={out['cursor']}"])
        self.assertEqual(out["live"], ["live", "live"])
        self.assertEqual(out["touched"], [])

    def test_a_hidden_tab_asks_nothing_and_catches_up_on_return_with_its_cursor(self):
        out = self.run_page(before="config.refresh_seconds = 5; config.feed = feedOf(3, [msg(1), msg(3)]);",
                            body="""
          await hide();
          out.timers = waits();
          await advance(300000);
          out.whileHidden = feeds();
          // What happened meanwhile comes in two pages: `more` asks again at once.
          queue.push({status: 200, data: feedOf(5, [msg(4), msg(5)], [], {more: true})},
                     {status: 200, data: feedOf(7, [msg(6), msg(7)])});
          await show();
          out.onReturn = feeds();
          out.at = calls.map((c) => c.at);
          out.ids = ids();
          out.then = waits();
          await advance(5000);
          out.next = feeds().slice(2);
        """)
        self.assertEqual(out["timers"], [])
        self.assertEqual(out["whileHidden"], [])
        self.assertEqual(out["onReturn"], ["?after=3", "?after=5"])   # once, then once more for `more`
        self.assertEqual(out["at"], [300000, 300000])
        self.assertEqual(out["ids"], ["m1", "m3", "m4", "m5", "m6", "m7"])
        self.assertEqual(out["then"], [5000])
        self.assertEqual(out["next"], ["?after=7"])
        self.assertEqual(out["mostFlying"], 1)

    def test_one_request_at_a_time_however_often_refresh_is_pressed(self):
        out = self.run_page(before="config.refresh_seconds = 5; config.feed = feedOf(1, [msg(1)]);",
                            body="""
          queue.push({hold: true, status: 200, data: feedOf(2, [msg(2)])});
          await advance(5000);
          part("refresh").click(); part("refresh").click();
          await show();                          // a visibility event during the request
          await advance(60000);                  // and no timer runs beside it
          out.out = feeds();
          held.shift()();
          await settle();
          out.after = feeds();
          out.ids = ids();
        """)
        self.assertEqual(out["out"], ["?after=1"])
        self.assertEqual(out["after"], ["?after=1", "?after=2"])
        self.assertEqual(out["mostFlying"], 1)
        self.assertEqual(out["ids"], ["m1", "m2"])

    def test_a_failed_request_backs_off_says_so_and_recovers(self):
        out = self.run_page(before="config.refresh_seconds = 5; config.feed = feedOf(1, [msg(1)]);",
                            body="""
          queue.push({network: true}, {status: 503, data: {error: "The key is away."}},
                     {status: 502, notJson: true}, {status: 200, data: feedOf(2, [msg(2)])});
          await advance(5000);
          out.one = [waits(), part("live").dataset.state, part("live").textContent, status()];
          await advance(10000);
          out.two = [waits(), status()];
          await advance(20000);
          out.three = waits();
          await advance(40000);
          out.back = [waits(), part("live").dataset.state, status(), ids()];
          out.feeds = feeds();
        """)
        self.assertEqual(out["one"], [[10000], "retrying", "offline, retrying", ["", True]])
        self.assertEqual(out["two"], [[20000], ["The key is away.", False]])
        self.assertEqual(out["three"], [40000])
        self.assertEqual(out["back"], [[5000], "live", ["", True], ["m1", "m2"]])
        # Every question carried the cursor the page kept: nothing was skipped.
        self.assertEqual(out["feeds"], ["?after=1"] * 4)


class PageMessagesTests(PageCase):
    def test_a_new_message_is_appended_once_and_the_nodes_there_are_not_rebuilt(self):
        out = self.run_page(before="config.feed = feedOf(2, [msg(1), msg(2)]);", body="""
          const before = list.children.map((n) => n.serial);
          queue.push({status: 200, data: feedOf(3, [msg(3)])},
                     {status: 200, data: feedOf(3, [msg(3)])},            // the same row again
                     {status: 200, data: feedOf(3, [])});
          await advance(5000);
          out.ids = ids();
          const third = node("m3").serial;
          await advance(5000); await advance(5000);
          out.again = ids();
          out.kept = [list.children.slice(0, 2).map((n) => n.serial).join() === before.join(),
                      node("m3").serial === third];
          out.history = feeds().filter((q) => q.indexOf("before") >= 0);
          out.empty = part("empty").classList.contains("hidden");
        """)
        self.assertEqual(out["ids"], ["m1", "m2", "m3"])
        self.assertEqual(out["again"], ["m1", "m2", "m3"])
        self.assertEqual(out["kept"], [True, True])
        self.assertEqual(out["history"], [])              # history is never loaded again
        self.assertTrue(out["empty"])

    def test_a_removal_becomes_a_quiet_line_and_a_cleanup_takes_nodes_out(self):
        out = self.run_page(
            before="config.feed = feedOf(5, [msg(1), msg(2), msg(4), msg(5)], [pollRow(3)]);", body="""
          queue.push({status: 200, data: feedOf(6, [{id: "m2", number: 2, seq: 6, removed: true}])});
          await advance(5000);
          out.afterRemoval = ids();
          out.line = list.children[1].textContent;
          out.text = list.textContent.indexOf("text 2");
          // The cleanup's edge lies between the fourth and the fifth: the quiet
          // line, two messages and the poll are from before it.
          queue.push({status: 200, data: feedOf(6, [], [], {purged_before: new Date(T0 + 4.5 * 600000).toISOString()})});
          await advance(5000);
          out.afterCleanup = ids();
          out.polls = polls.children.length;
          out.pollsEmpty = part("polls-empty").classList.contains("hidden");
          out.count = box.querySelectorAll("[data-forum-poll-count]").map((n) => n.textContent);
          out.held = Object.keys(mounted.state.messages);
          // A poll's own tombstone takes its card.
          queue.push({status: 200, data: feedOf(7, [], [pollRow(7)])},
                     {status: 200, data: feedOf(8, [], [{id: "p7", number: 7, seq: 8, removed: true}])});
          await advance(5000);
          out.opened = polls.children.length;
          await advance(5000);
          out.closed = polls.children.length;
        """)
        self.assertEqual(out["afterRemoval"], ["m1", "gone", "?p3", "m4", "m5"])
        self.assertEqual(out["line"], "A message was removed.")
        self.assertEqual(out["text"], -1)
        self.assertEqual(out["afterCleanup"], ["m5"])
        self.assertEqual((out["polls"], out["pollsEmpty"], out["count"]), (0, False, ["0"]))
        self.assertEqual(out["held"], ["m5"])
        self.assertEqual((out["opened"], out["closed"]), (1, 0))

    def test_what_a_member_typed_is_text_whatever_it_looks_like(self):
        evil = '</script><img src=x onerror=alert(1)> <b>bold</b> "q" https://evil.example/x'
        out = self.run_page(before=f"""
          const evil = {json.dumps(evil)};
          config.feed = feedOf(3, [msg(1, {{text: evil, sender: "<i>Mallory</i>"}}),
            msg(2, {{text: "see https://forum.example/vault/ and http://evil.example/"}})],
            [pollRow(3, {{title: "<u>Q</u>", opener: "<s>O</s>",
                          choices: [{{id: 1, label: "<b>a</b>", text: "<script>t</script>", ballots: 0}}]}})]);
        """, body="""
          out.text = bodyText("m1");
          out.sender = node("m1").querySelector("[data-forum-sender]").textContent;
          const tags = (root) => root.all().map((n) => n.tagName);
          out.tags = Array.from(new Set(tags(list).concat(tags(polls)))).sort();
          out.links = list.all().filter((n) => n.tagName === "A").map((a) => [a.textContent, a.href, a.rel]);
          out.joined = bodyText("m2");
          out.poll = card("p3").textContent;
          out.question = asked("p3").textContent;
        """)
        self.assertEqual(out["text"], evil)
        self.assertEqual(out["sender"], "<i>Mallory</i>")
        # Only what the script itself makes: no element came out of a member's words.
        self.assertEqual(out["tags"], ["A", "ARTICLE", "BUTTON", "DIV", "FORM", "H3", "INPUT",
                                       "LABEL", "LI", "P", "SPAN", "TIME", "UL"])
        self.assertEqual(out["links"], [["https://forum.example/vault/",
                                         "https://forum.example/vault/", "noopener"]])
        self.assertEqual(out["joined"], "see https://forum.example/vault/ and http://evil.example/")
        for words in ("<u>Q</u>", "<s>O</s>", "<b>a</b>", "<script>t</script>"):
            self.assertIn(words, out["poll"])
            self.assertIn(words, out["question"])       # the question in the conversation too

    def test_a_picture_is_the_image_doors_address_and_opens_in_a_new_tab(self):
        out = self.run_page(before="""
          config.feed = feedOf(2, [msg(1, {kind: "image", text: "",
            image: {url: "/forum/guild/messages/m1/image/", mime: "image/png", size: 10}}),
            msg(2, {avatar: "/media/avatars/ann.png"})]);
        """, body="""
          const pictures = list.all().filter((n) => n.tagName === "IMG");
          out.sources = pictures.map((img) => img.src);
          const door = pictures[0].parentNode;
          out.door = [door.tagName, door.href, door.target, door.rel];
          out.noBody = node("m1").querySelector("[data-forum-body]");
        """)
        self.assertEqual(out["sources"], ["/forum/guild/messages/m1/image/", "/media/avatars/ann.png"])
        self.assertEqual(out["door"], ["A", "/forum/guild/messages/m1/image/", "_blank",
                                       "noopener noreferrer"])
        self.assertIsNone(out["noBody"])

    def test_consecutive_messages_of_one_sender_share_one_name(self):
        out = self.run_page(before="""
          const near = (n, minutes, more) => msg(n, Object.assign(
            {created_at: new Date(T0 + minutes * 60000).toISOString()}, more || {}));
          config.feed = feedOf(5, [near(1, 0), near(2, 1), near(3, 2, {sender: "Bob", sender_id: 2}),
                                   near(4, 3), near(5, 30)]);
        """, body="""
          const joined = () => list.children.map((n) => n.dataset.joined === "1");
          const heads = () => list.children.map((n) => n.querySelector("[data-forum-head]").classList.contains("hidden"));
          out.joined = joined();
          out.heads = heads();
          // The message between them is removed: what follows starts anew.
          queue.push({status: 200, data: feedOf(6, [{id: "m1", number: 1, seq: 6, removed: true}])});
          await advance(5000);
          out.after = list.children.map((n) => n.dataset.messageId ? n.dataset.joined === "1" : "gone");
          out.mine = node("m2").dataset.mine || "";
        """)
        self.assertEqual(out["joined"], [False, True, False, False, False])
        self.assertEqual(out["heads"], out["joined"])
        self.assertEqual(out["after"], ["gone", False, False, False, False])
        self.assertEqual(out["mine"], "")

    def test_the_readers_place_is_kept_unless_they_are_at_the_bottom(self):
        out = self.run_page(before="config.feed = feedOf(2, [msg(1), msg(2)], [], {more: true, oldest: 1});",
                            body="""
          out.olderShown = !part("older").classList.contains("hidden");
          scroll.scrollHeight = 1000; scroll.clientHeight = 300; scroll.scrollTop = 200;
          scroll.fire("scroll");                         // reading further up
          queue.push({status: 200, data: feedOf(3, [msg(3)])});
          await advance(5000);
          out.up = [scroll.scrollTop, part("new").classList.contains("hidden")];
          part("new").click();
          out.jumped = [scroll.scrollTop, part("new").classList.contains("hidden")];
          scroll.scrollHeight = 1200;
          queue.push({status: 200, data: feedOf(4, [msg(4)])});
          await advance(5000);
          out.bottom = scroll.scrollTop;                 // at the bottom: it follows
          // Older history: the distance to the bottom stays what it was.
          scroll.scrollTop = 0; scroll.fire("scroll");
          queue.push({status: 200, data: {messages: [msg(0)], more: false, oldest: 0, purged_before: null}});
          scroll.scrollHeight = 1200;
          part("older").click();
          scroll.scrollHeight = 1500;                    // what the new nodes add
          await settle();
          out.ids = ids();
          out.olderCall = feeds().filter((q) => q.indexOf("before") >= 0);
          out.olderHidden = part("older").classList.contains("hidden");
        """)
        self.assertTrue(out["olderShown"])
        self.assertEqual(out["up"], [200, False])
        self.assertEqual(out["jumped"], [1000, True])
        self.assertEqual(out["bottom"], 1200)
        self.assertEqual(out["ids"], ["m0", "m1", "m2", "m3", "m4"])
        self.assertEqual(out["olderCall"], ["?before=1"])
        self.assertTrue(out["olderHidden"])


class PagePollTests(PageCase):
    def test_a_polls_counts_are_updated_in_its_card(self):
        out = self.run_page(before="config.feed = feedOf(2, [msg(1)], [pollRow(2)]);", body="""
          const same = card("p2").serial, question = asked("p2").serial;
          out.before = [bars("p2"), ballots("p2"), card("p2").querySelector("[data-forum-total]").textContent];
          queue.push({status: 200, data: feedOf(5, [], [pollRow(2, {seq: 5, total: 4,
            choices: [{id: 1, label: "Soup", text: "", ballots: 3},
                      {id: 2, label: "Salad", text: "with bread", ballots: 1}]})])});
          await advance(5000);
          out.after = [bars("p2"), ballots("p2"), card("p2").querySelector("[data-forum-total]").textContent];
          out.sameCard = [card("p2").serial === same, polls.children.length];
          out.messages = ids();
          out.sameQuestion = asked("p2").serial === question;
          out.counted = asked("p2").querySelectorAll("[data-forum-bar]").length;
          mounted.showTab("polls");
          out.noRing = ["[data-forum-ring]", "[data-forum-slice]", "canvas"].map((q) => box.querySelectorAll(q).length);
        """)
        self.assertEqual(out["before"], [[["0", "0%"], ["0", "0%"]], ["0", "0"], "Answers: 0"])
        self.assertEqual(out["after"], [[["75", "75%"], ["25", "25%"]], ["3", "1"], "Answers: 4"])
        self.assertEqual(out["sameCard"], [True, 1])
        self.assertEqual(out["messages"], ["m1", "?p2"])
        # The question is not drawn again for a count (what is ticked in it
        # stays), and holds none: the results are the Polls tab's.
        self.assertTrue(out["sameQuestion"])
        self.assertEqual(out["counted"], 0)
        # Where Chart.js did not load there is no ring and no legend: the
        # bars say everything.
        self.assertEqual(out["noRing"], [0, 0, 0])

    #: Chart.js as the page's script uses it, kept so a test can ask what it
    #: was handed.
    FAKE_CHART = """
          const charts = [];
          globalThis.Chart = class {
            constructor(canvas, config) {
              this.canvas = canvas; this.config = config; this.data = config.data;
              this.updates = 0; this.destroyed = false; charts.push(this);
            }
            update() { this.updates += 1; }
            destroy() { this.destroyed = true; }
          };
    """

    def test_a_polls_ring_is_drawn_in_the_polls_tab_and_keeps_its_canvas(self):
        """The owner, 2026-10-07: "poll results should also have pie chart"."""
        out = self.run_page(before=self.FAKE_CHART + """
          const soup = (a, b) => [{id: 1, label: "Soup", text: "", ballots: a},
                                  {id: 2, label: "<b>Salad</b>", text: "with bread", ballots: b}];
          config.feed = feedOf(4, [msg(1)], [
            pollRow(2, {total: 4, choices: soup(3, 1)}),
            pollRow(3),                                                  // nobody has answered
            pollRow(4, {visibility: "on_close", results_visible: false, total: null, choices: soup(null, null)})]);
        """, body="""
          const ring = (id) => card(id).querySelector("[data-forum-ring]");
          const dots = (id) => card(id).querySelectorAll("[data-forum-slice]")
            .map((d) => [d.dataset.forumSlice, d.style.backgroundColor]);
          // Messages is open: no ring is made yet; the legend's colours are there.
          out.shut = [charts.length, !!ring("p2"), dots("p2").length];
          mounted.showTab("polls");
          out.opened = [charts.length, !!ring("p2"), !!ring("p3"), !!ring("p4")];
          const chart = charts[0], canvas = ring("p2").querySelector("canvas");
          out.config = [chart.config.type, chart.config.options.cutout, chart.config.options.plugins.legend.display,
                        chart.config.options.responsive, chart.config.options.maintainAspectRatio,
                        chart.data.labels, chart.data.datasets[0].data, chart.data.datasets[0].backgroundColor,
                        chart.data.datasets[0].borderWidth];
          out.canvas = [chart.canvas === canvas, canvas.tagName, canvas.getAttribute("role"),
                        canvas.getAttribute("aria-label")];
          out.tip = chart.config.options.plugins.tooltip.callbacks.label(
            {chart: chart, datasetIndex: 0, dataIndex: 0, label: "Soup"});
          out.legend = [dots("p2"), dots("p3").length, dots("p4").length];
          out.bars = bars("p2");                                         // the bars stay beside it
          // An answer arrives: the same canvas, handed the new counts.
          const body = card("p2").querySelector("[data-forum-poll-body]");
          queue.push({status: 200, data: feedOf(5, [], [pollRow(2, {seq: 5, total: 5, choices: soup(3, 2)})])});
          await advance(5000);
          out.updated = [charts.length, chart.updates, chart.data.datasets[0].data,
                         ring("p2").querySelector("canvas") === canvas,
                         card("p2").querySelector("[data-forum-poll-body]") === body, ballots("p2")];
          // The first answer to the other poll: its ring is made.
          queue.push({status: 200, data: feedOf(6, [], [pollRow(3, {seq: 6, total: 1, choices: [
            {id: 1, label: "Soup", text: "", ballots: 1}, {id: 2, label: "Salad", text: "with bread", ballots: 0}]})])});
          await advance(5000);
          out.first = [charts.length, !!ring("p3"), charts[1].data.datasets[0].data, dots("p3").length];
          // What arrives while another tab is open is drawn when Polls is opened again.
          mounted.showTab("messages");
          queue.push({status: 200, data: feedOf(7, [], [pollRow(2, {seq: 7, total: 6, choices: soup(4, 2)})])});
          await advance(5000);
          out.away = [chart.updates, chart.data.datasets[0].data, ballots("p2")];
          mounted.showTab("polls");
          out.back = [chart.data.datasets[0].data, charts.length];
          // A removed poll takes its ring with its card.
          queue.push({status: 200, data: feedOf(8, [], [{id: "p2", number: 2, seq: 8, removed: true}])});
          await advance(5000);
          out.removed = [chart.destroyed, card("p2"), charts[1].destroyed, polls.children.length];
        """)
        self.assertEqual(out["shut"], [0, False, 2])
        self.assertEqual(out["opened"], [1, True, False, False])     # no answer, or no count to show: no ring
        # The vault's ring: a doughnut with a 60% hole. No legend of its own:
        # the options' list is the legend. An option's words are DATA.
        self.assertEqual(out["config"], ["doughnut", "60%", False, True, False, ["Soup", "<b>Salad</b>"],
                                         [3, 1], ["#3b82f6", "#ef4444"], 0])
        self.assertEqual(out["canvas"], [True, "CANVAS", "img", "Pie chart of the answers"])
        self.assertEqual(out["tip"], "Soup: 3 (75%)")
        self.assertEqual(out["legend"], [[["#3b82f6", "#3b82f6"], ["#ef4444", "#ef4444"]], 0, 0])
        self.assertEqual(out["bars"], [["75", "75%"], ["25", "25%"]])
        self.assertEqual(out["updated"], [1, 1, [3, 2], True, True, ["3", "2"]])
        self.assertEqual(out["first"], [2, True, [1, 0], 2])
        self.assertEqual(out["away"], [1, [3, 2], ["4", "2"]])       # the bars follow at once, the ring waits
        self.assertEqual(out["back"], [[4, 2], 2])
        self.assertEqual(out["removed"], [True, None, False, 2])

    def test_an_answer_is_chosen_and_submitted_in_the_conversation(self):
        """The owner, 2026-10-07: "once you answer you have to press submit
        button. The poll quarions box apears in the forum in the conversation
        not in polls tab. polls tab only hold results"."""
        out = self.run_page(before="config.feed = feedOf(3, [msg(1), msg(3)], [pollRow(2)]);", body="""
          const voted = pollRow(2, {seq: 4, total: 1, my_choice: 2,
            choices: [{id: 1, label: "Soup", text: "", ballots: 0},
                      {id: 2, label: "Salad", text: "with bread", ballots: 1}]});
          out.order = ids();                                    // where the poll was opened
          out.card = [card("p2").querySelectorAll("input").length,
                      card("p2").querySelectorAll("form").length,
                      card("p2").querySelectorAll("[data-forum-to-question]").length];
          const q = asked("p2"), send = q.querySelector("[data-forum-submit]");
          out.radios = radios("p2").map((r) => [r.type, r.value, !!r.checked]);
          out.text = q.textContent;
          out.off = send.disabled;
          q.querySelector("form").fire("submit");               // nothing is ticked: nothing is sent
          await settle();
          out.unsent = calls.length;
          radios("p2")[1].checked = true; radios("p2")[1].fire("change");
          await settle();
          out.ticked = [send.disabled, calls.length];           // a tick alone sends nothing
          queue.push({hold: true, status: 200, data: {poll: voted}});
          const pressed = q.querySelector("form").fire("submit");
          q.querySelector("form").fire("submit");               // a double press
          await settle();
          out.sent = [calls.length, pressed.defaultPrevented];
          held.shift()();
          await settle();
          out.call = calls[0];
          out.after = [asked("p2").dataset.state, radios("p2").length,
                       asked("p2").querySelectorAll("[data-forum-submit]").length,
                       asked("p2").querySelector("[data-forum-answered]").textContent];
          out.bars = bars("p2");
          out.yours = card("p2").querySelectorAll("[data-forum-yours]").length;
          out.cardAfter = card("p2").querySelectorAll("[data-forum-to-question]").length;
          const same = card("p2").serial;
          queue.push({status: 200, data: feedOf(4, [], [voted])});
          await advance(5000);
          out.fed = [polls.children.length, card("p2").serial === same, feeds(), ids()];
        """)
        self.assertEqual(out["order"], ["m1", "?p2", "m3"])
        # The Polls tab's card takes no answer: it leads to the question.
        self.assertEqual(out["card"], [0, 0, 1])
        self.assertEqual(out["radios"], [["radio", "1", False], ["radio", "2", False]])
        for words in ("Poll", "Ann", "One answer, final", "Lunch?", "Soup", "Salad", "with bread",
                      "Submit"):
            self.assertIn(words, out["text"])
        self.assertTrue(out["off"])
        self.assertEqual(out["unsent"], 0)
        self.assertEqual(out["ticked"], [False, 0])
        self.assertEqual(out["sent"], [1, True])
        self.assertTrue(out["call"]["url"].endswith("/polls/p2/vote/"))
        self.assertEqual((out["call"]["method"], out["call"]["body"]), ("POST", {"choice": 2}))
        # Answered: the question stays where it stood and says the answer;
        # nothing in it can be chosen or sent again.
        self.assertEqual(out["after"][:3], ["answered", 0, 0])
        self.assertIn("Salad", out["after"][3])
        self.assertNotIn("Soup", out["after"][3])
        self.assertEqual(out["bars"], [["0", "0%"], ["100", "100%"]])
        self.assertEqual(out["yours"], 1)
        self.assertEqual(out["cardAfter"], 0)
        self.assertEqual(out["fed"], [1, True, ["?after=3"], ["m1", "?p2", "m3"]])

    def test_a_refused_answer_says_why_and_may_be_sent_again(self):
        out = self.run_page(before="config.feed = feedOf(2, [], [pollRow(2)]);", body="""
          const q = asked("p2"), send = q.querySelector("[data-forum-submit]");
          radios("p2")[0].checked = true; radios("p2")[0].fire("change");
          queue.push({status: 409, data: {error: "This poll is closed. No more answers can be recorded."}});
          q.querySelector("form").fire("submit");
          await settle();
          out.refused = [status(), send.disabled, asked("p2").dataset.state,
                         radios("p2").map((r) => !!r.checked)];
          queue.push({network: true});
          q.querySelector("form").fire("submit");
          await settle();
          out.offline = [status()[0], send.disabled, calls.length];
        """)
        self.assertEqual(out["refused"], [["This poll is closed. No more answers can be recorded.",
                                           False], False, "asked", [True, False]])
        self.assertEqual(out["offline"], ["The request failed. Try again.", False, 2])

    def test_an_answered_a_closed_and_a_withheld_poll(self):
        out = self.run_page(before="""
          config.feed = feedOf(4, [], [
            pollRow(2, {my_choice: 1, total: 1,
                        choices: [{id: 1, label: "Soup", text: "", ballots: 1}, {id: 2, label: "Salad", text: "", ballots: 0}]}),
            pollRow(3, {open: false, status: "closed"}),
            pollRow(4, {visibility: "on_close", results_visible: false, total: null, may_manage: true,
                        choices: [{id: 1, label: "Soup", text: "", ballots: null}, {id: 2, label: "Salad", text: "", ballots: null}]})]);
        """, body="""
          out.order = polls.children.map((c) => c.dataset.pollId);      // the newest first
          out.questions = ids();                                        // the open ones, as they were opened
          out.states = [asked("p2").dataset.state, asked("p3"), asked("p4").dataset.state];
          out.answered = [radios("p2").length, asked("p2").querySelector("[data-forum-answered]").textContent];
          out.closed = [card("p3").dataset.open, card("p3").all().filter((n) => n.tagName === "BUTTON").length];
          out.hidden = [bars("p4").length, card("p4").querySelector("[data-forum-total]").textContent,
                        radios("p4").length];
          out.tools = card("p4").all().filter((n) => n.tagName === "BUTTON").map((b) => b.textContent);
          out.noTools = card("p2").all().filter((n) => n.tagName === "BUTTON").length;
          // The poll closes: its question leaves the conversation, its card stays.
          queue.push({status: 200, data: feedOf(6, [], [pollRow(4, {seq: 6, open: false, status: "closed",
            may_manage: true})])});
          await advance(5000);
          out.afterClose = [ids(), polls.children.length, card("p4").dataset.open];
          // A removed one takes both.
          queue.push({status: 200, data: feedOf(7, [], [{id: "p2", number: 2, seq: 7, removed: true}])});
          await advance(5000);
          out.afterRemoval = [ids(), polls.children.map((c) => c.dataset.pollId),
                              part("empty").classList.contains("hidden")];
        """)
        self.assertEqual(out["order"], ["p4", "p3", "p2"])
        self.assertEqual(out["questions"], ["?p2", "?p4"])
        self.assertEqual(out["states"], ["answered", None, "asked"])
        self.assertEqual(out["answered"][0], 0)
        self.assertIn("Soup", out["answered"][1])
        self.assertEqual(out["closed"], ["", 0])
        self.assertEqual(out["hidden"], [0, "The count appears when the poll closes.", 2])
        self.assertEqual(out["tools"], ["Answer in the conversation", "Close", "Remove"])
        self.assertEqual(out["noTools"], 0)
        self.assertEqual(out["afterClose"], [["?p2"], 3, ""])
        self.assertEqual(out["afterRemoval"], [[], ["p4", "p3"], False])

    def test_the_results_lead_to_the_question_and_the_question_to_the_results(self):
        out = self.run_page(before="config.feed = feedOf(2, [msg(1)], [pollRow(2)]);", body="""
          const panel = (name) => box.querySelector('[data-forum-tabpanel="' + name + '"]');
          const shown = () => ["messages", "polls"].filter((n) => !panel(n).classList.contains("hidden"));
          out.first = shown();
          asked("p2").querySelector("[data-forum-to-results]").click();
          out.results = shown();
          card("p2").querySelector("[data-forum-to-question]").click();
          out.back = shown();
          out.asked = calls.length;
        """)
        self.assertEqual((out["first"], out["results"], out["back"]),
                         (["messages"], ["polls"], ["messages"]))
        self.assertEqual(out["asked"], 0)

    def test_a_question_that_arrives_while_reading_further_up_is_announced(self):
        out = self.run_page(before="config.feed = feedOf(2, [msg(1), msg(2)]);", body="""
          scroll.scrollHeight = 2000; scroll.clientHeight = 400; scroll.scrollTop = 300;
          scroll.fire("scroll");                                 // reading further up
          queue.push({status: 200, data: feedOf(3, [], [pollRow(3)])});
          await advance(5000);
          out.arrived = [ids(), scroll.scrollTop, part("new").classList.contains("hidden")];
          queue.push({status: 200, data: feedOf(4, [], [pollRow(3, {seq: 4, total: 1})])});
          part("new").classList.add("hidden");
          await advance(5000);
          out.counted = part("new").classList.contains("hidden");   // a count is no news down there
        """)
        self.assertEqual(out["arrived"], [["m1", "m2", "?p3"], 300, False])
        self.assertTrue(out["counted"])

    def test_create_poll_opens_a_dialog_and_the_poll_is_opened_there(self):
        """The owner, 2026-10-07: "polls subtab in a channel shall have
        "create poll" button which will open the modal, open poll will be
        located there not below send message"."""
        out = self.run_page(before="config.feed = feedOf(1, [msg(1)]);", body="""
          const dialog = part("poll-dialog"), form = part("poll-form"), error = part("poll-error");
          const told = () => (dialog.sent || []).map((e) => [e.type, e.open]);
          const field = (name) => form.querySelector("[name=" + name + "]");
          out.closed = [dialog.dataset.open || "", told()];
          part("poll-create").click();
          out.opened = [dialog.dataset.open, told(), calls.length];
          // Left by Escape, the backdrop, the X or Cancel: closed, and what was typed is kept.
          field("title").value = "Where?";
          dialog.fire("forum-poll-dismiss");
          out.left = [dialog.dataset.open, told().map((t) => t[1]), field("title").value];
          dialog.fire("forum-poll-dismiss");                        // closed already: nothing is told again
          out.leftTwice = told().length;
          part("poll-create").click();
          field("options").value = "Here\\nThere";
          field("closes_at").value = "2030-01-02T03:04";
          // The channel is full: the door's sentence is said in the dialog, which stays, with what was typed.
          queue.push({status: 400, data: {error: "This channel already has 3 open polls. Close one before opening another."}});
          form.fire("submit");
          await settle();
          out.full = [error.textContent, error.classList.contains("hidden"), dialog.dataset.open,
                      field("title").value, status(), polls.children.length];
          queue.push({network: true});
          form.fire("submit");
          await settle();
          out.offline = [error.textContent, dialog.dataset.open];
          queue.push({hold: true, status: 201, data: {poll: pollRow(2, {title: "Where?", mine: true})}});
          const pressed = form.fire("submit");
          form.fire("submit");                                      // a double press
          await settle();
          out.sent = [calls.length, error.classList.contains("hidden"), pressed.defaultPrevented];
          held.shift()();
          await settle();
          const call = calls[calls.length - 1];
          out.call = [call.url.endsWith("/polls/open/"), call.method, call.body.title, call.body.options,
                      "revisability" in call.body, call.body.visibility,
                      call.body.closes_at === new Date("2030-01-02T03:04").toISOString()];
          out.done = [dialog.dataset.open, told()[told().length - 1], error.classList.contains("hidden")];
          out.cards = polls.children.map((c) => c.dataset.pollId);
          out.conversation = ids();
          out.cleared = [field("title").value, field("options").value, field("closes_at").value];
          part("poll-create").click();                              // opened again: empty, and no old sentence
          out.again = [dialog.dataset.open, error.textContent, field("title").value];
        """)
        self.assertEqual(out["closed"], ["", []])
        # Opening the dialog asks the server nothing.
        self.assertEqual(out["opened"], ["1", [["forum-poll-dialog", True]], 0])
        self.assertEqual(out["left"], ["", [True, False], "Where?"])
        self.assertEqual(out["leftTwice"], 2)
        # A refusal is said in the dialog and not on the status line, which
        # is under another tab.
        self.assertEqual(out["full"], ["This channel already has 3 open polls. "
                                       "Close one before opening another.", False, "1", "Where?",
                                       ["", True], 0])
        self.assertEqual(out["offline"], ["The request failed. Try again.", "1"])
        self.assertEqual(out["sent"], [3, True, True])
        # No word of changeable answers is sent: there is no such poll.
        self.assertEqual(out["call"], [True, "POST", "Where?", "Here\nThere", False, "live", True])
        # Opened: the dialog closes, the card is in the tab and the question in the conversation.
        self.assertEqual(out["done"], ["", ["forum-poll-dialog", False], True])
        self.assertEqual(out["cards"], ["p2"])
        self.assertEqual(out["conversation"], ["m1", "?p2"])
        self.assertEqual(out["cleared"], ["", "", ""])
        self.assertEqual(out["again"], ["1", "", ""])


class PagePostingTests(PageCase):
    def test_an_own_post_is_drawn_at_once_and_the_next_answer_does_not_draw_it_again(self):
        out = self.run_page(before="config.feed = feedOf(1, [msg(1)]);", body="""
          const mine = msg(2, {text: "hello there", mine: true, may_remove: true, sender: "Mem"});
          await type("hello there");
          queue.push({status: 201, data: {message: mine, replay: false}});
          await submit();
          out.post = [calls[0].url.endsWith("/post/"), calls[0].method, calls[0].body.text,
                      /^[0-9a-f-]{36}$/.test(calls[0].body.op), calls[0].headers["X-CSRFToken"].length > 10];
          out.ids = ids();
          out.box = part("text").value;
          out.cursor = mounted.state.cursor;             // the post's answer moves no cursor
          out.mine = node("m2").dataset.mine;
          const same = node("m2").serial;
          queue.push({status: 200, data: feedOf(2, [mine])});
          await advance(5000);
          out.fed = [ids(), node("m2").serial === same, feeds(), mounted.state.cursor];
        """)
        self.assertEqual(out["post"], [True, "POST", "hello there", True, True])
        self.assertEqual(out["ids"], ["m1", "m2"])
        self.assertEqual((out["box"], out["cursor"], out["mine"]), ("", 1, "1"))
        self.assertEqual(out["fed"], [["m1", "m2"], True, ["?after=1"], 2])

    def test_a_refusal_keeps_what_was_typed_and_shows_the_doors_sentence(self):
        out = self.run_page(before="config.feed = feedOf(1, [msg(1)]);", body="""
          await type("too dear");
          queue.push({status: 402, data: {error: "Not enough mana: this post costs 3."}});
          await submit();
          out.refused = [part("text").value, status(), ids(), part("send").disabled];
          out.bad = part("status").dataset.bad;
        """)
        self.assertEqual(out["refused"], ["too dear", ["Not enough mana: this post costs 3.", False],
                                          ["m1"], False])
        self.assertEqual(out["bad"], "1")

    def test_a_press_never_answered_is_sent_again_under_its_op_and_a_changed_one_is_new(self):
        out = self.run_page(before="config.feed = feedOf(1, [msg(1)]);", body="""
          await type("once");
          queue.push({network: true}, {status: 502, notJson: true},
                     {status: 400, data: {error: "no"}}, {network: true},
                     {status: 409, data: {error: "used"}});
          await submit(); await submit(); await submit();
          out.same = [calls[0].body.op === calls[1].body.op, calls[1].body.op === calls[2].body.op];
          await submit();                         // after an answer: a new press
          out.fresh = calls[3].body.op !== calls[2].body.op;
          await type("twice");                    // never answered, but the text changed
          await submit();
          out.changed = calls[4].body.op !== calls[3].body.op;
          out.kept = part("text").value;
        """)
        self.assertEqual(out["same"], [True, True])
        self.assertTrue(out["fresh"])
        self.assertTrue(out["changed"])
        self.assertEqual(out["kept"], "twice")

    def test_one_post_at_a_time_and_an_empty_one_is_not_sent(self):
        out = self.run_page(before="config.feed = feedOf(1, [msg(1)]);", body="""
          await submit();
          await type("   ");
          await submit();
          out.empty = calls.length;
          await type("slow");
          queue.push({hold: true, status: 201, data: {message: msg(2, {mine: true})}});
          await submit(); await submit();
          part("text").fire("keydown", {key: "Enter", shiftKey: false});
          await settle();
          out.sent = calls.length;
          out.busy = [part("send").disabled, status()[0]];
          held.shift()();
          await settle();
          out.done = [part("send").disabled, status(), ids()];
          // Enter sends; Shift+Enter does not.
          await type("by key");
          part("text").fire("keydown", {key: "Enter", shiftKey: true});
          await settle();
          out.shift = calls.length;
          queue.push({status: 201, data: {message: msg(3, {mine: true})}});
          const pressed = part("text").fire("keydown", {key: "Enter", shiftKey: false});
          await settle();
          out.enter = [calls.length, pressed.defaultPrevented, calls[1].body.text];
        """)
        self.assertEqual(out["empty"], 0)
        self.assertEqual(out["sent"], 1)
        self.assertEqual(out["busy"], [True, "Sending…"])
        self.assertEqual(out["done"], [False, ["", True], ["m1", "m2"]])
        self.assertEqual(out["shift"], 1)
        self.assertEqual(out["enter"], [2, True, "by key"])

    def test_a_picture_goes_with_the_post_and_a_wrong_or_large_file_is_refused_here(self):
        out = self.run_page(before="config.feed = feedOf(1, [msg(1)]);", body="""
          await choose("notes.pdf", 10, "application/pdf");
          out.wrong = [status()[0], part("image").files.length];
          await choose("huge.png", config.limits.image_bytes + 1);
          out.large = [status()[0], part("image").files.length];
          await choose("harbour.png", 2048);
          out.picked = [part("picked-name").textContent, part("picked").classList.contains("hidden"), status()];
          queue.push({status: 201, data: {message: msg(2, {kind: "image", text: "",
            image: {url: "/forum/guild/messages/m2/image/", mime: "image/png", size: 2048}})}});
          await submit();
          out.sent = calls[0].body;
          out.after = [part("image").files.length, part("picked").classList.contains("hidden")];
          await choose("again.png", 100);
          part("unpick").click();
          out.unpicked = [part("image").files.length, part("picked").classList.contains("hidden")];
        """)
        self.assertEqual(out["wrong"], ["Send a JPEG, PNG, GIF or WebP image.", 0])
        self.assertEqual(out["large"], ["The image is too large. The most is 10 MB.", 0])
        self.assertEqual(out["picked"], ["harbour.png · 2 KB", False, ["", True]])
        self.assertEqual(out["sent"]["image"], {"name": "harbour.png", "size": 2048})
        self.assertEqual(out["sent"]["text"], "")
        self.assertEqual(out["after"], [0, True])
        self.assertEqual(out["unpicked"], [0, True])

    def test_removing_a_message_asks_first_and_draws_the_answer(self):
        out = self.run_page(before="config.feed = feedOf(2, [msg(1), msg(2, {mine: true, may_remove: true})]);",
                            body="""
          out.buttons = [node("m1").all().filter((n) => n.tagName === "BUTTON").length,
                         node("m2").all().filter((n) => n.tagName === "BUTTON").length];
          queue.push({status: 200, data: {message: {id: "m2", number: 2, seq: 3, removed: true}}});
          node("m2").all().filter((n) => n.tagName === "BUTTON")[0].click();
          await settle();
          out.confirms = confirms;
          out.call = [calls[0].url.endsWith("/messages/m2/remove/"), calls[0].method];
          out.ids = ids();
          queue.push({status: 200, data: feedOf(3, [{id: "m2", number: 2, seq: 3, removed: true}])});
          await advance(5000);
          out.fed = ids();
        """)
        self.assertEqual(out["buttons"], [0, 1])
        self.assertEqual(out["confirms"], 1)
        self.assertEqual(out["call"], [True, "POST"])
        self.assertEqual(out["ids"], ["m1", "gone"])
        self.assertEqual(out["fed"], ["m1", "gone"])


class PageEstimateTests(PageCase):
    DOOR = 'config.urls.estimate = "/forum/guild/estimate/"; config.feed = feedOf(1, [msg(1)]);'

    def test_the_estimate_is_asked_after_the_typing_stops_and_shown_beside_send(self):
        out = self.run_page(before=self.DOOR, body="""
          queue.push({status: 200, data: {amount: "0.0021", text: "0.0021", image: "0", affordable: true,
                                          balance: "10", display: "0.0021 mana"}});
          await type("z"); await advance(200);
          await type("za"); await advance(200);
          await type("zażółć"); await advance(399);
          out.before = [calls.length, estimate()];
          await advance(1);
          out.call = [calls.length, calls[0].url, calls[0].method, calls[0].body];
          out.shown = [estimate(), part("send").disabled];
          // A picture is asked at once, with the text there is.
          queue.push({status: 200, data: {affordable: true, display: "0.5 mana"}});
          await choose("harbour.png", 4096);
          out.picture = [calls[1].body, estimate()[0]];
          // Emptied, nothing is asked and nothing is shown.
          part("unpick").click();
          await type("");
          await advance(1000);
          out.empty = [calls.filter((c) => c.url.indexOf("estimate") >= 0).length, estimate()];
        """)
        self.assertEqual(out["before"], [0, ["", True]])
        self.assertEqual(out["call"], [1, "/forum/guild/estimate/", "POST",
                                       {"text_bytes": str(len("zażółć".encode())), "image_bytes": "0"}])
        self.assertEqual(out["shown"], [["This will cost 0.0021 mana.", False], False])
        self.assertEqual(out["picture"], [{"text_bytes": str(len("zażółć".encode())),
                                           "image_bytes": "4096"}, "This will cost 0.5 mana."])
        self.assertEqual(out["empty"][1], ["", True])
        self.assertEqual(out["empty"][0], 3)      # the two above, and the picture taken away

    def test_a_balance_that_does_not_cover_it_switches_send_off(self):
        out = self.run_page(before=self.DOOR, body="""
          queue.push({status: 200, data: {affordable: false, balance: "0.001", display: "3 mana"}});
          await type("a long letter"); await advance(400);
          out.dear = [estimate(), part("estimate").dataset.bad, part("send").disabled];
          await submit();
          part("text").fire("keydown", {key: "Enter", shiftKey: false});
          await settle();
          out.posts = calls.filter((c) => c.url.indexOf("/post/") >= 0).length;
          queue.push({status: 200, data: {affordable: true, display: "0.1 mana"}});
          await type("short"); await advance(400);
          out.cheap = [estimate(), part("send").disabled];
          // The door refusing, or failing, shows nothing and blocks nothing.
          queue.push({status: 402, data: {error: "The forum is not part of your plan."}});
          await type("shorter"); await advance(400);
          out.refused = [estimate(), part("send").disabled];
          // Where nothing is priced the door answers an empty amount: nothing is said.
          queue.push({status: 200, data: {amount: "0", affordable: true, balance: null, display: ""}});
          await type("free"); await advance(400);
          out.free = [estimate(), part("send").disabled];
          queue.push({network: true});
          await type("shortest"); await advance(400);
          out.failed = [estimate(), part("send").disabled, status()];
        """)
        self.assertEqual(out["dear"], [["This will cost 3 mana. You do not have enough mana for this.",
                                        False], "1", True])
        self.assertEqual(out["posts"], 0)
        self.assertEqual(out["cheap"], [["This will cost 0.1 mana.", False], False])
        self.assertEqual(out["refused"], [["", True], False])
        self.assertEqual(out["free"], [["", True], False])
        self.assertEqual(out["failed"], [["", True], False, ["", True]])

    def test_without_the_door_nothing_is_asked_shown_or_broken(self):
        out = self.run_page(before="delete config.urls.estimate; delete config.prices; "
                                   "config.feed = feedOf(1, [msg(1)]);", body="""
          await type("hello"); await advance(1000);
          await choose("harbour.png", 2048); await advance(1000);
          out.asked = calls.length;
          out.shown = [estimate(), part("send").disabled];
          queue.push({status: 201, data: {message: msg(2, {mine: true})}});
          await submit();
          out.posted = [calls.length, ids()];
        """)
        self.assertEqual(out["asked"], 0)
        self.assertEqual(out["shown"], [["", True], False])
        self.assertEqual(out["posted"], [1, ["m1", "m2"]])

    def test_a_post_clears_the_estimate(self):
        out = self.run_page(before=self.DOOR, body="""
          queue.push({status: 200, data: {affordable: true, display: "1 mana"}});
          await type("hello"); await advance(400);
          out.shown = estimate();
          queue.push({status: 201, data: {message: msg(2, {mine: true})}});
          await submit();
          out.after = [estimate(), part("send").disabled];
          await advance(1000);
          out.doors = calls.map((c) => c.url.split("/").slice(-2)[0]);
        """)
        self.assertEqual(out["shown"], ["This will cost 1 mana.", False])
        self.assertEqual(out["after"], [["", True], False])
        self.assertEqual(out["doors"], ["estimate", "post"])


class PageNarrowTests(PageCase):
    def test_the_channels_card_folds_behind_the_headers_button(self):
        out = self.run_page(before="config.feed = feedOf(1, [msg(1)]);", body="""
          const panel = (name) => box.querySelector('[data-forum-panel="' + name + '"]');
          const knob = (name) => box.querySelector('[data-forum-toggle="' + name + '"]');
          const open = () => [!panel("channels").classList.contains("hidden"),
                              panel("channels").classList.contains("flex"),
                              knob("channels").getAttribute("aria-expanded")];
          out.start = open();
          knob("channels").click(); out.channels = open();
          knob("channels").click(); out.shut = open();
          // Polls are a tab of the channel now: no side card and no button for one.
          out.noPolls = [panel("polls"), knob("polls")];
          // On a wide screen the channels are a column whatever the button did.
          out.wide = panel("channels").classList.contains("lg:flex");
          out.links = panel("channels").all().filter((n) => n.tagName === "A").map((a) => a.attrs.href);
        """)
        self.assertEqual(out["start"], [False, False, "false"])
        self.assertEqual(out["channels"], [True, True, "true"])
        self.assertEqual(out["shut"], [False, False, "false"])
        self.assertEqual(out["noPolls"], [None, None])
        self.assertTrue(out["wide"])
        self.assertIn(self.url("channel_detail"), out["links"])

    def test_the_tabs_change_the_panel_and_search_and_images_ask_their_doors(self):
        """The owner, 2026-10-07: "polls and settings have to be TABS", "the
        3rd tab is search", "4th tab is images"."""
        out = self.run_page(before="config.feed = feedOf(1, [msg(1)]);", body="""
          const tab = (name) => box.querySelector('[data-forum-tab="' + name + '"]');
          const pane = (name) => box.querySelector('[data-forum-tabpanel="' + name + '"]');
          const NAMES = ["messages", "polls", "search", "images"];
          const open = () => NAMES.filter((name) => !pane(name).classList.contains("hidden"));
          const chosen = () => NAMES.filter((name) => tab(name).getAttribute("aria-selected") === "true");
          out.start = [open(), chosen(), mounted.tab()];
          const before = calls.length;
          tab("polls").click();
          out.polls = [open(), chosen(), mounted.tab(), calls.length - before];
          tab("search").click();
          out.search = [open(), chosen()];
          // A search is asked for with the form, and what is found is drawn as text.
          queue.push({status: 200, data: {query: "text", capped: true, scanned: 2, messages: [
            msg(7, {text: "<b>seven</b>"}), msg(3)]}});
          part("search-q").value = "  text  ";
          part("search").fire("submit", {preventDefault() {}});
          await settle();
          const hit = calls[calls.length - 1];
          out.asked = [hit.method, hit.url.split("/search/")[1]];
          const found = part("search-results").children;
          out.found = [found.length, found[0].querySelector("[data-forum-body]").textContent,
                       part("search-note").textContent.indexOf("2") >= 0,
                       part("search-note").classList.contains("hidden")];
          out.listUntouched = ids();
          // A refusal is said in the tab.
          queue.push({status: 400, data: {error: "Type at least 2 characters to search for."}});
          part("search-q").value = "x";
          part("search").fire("submit", {preventDefault() {}});
          await settle();
          out.refused = [part("search-results").children.length, part("search-note").textContent];
          // The Images tab asks its door when it is opened, and again for older ones.
          queue.push({status: 200, data: {more: true, oldest: 4, messages: [
            msg(9, {kind: "image", image: {url: "/forum/guild/messages/m9/image/", mime: "image/png", size: 5}}),
            msg(4, {kind: "image", image: {url: "/forum/guild/messages/m4/image/", mime: "image/png", size: 5}})]}});
          tab("images").click();
          await settle();
          const cells = part("images").children;
          out.images = [open(), calls[calls.length - 1].url.split("/images/")[1], cells.length,
                        cells[0].attrs.href || cells[0].href,
                        part("images-older").classList.contains("hidden"),
                        part("images-empty").classList.contains("hidden")];
          queue.push({status: 200, data: {more: false, oldest: 2, messages: [
            msg(2, {kind: "image", image: {url: "/forum/guild/messages/m2/image/", mime: "image/png", size: 5}})]}});
          part("images-older").click();
          await settle();
          out.older = [calls[calls.length - 1].url.split("/images/")[1], part("images").children.length,
                       part("images-older").classList.contains("hidden")];
          tab("messages").click();
          out.back = [open(), chosen()];
        """)
        self.assertEqual(out["start"], [["messages"], ["messages"], "messages"])
        self.assertEqual(out["polls"], [["polls"], ["polls"], "polls", 0], "a tab asks the server nothing")
        self.assertEqual(out["search"], [["search"], ["search"]])
        self.assertEqual(out["asked"], ["GET", "?q=text"])
        self.assertEqual(out["found"], [2, "<b>seven</b>", True, False])
        self.assertEqual(out["listUntouched"], ["m1"])
        self.assertEqual(out["refused"], [0, "Type at least 2 characters to search for."])
        self.assertEqual(out["images"], [["images"], "", 2, "/forum/guild/messages/m9/image/", False, True])
        self.assertEqual(out["older"], ["?before=4", 3, True])
        self.assertEqual(out["back"], [["messages"], ["messages"]])


class ScriptSourceTests(SimpleTestCase):
    """What the script may not hold, read as text (no node needed)."""

    def code(self):
        source = Path(finders.find("forum/channel.js")).read_text(encoding="utf-8")
        return source, re.sub(r"/\*.*?\*/", "", source, flags=re.S)

    def test_short_polling_and_nothing_else(self):
        _source, code = self.code()
        for word in ("WebSocket", "EventSource", "setInterval", "localStorage", "sessionStorage",
                     "indexedDB", "document.cookie", "sendBeacon", "XMLHttpRequest", "keepalive",
                     "ServiceWorker", "BroadcastChannel", "SharedWorker"):
            self.assertNotIn(word, code, word)
        self.assertIn("setTimeout", code)
        self.assertIn("visibilitychange", code)
        self.assertEqual(code.count("root.setTimeout("), 1)      # one way to set a timer
        for line in code.splitlines():                           # every comment is a block
            self.assertFalse(line.strip().startswith("//"), line)

    def test_everything_is_written_as_text(self):
        _source, code = self.code()
        for word in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                     "new Function", "DOMParser", "createContextualFragment", "srcdoc",
                     "createObjectURL"):
            self.assertNotIn(word, code, word)
        self.assertIn("textContent", code)
        self.assertIn("createTextNode", code)
        # A picture's address is the feed's own: the two places an <img> gets
        # one are a message's image and a sender's avatar.
        self.assertEqual(re.findall(r"\.src = ([\w.]+);", code),
                         ["row.avatar", "row.image.url", "row.image.url"])     # the third: an Images cell
        # A link's address is made by the one rule, and a picture's door is the feed's.
        self.assertEqual(sorted(re.findall(r"\.href = ([\w.]+);", code)),
                         ["part.href", "row.image.url", "row.image.url"])

    def test_every_class_is_written_whole(self):
        """The stylesheet is built from what it can read: no class is glued
        from pieces, and a dark colour always comes with the page's box."""
        source, _code = self.code()
        self.assertNotRegex(source, r"[a-z0-9]-[\"'] *\+")
        for variant in re.findall(r"group-data-\[[^\]]*\](?:/\w+)?:", source):
            self.assertEqual(variant, "group-data-[forum-theme=dark]/forum:")

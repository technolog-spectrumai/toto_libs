"""Focus stays inside an open modal (2026-10-01, todo 29.10).

``oya/focus_trap.js`` — a small trap shaped like Alpine's Focus plugin's
``x-trap`` (the plugin is not vendored) — run in node against a page faked
just far enough: focus goes in when a modal opens, Tab and Shift+Tab go round
at its ends, focus that leaves is brought back, and it returns to the opener
when the modal closes; traps stack; the Alpine directive follows its
expression. Then the wiring: oya/base.html loads it before Alpine starts, and
the vault's and the socialhub's modals wear it and close on Escape.
"""

import json
import re
import shutil
import subprocess
from unittest import skipUnless

from django.contrib.staticfiles import finders
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import SimpleTestCase

_NODE = shutil.which("node")

_HARNESS = r"""
const script = process.argv[1];
const listeners = {};
const fire = (type, event) => (listeners[type] || []).forEach((fn) => fn(event));
const NATIVE = new Set(["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA"]);

class El {
  constructor(id, tag, opts = {}) {
    Object.assign(this, {id, tagName: tag.toUpperCase(), children: [], parentNode: null,
                         attrs: {}, type: "", name: "", checked: false, disabled: false,
                         hidden: false, visibility: "visible"}, opts);
  }
  get tabIndex() {
    if ("tabindex" in this.attrs) return Number(this.attrs.tabindex);
    return NATIVE.has(this.tagName) ? 0 : -1;
  }
  append(...kids) { for (const k of kids) { k.parentNode = this; this.children.push(k); } return this; }
  all() { const out = []; const walk = (n) => n.children.forEach((c) => { out.push(c); walk(c); }); walk(this); return out; }
  candidate() { return NATIVE.has(this.tagName) || "tabindex" in this.attrs; }
  querySelectorAll() { return this.all().filter((e) => e.candidate()); }
  querySelector(selector) {
    if (selector !== "[autofocus]") throw new Error("unexpected selector " + selector);
    return this.all().find((e) => "autofocus" in e.attrs) || null;
  }
  getClientRects() { for (let n = this; n; n = n.parentNode) if (n.hidden) return []; return [{}]; }
  contains(other) { for (let n = other; n; n = n.parentNode) if (n === this) return true; return false; }
  hasAttribute(name) { return name in this.attrs; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  focus() {
    if (!this.getClientRects().length || this.visibility === "hidden" || this.disabled) return;
    if (!this.candidate()) return;
    document.activeElement = this;
    fire("focusin", {target: this});
  }
}

const el = (id, tag, opts) => new El(id, tag, opts);
const html = el("html", "html"), body = el("body", "body");
const opener = el("opener", "button"), before = el("before", "button"), after = el("after", "a");
const modal = el("modal", "div"), close = el("close", "button"), field = el("name", "input");
const wrap = el("wrap", "div", {hidden: true}), ghost = el("ghost", "button");
const kindA = el("kindA", "input", {type: "radio", name: "kind", checked: true});
const kindB = el("kindB", "input", {type: "radio", name: "kind"});
const secret = el("secret", "input", {type: "hidden"});
const off = el("off", "button", {disabled: true});
const dim = el("dim", "button", {visibility: "hidden"});
const skip = el("skip", "button", {attrs: {tabindex: "-1"}});
const save = el("save", "button");
const confirm = el("confirm", "div"), yes = el("yes", "button"), no = el("no", "button");
const empty = el("empty", "div");
wrap.append(ghost);
modal.append(close, field, wrap, kindA, kindB, secret, off, dim, skip, save);
confirm.append(yes, no);
html.append(body.append(before, opener, modal, after, confirm, empty));

globalThis.window = globalThis;
globalThis.getComputedStyle = (node) => ({visibility: node.visibility});
globalThis.document = {
  activeElement: body, body,
  addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  removeEventListener() {},
  contains(node) { return html.contains(node); },
};
require(script);

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const now = () => (document.activeElement ? document.activeElement.id : null);
function press(shift) {
  const event = {key: "Tab", shiftKey: !!shift, defaultPrevented: false,
                 preventDefault() { this.defaultPrevented = true; }};
  fire("keydown", event);
  if (event.defaultPrevented) return "trapped";
  // The browser's own Tab: the next control of the whole page.
  const order = html.all().filter((e) => e.candidate() && e.tabIndex >= 0 && !e.disabled
                                   && e.getClientRects().length && e.visibility !== "hidden");
  const at = order.indexOf(document.activeElement);
  const next = order[shift ? (at < 0 ? order.length - 1 : at - 1) : at + 1];
  if (next) next.focus();
  return "native";
}

(async () => {
  const out = {};
  out.list = totoFocusTrap.tabbables(modal).map((e) => e.id);

  opener.focus();
  const trap = totoFocusTrap(modal).activate();
  await wait(80);
  out.entered = now();
  save.focus(); out.tabFromLast = [press(false), now()];
  close.focus(); out.shiftTabFromFirst = [press(true), now()];
  field.focus(); out.tabInside = [press(false), now()];
  close.focus(); press(false);                 // forwards again
  after.focus(); out.leakForward = now();
  close.focus(); press(true);                  // to the last, backwards
  before.focus(); out.leakBack = now();
  trap.deactivate();
  out.closed = now();

  opener.focus(); trap.activate(); field.focus();   // the modal's own $nextTick
  await wait(80);
  out.kept = now();
  trap.deactivate();

  save.setAttribute("autofocus", "");
  opener.focus(); trap.activate(); await wait(80);
  out.autofocus = now();
  trap.deactivate();
  delete save.attrs.autofocus;

  modal.hidden = true;
  opener.focus(); trap.activate(); await wait(80);
  out.notDrawn = now();
  modal.hidden = false; await wait(80);
  out.drawn = now();
  trap.deactivate();

  opener.focus(); trap.activate(); await wait(80);
  field.focus();
  const inner = totoFocusTrap(confirm).activate(); await wait(80);
  out.innerEntered = now();
  no.focus(); press(false); out.innerTab = now();
  close.focus(); out.innerLeak = now();
  inner.deactivate();
  out.innerClosed = now();
  save.focus(); press(false); out.outerAgain = now();
  trap.deactivate();

  opener.focus();
  const bare = totoFocusTrap(empty).activate(); await wait(80);
  out.bare = [now(), empty.attrs.tabindex || null];
  out.bareTab = [press(false), now()];
  bare.deactivate();
  out.bareClosed = now();

  const scope = {open: false};
  const effects = [], cleanups = [];
  const Alpine = {directive(name, fn) { this.registered = {name, fn}; }};
  globalThis.Alpine = Alpine;
  fire("alpine:init", {});
  out.directive = Alpine.registered.name;
  Alpine.registered.fn(modal, {expression: "open", modifiers: []}, {
    evaluateLater: (expression) => (callback) => callback(scope[expression]),
    effect: (fn) => { effects.push(fn); fn(); },
    cleanup: (fn) => cleanups.push(fn),
  });
  const run = () => effects.forEach((fn) => fn());
  opener.focus();
  scope.open = true; run(); await wait(80);
  out.alpineOpen = now();
  save.focus(); out.alpineTab = [press(false), now()];
  run();                                       // the same value again: nothing moves
  out.alpineSame = now();
  scope.open = false; run();
  out.alpineClosed = now();
  scope.open = true; run(); await wait(80);
  cleanups.forEach((fn) => fn());
  out.afterCleanup = [now(), press(false)];

  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(2); });
"""


@skipUnless(_NODE, "node is not installed")
class TrapBehaviourTests(SimpleTestCase):
    """oya/focus_trap.js as the page ships it, in node."""

    _out = None

    def setUp(self):
        if TrapBehaviourTests._out is None:
            script = finders.find("oya/focus_trap.js")
            self.assertIsNotNone(script, "oya/focus_trap.js is not a static file")
            done = subprocess.run([_NODE, "-e", _HARNESS, script],
                                  capture_output=True, text=True, timeout=60)
            if done.returncode != 0:
                raise AssertionError(f"node failed: {done.stderr}")
            TrapBehaviourTests._out = json.loads(done.stdout)
        self.out = TrapBehaviourTests._out

    def test_tab_reaches_drawn_enabled_controls_and_one_radio_of_a_ticked_group(self):
        self.assertEqual(self.out["list"], ["close", "name", "kindA", "save"])

    def test_focus_goes_in_when_it_opens_and_back_when_it_closes(self):
        self.assertEqual(self.out["entered"], "close")
        self.assertEqual(self.out["closed"], "opener")

    def test_tab_goes_round_at_the_ends_and_is_the_browsers_inside(self):
        self.assertEqual(self.out["tabFromLast"], ["trapped", "close"])
        self.assertEqual(self.out["shiftTabFromFirst"], ["trapped", "save"])
        self.assertEqual(self.out["tabInside"], ["native", "kindA"])

    def test_focus_that_leaves_is_brought_back(self):
        self.assertEqual(self.out["leakForward"], "close")
        self.assertEqual(self.out["leakBack"], "save")

    def test_a_control_the_modal_focused_keeps_it_and_autofocus_goes_first(self):
        self.assertEqual(self.out["kept"], "name")
        self.assertEqual(self.out["autofocus"], "save")

    def test_it_waits_until_the_modal_is_drawn(self):
        self.assertEqual(self.out["notDrawn"], "opener")
        self.assertEqual(self.out["drawn"], "close")

    def test_the_newest_trap_counts_and_closing_it_hands_back(self):
        self.assertEqual(self.out["innerEntered"], "yes")
        self.assertEqual(self.out["innerTab"], "yes")
        self.assertEqual(self.out["innerLeak"], "yes")
        self.assertEqual(self.out["innerClosed"], "name")
        self.assertEqual(self.out["outerAgain"], "close")

    def test_a_modal_with_nothing_to_focus_holds_focus_itself(self):
        self.assertEqual(self.out["bare"], ["empty", "-1"])
        self.assertEqual(self.out["bareTab"], ["trapped", "empty"])
        self.assertEqual(self.out["bareClosed"], "opener")

    def test_the_alpine_directive_follows_its_expression(self):
        self.assertEqual(self.out["directive"], "trap")
        self.assertEqual(self.out["alpineOpen"], "close")
        self.assertEqual(self.out["alpineTab"], ["trapped", "close"])
        self.assertEqual(self.out["alpineSame"], "close")
        self.assertEqual(self.out["alpineClosed"], "opener")
        # Taken off the page: it lets go and leaves focus where it is.
        self.assertEqual(self.out["afterCleanup"], ["close", "native"])


#: Every modal that wears the trap, by template: the x-trap expressions in
#: document order (each the same as its modal's x-show).
TRAPPED = {
    "vault/manage/_create_modal.html": ["modal === 'create'"],
    "vault/manage/_edit_modal.html": ["modal === 'edit' && edit.row"],
    "vault/manage/_delete_modal.html": ["modal === 'delete' && del.row"],
    "vault/manage/_share_modal.html": ["open"],
    "vault/manage/_connect_modal.html": ["open"],
    "vault/trash.html": ["modal === 'purge'", "modal === 'empty'"],
    "vault/partials/_bulk_modals.html": ["bulkModal === 'trash'", "bulkModal === 'move'"],
    # The erasure dialog, on the profile's Your data tab since stage 50.
    "socialhub/_profile_data.html": ["confirming"],
    "socialhub/clearances.html": ["open"],
    "socialhub/erasure_requests.html": ["decline"],
    # The address picker: on the map's profile plugin since 2026-10-04, when
    # the profile's own address became text. A host without the map skips it.
    "locations/profile_plugins/home.html": ["addressModal"],
}

#: Where a modal's Escape handler is when it is not in the modal's own
#: template: the Management page closes its three modals itself.
ESCAPE_IN = {
    "vault/manage/_create_modal.html": "vault/manage/manage.html",
    "vault/manage/_edit_modal.html": "vault/manage/manage.html",
    "vault/manage/_delete_modal.html": "vault/manage/manage.html",
}

#: An opening tag, attribute values quoted (an Alpine expression may hold ">").
_TAG = re.compile(r"""<[a-zA-Z][\w-]*(?:\s+[^\s=>"']+(?:\s*=\s*(?:"[^"]*"|'[^']*'))?)*\s*/?>""")


def _source(name):
    try:
        template = get_template(name)
    except TemplateDoesNotExist:
        return None
    return template.template.source


class WiringTests(SimpleTestCase):
    def test_the_base_page_loads_it_before_alpine_starts(self):
        source = _source("oya/base.html")
        tags = [t for t in re.findall(r"<script[^>]*>", source) if "oya/focus_trap.js" in t]
        self.assertEqual(len(tags), 1, tags)
        self.assertNotIn("defer", tags[0])
        self.assertNotIn("async", tags[0])
        self.assertIsNotNone(finders.find("oya/focus_trap.js"))

    def test_every_modal_wears_the_trap_and_closes_on_escape(self):
        for name, expressions in TRAPPED.items():
            source = _source(name)
            if source is None:                 # an app this host leaves out
                continue
            with self.subTest(template=name):
                dialogs = [t for t in _TAG.findall(source) if 'role="dialog"' in t]
                self.assertEqual(len(dialogs), len(expressions))
                found = [re.search(r'x-trap="([^"]*)"', t) for t in dialogs]
                self.assertEqual([m.group(1) if m else None for m in found], expressions)
                closer = _source(ESCAPE_IN.get(name, name))
                self.assertTrue("@keydown.escape.window" in closer, f"{name}: Escape closes nothing")

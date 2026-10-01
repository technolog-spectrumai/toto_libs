/* Focus stays inside an open modal (2026-10-01).
 *
 * `x-trap="expression"` on a modal: while the expression is true, Tab and
 * Shift+Tab go round the modal's own controls and never reach the page
 * behind it, and focus that leaves it anyway (a click on the page, a control
 * that vanished, a radio group Tab leaves early) is brought back. When the
 * modal opens, focus goes in — to what the modal focused itself, else an
 * [autofocus] control, else the first control, else the modal; when it
 * closes, back to what had it before. Escape stays the modal's own: each one
 * closes itself with @keydown.escape.window.
 *
 * Named and shaped like Alpine's Focus plugin's x-trap, which is not
 * vendored here; if it ever is, this file goes. The core bundle registers an
 * x-trap that only warns ("You can't use [x-trap] without first installing
 * the Focus plugin"); `alpine:init` replaces it before Alpine walks the page,
 * which is why oya/base.html loads this file WITHOUT defer (Alpine starts in
 * a microtask right after its own deferred script — scripts/test_monorepo.py,
 * AlpineFactoryLoadOrderTests).
 *
 * `window.totoFocusTrap(el)` is the same trap without Alpine:
 * `.activate()`, `.deactivate(returnFocus)`. Traps stack: the newest open one
 * is the one that counts. Tests: toto/core/tests_focus_trap.py (in node).
 */
(function (global) {
  "use strict";

  var CANDIDATES = [
    "a[href]", "area[href]", "button", "input", "select", "textarea", "iframe",
    "summary", "audio[controls]", "video[controls]", "[contenteditable]", "[tabindex]"
  ].join(",");
  // A modal is drawn a frame after it opens (x-show, a transition): try to go
  // in every STEP ms, for half a second at most.
  var STEP = 20;
  var TRIES = 25;
  var stack = [];
  var listening = false;

  function shown(el) {
    if (!el.getClientRects().length) return false;      // display: none, or inside it
    return global.getComputedStyle(el).visibility !== "hidden";
  }

  // What Tab reaches inside `root`, in document order: enabled, drawn, not
  // tabindex=-1; of a radio group with a ticked radio, only that one.
  function tabbables(root) {
    var all = Array.prototype.slice.call(root.querySelectorAll(CANDIDATES));
    var ticked = {};
    all.forEach(function (el) {
      if (el.type === "radio" && el.name && el.checked) ticked[el.name] = true;
    });
    return all.filter(function (el) {
      if (el.disabled || el.tabIndex < 0 || el.type === "hidden") return false;
      if (el.type === "radio" && el.name && !el.checked && ticked[el.name]) return false;
      return shown(el);
    });
  }

  function hold(el) {
    if (!el) return;
    if (el.tabIndex < 0 && !el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
    el.focus();
  }

  function top() {
    return stack.length ? stack[stack.length - 1] : null;
  }

  function onKeydown(event) {
    var trap = top();
    if (!trap || event.key !== "Tab") return;
    trap.back = !!event.shiftKey;
    var list = tabbables(trap.el);
    if (!list.length) {
      event.preventDefault();
      hold(trap.el);
      return;
    }
    var active = document.activeElement;
    var inside = !!active && active !== trap.el && trap.el.contains(active);
    var first = list[0];
    var last = list[list.length - 1];
    // Only at the ends: inside, the browser's own order (and its radio
    // groups) is left alone.
    if (event.shiftKey && (!inside || active === first)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (!inside || active === last)) {
      event.preventDefault();
      first.focus();
    }
  }

  function onFocusin(event) {
    var trap = top();
    if (!trap || trap.el.contains(event.target)) return;
    var list = tabbables(trap.el);
    hold((trap.back ? list[list.length - 1] : list[0]) || trap.el);
  }

  function listen() {
    if (listening) return;
    listening = true;
    document.addEventListener("keydown", onKeydown, true);
    document.addEventListener("focusin", onFocusin, true);
  }

  function Trap(el) {
    this.el = el;
    this.active = false;
    this.opener = null;
    this.back = false;
    this.timer = null;
  }

  Trap.prototype.activate = function () {
    if (this.active) return this;
    this.active = true;
    this.back = false;
    this.opener = document.activeElement;
    stack.push(this);
    listen();
    this.enter(0);
    return this;
  };

  Trap.prototype.enter = function (tries) {
    var self = this;
    clearTimeout(this.timer);
    // After the modal is drawn, and after its own $nextTick focus: a control
    // the modal focused itself keeps it.
    this.timer = setTimeout(function () {
      self.timer = null;
      if (!self.active) return;
      if (!shown(self.el)) {
        if (tries < TRIES) self.enter(tries + 1);
        return;
      }
      var active = document.activeElement;
      if (active && active !== document.body && self.el.contains(active)) return;
      var auto = self.el.querySelector("[autofocus]");
      hold((auto && shown(auto)) ? auto : (tabbables(self.el)[0] || self.el));
    }, STEP);
  };

  Trap.prototype.deactivate = function (returnFocus) {
    if (!this.active) return this;
    this.active = false;
    clearTimeout(this.timer);
    this.timer = null;
    var at = stack.indexOf(this);
    if (at >= 0) stack.splice(at, 1);
    var opener = this.opener;
    this.opener = null;
    if (returnFocus === false || !opener || opener === document.body) return this;
    if (typeof opener.focus !== "function" || !document.contains(opener)) return this;
    // Back to the opener only when focus is still the modal's (or nobody's):
    // a click that closed it may have put focus somewhere on purpose.
    var now = document.activeElement;
    if (!now || now === document.body || this.el.contains(now)) opener.focus();
    return this;
  };

  global.totoFocusTrap = function (el) {
    return new Trap(el);
  };
  global.totoFocusTrap.tabbables = tabbables;

  document.addEventListener("alpine:init", function () {
    global.Alpine.directive("trap", function (el, directive, utils) {
      var evaluate = utils.evaluateLater(directive.expression);
      var trap = new Trap(el);
      var was = false;
      utils.effect(function () {
        evaluate(function (value) {
          var on = !!value;
          if (on === was) return;
          was = on;
          if (on) trap.activate(); else trap.deactivate();
        });
      });
      // Taken off the page (an x-if, a re-render): let go, focus stays put.
      utils.cleanup(function () { trap.deactivate(false); });
    });
  });
})(window);

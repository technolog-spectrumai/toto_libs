/* The bell (2026-10-06).
 *
 * It asks its door (notify:api_list) for the unread count and the latest
 * notifications only when somebody is there to see the answer:
 *
 *   - once when the page has loaded;
 *   - when the tab is looked at again (visibilitychange, focus, a page
 *     brought back by the Back button) — and then not more often than every
 *     MIN_GAP_MS;
 *   - when the person opens the panel, and after they marked something read.
 *
 * Nothing is asked on a timer and no request is kept open: a tab nobody
 * looks at asks nothing, however long it stays open. (The door that kept one
 * request per open tab waiting is gone — the owner, 2026-10-06: "remove long
 * polls they are a burden on a server".) So the bell is as fresh as the last
 * load of the page or the last return to it.
 *
 * A toast is shown only when the count GREW between two questions, the
 * second asked on a return to the tab: what arrived while the person was
 * away. The one timer in this file takes a toast off the page again.
 *
 * Nothing the server knows is written into this file. The addresses come from
 * the bell element's data- attributes, the CSRF token from its hidden input,
 * and every text is put on the page with textContent.
 */
(function (root) {
  "use strict";

  // A return to the tab asks again only this long after the last question.
  var MIN_GAP_MS = 30000;
  var TOAST_MS = 6000;
  var MAX_TOASTS = 3;

  function createBell(env) {
    // env: roots, fetch, document, location, setTimeout, now(), visible()
    var state = {items: [], unread: null, known: null, busy: false, again: false,
                 pending: false, asked: null};
    var toasts = null;

    function token() {
      for (var i = 0; i < env.roots.length; i += 1) {
        var input = env.roots[i].querySelector("input[name=csrfmiddlewaretoken]");
        if (input && input.value) return input.value;
      }
      return "";
    }

    function address(name) { return env.roots[0].dataset[name] || ""; }

    function post(url, body) {
      return env.fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {"X-CSRFToken": token(), "Accept": "application/json",
                  "Content-Type": "application/x-www-form-urlencoded"},
        body: body || "",
      }).then(function (response) { return response.ok ? response.json() : null; });
    }

    function follow(item) {
      var link = String(item.link || "");
      // A path of this platform only: never another site, never a scheme.
      if (link.charAt(0) === "/" && link.charAt(1) !== "/") env.location.assign(link);
    }

    function choose(item) {
      var done = function () { follow(item); if (!item.link) refresh(false); };
      if (item.read) { done(); return; }
      post(address("readUrl"), "id=" + encodeURIComponent(item.id)).then(done, done);
    }

    function line(item) {
      var doc = env.document;
      var li = doc.createElement("li");
      li.className = "border-b border-current/10 last:border-b-0";
      var button = doc.createElement("button");
      button.type = "button";
      button.className = "flex w-full items-start gap-3 px-3 py-2 text-left text-sm transition hover:bg-black/5";
      var icon = doc.createElement("i");
      icon.className = (item.icon || "fa-solid fa-bell") + " mt-0.5 w-4 shrink-0 text-center opacity-70";
      icon.setAttribute("aria-hidden", "true");
      var body = doc.createElement("span");
      body.className = "min-w-0 flex-1";
      var text = doc.createElement("span");
      text.className = item.read ? "block break-words opacity-70" : "block break-words font-semibold";
      text.textContent = item.text;
      var meta = doc.createElement("span");
      meta.className = "block text-xs opacity-60";
      meta.textContent = item.actor ? item.actor + " · " + item.when : item.when;
      body.appendChild(text);
      body.appendChild(meta);
      button.appendChild(icon);
      button.appendChild(body);
      button.addEventListener("click", function () { choose(item); });
      li.appendChild(button);
      return li;
    }

    function draw() {
      env.roots.forEach(function (rootEl) {
        var count = rootEl.querySelector("[data-notify-count]");
        if (count && state.unread !== null) {
          count.textContent = state.unread > 99 ? "99+" : String(state.unread);
          count.hidden = !state.unread;
        }
        var list = rootEl.querySelector("[data-notify-list]");
        var empty = rootEl.querySelector("[data-notify-empty]");
        var all = rootEl.querySelector("[data-notify-read-all]");
        if (list) {
          while (list.firstChild) list.removeChild(list.firstChild);
          state.items.forEach(function (item) { list.appendChild(line(item)); });
        }
        if (empty) empty.hidden = state.items.length > 0;
        if (all) all.hidden = !state.unread;
      });
    }

    function toast(item) {
      var doc = env.document;
      if (!doc.body) return;
      if (!toasts) {
        toasts = doc.createElement("div");
        toasts.className = "fixed right-4 top-16 z-50 flex max-w-sm flex-col gap-2";
        toasts.setAttribute("role", "status");
        toasts.setAttribute("aria-live", "polite");
        doc.body.appendChild(toasts);
      }
      var box = doc.createElement("button");
      box.type = "button";
      box.className = "flex items-start gap-3 rounded-lg bg-gray-700 px-4 py-3 text-left text-sm font-medium text-white shadow-lg";
      var icon = doc.createElement("i");
      icon.className = (item.icon || "fa-solid fa-bell") + " mt-0.5";
      icon.setAttribute("aria-hidden", "true");
      var text = doc.createElement("span");
      text.className = "min-w-0 flex-1 break-words";
      text.textContent = item.text;
      box.appendChild(icon);
      box.appendChild(text);
      var gone = false;
      var drop = function () { if (!gone) { gone = true; toasts.removeChild(box); } };
      box.addEventListener("click", function () { drop(); choose(item); });
      toasts.appendChild(box);
      env.setTimeout(drop, TOAST_MS);
    }

    function take(data, announce) {
      var items = Array.isArray(data.items) ? data.items : [];
      var known = state.known;
      var before = state.unread;
      state.items = items;
      state.unread = Number(data.unread) || 0;
      state.known = {};
      items.forEach(function (item) { state.known[item.id + ":" + item.created] = true; });
      draw();
      // Only what arrived between two questions, and only if the count grew.
      if (!announce || known === null || before === null || state.unread <= before) return;
      var fresh = items.filter(function (item) {
        return !item.read && !known[item.id + ":" + item.created];
      }).slice(0, MAX_TOASTS);
      // Newest last, so the newest toast ends on top of the stack's reading order.
      fresh.reverse().forEach(toast);
    }

    function refresh(announce) {
      if (state.busy) { state.again = state.again || announce || false; state.pending = true; return null; }
      state.busy = true;
      state.asked = env.now();
      var finish = function () {
        state.busy = false;
        if (state.pending) { var next = state.again; state.pending = false; state.again = false; refresh(next); }
      };
      return env.fetch(address("listUrl"), {credentials: "same-origin", cache: "no-store",
                                            headers: {"Accept": "application/json"}})
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) { if (data) take(data, announce); })
        .then(finish, finish);
    }

    // The tab is looked at again: ask, unless it was asked a moment ago.
    function comeBack() {
      if (!env.visible()) return false;
      if (state.asked !== null && env.now() - state.asked < MIN_GAP_MS) return false;
      refresh(true);
      return true;
    }

    function toggle(rootEl, show) {
      var panel = rootEl.querySelector("[data-notify-panel]");
      var button = rootEl.querySelector("[data-notify-toggle]");
      if (!panel) return;
      var opening = show === undefined ? panel.hidden : show;
      panel.hidden = !opening;
      if (button) button.setAttribute("aria-expanded", opening ? "true" : "false");
      if (opening) refresh(false);
    }

    function start() {
      env.roots.forEach(function (rootEl) {
        var button = rootEl.querySelector("[data-notify-toggle]");
        if (button) button.addEventListener("click", function (event) {
          event.stopPropagation();
          toggle(rootEl);
        });
        var all = rootEl.querySelector("[data-notify-read-all]");
        if (all) all.addEventListener("click", function () {
          post(address("readAllUrl"), "").then(function () { refresh(false); },
                                                function () { refresh(false); });
        });
      });
      env.document.addEventListener("click", function (event) {
        env.roots.forEach(function (rootEl) {
          if (!rootEl.contains(event.target)) toggle(rootEl, false);
        });
      });
      env.document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") env.roots.forEach(function (rootEl) { toggle(rootEl, false); });
      });
      // The page has loaded: the one question nobody had to ask for.
      refresh(false);
    }

    return {start: start, refresh: refresh, comeBack: comeBack, state: state};
  }

  function boot() {
    var doc = root.document;
    var roots = Array.prototype.slice.call(doc.querySelectorAll("[data-notify-bell]"));
    if (!roots.length || root.totoBell) return;
    var bell = createBell({
      roots: roots,
      document: doc,
      location: root.location,
      fetch: function (url, options) { return root.fetch(url, options); },
      setTimeout: function (fn, ms) { return root.setTimeout(fn, ms); },
      now: function () { return Date.now(); },
      visible: function () { return doc.visibilityState !== "hidden"; },
    });
    root.totoBell = bell;
    bell.start();
    // A tab nobody looks at asks nothing; it asks when it is looked at again.
    doc.addEventListener("visibilitychange", function () { bell.comeBack(); });
    root.addEventListener("focus", function () { bell.comeBack(); });
    root.addEventListener("pageshow", function (event) { if (event.persisted) bell.comeBack(); });
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {createBell: createBell, MIN_GAP_MS: MIN_GAP_MS, MAX_TOASTS: MAX_TOASTS};
  } else if (root.document) {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", boot);
    } else {
      boot();
    }
  }
})(typeof window !== "undefined" ? window : globalThis);

/* The long poll and the bell (2026-10-04).
 *
 * One request per page is held open at the long-poll door (notify:api_wait),
 * asked here and nowhere else — no WebSocket:
 *
 *   - "anything new since my cursor?": the server answers at once when there
 *     is, otherwise it holds the request until something changes or some
 *     25 seconds pass; then the page asks again, with the cursor it was given;
 *   - what the answer says becomes a DOM event on `document`:
 *       toto:notification                 the member's bell has news
 *       toto:folder {directory, files}    a watched folder changed; files is
 *                                         {file id: version tag} — the files
 *                                         the reader is shown there — or null
 *       toto:live-open / toto:live-closed
 *   - a page asks to watch a folder with
 *       (window.totoLiveWatch = window.totoLiveWatch || []).push(directoryId)
 *     which works before this script has loaded (the array is drained) and
 *     after (push asks again at once, naming the folder);
 *   - a hidden tab stops asking (the held request is dropped) and asks again
 *     when it is looked at, with the cursor it had: what changed meanwhile
 *     is answered at once;
 *   - a failed request is tried again, waiting longer each time (BACKOFF);
 *     an answer that says "retry" (too many tabs, or a server that cannot
 *     hold a request) is obeyed; a 401 or 403 ends it — the session is over.
 *
 * The answer carries ids only. The bell draws the list its door answers
 * (notify:api_list), a file list asks the row door for each row; nothing the
 * long poll carried is ever drawn. While the long poll is not working the
 * bell asks its door every POLL_MS instead.
 *
 * Nothing the server knows is written into this file. The addresses come from
 * the bell element's data- attributes, the CSRF token from its hidden input,
 * and every text is put on the page with textContent.
 */
(function (root) {
  "use strict";

  var BACKOFF = [1000, 2000, 5000, 10000, 30000, 60000];
  var POLL_MS = 60000;
  var TOAST_MS = 6000;
  var REFRESH_SETTLE_MS = 400;
  // Never more than one question a second, whatever the answers say.
  var MIN_GAP_MS = 1000;
  // A folder opened while a request is held: ask again, once for a burst.
  var REASK_MS = 100;
  var MAX_RETRY_S = 300;

  function createLive(env) {
    // env: url, fetch, AbortController, setTimeout, clearTimeout, dispatch,
    //      visible(), now()
    var cursor = "", watched = [], asking = null, timer = null;
    var healthy = false, attempt = 0, stopped = false, started = 0;

    function address() {
      var query = [];
      if (cursor) query.push("cursor=" + encodeURIComponent(cursor));
      if (watched.length) query.push("folders=" + watched.join(","));
      return env.url + (query.length ? "?" + query.join("&") : "");
    }

    function later(ms) {
      if (timer !== null) env.clearTimeout(timer);
      timer = env.setTimeout(function () { timer = null; ask(); }, Math.max(0, ms));
    }

    function closed() {
      if (healthy) { healthy = false; env.dispatch("toto:live-closed", {}); }
    }

    function fail() {
      closed();
      var delay = BACKOFF[Math.min(attempt, BACKOFF.length - 1)];
      attempt += 1;
      later(delay);
    }

    function take(data) {
      attempt = 0;
      if (!healthy) { healthy = true; env.dispatch("toto:live-open", {}); }
      if (typeof data.cursor === "string") cursor = data.cursor;
      if (data.notifications) env.dispatch("toto:notification", {});
      var files = data.files && typeof data.files === "object" ? data.files : {};
      (Array.isArray(data.folders) ? data.folders : []).forEach(function (id) {
        var listed = files[id];
        env.dispatch("toto:folder", {directory: Number(id),
                                     files: listed && typeof listed === "object" ? listed : null});
      });
      var retry = Number(data.retry);
      if (retry > 0) later(Math.min(retry, MAX_RETRY_S) * 1000);
      else later(MIN_GAP_MS - (env.now() - started));
    }

    function ask() {
      if (stopped || asking || !env.url || !env.visible()) return;
      var mine = {dropped: false, controller: env.AbortController ? new env.AbortController() : null};
      asking = mine;
      started = env.now();
      var options = {credentials: "same-origin", cache: "no-store",
                     headers: {"Accept": "application/json"}};
      if (mine.controller) options.signal = mine.controller.signal;
      var done = function (data) {
        if (asking === mine) asking = null;
        if (mine.dropped) return;
        if (data === "over") { stopped = true; closed(); return; }
        if (!data || typeof data !== "object") { fail(); return; }
        take(data);
      };
      env.fetch(address(), options).then(function (response) {
        if (response.status === 401 || response.status === 403) return "over";
        return response.ok ? response.json() : null;
      }).then(done, function () { done(null); });
    }

    // Drop the held request without counting it as a failure.
    function drop() {
      if (timer !== null) { env.clearTimeout(timer); timer = null; }
      if (asking) {
        var mine = asking;
        asking = null;
        mine.dropped = true;
        if (mine.controller) mine.controller.abort();
      }
    }

    function watch(id) {
      id = Number(id);
      if (!Number.isInteger(id) || id <= 0 || watched.indexOf(id) !== -1) return;
      watched.push(id);
      // The held request does not know this folder: ask again, naming it.
      if (asking) { drop(); later(REASK_MS); }
    }

    return {
      start: function () { if (!asking && timer === null) ask(); },
      pause: drop,
      watch: watch,
      isOpen: function () { return healthy && !stopped; },
      stop: function () { stopped = true; drop(); closed(); },
    };
  }

  function createBell(env) {
    // env: roots, fetch, document, location, live, setTimeout, setInterval, visible()
    var state = {items: [], unread: null, known: null, busy: false, again: false};
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
      state.items = items;
      state.unread = Number(data.unread) || 0;
      state.known = {};
      items.forEach(function (item) { state.known[item.id + ":" + item.created] = true; });
      draw();
      if (!announce || known === null) return;
      // Newest last, so the newest toast ends on top of the stack's reading order.
      items.slice().reverse().forEach(function (item) {
        if (!item.read && !known[item.id + ":" + item.created]) toast(item);
      });
    }

    function refresh(announce) {
      if (state.busy) { state.again = state.again || announce || false; state.pending = true; return null; }
      state.busy = true;
      var finish = function () {
        state.busy = false;
        if (state.pending) { var next = state.again; state.pending = false; state.again = false; refresh(next); }
      };
      return env.fetch(address("listUrl"), {credentials: "same-origin",
                                            headers: {"Accept": "application/json"}})
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) { if (data) take(data, announce); })
        .then(finish, finish);
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
      // The long poll says THAT there is news; a burst settles into one question.
      var settle = null;
      env.document.addEventListener("toto:notification", function () {
        if (settle !== null) return;
        settle = env.setTimeout(function () { settle = null; refresh(true); }, REFRESH_SETTLE_MS);
      });
      // The long poll is not working: ask every minute instead, while the
      // page is looked at.
      env.setInterval(function () {
        if (!env.live.isOpen() && env.visible()) refresh(true);
      }, POLL_MS);
      env.setTimeout(function () { refresh(false); }, 1000);
    }

    return {start: start, refresh: refresh, state: state};
  }

  function boot() {
    var doc = root.document;
    var roots = Array.prototype.slice.call(doc.querySelectorAll("[data-notify-bell]"));
    if (!roots.length || root.totoLive) return;
    var visible = function () { return doc.visibilityState !== "hidden"; };
    var live = createLive({
      url: roots[0].dataset.waitUrl || "",
      AbortController: root.AbortController,
      fetch: function (url, options) { return root.fetch(url, options); },
      setTimeout: function (fn, ms) { return root.setTimeout(fn, ms); },
      clearTimeout: function (id) { root.clearTimeout(id); },
      now: function () { return Date.now(); },
      visible: visible,
      dispatch: function (name, detail) {
        doc.dispatchEvent(new root.CustomEvent(name, {detail: detail}));
      },
    });
    root.totoLive = live;
    // A page that asked before this script ran, and one that asks after it.
    var asked = Array.isArray(root.totoLiveWatch) ? root.totoLiveWatch : [];
    root.totoLiveWatch = {push: function (id) { live.watch(id); }};
    asked.forEach(function (id) { live.watch(id); });
    createBell({
      roots: roots,
      document: doc,
      location: root.location,
      live: live,
      fetch: function (url, options) { return root.fetch(url, options); },
      setTimeout: function (fn, ms) { return root.setTimeout(fn, ms); },
      setInterval: function (fn, ms) { return root.setInterval(fn, ms); },
      visible: visible,
    }).start();
    // A tab nobody looks at asks nothing; it asks again when it is looked at.
    doc.addEventListener("visibilitychange", function () {
      if (visible()) live.start(); else live.pause();
    });
    root.addEventListener("focus", function () { live.start(); });
    live.start();
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {createLive: createLive, createBell: createBell,
                      BACKOFF: BACKOFF, POLL_MS: POLL_MS, MIN_GAP_MS: MIN_GAP_MS};
  } else if (root.document) {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", boot);
    } else {
      boot();
    }
  }
})(typeof window !== "undefined" ? window : globalThis);

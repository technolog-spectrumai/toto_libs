/* The live socket and the bell (2026-10-04).
 *
 * One WebSocket per page (ws/live/), opened here and nowhere else:
 *
 *   - it reconnects by itself, waiting longer after each failure (BACKOFF);
 *   - what the server says becomes a DOM event on `document`:
 *       toto:notification            the member has news
 *       toto:folder {kind, file, directory}   a file changed in a watched folder
 *       toto:live-open / toto:live-closed
 *   - a page asks to watch a folder with
 *       (window.totoLiveWatch = window.totoLiveWatch || []).push(directoryId)
 *     which works before this script has loaded (the array is drained) and
 *     after (push sends). Watches are sent again after every reconnect.
 *
 * The bell draws the list its door answers (notify:api_list) and never
 * anything the socket carried: the socket only says THAT there is news. While
 * the socket is not open the bell asks its door every POLL_MS instead, so it
 * works on a host with no socket at all.
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

  function createLive(env) {
    var socket = null, open = false, attempt = 0, stopped = false, watched = [];

    function address() {
      var scheme = env.location.protocol === "https:" ? "wss://" : "ws://";
      return scheme + env.location.host + env.path;
    }

    function retry() {
      if (stopped) return;
      var delay = BACKOFF[Math.min(attempt, BACKOFF.length - 1)];
      attempt += 1;
      env.setTimeout(connect, delay);
    }

    function sendWatch(id) {
      if (open && socket) socket.send(JSON.stringify({type: "watch", directory: id}));
    }

    function hear(text) {
      var message;
      try { message = JSON.parse(text); } catch (error) { return; }
      if (!message || typeof message !== "object") return;
      if (message.type === "notification") {
        env.dispatch("toto:notification", {});
      } else if (message.type === "folder") {
        env.dispatch("toto:folder", {kind: String(message.kind || ""),
                                     file: Number(message.file),
                                     directory: Number(message.directory)});
      }
    }

    function connect() {
      if (stopped || !env.path || !env.WebSocket) return;
      try { socket = new env.WebSocket(address()); } catch (error) { socket = null; retry(); return; }
      socket.onopen = function () {
        open = true;
        attempt = 0;
        watched.forEach(sendWatch);
        env.dispatch("toto:live-open", {});
      };
      socket.onmessage = function (event) { hear(event.data); };
      socket.onerror = function () {};
      socket.onclose = function () {
        var was = open;
        open = false;
        socket = null;
        if (was) env.dispatch("toto:live-closed", {});
        retry();
      };
    }

    function watch(id) {
      id = Number(id);
      if (!Number.isInteger(id) || id <= 0 || watched.indexOf(id) !== -1) return;
      watched.push(id);
      sendWatch(id);
    }

    return {
      connect: connect,
      watch: watch,
      isOpen: function () { return open; },
      stop: function () { stopped = true; if (socket) socket.close(); },
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
      // The socket says THAT there is news; a burst settles into one question.
      var settle = null;
      env.document.addEventListener("toto:notification", function () {
        if (settle !== null) return;
        settle = env.setTimeout(function () { settle = null; refresh(true); }, REFRESH_SETTLE_MS);
      });
      env.document.addEventListener("toto:live-open", function () { refresh(true); });
      // No socket: ask every minute instead, while the page is looked at.
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
    var live = createLive({
      WebSocket: root.WebSocket,
      location: root.location,
      path: roots[0].dataset.wsPath || "",
      setTimeout: function (fn, ms) { return root.setTimeout(fn, ms); },
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
      visible: function () { return doc.visibilityState !== "hidden"; },
    }).start();
    live.connect();
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {createLive: createLive, createBell: createBell,
                      BACKOFF: BACKOFF, POLL_MS: POLL_MS};
  } else if (root.document) {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", boot);
    } else {
      boot();
    }
  }
})(typeof window !== "undefined" ? window : globalThis);

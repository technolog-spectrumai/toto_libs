/* One community's channel: the forum's page (stage 68, 2026-10-07; live
 * since stage 70 of the same day; what it draws wears the platform's look:
 * the theme's colours at full strength and rounded shapes).
 *
 * Two halves. The functions at the top have no page in them and run under
 * node: the state a page keeps of its channel and what one feed answer does
 * to it (`applyFeed`), the timer that asks the feed (`createPoller`), the
 * wait before an estimate is asked (`createEstimator`), the rule that makes
 * a link (`splitLinks`). `mount` is the page.
 *
 * THE RULES KEPT HERE
 *  - Every name, message, question and option is written as TEXT
 *    (textContent, text nodes). Nothing a member wrote is ever put into the
 *    page as markup.
 *  - An address in a message becomes a link only when it points at THIS
 *    platform (the page's own host, or a name the page data lists); the link
 *    then leads to this page's origin. Every other address stays text.
 *  - A picture is an <img> whose address is the image door's, as the feed
 *    gave it, and nothing else; pressed, it opens that door in a new tab.
 *  - SHORT POLLING, and nothing else. The page asks the feed door for what
 *    changed after its CURSOR (the last event of the channel it has seen),
 *    waits for the answer, and only then sets a timer for the next question,
 *    so one request is out at a time and a slow server is never asked
 *    twice. The wait is the page data's `refresh_seconds` (the forum's
 *    Settings). A hidden tab asks nothing; shown again it asks once at once
 *    with the cursor it kept, and again at once while the answer says there
 *    is more. A failed request doubles the wait, up to a minute, and the
 *    first answer brings it back. No socket, no event stream, no request
 *    held open, no repeating timer.
 *  - What an answer brings is applied to the nodes that are there: a new
 *    message is added in its place, a removed one becomes a quiet line, a
 *    poll's card is filled again with its counts (its ring, where Chart.js
 *    loaded, keeps its canvas and is handed them), and everything from before
 *    a cleanup's edge (`purged_before`) is taken out. History is never
 *    loaded again, and the reader's place is kept unless they are at the
 *    bottom.
 *  - Rows are kept by id, and a row is replaced only by one whose `seq` is
 *    newer: what the member's own post, vote or removal answered is drawn at
 *    once, and the next answer of the feed, which holds it too, adds nothing.
 *  - A post carries an `op` minted once per deliberate press. It is kept and
 *    sent again only when the server never answered (or answered 5xx) and
 *    the member changed nothing, so a retry is the same post: stored once,
 *    charged once.
 *  - Before a post the page asks what it will cost (the estimate door, when
 *    the page data names one), a moment after the typing stops, and shows
 *    the answer beside Send; when the balance does not cover it, Send is
 *    off. Without that door nothing is shown and nothing fails.
 *  - Nothing is kept in the browser's storage.
 */
(function (root) {
  "use strict";

  var api = {};

  /* --- no page from here to `mount` ---------------------------------------- */

  /* The longest wait between two questions after failures, in milliseconds. */
  var MOST_WAIT = 60000;
  /* How long after the typing stops the estimate is asked, in milliseconds. */
  var ESTIMATE_WAIT = 400;

  function mintOp() {
    var c = root.crypto;
    if (c && typeof c.randomUUID === "function") { return c.randomUUID(); }
    var bytes = [];
    for (var i = 0; i < 16; i++) { bytes.push(Math.floor(Math.random() * 256)); }
    if (c && typeof c.getRandomValues === "function") {
      bytes = Array.prototype.slice.call(c.getRandomValues(new Uint8Array(16)));
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    var hex = bytes.map(function (b) { return (b + 0x100).toString(16).slice(1); }).join("");
    return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) + "-" +
      hex.slice(16, 20) + "-" + hex.slice(20);
  }

  function urlFor(pattern, nil, id) { return String(pattern).replace(nil, String(id)); }

  /* The UTF-8 length of `text`, which is what a post is priced by. */
  function utf8Length(text) {
    text = text === undefined || text === null ? "" : String(text);
    if (typeof root.TextEncoder === "function") { return new root.TextEncoder().encode(text).length; }
    var bytes = 0;
    for (var i = 0; i < text.length; i++) {
      var code = text.charCodeAt(i);
      if (code < 0x80) { bytes += 1; }
      else if (code < 0x800) { bytes += 2; }
      else if (code >= 0xd800 && code < 0xdc00) { bytes += 4; i += 1; }
      else { bytes += 3; }
    }
    return bytes;
  }

  function newState() {
    return {cursor: 0, messages: {}, polls: {}, gone: {}, oldest: null, more: false,
            purgedBefore: null};
  }

  function byNumber(map) {
    return Object.keys(map).map(function (id) { return map[id]; })
      .sort(function (a, b) { return a.number - b.number; });
  }

  /* One list of rows of an answer into what the page holds. A tombstone
   * takes its row out and is remembered, so an older answer that arrives
   * late cannot bring the row back; a row replaces the one held only when
   * its `seq` is newer. */
  function merge(held, gone, rows, changedRows, removedIds) {
    (rows || []).forEach(function (row) {
      if (!row || row.id === undefined || row.id === null) { return; }
      var seq = Number(row.seq) || 0;
      if (row.removed) {
        gone[row.id] = Math.max(gone[row.id] || 0, seq);
        if (held[row.id]) { delete held[row.id]; removedIds.push(row.id); }
        return;
      }
      if (gone[row.id] !== undefined && seq <= gone[row.id]) { return; }
      var old = held[row.id];
      if (old && (Number(old.seq) || 0) >= seq) { return; }
      held[row.id] = row;
      changedRows.push(row);
    });
  }

  function drop(held, edge, removedIds, purgedIds) {
    Object.keys(held).forEach(function (id) {
      if (Date.parse(held[id].created_at) < edge) {
        delete held[id];
        removedIds.push(id);
        purgedIds.push(id);
      }
    });
  }

  /* Merge one answer of the feed door (or of a door the member used: the
   * same shape, without a cursor) into `state`. Returns what changed:
   * {messages: [added or newer], removedMessages: [ids], polls: [added or
   * newer], removedPolls: [ids], purgedMessages: [ids], purgedPolls: [ids],
   * edge: the cleanup's instant in milliseconds, or null}. The purged ids
   * are among the removed ones too. An answer of older history (no cursor
   * in it) adds messages and moves `oldest` only. */
  function applyFeed(state, answer) {
    var changed = {messages: [], removedMessages: [], polls: [], removedPolls: [],
                   purgedMessages: [], purgedPolls: [], edge: null};
    if (!answer) { return changed; }
    if (!state.gone) { state.gone = {}; }
    merge(state.messages, state.gone, answer.messages, changed.messages, changed.removedMessages);
    merge(state.polls, state.gone, answer.polls, changed.polls, changed.removedPolls);
    if (typeof answer.cursor === "number" && answer.cursor > state.cursor) {
      state.cursor = answer.cursor;
    }
    if (answer.oldest !== undefined && answer.oldest !== null &&
        (state.oldest === null || answer.oldest < state.oldest)) {
      state.oldest = answer.oldest;
    }
    if (answer.oldest !== undefined && answer.more !== undefined) { state.more = !!answer.more; }
    if (answer.purged_before && answer.purged_before !== state.purgedBefore) {
      var edge = Date.parse(answer.purged_before);
      if (!isNaN(edge)) {
        state.purgedBefore = answer.purged_before;
        changed.edge = edge;
        drop(state.messages, edge, changed.removedMessages, changed.purgedMessages);
        drop(state.polls, edge, changed.removedPolls, changed.purgedPolls);
      }
    }
    return changed;
  }

  /* The interval of the page data as milliseconds: a second at the least,
   * an hour at the most, five seconds when it says nothing usable. */
  function waitOf(seconds) {
    var value = Number(seconds);
    if (!isFinite(value) || value <= 0) { value = 5; }
    return Math.round(Math.min(Math.max(value, 1), 3600) * 1000);
  }

  /* The timer that asks the feed.
   *
   *   ask()        asks once; a promise of {ok, more}. A rejection is a
   *                failure, as an answer that is not ok is.
   *   seconds      the wait between an answer and the next question
   *   hidden()     true while the tab is hidden: nothing is asked then
   *   setTimer(fn, ms), clearTimer(handle)   the one-shot timer to use
   *   onState({ok, failures, wait})          told after every answer
   *
   * start() sets the first timer. now() asks at once (the Refresh button;
   * during a request it asks again as soon as that one is answered).
   * visibility() is for the page's `visibilitychange`: hidden, the timer is
   * dropped; shown, one question at once. stop() ends it. One request is
   * out at a time, and the next timer is set only when it has come back. */
  function createPoller(options) {
    var base = waitOf(options.seconds);
    var most = Math.max(base, MOST_WAIT);
    var hidden = options.hidden || function () { return false; };
    var wait = base;
    var failures = 0;
    var timer = null;
    var flying = null;
    var again = false;
    var stopped = true;

    function cancel() {
      if (timer !== null) { options.clearTimer(timer); timer = null; }
    }

    function plan() {
      cancel();
      if (stopped || hidden()) { return; }
      timer = options.setTimer(function () {
        timer = null;
        if (!stopped && !hidden()) { go(); }
      }, wait);
    }

    function go() {
      if (flying) { again = true; return flying; }
      again = false;
      var asked;
      try { asked = Promise.resolve(options.ask()); } catch (error) { asked = Promise.reject(error); }
      flying = asked.then(function (answer) { return answer || {ok: false}; },
                          function () { return {ok: false}; })
        .then(function (answer) {
          flying = null;
          if (answer.ok) { failures = 0; wait = base; }
          else { failures += 1; wait = Math.min(wait * 2, most); }
          if (options.onState) { options.onState({ok: !!answer.ok, failures: failures, wait: wait}); }
          if (stopped) { return answer; }
          if (answer.ok && (answer.more || again) && !hidden()) { return go(); }
          again = false;
          plan();
          return answer;
        });
      return flying;
    }

    return {
      start: function () { stopped = false; plan(); },
      stop: function () { stopped = true; again = false; cancel(); },
      now: function () { stopped = false; cancel(); return go(); },
      visibility: function () {
        if (stopped) { return null; }
        cancel();
        return hidden() ? null : go();
      },
      wait: function () { return wait; },
      base: function () { return base; },
      waiting: function () { return timer !== null; },
      flying: function () { return flying !== null; }
    };
  }

  /* The estimate, asked a moment after the last change.
   *
   *   ask(textBytes, imageBytes)   a promise of {ok, data}
   *   show(data | null)            the estimate to show, or nothing
   *   wait                         milliseconds after the last change
   *   setTimer, clearTimer
   *
   * request(textBytes, imageBytes, atOnce) is called on every change. An
   * empty post asks nothing and shows nothing. An answer to a question that
   * has been overtaken by a newer one is dropped. */
  function createEstimator(options) {
    var wait = options.wait === undefined ? ESTIMATE_WAIT : options.wait;
    var timer = null;
    var serial = 0;

    function cancel() {
      if (timer !== null) { options.clearTimer(timer); timer = null; }
      serial += 1;
    }

    function fire(textBytes, imageBytes) {
      var mine = serial;
      var asked;
      try { asked = Promise.resolve(options.ask(textBytes, imageBytes)); }
      catch (error) { asked = Promise.reject(error); }
      return asked.then(function (answer) {
        if (mine !== serial) { return; }
        options.show(answer && answer.ok && answer.data ? answer.data : null);
      }, function () {
        if (mine === serial) { options.show(null); }
      });
    }

    function request(textBytes, imageBytes, atOnce) {
      cancel();
      if (!textBytes && !imageBytes) { options.show(null); return null; }
      if (atOnce) { return fire(textBytes, imageBytes); }
      timer = options.setTimer(function () {
        timer = null;
        fire(textBytes, imageBytes);
      }, wait);
      return null;
    }

    return {request: request, cancel: cancel};
  }

  /* --- links: only to this platform ---------------------------------------- */

  /* An address the way Markdown finds one: http://, https:// or www., not
   * glued to a word, an address or another URL. Built in a try: a browser
   * that does not know the look-behind keeps every message plain text. */
  var URL_RE = null;
  try {
    URL_RE = new RegExp("(?<![\\p{L}\\p{N}_/@.:\\-])(?:https?:\\/\\/|www\\.)" +
                        "[^\\s\\x85<>\"'\\x00-\\x1f\\x7f]+", "giu");
  } catch (error) { URL_RE = null; }
  var URL_TRAILING = ".,:;!?'\"*_~";

  function count(text, char) { return text.split(char).length - 1; }

  function trimUrl(url) {
    while (url && (URL_TRAILING.indexOf(url[url.length - 1]) !== -1 ||
                   (url[url.length - 1] === ")" && count(url, "(") < count(url, ")")))) {
      url = url.slice(0, -1);
    }
    return url;
  }

  /* Where a candidate address may point, or null when it stays text. */
  function selfHref(candidate, hosts, origin) {
    var absolute = /^https?:\/\//i.test(candidate) ? candidate : "https://" + candidate;
    /* No user@ before the host: "https://ours@elsewhere" reads as ours. */
    var authority = absolute.replace(/^https?:\/\//i, "").split(/[/?#\\]/)[0];
    if (authority.indexOf("@") !== -1) { return null; }
    var url;
    try { url = new root.URL(absolute); } catch (error) { return null; }
    if (url.protocol !== "http:" && url.protocol !== "https:") { return null; }
    if (url.username || url.password) { return null; }
    var names = {};
    (hosts || []).forEach(function (host) {
      if (typeof host === "string" && host.trim()) { names[host.trim().toLowerCase()] = true; }
    });
    if (!names[url.hostname.toLowerCase()]) { return null; }
    return String(origin) + url.pathname + url.search + url.hash;
  }

  /* The message cut into pieces: {text} or {text, href}. Joined, the texts
   * are the message exactly. */
  function splitLinks(text, hosts, origin) {
    var message = text === undefined || text === null ? "" : String(text);
    if (!URL_RE || !message) { return message ? [{text: message}] : []; }
    var parts = [];
    var last = 0;
    var match;
    URL_RE.lastIndex = 0;
    while ((match = URL_RE.exec(message)) !== null) {
      var url = trimUrl(match[0]);
      var low = url.toLowerCase();
      if (low === "http://" || low === "https://" || low === "www." || low === "") { continue; }
      var href = selfHref(url, hosts, origin);
      if (!href) { continue; }
      if (match.index > last) { parts.push({text: message.slice(last, match.index)}); }
      parts.push({text: url, href: href});
      last = match.index + url.length;
    }
    if (last < message.length) { parts.push({text: message.slice(last)}); }
    return parts;
  }

  /* --- a poll's ring --------------------------------------------------- */

  /* The slices of a poll's ring, in the options' order: the hues of the
   * vault's "files by type" ring and of a company's ownership ring. Ten: as
   * many as a poll has options at the most. */
  var SLICES = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6",
                "#ec4899", "#0ea5e9", "#84cc16", "#f97316", "#14b8a6"];

  /* What a poll's ring is drawn from, {labels, values, colours}, or null
   * where there is nothing to draw: no count to show yet (it appears when
   * the poll closes) or no answer at all. An option nobody chose keeps its
   * place and its colour, with a slice of nothing. */
  function ringData(row) {
    if (!row || !row.choices || !row.choices.length || !(Number(row.total) > 0)) { return null; }
    var labels = [], values = [], colours = [];
    for (var at = 0; at < row.choices.length; at++) {
      var ballots = row.choices[at].ballots;
      if (ballots === null || ballots === undefined) { return null; }
      labels.push(String(row.choices[at].label));
      values.push(Number(ballots) || 0);
      colours.push(SLICES[at % SLICES.length]);
    }
    return {labels: labels, values: values, colours: colours};
  }

  /* A slice's tip: the option, its answers, its share of the answers. */
  function ringTip(item) {
    var values = item.chart.data.datasets[item.datasetIndex].data;
    var total = 0;
    for (var at = 0; at < values.length; at++) { total += Number(values[at]) || 0; }
    var value = Number(values[item.dataIndex]) || 0;
    return item.label + ": " + value + " (" + (total ? Math.round(100 * value / total) : 0) + "%)";
  }

  api.SLICES = SLICES;
  api.ringData = ringData;
  api.ringTip = ringTip;
  api.mintOp = mintOp;
  api.urlFor = urlFor;
  api.utf8Length = utf8Length;
  api.newState = newState;
  api.byNumber = byNumber;
  api.applyFeed = applyFeed;
  api.waitOf = waitOf;
  api.createPoller = createPoller;
  api.createEstimator = createEstimator;
  api.selfHref = selfHref;
  api.splitLinks = splitLinks;
  api.MOST_WAIT = MOST_WAIT;
  api.ESTIMATE_WAIT = ESTIMATE_WAIT;

  /* --- the page ------------------------------------------------------------ */

  /* The theme's own colours, light and dark, as the platform's pages wear
   * them: the accent lines of a card, the page's ground, the sunken ground
   * of a box inside a card. The page's section carries data-forum-theme
   * (the header's switch sets it), and a node made here names both colours,
   * so nothing is redrawn when the switch is pressed. Every class is
   * written whole: the stylesheet is built from what it can read. */
  var LINE = "border-accent-2 group-data-[forum-theme=dark]/forum:border-accent-1";
  var GROUND = "bg-primary-bg-light group-data-[forum-theme=dark]/forum:bg-primary-bg-dark";
  var SUNKEN = "bg-sunken-light group-data-[forum-theme=dark]/forum:bg-sunken-dark";
  var NAME = "text-accent-light group-data-[forum-theme=dark]/forum:text-accent-dark";
  var MINE = "border-accent-light group-data-[forum-theme=dark]/forum:border-accent-dark";
  var FILL = "bg-accent-light group-data-[forum-theme=dark]/forum:bg-accent-dark";
  var TRACK = "bg-black/10 group-data-[forum-theme=dark]/forum:bg-white/10";
  var HOVER = "hover:bg-primary-bg-light/50 group-data-[forum-theme=dark]/forum:hover:bg-primary-bg-dark/50";
  var LINK = "text-link-light group-data-[forum-theme=dark]/forum:text-link-dark";
  var GOOD = "text-success-light group-data-[forum-theme=dark]/forum:text-success-dark";
  var BAD = "text-warn-light group-data-[forum-theme=dark]/forum:text-warn-dark";
  var BUTTON = "rounded-lg border px-2.5 py-1 text-xs font-semibold transition hover:opacity-80";
  var QUIET = "text-xs underline opacity-70 hover:opacity-100";
  var CAPS = "text-xs font-semibold uppercase tracking-wide";

  /* Messages of one sender this close together are drawn under one name. */
  var GROUP_MS = 5 * 60 * 1000;
  /* This near the bottom, the reader is "at the bottom". */
  var NEAR = 60;

  function mount(section) {
    var doc = root.document;
    var config = JSON.parse(doc.getElementById("forum-channel-config").textContent);
    var urls = config.urls || {};
    var limits = config.limits || {};
    var words = section.querySelector("[data-forum-words]").dataset;
    var scroll = section.querySelector("[data-forum-scroll]");
    var list = section.querySelector("[data-forum-messages]");
    var pollBox = section.querySelector("[data-forum-polls]");
    var pollsEmpty = section.querySelector("[data-forum-polls-empty]");
    var status = section.querySelector("[data-forum-status]");
    var liveChip = section.querySelector("[data-forum-live]");
    var older = section.querySelector("[data-forum-older]");
    var newer = section.querySelector("[data-forum-new]");
    var empty = section.querySelector("[data-forum-empty]");
    var postForm = section.querySelector("[data-forum-post]");
    var textBox = section.querySelector("[data-forum-text]");
    var fileInput = section.querySelector("[data-forum-image]");
    var pick = section.querySelector("[data-forum-pick]");
    var picked = section.querySelector("[data-forum-picked]");
    var pickedName = section.querySelector("[data-forum-picked-name]");
    var unpick = section.querySelector("[data-forum-unpick]");
    var send = section.querySelector("[data-forum-send]");
    var estimateBox = section.querySelector("[data-forum-estimate]");
    var pollForm = section.querySelector("[data-forum-poll-form]");
    var csrfInput = section.querySelector("[data-forum-csrf] input[name=csrfmiddlewaretoken]");
    var csrf = csrfInput ? csrfInput.value : "";
    var lang = (doc.documentElement && doc.documentElement.lang) || undefined;
    var place = root.location || {};
    var hosts = [place.hostname].concat(config.link_hosts || []);
    var state = newState();
    var pending = null;      /* the op of a press the server has not answered */
    var busy = false;        /* a post is on its way */
    var blocked = false;     /* the estimate says the balance does not cover it */
    var pinned = true;       /* the reader is at the bottom of the messages */
    var pollSaid = false;    /* the status line holds a sentence of the timer's */

    function setTimer(fn, ms) { return root.setTimeout(fn, ms); }
    function clearTimer(handle) { root.clearTimeout(handle); }

    /* The warning colour on or off: `classes` is one of the pairs above. */
    function tint(node, classes, on) {
      classes.split(" ").forEach(function (name) { node.classList.toggle(name, !!on); });
    }

    function say(text, bad) {
      pollSaid = false;
      status.textContent = text || "";
      status.classList.toggle("hidden", !text);
      status.dataset.bad = bad ? "1" : "";
      tint(status, BAD, !!text && !!bad);
    }

    function ask(url, options) {
      options = options || {};
      options.credentials = "same-origin";
      options.headers = Object.assign({"Accept": "application/json", "X-CSRFToken": csrf},
                                      options.headers || {});
      return root.fetch(url, options).then(function (response) {
        return response.json().then(function (data) {
          return {ok: response.ok, status: response.status, data: data || {}};
        }, function () { return {ok: false, status: response.status, data: {}}; });
      });
    }

    function sendJson(url, body) {
      return ask(url, {method: "POST", headers: {"Content-Type": "application/json"},
                       body: JSON.stringify(body || {})});
    }

    function el(tag, className, text) {
      var node = doc.createElement(tag);
      if (className) { node.className = className; }
      if (text !== undefined && text !== null) { node.textContent = String(text); }
      return node;
    }

    function button(className, text, press) {
      var node = el("button", className, text);
      node.type = "button";
      node.addEventListener("click", press);
      return node;
    }

    /* "14:05" for today, "7 Oct, 14:05" for another day of this year. */
    /* A date in the page's language; in the browser's when the page's is
     * one it does not know. */
    function local(date, method, options) {
      try { return date[method](lang, options); } catch (error) { return date[method](undefined, options); }
    }

    function when(iso) {
      var date = new Date(iso);
      if (isNaN(date.getTime())) { return ""; }
      var now = new Date();
      var time = local(date, "toLocaleTimeString", {hour: "2-digit", minute: "2-digit"});
      if (date.toDateString() === now.toDateString()) { return time; }
      var day = date.getFullYear() === now.getFullYear()
        ? local(date, "toLocaleDateString", {day: "numeric", month: "short"})
        : local(date, "toLocaleDateString", {day: "numeric", month: "short", year: "numeric"});
      return day + ", " + time;
    }

    function whole(iso) {
      var date = new Date(iso);
      return isNaN(date.getTime()) ? "" : local(date, "toLocaleString");
    }

    /* --- where the reader is ------------------------------------------- */

    function nearBottom() {
      return scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < NEAR;
    }

    function toBottom() {
      scroll.scrollTop = scroll.scrollHeight;
      pinned = true;
      if (newer) { newer.classList.add("hidden"); }
    }

    scroll.addEventListener("scroll", function () {
      pinned = nearBottom();
      if (pinned && newer) { newer.classList.add("hidden"); }
    });
    if (newer) { newer.addEventListener("click", toBottom); }

    /* The list grows after it was drawn: the theme's font arrives and the
     * lines wrap anew, a picture gets its size, the box itself is resized.
     * A reader who was at the bottom stays there; one who is reading
     * further up is not moved. */
    if (typeof root.ResizeObserver === "function") {
      var grown = new root.ResizeObserver(function () { if (pinned) { toBottom(); } });
      grown.observe(list);
      grown.observe(scroll);
    } else if (doc.fonts && doc.fonts.ready && typeof doc.fonts.ready.then === "function") {
      doc.fonts.ready.then(function () { if (pinned) { toBottom(); } });
    }

    /* --- messages ------------------------------------------------------- */

    function textNode(row) {
      var body = el("p", "whitespace-pre-wrap break-words text-sm leading-snug [overflow-wrap:anywhere]");
      body.dataset.forumBody = "";
      splitLinks(row.text, hosts, place.origin).forEach(function (part) {
        if (!part.href) { body.appendChild(doc.createTextNode(part.text)); return; }
        var link = el("a", "underline underline-offset-2 hover:opacity-80 " + LINK, part.text);
        link.href = part.href;
        link.rel = "noopener";
        body.appendChild(link);
      });
      return body;
    }

    function messageNode(row) {
      var item = el("li", "group/row relative flex gap-3 rounded-r-lg border-l-2 px-3 py-1 transition " +
                          HOVER + " " + (row.mine ? MINE : "border-transparent"));
      item.dataset.messageId = row.id;
      item.dataset.number = String(row.number);
      item.dataset.at = String(Date.parse(row.created_at) || 0);
      item.dataset.sender = String(row.sender_id) + "|" + String(row.sender);
      if (row.mine) { item.dataset.mine = "1"; }
      item.title = whole(row.created_at);

      var gutter = el("div", "w-8 shrink-0 pt-0.5");
      gutter.dataset.forumGutter = "";
      var face;
      if (row.avatar) {
        face = el("img", "h-8 w-8 rounded-lg border object-cover " + LINE);
        face.alt = "";
        face.loading = "lazy";
        face.src = row.avatar;
      } else {
        face = el("div", "flex h-8 w-8 items-center justify-center rounded-lg border text-xs font-bold " +
                         LINE + " " + SUNKEN, (String(row.sender || "?").trim().charAt(0) || "?").toUpperCase());
        face.setAttribute("aria-hidden", "true");
      }
      gutter.appendChild(face);
      item.appendChild(gutter);

      var main = el("div", "min-w-0 flex-1");
      var head = el("div", "flex flex-wrap items-baseline gap-x-2");
      head.dataset.forumHead = "";
      var name = el("span", "text-sm font-semibold " + NAME, row.sender);
      name.dataset.forumSender = "";
      head.appendChild(name);
      var time = el("time", "text-xs opacity-60", when(row.created_at));
      time.setAttribute("datetime", String(row.created_at));
      head.appendChild(time);
      if (row.mine) { head.appendChild(el("span", CAPS + " opacity-50", words.you)); }
      main.appendChild(head);
      if (row.unreadable) {
        main.appendChild(el("p", "text-sm italic opacity-60", words.unreadable));
      } else if (row.text) {
        main.appendChild(textNode(row));
      }
      if (row.image && row.image.url) {
        var door = el("a", "mt-1 inline-block");
        door.href = row.image.url;
        door.target = "_blank";
        door.rel = "noopener noreferrer";
        var image = el("img", "max-h-56 max-w-[min(100%,18rem)] rounded-lg border object-contain " + LINE);
        image.alt = words.image;
        image.loading = "lazy";
        image.addEventListener("load", function () { if (pinned) { toBottom(); } });
        image.src = row.image.url;
        door.appendChild(image);
        main.appendChild(door);
      }
      item.appendChild(main);

      if (row.may_remove) {
        item.appendChild(button(
          "shrink-0 self-start lg:opacity-0 lg:focus:opacity-100 lg:group-hover/row:opacity-100 " + QUIET,
          words.remove, function () {
            if (root.confirm && !root.confirm(words.confirmRemove)) { return; }
            sendJson(urlFor(urls.message_remove, urls.nil, row.id)).then(mine, failed);
          }));
      }
      return item;
    }

    /* What stands where a removed message stood: one quiet line. */
    function goneNode(old) {
      var item = el("li", "py-1 pl-14 pr-3 text-xs italic opacity-50", words.gone);
      item.dataset.forumGone = "";
      item.dataset.number = old.dataset.number;
      item.dataset.at = old.dataset.at;
      return item;
    }

    function messageById(id) {
      return list.querySelector('[data-message-id="' + id + '"]');
    }

    /* Into the conversation at its number's place: a message, or the
     * question of an open poll (one run of numbers serves both). From the
     * end: a new row nearly always belongs there. */
    function inOrder(node, number) {
      var after = null;
      var rows = list.children;
      for (var i = rows.length - 1; i >= 0; i--) {
        if (Number(rows[i].dataset.number) < number) { break; }
        after = rows[i];
      }
      list.insertBefore(node, after);
    }

    function drawMessage(row) {
      var node = messageNode(row);
      var old = messageById(row.id);
      if (old) { list.replaceChild(node, old); return; }
      inOrder(node, row.number);
    }

    /* Consecutive messages of one sender, minutes apart, under one name. */
    function regroup() {
      var before = null;
      Array.prototype.forEach.call(list.children, function (node) {
        if (node.dataset.messageId) {
          var joined = !!(before && before.dataset.messageId &&
            before.dataset.sender === node.dataset.sender &&
            Number(node.dataset.at) - Number(before.dataset.at) < GROUP_MS &&
            new Date(Number(node.dataset.at)).toDateString() ===
              new Date(Number(before.dataset.at)).toDateString());
          var head = node.querySelector("[data-forum-head]");
          var gutter = node.querySelector("[data-forum-gutter]");
          if (head) { head.classList.toggle("hidden", joined); }
          if (gutter) { gutter.classList.toggle("invisible", joined); }
          if (gutter) { gutter.classList.toggle("h-0", joined); }
          node.classList.toggle("mt-2", !joined);
          node.dataset.joined = joined ? "1" : "";
        }
        before = node;
      });
    }

    /* --- polls ----------------------------------------------------------- */

    function fillPoll(card, row) {
      var body = card.querySelector("[data-forum-poll-body]");
      body.textContent = "";
      card.dataset.seq = String(row.seq);
      card.dataset.open = row.open ? "1" : "";
      var answered = row.my_choice !== null && row.my_choice !== undefined;

      var head = el("div", "flex items-baseline gap-2");
      head.appendChild(el("span", CAPS + " opacity-60", words.poll));
      head.appendChild(el("span", "ml-auto " + CAPS + " " + (row.open ? GOOD : "opacity-50"),
                          row.open ? words.open : words.closed));
      body.appendChild(head);
      body.appendChild(el("h3", "mt-1 break-words text-sm font-bold leading-snug [overflow-wrap:anywhere]",
                          row.title));
      var facts = [row.opener, words.ruleFinal];
      if (row.closes_at) { facts.push(words.closes.replace("{when}", when(row.closes_at))); }
      body.appendChild(el("p", "mt-1 text-xs leading-snug opacity-60",
                          facts.filter(Boolean).join(" · ")));

      var options = el("ul", "mt-2 space-y-2");
      var ringed = ringWanted(row);
      row.choices.forEach(function (choice, at) {
        var line = el("li", "text-sm");
        line.dataset.choiceId = String(choice.id);
        var top = el("div", "flex items-center gap-2");
        var chosen = answered && row.my_choice === choice.id;
        if (ringed) {
          /* The list is the ring's legend: an option wears its slice's colour. */
          var dot = el("span", "h-2.5 w-2.5 shrink-0 rounded-full");
          dot.style.backgroundColor = SLICES[at % SLICES.length];
          dot.dataset.forumSlice = SLICES[at % SLICES.length];
          dot.setAttribute("aria-hidden", "true");
          top.appendChild(dot);
        }
        var label = el("span", "min-w-0 break-words [overflow-wrap:anywhere]");
        label.appendChild(el("span", "font-semibold", choice.label));
        if (choice.text) { label.appendChild(el("span", "ml-1.5 opacity-70", choice.text)); }
        top.appendChild(label);
        if (chosen) {
          var yours = el("span", "ml-auto shrink-0 " + CAPS + " " + NAME, words.yourAnswer);
          yours.dataset.forumYours = "";
          top.appendChild(yours);
        }
        line.appendChild(top);
        if (choice.ballots !== null && choice.ballots !== undefined) {
          var share = row.total ? Math.round(100 * choice.ballots / row.total) : 0;
          var meter = el("div", "mt-1 flex items-center gap-2");
          var track = el("div", "h-2 min-w-0 flex-1 overflow-hidden rounded-full " + TRACK);
          var fill = el("div", "h-full rounded-full " + FILL);
          fill.style.width = share + "%";
          fill.dataset.forumBar = String(share);
          track.appendChild(fill);
          meter.appendChild(track);
          var tally = el("span", "w-20 shrink-0 text-right text-xs tabular-nums opacity-70",
                         choice.ballots + " · " + share + "%");
          tally.dataset.forumBallots = String(choice.ballots);
          meter.appendChild(tally);
          line.appendChild(meter);
        }
        options.appendChild(line);
      });
      body.appendChild(options);

      var foot = el("p", "mt-2 text-xs opacity-60",
                    row.total === null || row.total === undefined
                      ? words.hiddenCount
                      : words.answersCount.replace("{count}", String(row.total)));
      foot.dataset.forumTotal = row.total === null || row.total === undefined ? "" : String(row.total);
      body.appendChild(foot);

      /* Results only: the answer is given in the conversation, and this
       * leads who has not answered to the question there. */
      if (row.open && !answered) {
        var go = button("mt-2 " + BUTTON + " " + LINE + " " + GROUND, words.toQuestion, function () {
          showTab("messages", true);
          var asked = questionById(row.id);
          if (asked && typeof asked.scrollIntoView === "function") {
            asked.scrollIntoView({block: "center"});
            pinned = nearBottom();
          }
        });
        go.dataset.forumToQuestion = "";
        body.appendChild(go);
      }

      if (row.may_manage) {
        var tools = el("div", "mt-2 flex gap-3 border-t pt-2 " + LINE);
        if (row.open) {
          tools.appendChild(button(QUIET, words.close, function () {
            sendJson(urlFor(urls.poll_close, urls.nil, row.id)).then(mine, failed);
          }));
        }
        tools.appendChild(button(QUIET, words.remove, function () {
          if (root.confirm && !root.confirm(words.confirmRemove)) { return; }
          sendJson(urlFor(urls.poll_remove, urls.nil, row.id)).then(mine, failed);
        }));
        body.appendChild(tools);
      }
    }

    /* --- a poll's ring -----------------------------------------------------------
     * The owner, 2026-10-07: "poll results should also have pie chart". It is
     * the ring of the vault's "files by type" and of a company's ownership:
     * Chart.js, a doughnut with a 60% hole, beside the bars. It has no legend
     * of its own: the options' list is the legend, each option wearing its
     * slice's colour, so the ring writes no word but a slice's tip, and that
     * on its canvas. Drawn where there is a count to draw (`ringData`) and
     * Chart.js loaded; without either the bars say everything it would.
     *
     * A ring is made while the Polls tab is open and no sooner: one made in a
     * panel that is not shown has no size to be drawn at, and most readers of
     * a channel never open the tab. It keeps its canvas and is handed the new
     * counts, so it does not blink when an answer arrives. */
    var rings = {};

    function ringWanted(row) {
      return typeof root.Chart === "function" && ringData(row) !== null;
    }

    function dropRing(id) {
      var ring = rings[id];
      if (!ring) { return; }
      delete rings[id];
      ring.chart.destroy();
      if (ring.box.parentNode) { ring.box.parentNode.removeChild(ring.box); }
    }

    function drawRing(row) {
      var card = pollById(row.id);
      var data = card && ringWanted(row) ? ringData(row) : null;
      if (!data) { dropRing(row.id); return; }
      if (openTab !== "polls") { return; }
      var ring = rings[row.id];
      if (ring) {
        ring.chart.data.labels = data.labels;
        ring.chart.data.datasets[0].data = data.values;
        ring.chart.data.datasets[0].backgroundColor = data.colours;
        ring.chart.update();
        return;
      }
      var box = el("div", "relative mx-auto mt-3 h-36 w-36 shrink-0 sm:mt-0");
      box.dataset.forumRing = "";
      var canvas = el("canvas");
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", words.ring);
      box.appendChild(canvas);
      card.appendChild(box);
      rings[row.id] = {box: box, chart: new root.Chart(canvas, {
        type: "doughnut",
        data: {labels: data.labels,
               datasets: [{data: data.values, backgroundColor: data.colours, borderWidth: 0}]},
        options: {responsive: true, maintainAspectRatio: false, cutout: "60%",
                  plugins: {legend: {display: false}, tooltip: {callbacks: {label: ringTip}}}}
      })};
    }

    /* Every poll's ring, as the page holds the polls now: the Polls tab was
     * opened, and what arrived while it was shut is in them. */
    function drawRings() {
      byNumber(state.polls).forEach(drawRing);
    }

    function pollById(id) {
      return pollBox.querySelector('[data-poll-id="' + id + '"]');
    }

    /* --- a poll's question, in the conversation ------------------------------
     * The Polls tab holds results only. The QUESTION of a poll that is open
     * stands in the conversation itself, at the place where the poll was
     * opened (a poll takes its number from the same run as the messages):
     * the options, one to choose, and Submit. An answer is final, so it is
     * chosen first and sent on purpose; who has answered reads their answer
     * there instead. A closed or a removed poll leaves the conversation, so
     * no more questions stand in it than the server lets be open (three). */

    function questionById(id) {
      return list.querySelector('[data-question-id="' + id + '"]');
    }

    function tickedIn(box) {
      var found = null;
      Array.prototype.forEach.call(box.querySelectorAll("input"), function (input) {
        if (input.checked) { found = input.value; }
      });
      return found;
    }

    function questionNode(row, answered) {
      var item = el("li", "my-2 rounded-xl border p-3 text-sm " + LINE + " " + SUNKEN);
      item.dataset.questionId = row.id;
      item.dataset.number = String(row.number);
      item.dataset.at = String(Date.parse(row.created_at) || 0);
      item.dataset.state = answered ? "answered" : "asked";
      item.title = whole(row.created_at);

      var head = el("div", "flex items-baseline gap-2");
      head.appendChild(el("span", CAPS + " opacity-60", words.poll));
      var facts = [row.opener, words.ruleFinal];
      if (row.closes_at) { facts.push(words.closes.replace("{when}", when(row.closes_at))); }
      head.appendChild(el("span", "min-w-0 truncate text-xs opacity-60", facts.filter(Boolean).join(" · ")));
      item.appendChild(head);
      item.appendChild(el("h3", "mt-1 break-words font-bold leading-snug [overflow-wrap:anywhere]", row.title));

      var results = button(QUIET, words.results, function () { showTab("polls", true); });
      results.dataset.forumToResults = "";

      if (answered) {
        var said = el("p", "mt-2 break-words [overflow-wrap:anywhere]");
        said.dataset.forumAnswered = "";
        said.appendChild(el("span", CAPS + " " + NAME, words.yourAnswer));
        row.choices.forEach(function (choice) {
          if (choice.id !== row.my_choice) { return; }
          said.appendChild(el("span", "ml-2 font-semibold", choice.label));
          if (choice.text) { said.appendChild(el("span", "ml-1.5 opacity-70", choice.text)); }
        });
        item.appendChild(said);
        var under = el("div", "mt-2");
        under.appendChild(results);
        item.appendChild(under);
        return item;
      }

      var form = el("form", "mt-2");
      form.setAttribute("aria-label", words.question);
      var send = el("button", "rounded-lg px-4 py-1.5 text-xs font-semibold shadow-sm transition hover:opacity-90 " +
                              "disabled:cursor-not-allowed disabled:opacity-40 " + FILL + " " +
                              "text-primary-bg-light group-data-[forum-theme=dark]/forum:text-primary-bg-dark",
                    words.submit);
      send.type = "submit";
      send.dataset.forumSubmit = "";
      send.disabled = true;
      var options = el("div", "space-y-1");
      row.choices.forEach(function (choice) {
        var line = el("label", "flex cursor-pointer items-start gap-2");
        var input = el("input", "mt-1 shrink-0");
        input.type = "radio";
        input.name = "choice_" + row.id;
        input.value = String(choice.id);
        input.addEventListener("change", function () { send.disabled = false; });
        line.appendChild(input);
        var label = el("span", "min-w-0 break-words [overflow-wrap:anywhere]");
        label.appendChild(el("span", "font-semibold", choice.label));
        if (choice.text) { label.appendChild(el("span", "ml-1.5 opacity-70", choice.text)); }
        line.appendChild(label);
        options.appendChild(line);
      });
      form.appendChild(options);
      var foot = el("div", "mt-2 flex items-center gap-3");
      foot.appendChild(send);
      foot.appendChild(results);
      form.appendChild(foot);
      form.addEventListener("submit", function (event) {
        if (event && event.preventDefault) { event.preventDefault(); }
        var choice = tickedIn(form);
        if (choice === null || send.disabled) { return; }
        send.disabled = true;
        sendJson(urlFor(urls.poll_vote, urls.nil, row.id), {choice: Number(choice)})
          .then(function (answer) { if (!answer.ok) { send.disabled = false; } mine(answer); },
                function () { send.disabled = false; failed(); });
      });
      item.appendChild(form);
      return item;
    }

    /* True when the question is new to the conversation. One that stands
     * keeps its node, and what is ticked in it, until its state changes:
     * asked, answered, gone. The counts are the Polls tab's, not its. */
    function drawQuestion(row) {
      var old = questionById(row.id);
      if (!row.open) {
        if (old) { list.removeChild(old); }
        return false;
      }
      var answered = row.my_choice !== null && row.my_choice !== undefined;
      if (old && old.dataset.state === (answered ? "answered" : "asked")) { return false; }
      var node = questionNode(row, answered);
      if (old) { list.replaceChild(node, old); return false; }
      inOrder(node, row.number);
      return true;
    }

    /* A poll that is there keeps its card: the card is filled again, so its
     * counts, its bars and its buttons are the answer's. The newest first.
     * True when its question is new to the conversation. */
    function drawPoll(row) {
      var card = pollById(row.id);
      if (!card) {
        /* The body is filled again with every answer; the ring beside it
         * (under it on a narrow screen) is kept. */
        card = el("article", "rounded-xl border p-3 sm:flex sm:items-center sm:gap-4 " + LINE + " " + SUNKEN);
        card.dataset.pollId = row.id;
        card.dataset.number = String(row.number);
        card.dataset.at = String(Date.parse(row.created_at) || 0);
        var body = el("div", "min-w-0 flex-1");
        body.dataset.forumPollBody = "";
        card.appendChild(body);
        var after = null;
        var cards = pollBox.children;
        for (var i = 0; i < cards.length; i++) {
          if (Number(cards[i].dataset.number) < row.number) { after = cards[i]; break; }
        }
        pollBox.insertBefore(card, after);
      }
      fillPoll(card, row);
      drawRing(row);
      return drawQuestion(row);
    }

    /* --- one answer onto the page ------------------------------------------ */

    /* `how`: "first" (the page's own first feed), "live" (the timer),
     * "older" (history asked for), "mine" (what the member just did). */
    function draw(changed, how) {
      var wasPinned = pinned || how === "first";
      var fromBottom = scroll.scrollHeight - scroll.scrollTop;
      var purged = {};
      changed.purgedMessages.forEach(function (id) { purged[id] = true; });
      changed.removedMessages.forEach(function (id) {
        var node = messageById(id);
        if (!node) { return; }
        if (purged[id]) { list.removeChild(node); }
        else { list.replaceChild(goneNode(node), node); }
      });
      changed.removedPolls.forEach(function (id) {
        dropRing(id);
        var node = pollById(id);
        if (node) { pollBox.removeChild(node); }
        var asked = questionById(id);
        if (asked) { list.removeChild(asked); }
      });
      if (changed.edge !== null) {
        /* The quiet lines of removed messages from before the cleanup. */
        Array.prototype.slice.call(list.children).forEach(function (node) {
          if (Number(node.dataset.at) < changed.edge) { list.removeChild(node); }
        });
      }
      changed.messages.forEach(drawMessage);
      var questions = 0;
      changed.polls.forEach(function (row) { if (drawPoll(row)) { questions += 1; } });
      regroup();

      older.classList.toggle("hidden", !state.more);
      empty.classList.toggle("hidden", list.children.length > 0);
      if (pollsEmpty) { pollsEmpty.classList.toggle("hidden", pollBox.children.length > 0); }
      Array.prototype.forEach.call(section.querySelectorAll("[data-forum-poll-count]"),
        function (node) { node.textContent = String(pollBox.children.length); });

      if (how === "older") {
        scroll.scrollTop = scroll.scrollHeight - fromBottom;
      } else if (wasPinned || how === "mine") {
        toBottom();
      } else if (newer && (changed.messages.length || questions)) {
        newer.classList.remove("hidden");
      }
    }

    function failed() { say(words.failed, true); }

    /* What a door answered to the member's own press, drawn at once. The
     * next answer of the feed holds the same row with the same `seq`, and
     * `applyFeed` passes it by. */
    function mine(answer) {
      if (!answer.ok) { say(answer.data.error || words.failed, true); return answer; }
      say("");
      var rows = {};
      if (answer.data.message) { rows.messages = [answer.data.message]; }
      if (answer.data.poll) { rows.polls = [answer.data.poll]; }
      draw(applyFeed(state, rows), answer.data.message && !answer.data.message.removed ? "mine" : "live");
      return answer;
    }

    /* --- the timer ------------------------------------------------------------ */

    function showLive(ok) {
      if (!liveChip) { return; }
      liveChip.textContent = ok ? words.live : words.retrying;
      liveChip.dataset.state = ok ? "live" : "retrying";
      liveChip.classList.toggle("opacity-60", ok);
      tint(liveChip, BAD, !ok);
    }

    function askFeed() {
      return ask(urls.feed + "?after=" + state.cursor).then(function (answer) {
        if (!answer.ok) {
          if (answer.data.error) { say(answer.data.error, true); pollSaid = true; }
          return {ok: false};
        }
        if (pollSaid) { say(""); }
        draw(applyFeed(state, answer.data), "live");
        return {ok: true, more: !!answer.data.more};
      });
    }

    var poller = createPoller({
      ask: askFeed,
      seconds: config.refresh_seconds,
      hidden: function () { return doc.visibilityState === "hidden" || doc.hidden === true; },
      setTimer: setTimer,
      clearTimer: clearTimer,
      onState: function (now) { showLive(now.ok); }
    });

    /* --- the composer -------------------------------------------------------- */

    function chosenFile() {
      return fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
    }

    function syncSend() {
      send.disabled = busy || blocked;
      if (busy) { send.setAttribute("aria-busy", "true"); } else { send.removeAttribute("aria-busy"); }
    }

    function showEstimate(data) {
      blocked = !!data && data.affordable === false;
      if (estimateBox) {
        /* Where nothing is priced the door's `display` is empty: nothing to say. */
        if (!data || !data.display) {
          estimateBox.textContent = "";
          estimateBox.classList.add("hidden");
        } else {
          estimateBox.textContent = words.cost.replace("{amount}", String(data.display)) +
            (blocked ? " " + words.unaffordable : "");
          estimateBox.classList.remove("hidden");
        }
        estimateBox.dataset.bad = blocked ? "1" : "";
        tint(estimateBox, BAD, blocked);
      }
      syncSend();
    }

    var estimator = null;
    if (urls.estimate && estimateBox) {
      estimator = createEstimator({
        wait: ESTIMATE_WAIT,
        setTimer: setTimer,
        clearTimer: clearTimer,
        show: showEstimate,
        ask: function (textBytes, imageBytes) {
          var body = new root.URLSearchParams();
          body.append("text_bytes", String(textBytes));
          body.append("image_bytes", String(imageBytes));
          return ask(urls.estimate, {method: "POST", body: body});
        }
      });
    }

    function estimate(atOnce) {
      if (!estimator) { return; }
      var file = chosenFile();
      estimator.request(utf8Length(textBox.value.trim()), file ? Number(file.size) || 0 : 0, atOnce);
    }

    function grow() {
      if (!textBox.style) { return; }
      textBox.style.height = "auto";
      if (textBox.scrollHeight) { textBox.style.height = Math.min(textBox.scrollHeight + 2, 160) + "px"; }
    }

    function showPicked() {
      var file = chosenFile();
      if (picked) {
        picked.classList.toggle("hidden", !file);
        picked.classList.toggle("flex", !!file);
      }
      if (pickedName) {
        pickedName.textContent = file
          ? String(file.name) + " · " + Math.max(1, Math.round((Number(file.size) || 0) / 1024)) + " KB"
          : "";
      }
    }

    function clearFile() {
      fileInput.value = "";
      showPicked();
    }

    fileInput.addEventListener("change", function () {
      var file = chosenFile();
      if (file) {
        var types = limits.image_types || [];
        if (file.type && types.length && types.indexOf(file.type) === -1) {
          clearFile(); say(words.imageType, true); estimate(true); return;
        }
        if (limits.image_bytes && Number(file.size) > limits.image_bytes) {
          clearFile(); say(words.imageLarge, true); estimate(true); return;
        }
        say("");
      }
      showPicked();
      estimate(true);
    });
    if (pick) { pick.addEventListener("click", function () { fileInput.click(); }); }
    if (unpick) { unpick.addEventListener("click", function () { clearFile(); estimate(true); }); }

    textBox.addEventListener("input", function () { grow(); estimate(false); });
    textBox.addEventListener("keydown", function (event) {
      /* Enter sends, Shift+Enter is a new line; on a touch keyboard Enter is
       * a new line and Send is the button. */
      if (event.key !== "Enter" || event.shiftKey || event.isComposing) { return; }
      if (root.matchMedia && root.matchMedia("(pointer: coarse)").matches) { return; }
      event.preventDefault();
      post();
    });

    function post() {
      if (busy || blocked) { return null; }
      var text = textBox.value;
      var file = chosenFile();
      if (!text.trim() && !file) { textBox.focus(); return null; }
      /* The same op again only for the same press, never answered. */
      if (!pending || pending.text !== text || pending.file !== file) {
        pending = {op: mintOp(), text: text, file: file};
      }
      var body = new root.FormData();
      body.append("op", pending.op);
      body.append("text", text);
      if (file) { body.append("image", file); }
      busy = true;
      syncSend();
      say(words.sending);
      return ask(urls.post, {method: "POST", body: body}).then(function (answer) {
        busy = false;
        if (answer.status < 500) { pending = null; }
        if (!answer.ok) {
          /* The door's own sentence (not enough mana, too fast, too long),
           * and what was typed stays in the box. */
          say(answer.data.error || words.failed, true);
          syncSend();
          return answer;
        }
        textBox.value = "";
        clearFile();
        grow();
        if (estimator) { estimator.cancel(); }
        showEstimate(null);
        mine(answer);
        return answer;
      }, function () {
        busy = false;
        syncSend();
        failed();
      });
    }

    postForm.addEventListener("submit", function (event) {
      event.preventDefault();
      post();
    });

    /* --- opening a poll -------------------------------------------------------- */

    /* The Polls tab's "Create poll" opens a dialog, and a poll is opened
     * there and nowhere else (the owner, 2026-10-07). The element is the
     * library's modal (the template: role="dialog", x-show and x-trap on its
     * own `open`, Escape, the backdrop, the X): the page tells it to open or
     * close with the event "forum-poll-dialog" and hears
     * "forum-poll-dismiss" when the member leaves it. Leaving keeps what was
     * typed. A refusal is said inside the dialog: the status line is under
     * another tab. `data-open` says the same to whoever reads the page. */
    var pollDialog = section.querySelector("[data-forum-poll-dialog]");
    var pollCreate = section.querySelector("[data-forum-poll-create]");
    var pollError = section.querySelector("[data-forum-poll-error]");
    var pollShown = false;

    function pollSay(text) {
      if (!pollError) { return; }
      pollError.textContent = text || "";
      pollError.classList.toggle("hidden", !text);
    }

    function showPollDialog(on) {
      if (!pollDialog) { return; }
      pollShown = !!on;
      pollDialog.dataset.open = pollShown ? "1" : "";
      if (typeof root.CustomEvent === "function") {
        pollDialog.dispatchEvent(new root.CustomEvent("forum-poll-dialog", {detail: {open: pollShown}}));
      }
    }

    if (pollDialog) {
      pollDialog.addEventListener("forum-poll-dismiss", function () {
        if (pollShown) { showPollDialog(false); }
      });
    }
    if (pollCreate) {
      pollCreate.addEventListener("click", function () {
        pollSay("");
        showPollDialog(true);
      });
    }

    if (pollForm) {
      var pollBusy = false;
      pollForm.addEventListener("submit", function (event) {
        event.preventDefault();
        if (pollBusy) { return; }
        var title = pollForm.querySelector("[name=title]");
        var options = pollForm.querySelector("[name=options]");
        var closes = pollForm.querySelector("[name=closes_at]");
        var body = {
          title: title.value, options: options.value,
          visibility: pollForm.querySelector("[name=visibility]").value
        };
        if (closes && closes.value) {
          var moment = new Date(closes.value);
          body.closes_at = isNaN(moment.getTime()) ? closes.value : moment.toISOString();
        }
        pollBusy = true;
        pollSay("");
        sendJson(urls.poll_open, body).then(function (answer) {
          pollBusy = false;
          if (!answer.ok) { pollSay(answer.data.error || words.failed); return; }
          title.value = ""; options.value = "";
          if (closes) { closes.value = ""; }
          showPollDialog(false);
          mine(answer);
          /* The question stands at the end of the conversation: the opener
           * finds it there on going to Messages. */
          toBottom();
        }, function () { pollBusy = false; pollSay(words.failed); });
      });
    }

    /* --- the tabs: Messages, Polls, Search, Images ----------------------------- */

    /* One panel shows at a time. The server draws the one the address asks
     * for (?tab=polls); a press changes it here, asks the server nothing and
     * rewrites the address in place, so a reload and a link keep the tab.
     * Settings is a page of its own: its tab is a plain link. */
    var tabLinks = section.querySelectorAll("[data-forum-tab]");
    var tabPanels = section.querySelectorAll("[data-forum-tabpanel]");
    var openTab = "messages";

    function showTab(name, write) {
      var known = false;
      Array.prototype.forEach.call(tabPanels, function (panel) {
        if (panel.getAttribute("data-forum-tabpanel") === name) { known = true; }
      });
      if (!known) { name = "messages"; }
      Array.prototype.forEach.call(tabPanels, function (panel) {
        if (panel.getAttribute("data-forum-tabpanel") === name) { panel.classList.remove("hidden"); }
        else { panel.classList.add("hidden"); }
      });
      Array.prototype.forEach.call(tabLinks, function (link) {
        link.setAttribute("aria-selected", link.getAttribute("data-forum-tab") === name ? "true" : "false");
      });
      openTab = name;
      if (write && root.history && root.history.replaceState && place.pathname) {
        root.history.replaceState(null, "", place.pathname + (name === "messages" ? "" : "?tab=" + name));
      }
      if (name === "messages" && pinned) { toBottom(); }
      if (name === "polls") { drawRings(); }
      if (name === "images") { loadImages(true); }
    }

    Array.prototype.forEach.call(tabLinks, function (link) {
      link.addEventListener("click", function (event) {
        if (event && event.preventDefault) { event.preventDefault(); }
        showTab(link.getAttribute("data-forum-tab"), true);
      });
    });

    /* --- search through this channel's messages -------------------------------- */

    /* Asked with the button or Enter, never while typing: the door opens the
     * messages one by one to read them. What is found is drawn as a message
     * is, as text; nothing of the search is kept here or there. */
    var searchForm = section.querySelector("[data-forum-search]");
    var searchBox = section.querySelector("[data-forum-search-q]");
    var searchList = section.querySelector("[data-forum-search-results]");
    var searchNote = section.querySelector("[data-forum-search-note]");

    function searchSay(text, bad) {
      if (!searchNote) { return; }
      searchNote.textContent = text || "";
      if (text) { searchNote.classList.remove("hidden"); } else { searchNote.classList.add("hidden"); }
      tint(searchNote, BAD, !!text && !!bad);
    }

    if (searchForm && searchBox && searchList && urls.search) {
      searchForm.addEventListener("submit", function (event) {
        if (event && event.preventDefault) { event.preventDefault(); }
        var query = String(searchBox.value || "").trim();
        if (!query) { return; }
        searchSay(words.searching);
        ask(urls.search + "?q=" + encodeURIComponent(query)).then(function (answer) {
          searchList.textContent = "";
          if (!answer.ok) { searchSay(answer.data.error || words.failed, true); return; }
          var found = answer.data.messages || [];
          found.forEach(function (row) { searchList.appendChild(messageNode(row)); });
          var said = found.length ? String(words.searchFound).replace("{count}", String(found.length))
                                  : words.searchNone;
          searchSay(answer.data.capped ? said + " " + words.searchCapped : said);
        }, function () { searchSay(words.failed, true); });
      });
    }

    /* --- every picture of the channel ------------------------------------------ */

    /* Asked for when the Images tab is opened, the newest first, a page at a
     * time. A picture's address is the image door's, which asks who may see
     * it every time; the name under it is text. */
    var imageGrid = section.querySelector("[data-forum-images]");
    var imagesEmpty = section.querySelector("[data-forum-images-empty]");
    var imagesOlder = section.querySelector("[data-forum-images-older]");
    var imagesOldest = null;
    var imagesBusy = false;

    function imageCell(row) {
      var cell = el("a", "block overflow-hidden rounded-lg border transition hover:opacity-90 " + LINE + " " + GROUND);
      cell.href = row.image.url;
      cell.target = "_blank";
      cell.rel = "noopener noreferrer";
      cell.dataset.messageId = row.id;
      var image = el("img", "aspect-square w-full object-cover");
      image.alt = words.image;
      image.loading = "lazy";
      image.src = row.image.url;
      cell.appendChild(image);
      cell.appendChild(el("span", "block truncate px-2 pt-1 text-xs font-semibold " + NAME, row.sender));
      cell.appendChild(el("span", "block truncate px-2 pb-1 text-xs opacity-60", when(row.created_at)));
      return cell;
    }

    function loadImages(fresh) {
      if (!imageGrid || !urls.images || imagesBusy) { return; }
      if (!fresh && imagesOldest === null) { return; }
      imagesBusy = true;
      ask(urls.images + (fresh ? "" : "?before=" + imagesOldest)).then(function (answer) {
        imagesBusy = false;
        if (!answer.ok) { say(answer.data.error || words.failed, true); return; }
        if (fresh) { imageGrid.textContent = ""; }
        var rows = answer.data.messages || [];
        rows.forEach(function (row) { if (row.image && row.image.url) { imageGrid.appendChild(imageCell(row)); } });
        imagesOldest = answer.data.oldest === undefined ? null : answer.data.oldest;
        if (imagesEmpty) {
          if (imageGrid.children.length) { imagesEmpty.classList.add("hidden"); }
          else { imagesEmpty.classList.remove("hidden"); }
        }
        if (imagesOlder) {
          if (answer.data.more) { imagesOlder.classList.remove("hidden"); }
          else { imagesOlder.classList.add("hidden"); }
        }
      }, function () { imagesBusy = false; failed(); });
    }

    if (imagesOlder) { imagesOlder.addEventListener("click", function () { loadImages(false); }); }

    /* --- the channels' card on a narrow screen ---------------------------------- */

    Array.prototype.forEach.call(section.querySelectorAll("[data-forum-toggle]"), function (knob) {
      knob.addEventListener("click", function () {
        var name = knob.dataset.forumToggle;
        Array.prototype.forEach.call(section.querySelectorAll("[data-forum-panel]"), function (panel) {
          var show = panel.dataset.forumPanel === name && panel.classList.contains("hidden");
          panel.classList.toggle("hidden", !show);
          panel.classList.toggle("flex", show);
        });
        Array.prototype.forEach.call(section.querySelectorAll("[data-forum-toggle]"), function (other) {
          var panel = section.querySelector('[data-forum-panel="' + other.dataset.forumToggle + '"]');
          other.setAttribute("aria-expanded", panel && !panel.classList.contains("hidden") ? "true" : "false");
        });
      });
    });

    /* --- start ---------------------------------------------------------------- */

    /* The box fills the window under whatever stands above it, so only the
     * messages scroll and the composer stays on the screen. */
    function fit() {
      if (!section.getBoundingClientRect || !root.innerHeight) { return; }
      var top = section.getBoundingClientRect().top + (root.scrollY || 0);
      section.style.height = Math.max(320, Math.round(root.innerHeight - top)) + "px";
      dodge();
      if (pinned) { toBottom(); }
    }

    /* A button the platform floats in the window's corner (what an app
     * costs) may lie on Send on a narrow screen: the composer then keeps
     * clear of it. */
    function dodge() {
      if (!doc.elementFromPoint || !send.getBoundingClientRect) { return; }
      postForm.style.paddingRight = "";
      if (estimateBox) { estimateBox.style.paddingRight = ""; }
      var box = send.getBoundingClientRect();
      var over = doc.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      if (!over || section.contains(over) || !over.getBoundingClientRect) { return; }
      var shift = Math.ceil(box.right - over.getBoundingClientRect().left) + 8;
      if (shift <= 0 || shift >= 200) { return; }
      postForm.style.paddingRight = shift + "px";
      if (estimateBox) { estimateBox.style.paddingRight = shift + "px"; }
    }
    fit();
    if (section.getBoundingClientRect && root.addEventListener) {
      root.addEventListener("resize", fit);
    }

    draw(applyFeed(state, config.feed), "first");
    showLive(true);
    Array.prototype.forEach.call(tabLinks, function (link) {
      if (link.getAttribute("aria-selected") === "true") { openTab = link.getAttribute("data-forum-tab"); }
    });
    if (openTab !== "messages") { showTab(openTab, false); }

    section.querySelector("[data-forum-refresh]").addEventListener("click", function () {
      say("");
      poller.now();
    });

    older.addEventListener("click", function () {
      if (state.oldest === null) { return; }
      ask(urls.feed + "?before=" + state.oldest).then(function (answer) {
        if (!answer.ok) { say(answer.data.error || words.failed, true); return; }
        draw(applyFeed(state, answer.data), "older");
      }, failed);
    });

    doc.addEventListener("visibilitychange", function () { poller.visibility(); });
    poller.start();

    return {state: state, refresh: poller.now, poller: poller, config: config, post: post,
            showTab: showTab, tab: function () { return openTab; }};
  }

  api.mount = mount;
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
  root.TotoForumChannel = api;
  if (root.document && root.document.addEventListener && root.document.querySelectorAll) {
    var all = function () {
      Array.prototype.forEach.call(
        root.document.querySelectorAll("[data-forum-channel]"), mount);
    };
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", all);
    } else { all(); }
  }
})(typeof window !== "undefined" ? window : globalThis);

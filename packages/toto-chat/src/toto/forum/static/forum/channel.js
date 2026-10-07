/* One community's channel (stage 68, 2026-10-07): plain and working.
 *
 * Two halves. The functions at the top have no page in them (the state a
 * page keeps of its channel, and what one feed answer does to it) and can
 * run under node. `mount` is the page.
 *
 * THE RULES KEPT HERE
 *  - Every name, message, question and option is written as TEXT
 *    (textContent). Nothing a member wrote is ever put into the page as
 *    markup, and an address in a message is not made a link.
 *  - The page keeps a CURSOR: the last event of the channel it has seen.
 *    `applyFeed` merges one answer of the feed door into the state and says
 *    what changed, so the page touches only those nodes: a new message is
 *    added, a removed one is taken out, a poll's counts are redrawn. History
 *    is never loaded again.
 *  - A post carries an `op` minted once per deliberate press. It is kept and
 *    sent again only when the server never answered, so a retry is the same
 *    post and is stored (and, from stage 69, charged) once.
 *  - This page asks the feed when it is loaded, when Refresh is pressed and
 *    after each thing the member does. A timer that asks by itself, paused
 *    in a hidden tab, is stage 70's: it calls `refresh`.
 */
(function (root) {
  "use strict";

  var api = {};

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

  function newState() {
    return {cursor: 0, messages: {}, polls: {}, oldest: null, more: false, purgedBefore: null};
  }

  function byNumber(map) {
    return Object.keys(map).map(function (id) { return map[id]; })
      .sort(function (a, b) { return a.number - b.number; });
  }

  /* Merge one answer of the feed door into `state`. Returns what changed:
   * {messages: [added or changed], removedMessages: [ids], polls: [added or
   * changed], removedPolls: [ids]}. An answer of older history (no cursor in
   * it) adds messages and moves `oldest` only. */
  function applyFeed(state, answer) {
    var changed = {messages: [], removedMessages: [], polls: [], removedPolls: []};
    if (!answer) { return changed; }
    (answer.messages || []).forEach(function (row) {
      if (row.removed) {
        if (state.messages[row.id]) { delete state.messages[row.id]; changed.removedMessages.push(row.id); }
        return;
      }
      state.messages[row.id] = row;
      changed.messages.push(row);
    });
    (answer.polls || []).forEach(function (row) {
      if (row.removed) {
        if (state.polls[row.id]) { delete state.polls[row.id]; changed.removedPolls.push(row.id); }
        return;
      }
      state.polls[row.id] = row;
      changed.polls.push(row);
    });
    if (typeof answer.cursor === "number" && answer.cursor > state.cursor) {
      state.cursor = answer.cursor;
    }
    if (answer.oldest !== undefined && answer.oldest !== null &&
        (state.oldest === null || answer.oldest < state.oldest)) {
      state.oldest = answer.oldest;
    }
    if (answer.oldest !== undefined && answer.more !== undefined) { state.more = !!answer.more; }
    if (answer.purged_before && answer.purged_before !== state.purgedBefore) {
      state.purgedBefore = answer.purged_before;
      var edge = Date.parse(answer.purged_before);
      Object.keys(state.messages).forEach(function (id) {
        if (Date.parse(state.messages[id].created_at) < edge) {
          delete state.messages[id]; changed.removedMessages.push(id);
        }
      });
      Object.keys(state.polls).forEach(function (id) {
        if (Date.parse(state.polls[id].created_at) < edge) {
          delete state.polls[id]; changed.removedPolls.push(id);
        }
      });
    }
    return changed;
  }

  api.mintOp = mintOp;
  api.urlFor = urlFor;
  api.newState = newState;
  api.byNumber = byNumber;
  api.applyFeed = applyFeed;

  /* --- the page ------------------------------------------------------------ */

  function mount(section) {
    var doc = root.document;
    var config = JSON.parse(doc.getElementById("forum-channel-config").textContent);
    var urls = config.urls;
    var words = section.querySelector("[data-forum-words]").dataset;
    var list = section.querySelector("[data-forum-messages]");
    var pollBox = section.querySelector("[data-forum-polls]");
    var status = section.querySelector("[data-forum-status]");
    var older = section.querySelector("[data-forum-older]");
    var empty = section.querySelector("[data-forum-empty]");
    var csrfInput = section.querySelector("[data-forum-csrf] input[name=csrfmiddlewaretoken]");
    var csrf = csrfInput ? csrfInput.value : "";
    var state = newState();
    var pendingOp = null;

    function say(text, bad) {
      status.textContent = text || "";
      status.classList.toggle("hidden", !text);
      status.dataset.bad = bad ? "1" : "";
    }

    function ask(url, options) {
      options = options || {};
      options.credentials = "same-origin";
      options.headers = Object.assign({"Accept": "application/json", "X-CSRFToken": csrf},
                                      options.headers || {});
      return root.fetch(url, options).then(function (response) {
        return response.json().then(function (data) {
          return {ok: response.ok, status: response.status, data: data};
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

    function when(iso) {
      var date = new Date(iso);
      return isNaN(date.getTime()) ? "" : date.toLocaleString();
    }

    function messageNode(row) {
      var item = el("li", "border border-current/15 px-2 py-1");
      item.dataset.messageId = row.id;
      item.dataset.number = String(row.number);
      var head = el("div", "flex flex-wrap items-baseline gap-2 text-xs opacity-80");
      head.appendChild(el("span", "font-semibold", row.sender));
      head.appendChild(el("time", "", when(row.created_at)));
      if (row.may_remove) {
        var remove = el("button", "ml-auto underline", words.remove);
        remove.type = "button";
        remove.addEventListener("click", function () {
          if (root.confirm && !root.confirm(words.confirmRemove)) { return; }
          sendJson(urlFor(urls.message_remove, urls.nil, row.id)).then(afterAction, failed);
        });
        head.appendChild(remove);
      }
      item.appendChild(head);
      if (row.text) { item.appendChild(el("p", "whitespace-pre-wrap break-words text-sm", row.text)); }
      if (row.image) {
        var image = el("img", "mt-1 max-h-80 max-w-full");
        image.alt = words.image;
        image.loading = "lazy";
        image.src = row.image.url;
        item.appendChild(image);
      }
      return item;
    }

    function drawMessage(row) {
      var old = list.querySelector('[data-message-id="' + row.id + '"]');
      var node = messageNode(row);
      if (old) { list.replaceChild(node, old); return; }
      var after = null;
      Array.prototype.forEach.call(list.children, function (child) {
        if (after === null && Number(child.dataset.number) > row.number) { after = child; }
      });
      list.insertBefore(node, after);
    }

    function pollNode(row) {
      var card = el("div", "mb-2 border border-current/30 p-2 text-sm");
      card.dataset.pollId = row.id;
      card.dataset.number = String(row.number);
      var head = el("div", "flex flex-wrap items-baseline gap-2");
      head.appendChild(el("span", "text-xs font-semibold uppercase opacity-70", words.poll));
      head.appendChild(el("span", "font-semibold", row.title));
      head.appendChild(el("span", "text-xs opacity-70", row.opener));
      if (!row.open) { head.appendChild(el("span", "text-xs italic", words.closed)); }
      card.appendChild(head);
      var options = el("ul", "mt-1 space-y-1");
      row.choices.forEach(function (choice) {
        var line = el("li", "flex flex-wrap items-baseline gap-2");
        if (row.open) {
          var vote = el("button", "border border-current/40 px-2", words.vote);
          vote.type = "button";
          vote.addEventListener("click", function () {
            sendJson(urlFor(urls.poll_vote, urls.nil, row.id), {choice: choice.id})
              .then(afterAction, failed);
          });
          line.appendChild(vote);
        }
        line.appendChild(el("span", "font-semibold", choice.label));
        if (choice.text) { line.appendChild(el("span", "opacity-80", choice.text)); }
        if (choice.ballots !== null) { line.appendChild(el("span", "opacity-70", choice.ballots)); }
        if (row.my_choice === choice.id) {
          line.appendChild(el("span", "text-xs italic", words.yourAnswer));
        }
        options.appendChild(line);
      });
      card.appendChild(options);
      card.appendChild(el("p", "mt-1 text-xs opacity-70",
        row.total === null ? words.hiddenCount : row.total + " " + words.answers));
      if (row.may_manage) {
        var tools = el("div", "mt-1 flex gap-3 text-xs");
        if (row.open) {
          var close = el("button", "underline", words.close);
          close.type = "button";
          close.addEventListener("click", function () {
            sendJson(urlFor(urls.poll_close, urls.nil, row.id)).then(afterAction, failed);
          });
          tools.appendChild(close);
        }
        var remove = el("button", "underline", words.remove);
        remove.type = "button";
        remove.addEventListener("click", function () {
          if (root.confirm && !root.confirm(words.confirmRemove)) { return; }
          sendJson(urlFor(urls.poll_remove, urls.nil, row.id)).then(afterAction, failed);
        });
        tools.appendChild(remove);
        card.appendChild(tools);
      }
      return card;
    }

    function drawPoll(row) {
      var old = pollBox.querySelector('[data-poll-id="' + row.id + '"]');
      var node = pollNode(row);
      if (old) { pollBox.replaceChild(node, old); } else { pollBox.appendChild(node); }
    }

    function draw(changed) {
      changed.removedMessages.forEach(function (id) {
        var node = list.querySelector('[data-message-id="' + id + '"]');
        if (node) { list.removeChild(node); }
      });
      changed.removedPolls.forEach(function (id) {
        var node = pollBox.querySelector('[data-poll-id="' + id + '"]');
        if (node) { pollBox.removeChild(node); }
      });
      changed.messages.forEach(drawMessage);
      changed.polls.forEach(drawPoll);
      older.classList.toggle("hidden", !state.more);
      empty.classList.toggle("hidden", list.children.length > 0);
    }

    function failed() { say(words.failed, true); }

    /* Ask the feed for what changed since the cursor, until it has no more. */
    function refresh() {
      return ask(urls.feed + "?after=" + state.cursor).then(function (answer) {
        if (!answer.ok) { say(answer.data.error || words.failed, true); return null; }
        draw(applyFeed(state, answer.data));
        if (answer.data.more) { return refresh(); }
        return answer.data;
      }, failed);
    }

    function afterAction(answer) {
      if (!answer.ok) { say(answer.data.error || words.failed, true); return; }
      say("");
      return refresh();
    }

    draw(applyFeed(state, config.feed));

    section.querySelector("[data-forum-refresh]").addEventListener("click", function () {
      say(""); refresh();
    });

    older.addEventListener("click", function () {
      if (state.oldest === null) { return; }
      ask(urls.feed + "?before=" + state.oldest).then(function (answer) {
        if (!answer.ok) { say(answer.data.error || words.failed, true); return; }
        draw(applyFeed(state, answer.data));
      }, failed);
    });

    var postForm = section.querySelector("[data-forum-post]");
    postForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var text = postForm.querySelector("[data-forum-text]");
      var file = postForm.querySelector("[data-forum-image]");
      var body = new root.FormData();
      /* The same op again only for a press the server never answered. */
      pendingOp = pendingOp || mintOp();
      body.append("op", pendingOp);
      body.append("text", text.value);
      if (file.files && file.files[0]) { body.append("image", file.files[0]); }
      ask(urls.post, {method: "POST", body: body}).then(function (answer) {
        pendingOp = null;
        if (!answer.ok) { say(answer.data.error || words.failed, true); return; }
        text.value = "";
        file.value = "";
        afterAction(answer);
      }, failed);
    });

    var pollForm = section.querySelector("[data-forum-poll-form]");
    pollForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var fields = pollForm.elements;
      sendJson(urls.poll_open, {
        title: fields.title.value, options: fields.options.value,
        revisability: fields.revisability.value, visibility: fields.visibility.value
      }).then(function (answer) {
        if (answer.ok) { fields.title.value = ""; fields.options.value = ""; }
        afterAction(answer);
      }, failed);
    });

    return {state: state, refresh: refresh, config: config};
  }

  api.mount = mount;
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
  root.TotoForumChannel = api;
  if (root.document && root.document.addEventListener) {
    var all = function () {
      Array.prototype.forEach.call(
        root.document.querySelectorAll("[data-forum-channel]"), mount);
    };
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", all);
    } else { all(); }
  }
})(typeof window !== "undefined" ? window : globalThis);

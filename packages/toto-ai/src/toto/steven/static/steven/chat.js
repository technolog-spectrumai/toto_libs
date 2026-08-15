/* The floating chat chip's component. Separate from actions.js on purpose:
 * the chip renders on EVERY authenticated page, and most pages are not
 * editors — they should not load the whole editor client to show a chat box,
 * and editor pages (which load actions.js via steven/_head.html) must not
 * find the same component defined twice.
 *
 * EPHEMERAL by decision. `messages` is Alpine state: it dies with the page,
 * and every send is a single-turn run — "say" with the message alone, or
 * "ask" with the page's document when the editor registered one and the
 * toggle is on. There is no history parameter to fill: the server's
 * build_messages refuses conversation history by design (the parked app's
 * unbounded resend grew the bill quadratically), and an ephemeral chat is the
 * one place that rule costs nothing to keep.
 *
 * The poll loop is stevenAi's cancellable one, not the old drawer's: the
 * timer handle is kept and close() clears it, so a closed chip stops asking
 * the server about a run nobody is watching.
 */
(function (global) {
  "use strict";

  /* NAMING RULE, learned the hard way. Alpine falls back to the GLOBAL scope
   * when x-data fails to evaluate, so a component state named `open` resolves
   * to `window.open` — a function, therefore truthy — and an x-show on it
   * renders the panel OPEN over the page, while `close()` calls
   * `window.close()` and silently does nothing. Every name here is prefixed
   * for that reason; the gas pump's `gasOpen` is the same defence. */

  var POLL_MS = 1500;
  var MAX_POLLS = 240;

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  function stevenChat(config) {
    return {
      askUrl: config.askUrl,
      chatOpen: false,
      draft: "",
      includeDocument: false,
      busy: false,
      messages: [],
      runId: null,
      _polls: 0,
      _timer: null,

      /* Whether the page has a document to offer — an editor registered a
         `document` handler with StevenActions. Read guardedly: most pages
         load neither actions.js nor any editor. */
      get hasDocument() {
        try {
          return !!(global.StevenActions &&
                    typeof global.StevenActions.documentText === "function" &&
                    global.StevenActions.hasDocument());
        } catch (e) { return false; }
      },

      toggleChat: function () { this.chatOpen = !this.chatOpen; },

      closeChat: function () {
        if (this._timer) { clearTimeout(this._timer); this._timer = null; }
        this.chatOpen = false;
        this.busy = false;
        this.runId = null;
      },

      _push: function (role, text, tokens, error) {
        this.messages.push({ role: role, text: text, tokens: tokens || 0,
                             error: !!error });
        var self = this;
        this.$nextTick(function () {
          var box = self.$refs.transcript;
          if (box) box.scrollTop = box.scrollHeight;
        });
      },

      send: function () {
        var self = this;
        if (this.busy) return;
        var question = this.draft.trim();
        if (!question) return;

        var payload;
        if (this.includeDocument && this.hasDocument) {
          var text = "";
          try { text = global.StevenActions.documentText() || ""; }
          catch (e) { text = ""; }
          if (!text.trim()) {
            this._push("assistant", "There is nothing in this document yet.",
                       0, true);
            return;
          }
          payload = { surface: "chat", action: "ask",
                      selection: text, instruction: question };
        } else {
          payload = { surface: "chat", action: "say", selection: question };
        }

        this.draft = "";
        this._push("you", question);
        this.busy = true;
        this._polls = 0;

        fetch(this.askUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          credentials: "same-origin",
          body: JSON.stringify(payload),
        })
          .then(function (r) {
            return r.json().then(function (d) { return { ok: r.ok, data: d }; });
          })
          .then(function (res) {
            if (!res.ok) {
              /* 402/429/503 all arrive here — the reason renders as an
                 assistant-side bubble rather than a dead button. */
              self.busy = false;
              self._push("assistant",
                         (res.data && res.data.error) || "That did not work.",
                         0, true);
              return;
            }
            self.runId = res.data.run_id;
            self.poll();
          })
          .catch(function () {
            self.busy = false;
            self._push("assistant", "Could not reach the server.", 0, true);
          });
      },

      poll: function () {
        var self = this;
        if (this.runId === null) return;
        this._timer = setTimeout(function () {
          self._polls += 1;
          if (self._polls > MAX_POLLS) {
            self.busy = false;
            self._push("assistant", "This is taking too long.", 0, true);
            return;
          }
          fetch("/steven/runs/" + self.runId + "/", { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              if (!d.finished) { self.poll(); return; }
              self.busy = false;
              self.runId = null;
              if (d.status === "success") {
                self._push("assistant", d.result || "", d.tokens || 0);
              } else {
                self._push("assistant",
                           d.error || "The assistant could not answer.",
                           d.tokens || 0, true);
              }
            })
            .catch(function () { self.poll(); });
        }, POLL_MS);
      },
    };
  }

  global.stevenChat = stevenChat;
})(window);

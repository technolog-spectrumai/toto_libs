/* Steven's editor client: ask, poll, propose, accept or reject.
 *
 * One file for six editors. What differs per editor is exactly two functions —
 * how to READ the selection and how to WRITE a replacement — so an editor
 * registers those and gets the button, the polling, the diff and the accept/
 * reject flow for free.
 *
 * Registration, from an editor's own JS or inline in its template:
 *
 *     window.StevenActions.register("cyprian", {
 *       read:  function () { ... return selected text or "" ... },
 *       write: function (text) { ... replace the selection ... },
 *     });
 *
 * **Nothing is ever applied automatically.** A result is a proposal with Accept
 * and Reject. That is the same contract sketch keeps with an imported drawing
 * and antivirus keeps with a scanned file: this platform shows you what it
 * found and lets you decide. An assistant that silently rewrote a paragraph
 * would be the one feature here that edits your document without asking.
 */
(function (global) {
  "use strict";

  var POLL_MS = 1500;
  /* Long enough that a slow model finishes, short enough that a wedged run does
     not spin forever — the sweeper closes anything past an hour anyway. */
  var MAX_POLLS = 240;

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  var surfaces = {};

  function register(key, handlers) {
    surfaces[key] = handlers || {};
  }

  function handlersFor(key) {
    return surfaces[key] || {};
  }

  /* The Alpine component every editor's toolbar include instantiates. */
  function stevenPanel(config) {
    return {
      surfaceKey: config.surface,
      askUrl: config.askUrl,
      actionsUrl: config.actionsUrl,

      open: false,
      actions: [],
      loaded: false,
      chosen: null,
      instruction: "",

      busy: false,
      error: "",
      result: "",
      source: "",
      runId: null,
      tokens: 0,
      _polls: 0,
      _timer: null,

      /* The action list comes from the server rather than being templated into
         six toolbars — one vocabulary, one place to change it. Fetched on first
         open so a page that never uses the assistant pays nothing. */
      toggle: function () {
        this.open = !this.open;
        if (this.open && !this.loaded) this.loadActions();
      },

      loadActions: function () {
        var self = this;
        fetch(this.actionsUrl, { credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            self.actions = d.actions || [];
            self.loaded = true;
          })
          .catch(function () { self.error = "Could not load the actions."; });
      },

      choose: function (action) {
        this.chosen = action;
        this.instruction = "";
        this.error = "";
        this.result = "";
      },

      /* Read the selection through the editor's own handler. An empty selection
         is refused here rather than server-side so the user is told before a
         request is made — and before anything is charged. */
      selection: function () {
        var read = handlersFor(this.surfaceKey).read;
        if (typeof read !== "function") return "";
        try { return read() || ""; } catch (e) { return ""; }
      },

      run: function () {
        var self = this;
        if (!this.chosen || this.busy) return;

        var selection = this.selection();
        if (!selection.trim()) {
          this.error = "Select some text first.";
          return;
        }
        if (this.chosen.needs_instruction && !this.instruction.trim()) {
          this.error = "This action needs an instruction.";
          return;
        }

        this.busy = true;
        this.error = "";
        this.result = "";
        this.source = selection;
        this._polls = 0;

        fetch(this.askUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          credentials: "same-origin",
          body: JSON.stringify({
            surface: this.surfaceKey,
            action: this.chosen.key,
            selection: selection,
            instruction: this.instruction,
          }),
        })
          .then(function (r) {
            return r.json().then(function (d) { return { ok: r.ok, data: d }; });
          })
          .then(function (res) {
            if (!res.ok) {
              self.busy = false;
              self.error = (res.data && res.data.error) || "That did not work.";
              return;
            }
            self.runId = res.data.run_id;
            self.poll();
          })
          .catch(function () {
            self.busy = false;
            self.error = "Could not reach the server.";
          });
      },

      poll: function () {
        var self = this;
        if (this.runId === null) return;
        this._timer = setTimeout(function () {
          self._polls += 1;
          if (self._polls > MAX_POLLS) {
            self.busy = false;
            self.error = "This is taking too long. Check the assistant console.";
            return;
          }
          fetch("/steven/runs/" + self.runId + "/", { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              if (!d.finished) { self.poll(); return; }
              self.busy = false;
              self.tokens = d.tokens || 0;
              if (d.status === "success") {
                self.result = d.result || "";
              } else {
                self.error = d.error || "The assistant could not answer.";
              }
            })
            .catch(function () { self.poll(); });
        }, POLL_MS);
      },

      /* Accept writes through the editor's own handler, so the undo stack, the
         dirty flag and the conflict hash all behave exactly as they do for a
         human edit. */
      accept: function () {
        var write = handlersFor(this.surfaceKey).write;
        if (typeof write !== "function" || !this.result) return;
        try {
          write(this.result);
        } catch (e) {
          this.error = "Could not apply that to the document.";
          return;
        }
        this.reset();
        this.open = false;
      },

      reject: function () {
        this.reset();
      },

      reset: function () {
        if (this._timer) { clearTimeout(this._timer); this._timer = null; }
        this.result = "";
        this.source = "";
        this.error = "";
        this.runId = null;
        this.busy = false;
        this.chosen = null;
        this.instruction = "";
      },
    };
  }

  global.StevenActions = { register: register, handlersFor: handlersFor };
  global.stevenPanel = stevenPanel;
})(window);

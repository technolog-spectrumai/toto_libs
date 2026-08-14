/* Steven's editor client: ask, poll, propose, accept or reject.
 *
 * One file for six editors. What differs per editor is a handful of functions —
 * how to READ the selection, how to WRITE a replacement, how to read and
 * replace the WHOLE DOCUMENT — so an editor registers those and gets the
 * buttons, the modal, the polling and the accept/reject flow for free.
 *
 * Registration, from an editor's own JS or inline in its template:
 *
 *     window.StevenActions.register("cyprian", {
 *       read:          function () { ... return selected text or "" ... },
 *       write:         function (text) { ... replace the selection ... },
 *       document:      function () { ... return the whole source ... },
 *       writeDocument: function (text) { ... replace the whole document ... },
 *       anchor:        function () { ... return a viewport DOMRect ... },
 *     });
 *
 * `source`/`writeDocument` are what the toolbar's AI button uses: you type what
 * should change and the model returns a COMPLETE new file in that document's
 * own language. An editor that registers neither still gets the selection half.
 *
 * `source` and `document` are deliberately NOT the same handler. `document` is
 * for ASKING — the side panel wants readable text and markup would be most of
 * the tokens and none of the meaning. `source` is for REGENERATING, and a
 * rewrite must see exactly the markup it is being asked to reproduce, or the
 * model invents structure it was never shown. In an editor holding raw source
 * they are the same function; in cyprian and memo they are not, and conflating
 * them is how a whole-document rewrite quietly loses every table in the file.
 * `source` falls back to `document` when an editor supplies only one.
 *
 * `anchor` is optional. It reports where the selection is on screen so the
 * floating AI button can sit beside it; without one, the component falls back
 * to the browser's own selection rectangle, which is right for every editor
 * that renders contenteditable or plain DOM text.
 *
 * **Nothing is ever applied automatically.** A result is a proposal with Accept
 * and Reject. That is the same contract sketch keeps with an imported drawing
 * and antivirus keeps with a scanned file: this platform shows you what it
 * found and lets you decide. An assistant that silently rewrote a paragraph
 * would be the one feature here that edits your document without asking.
 */
(function (global) {
  "use strict";

  /* Must match toto.core.ai_surfaces.DOCUMENT_ACTION. The server synthesises
     this action from the surface's own file type, so it is the one action key
     that appears in no action list and cannot be discovered — it is a name both
     sides have to agree on. */
  var DOCUMENT_ACTION = "rewrite_document";

  var POLL_MS = 1500;
  /* Long enough that a slow model finishes, short enough that a wedged run does
     not spin forever — the sweeper closes anything past an hour anyway. */
  var MAX_POLLS = 240;

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  var surfaces = {};
  var currentKey = "";
  /* The last surface that registered a `document` handler — what the floating
     chat chip reads through documentText(). Tracked separately from
     `currentKey` because memo re-registers its block surface on every dialog
     mount, and the chip wants "the page's document", not "whoever spoke
     last". */
  var documentKey = "";

  /* handlers: { read, write, insert?, source?, writeDocument?, document?, anchor? }
   *
   * `read`/`write` are the SELECTION pair. `source`/`writeDocument` are the
   * whole-file pair. `insert` APPENDS one generated element through the
   * editor's own command path. An editor that supplies only some handlers
   * gets only those halves of the feature rather than a button that fails
   * when pressed. */
  function register(key, handlers) {
    surfaces[key] = handlers || {};
    currentKey = key;
    if (handlers && typeof handlers.document === "function") {
      documentKey = key;
    }
  }

  function handlersFor(key) {
    return surfaces[key] || {};
  }

  function current() {
    return currentKey;
  }

  /* ---- what the chat chip reads --------------------------------------- */

  function hasDocument() {
    return typeof handlersFor(documentKey).document === "function";
  }

  function documentText() {
    var h = handlersFor(documentKey);
    try {
      return typeof h.document === "function" ? (h.document() || "") : "";
    } catch (e) {
      return "";
    }
  }

  /* The old side-panel drawer lived here. Its whole feature — ask about the
   * page's document — survives as the chat chip's "include this document"
   * toggle (steven/chat.js), which reads documentText() above. */

  /* ---- the AI button, and the one modal both buttons open ---------------
   * Replaces the old dropdown of canned actions. Two entry points, one box:
   *
   *   scope "document"  — the square AI button in the toolbar. Sends the whole
   *                       source and expects a whole new file back.
   *   scope "selection" — the button that floats beside a selection. Sends the
   *                       selection only.
   *
   * The scopes differ in what is sent and what Accept writes, and in nothing
   * else — same endpoint, same run, same polling, same refusal to apply
   * anything until somebody presses Accept.
   */
  function stevenAi(config) {
    return {
      surfaceKey: config.surface,
      askUrl: config.askUrl,
      actionsUrl: config.actionsUrl,

      open: false,
      scope: "document",
      instruction: "",
      source: "",
      sourceLength: 0,

      agent: null,
      language: "",
      loaded: false,

      busy: false,
      error: "",
      result: "",
      tokens: 0,
      runId: null,
      /* The selection's on-screen box, or null when there is no selection.
         Drives the floating button's position. */
      anchor: null,
      /* Whether this editor can take a whole-document rewrite at all. Kept in
         reactive state and refreshed by the same timer as `anchor`, because
         handlers are registered by the editor's own init — which may run after
         Alpine mounts — and a plain getter over a non-reactive lookup would
         never re-evaluate. Primula is the editor this exists for: a workbook is
         not a source file, so it registers no writeDocument and simply has no
         toolbar button rather than one that fails on Accept. */
      canRewrite: false,
      _polls: 0,
      _timer: null,

      handlers: function () {
        return StevenActions.handlersFor(this.surfaceKey);
      },

      /* The agent's name and the document's language, fetched once and only
         when a button is actually pressed — a page that never opens the modal
         pays nothing for it. */
      loadMeta: function () {
        var self = this;
        if (this.loaded) return;
        this.loaded = true;
        fetch(this.actionsUrl, { credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            self.agent = d.agent || null;
            self.language = d.language || "";
          })
          .catch(function () { /* the modal works without a name */ });
      },

      /* ---- opening ------------------------------------------------------ */

      openDocument: function () {
        /* `source` first: it is the regenerable markup. `document` is the
           side panel's readable flattening and is only a fallback for editors
           where the two are the same thing. */
        var h = this.handlers();
        var read = h.source || h.document;
        var text = "";
        try { text = typeof read === "function" ? (read() || "") : ""; }
        catch (e) { text = ""; }
        if (!text.trim()) {
          this.scope = "document";
          this.source = "";
          this.sourceLength = 0;
          this._show("There is nothing in this document yet.");
          return;
        }
        this.scope = "document";
        this._show("", text);
      },

      openSelection: function () {
        var read = this.handlers().read;
        var text = "";
        try { text = typeof read === "function" ? (read() || "") : ""; }
        catch (e) { text = ""; }
        if (!text.trim()) {
          /* The floating button only exists while something is selected, so
             this is the race where it went away between render and click. */
          this.anchor = null;
          return;
        }
        this.scope = "selection";
        this._show("", text);
      },

      _show: function (error, text) {
        this.loadMeta();
        this.source = text || "";
        this.sourceLength = this.source.length;
        this.instruction = "";
        this.result = "";
        this.error = error || "";
        this.tokens = 0;
        this.runId = null;
        this.open = true;
        var self = this;
        this.$nextTick(function () {
          if (self.$refs.prompt) self.$refs.prompt.focus();
        });
      },

      close: function () {
        if (this._timer) { clearTimeout(this._timer); this._timer = null; }
        this.open = false;
        this.busy = false;
        this.result = "";
        this.error = "";
        this.runId = null;
      },

      /* ---- the floating button ------------------------------------------
       * Polled rather than bound to `selectionchange`: an editor may render its
       * own selection (ACE draws one on a canvas layer and the DOM selection is
       * empty), so an editor-supplied `anchor` has to be consulted too, and one
       * timer covers both without every editor emitting events.
       */
      watchSelection: function () {
        var self = this;
        this.refreshAnchor();
        setInterval(function () { self.refreshAnchor(); }, 300);
      },

      refreshAnchor: function () {
        var h = this.handlers();
        this.canRewrite = typeof h.writeDocument === "function" &&
                          typeof (h.source || h.document) === "function";
        if (this.open) { this.anchor = null; return; }

        var custom = this.handlers().anchor;
        var rect = null;
        if (typeof custom === "function") {
          try { rect = custom(); } catch (e) { rect = null; }
        } else {
          var sel = window.getSelection && window.getSelection();
          if (sel && sel.rangeCount && !sel.isCollapsed) {
            var r = sel.getRangeAt(0).getBoundingClientRect();
            if (r && (r.width || r.height)) rect = r;
          }
        }
        if (!rect) { this.anchor = null; return; }

        /* Beside the selection, not on top of it: to its right, vertically
           centred, nudged inside the viewport so it cannot be clipped. */
        var top = Math.max(4, Math.min(window.innerHeight - 36,
                                       rect.top + (rect.height / 2) - 14));
        var left = Math.max(4, Math.min(window.innerWidth - 36, rect.right + 6));
        this.anchor = { top: Math.round(top), left: Math.round(left) };
      },

      /* ---- running ------------------------------------------------------ */

      run: function () {
        var self = this;
        if (this.busy) return;
        if (!this.instruction.trim()) {
          this.error = "Type what you want changed.";
          return;
        }
        if (!this.source.trim()) {
          this.error = "There is nothing to work on.";
          return;
        }

        this.busy = true;
        this.error = "";
        this.result = "";
        this._polls = 0;

        fetch(this.askUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          credentials: "same-origin",
          body: JSON.stringify({
            surface: this.surfaceKey,
            /* The document scope names the synthesised rewrite action; the
               selection scope reuses the shared "rewrite as…" one. */
            action: this.scope === "document" ? DOCUMENT_ACTION : "rewrite",
            selection: this.source,
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

      /* ---- applying ------------------------------------------------------
       * Through the editor's own handler, so the undo stack, the dirty flag and
       * the conflict hash all behave exactly as they do for a human edit. */
      accept: function () {
        var h = this.handlers();
        var write = this.scope === "document" ? h.writeDocument : h.write;
        if (typeof write !== "function" || !this.result) return;
        try {
          write(this.result);
        } catch (e) {
          this.error = "Could not apply that to the document.";
          return;
        }
        this.close();
      },

      reject: function () {
        this.result = "";
        this.error = "";
        this.tokens = 0;
        this.runId = null;
      },
    };
  }

  global.StevenActions = {
    register: register,
    handlersFor: handlersFor,
    current: current,
    hasDocument: hasDocument,
    documentText: documentText,
  };
  global.stevenAi = stevenAi;
})(window);

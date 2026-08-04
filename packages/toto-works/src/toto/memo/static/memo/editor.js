/* The editor: a block canvas over MemoModel, with autosave, undo and drag.
 *
 * `state` is the only source of truth. Every mutation goes through `mutate`,
 * which commits to history and schedules a save — so there is no path that
 * changes the deck without those two things happening.
 *
 * The contenteditable rule, which is most of what makes this work:
 *
 *   the DOM is written ONCE, when a block element is created, and after that
 *   the model is updated FROM the DOM and never the reverse.
 *
 * Writing back into a focused contenteditable moves the caret to the start on
 * every keystroke, which is the single most common way rich-text editors are
 * broken. When the model does change underneath — undo, redo, an import — the
 * fix is to recreate the elements instead, by bumping `ui.nonce`, which is part
 * of every :key. Stable block ids are what make that safe: without them Alpine
 * reuses nodes by position and a reorder leaves the text in the wrong block.
 */
(function (global) {
  "use strict";

  var M = global.MemoModel;
  var AUTOSAVE_MS = 2000;
  var AUTOSAVE_CEILING_MS = 30000;

  function readJson(id, fallback) {
    var el = document.getElementById(id);
    if (!el) return fallback;
    try { return JSON.parse(el.textContent); } catch (e) { return fallback; }
  }

  /* Light or dark, as ACE needs to be told it.
   *
   * From localStorage, which is where `oya/base.html` seeds the page's own
   * `darkMode` from — NOT from `this.darkMode`. Alpine resolves a parent
   * component's data for template EXPRESSIONS but not for property access
   * inside a method, so `this.darkMode` is undefined here and every read of it
   * silently fell through to this fallback anyway. Saying so beats relying on
   * it.
   *
   * The theme toggle reloads the page, so mount time is the only time this has
   * to be right — `boot()` also watches `darkMode` so a future toggle that
   * stops reloading still re-themes whatever is open.
   */
  function isDarkMode() {
    try { return localStorage.getItem("darkMode") === "true"; }
    catch (e) { return false; }
  }

  function aceTheme() {
    return isDarkMode() ? "ace/theme/twilight" : "ace/theme/textmate";
  }

  /* The three kinds of box whose payload is prose, and what each field says
   * when it is empty. `list` is not here: a list is one item per line, which is
   * how people type a list, and a rich editor would only get in the way. */
  var PROSE_TYPES = ["heading", "text", "quote"];
  var PROSE_PLACEHOLDER = {
    heading: "Title of this slide",
    text: "Write something",
    quote: "The quotation"
  };

  /* What the toolbar offers, and the TipTap command behind each button.
   *
   * Every one of these survives `memo/sanitize.py`: strong, em, u, s and code
   * are in INLINE_TAGS, the lists and the blockquote in BLOCK_TAGS, and a link
   * keeps its href. Nothing here carries a class or a style, because the
   * sanitiser strips both — a mark that did would vanish on the first save.
   *
   * The list and quote buttons are hidden for heading and quote boxes, whose
   * payload goes through sanitize.inline() and would lose the block anyway; see
   * MemoTipTap.INLINE_ONLY, which is the same fact expressed in the schema. */
  var PROSE_MARKS = ["bold", "italic", "underline", "strike", "code", "link",
                     "bulletList", "orderedList", "blockquote"];
  var PROSE_COMMANDS = {
    bold: "toggleBold", italic: "toggleItalic", underline: "toggleUnderline",
    strike: "toggleStrike", code: "toggleCode",
    bulletList: "toggleBulletList", orderedList: "toggleOrderedList",
    blockquote: "toggleBlockquote"
  };

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  global.memoEditor = function () {
    var config = readJson("memo-config", {});
    var history = new global.MemoHistory();

    /* The TipTap instance for the box dialog's prose field.
     *
     * Held HERE, in the closure, and deliberately not on the returned component.
     * Alpine wraps component state in a reactive Proxy, and ProseMirror compares
     * `transaction.before` with `state.doc` by IDENTITY — through a Proxy those
     * are never the same object, and every keystroke throws "Applying a
     * mismatched transaction". cyprian learned this the hard way; see its
     * editor.js. The same reasoning already applies to `_ace` below.
     */
    var prose = null;

    return {
      config: config,
      state: M.fromServer(readJson("memo-data", {})),
      baseHash: config.contentHash || "",

      ui: {
        activeId: "",
        focusedId: "",
        nonce: 0,
        // The box being worked on. A slide is a set of boxes; this is which of
        // them the toolbar belongs to.
        targetSlot: "",
        dialog: { open: false, slot: "", type: "text", payload: "",
                  items: "", attrs: {} },
        // Which marks are under the caret in the prose field. Reactive, so the
        // toolbar buttons can show themselves as active — see syncProse.
        prose: {},
        // Reactive mirrors of MemoHistory's state — see canUndo.
        canUndo: false,
        canRedo: false,
        filmstrip: true,
        saving: false,
        dirty: false,
        status: "",
        conflict: false,
        mediaSearch: "",
        mediaBusy: false,
        // Slide ids whose thumbnail is near the viewport. Reactive, so Alpine
        // builds a thumbnail's contents exactly when it becomes visible.
        live: {},
        menu: { open: false, x: 0, y: 0, id: "", kind: "" },
        popover: { open: false, x: 0, y: 0 }
      },

      layouts: M.LAYOUTS,
      fonts: M.FONTS,
      formulaHelp: M.FORMULA_HELP,
      blockTypes: M.BLOCK_TYPES,
      media: readJson("memo-media", []),

      _timer: null,
      _ceiling: null,
      _fitTimer: null,
      _inflight: false,

      // ---- boot ------------------------------------------------------------
      boot: function () {
        var self = this;
        this.ui.activeId = this.state.slides[0].id;
        history.seed(this.state);
        this.syncHistory();

        this._stopCanvas = global.MemoCanvas.observe(this.$refs.stage);
        this._stopStrip = global.MemoCanvas.watchFilmstrip(
          this.$refs.filmstrip,
          function (id, visible) {
            if (visible) self.ui.live[id] = true;
            else delete self.ui.live[id];
          });
        this.scheduleAutofit();

        // No MemoDrag. Nothing on a slide is dragged any more: the layout owns
        // the geometry, a box is filled from its own toolbar, and slides are
        // reordered with the arrows the filmstrip already had. Dragging was the
        // only way to end up with a deck whose boxes disagreed with its layout.

        global.addEventListener("beforeunload", function (e) {
          if (self.ui.dirty || self._inflight) { e.preventDefault(); e.returnValue = ""; }
        });

        // The theme toggle reloads the page today, so this is insurance rather
        // than a live requirement — but an ACE stuck in the light theme inside
        // a dark dialog is exactly the mismatch this suite keeps fixing.
        this.$watch("darkMode", function () { self.syncSourceTheme(); });
      },

      destroy: function () {
        if (this._stopCanvas) this._stopCanvas();
        if (this._stopStrip) this._stopStrip();
      },

      /* Thumbnail text, computed once per block rather than per render.
       *
       * The old expression ran a tag-stripping regex over every block of every
       * slide on every keystroke. Cached against the payload, so it recomputes
       * exactly when the payload changes. */
      thumbText: function (block) {
        if (block._thumbFor !== block.payload) {
          block._thumbFor = block.payload;
          block._thumb = (block.payload || "").replace(/<[^>]*>/g, "").slice(0, 90);
        }
        return block._thumb;
      },

      // ---- derived ---------------------------------------------------------
      get activeSlide() {
        var at = M.indexOfId(this.state.slides, this.ui.activeId);
        return this.state.slides[at === -1 ? 0 : at];
      },
      get activeIndex() { return M.indexOfId(this.state.slides, this.ui.activeId); },
      get columns() { return M.columns(this.activeSlide); },

      /* The boxes this slide's layout has, and which of them a new block goes
       * into. A layout is a fixed set of boxes — nothing here resizes one — so
       * "which box" is the only placement question there is, and it is asked
       * once rather than guessed per block. */
      get boxes() { return M.slotsFor(this.activeSlide ? this.activeSlide.layout : ""); },
      slotLabel: function (slot) { return M.slotLabel(slot); },
      get targetSlot() {
        var boxes = this.boxes;
        return boxes.indexOf(this.ui.targetSlot) !== -1 ? this.ui.targetSlot : boxes[0];
      },
      /* Read from `ui`, NOT from the history object.
       *
       * `history` is a plain object Alpine knows nothing about, so a getter
       * calling into it has no reactive dependency: the effect behind
       * `:disabled="!canUndo"` ran once at boot, when there was nothing to
       * undo, and never ran again. Both buttons were therefore disabled for
       * the whole session — the bug reported as "undo and redo do not work".
       * `syncHistory()` pushes the answer into reactive state instead. */
      get canUndo() { return this.ui.canUndo; },
      get canRedo() { return this.ui.canRedo; },
      get filteredMedia() {
        var q = (this.ui.mediaSearch || "").toLowerCase();
        if (!q) return this.media;
        return this.media.filter(function (m) {
          return (m.title + " " + m.location).toLowerCase().indexOf(q) !== -1;
        });
      },

      placeholderFor: function (block) { return M.PLACEHOLDER[block.type] || ""; },

      /* A box holds ONE thing.
       *
       * The format allows several blocks in a slot and still reads them, but the
       * editor works one-per-box: it is what makes "what kind of content is
       * this" a property of the box rather than a list to manage, and it is why
       * there is no add/remove/reorder inside a box at all. A deck from an
       * older build with two blocks in one box shows both and edits the first.
       */
      boxBlock: function (slot) {
        var slide = this.activeSlide;
        if (!slide) return null;
        var slots = M.slotsFor(slide.layout);
        for (var i = 0; i < slide.blocks.length; i++) {
          var block = slide.blocks[i];
          var where = slots.indexOf(block.slot) !== -1 ? block.slot : slots[0];
          if (where === slot) return block;
        }
        return null;
      },

      selectBox: function (slot) {
        this.ui.targetSlot = slot;
        var block = this.boxBlock(slot);
        this.ui.focusedId = block ? block.id : "";
      },

      /* Set what kind of content this box holds.
       *
       * Switching keeps the words where that means anything — heading, text and
       * quote are all prose — and starts clean where it does not: a formula is
       * not a paragraph, and pretending its LaTeX is a sentence would put
       * `\frac{a}{b}` on the slide as text.
       */
      setBoxType: function (slot, type) {
        var self = this;
        var block = this.boxBlock(slot);
        var PROSE = ["heading", "text", "quote"];
        if (!block) {
          var made = M.newBlock(type);
          made.slot = slot;
          this.mutate(function (s) {
            var slide = M.findSlide(s, self.ui.activeId);
            if (slide) slide.blocks.push(made);
          });
          this.ui.focusedId = made.id;
          this.refresh();
          this.openBoxDialog(slot);
          return;
        }
        if (block.type === type) { this.openBoxDialog(slot); return; }
        var keep = PROSE.indexOf(block.type) !== -1 && PROSE.indexOf(type) !== -1;
        this.mutate(function () {
          block.type = type;
          if (!keep) { block.payload = ""; block.items = []; block.render = ""; }
          if (type === "list" && !block.items.length) block.items = [""];
          if (type === "heading" && !block.attrs.level) block.attrs.level = "2";
          if (type === "image" && !block.attrs.fit) block.attrs.fit = "contain";
        });
        this.refresh();
        this.openBoxDialog(slot);
      },

      /* Empty a box. Not "delete the block" — the box stays, because the layout
       * says it exists; only what is in it goes. */
      clearBox: function (slot) {
        var self = this;
        var slide = this.activeSlide;
        if (!slide) return;
        var slots = M.slotsFor(slide.layout);
        var doomed = slide.blocks.filter(function (block) {
          var where = slots.indexOf(block.slot) !== -1 ? block.slot : slots[0];
          return where === slot;
        }).map(function (block) { return block.id; });
        if (!doomed.length) return;
        this.mutate(function (s) {
          var target = M.findSlide(s, self.ui.activeId);
          if (!target) return;
          target.blocks = target.blocks.filter(function (block) {
            return doomed.indexOf(block.id) === -1;
          });
        });
        this.ui.focusedId = "";
        this.refresh();
      },
      isFocused: function (id) { return this.ui.focusedId === id; },

      // ---- the one mutation path -------------------------------------------
      mutate: function (fn, coalesceKey) {
        fn(this.state);
        history.commit(this.state, coalesceKey);
        this.syncHistory();
        this.ui.dirty = true;
        this.scheduleSave();
      },

      /* Publish the history's state into `ui` so the toolbar can see it. Called
       * after everything that can change it — a mutation, an undo, a redo — and
       * once at boot. */
      syncHistory: function () {
        this.ui.canUndo = history.canUndo();
        this.ui.canRedo = history.canRedo();
      },

      /* Redraw the editable DOM from the model. Only for changes the DOM did
       * not make itself — see the header. */
      refresh: function () { this.ui.nonce += 1; this.scheduleAutofit(); },

      // ---- auto-fit --------------------------------------------------------
      /* Measure after the DOM has settled, then write the resolved scale back
       * into the document so the player and the PDF get the same size. Debounced
       * because it reads layout, which forces a reflow. */
      scheduleAutofit: function () {
        var self = this;
        clearTimeout(this._fitTimer);
        this._fitTimer = setTimeout(function () { self.autofit(); }, 120);
      },

      autofit: function () {
        var self = this;
        var stage = this.$refs.stage;
        if (!stage || !global.MemoCanvas.autofit) return;
        var changed = false;
        global.MemoCanvas.autofit(stage, function (blockId, scale, spill) {
          var found = M.findBlock(self.state, blockId);
          if (!found || !M.isAutoScaled(found.block)) return;
          var next = scale >= 1 ? "auto" : "auto:" + scale.toFixed(2);
          // Only mark the document dirty if the number actually moved —
          // otherwise every render would schedule a save forever.
          if (found.block.attrs.scale !== next) {
            found.block.attrs.scale = next;
            changed = true;
          }
        });
        if (changed) { this.ui.dirty = true; this.scheduleSave(); }
      },

      blockScale: function (block) { return M.blockScale(block); },
      isAutoScaled: function (block) { return M.isAutoScaled(block); },



      // ---- slides ----------------------------------------------------------
      select: function (id) { this.ui.activeId = id; this.ui.focusedId = ""; },

      addSlide: function (layout) {
        var self = this;
        var slide = M.newSlide(layout || "title-content");
        this.mutate(function (s) {
          s.slides.splice(self.activeIndex + 1, 0, slide);
        });
        this.select(slide.id);
      },

      duplicateSlide: function (id) {
        var at = M.indexOfId(this.state.slides, id);
        if (at === -1) return;
        var copy = JSON.parse(JSON.stringify(this.state.slides[at]));
        copy.id = M.newId("s");
        copy.blocks.forEach(function (b) { b.id = M.newId("b"); });
        this.mutate(function (s) { s.slides.splice(at + 1, 0, copy); });
        this.select(copy.id);
      },

      removeSlide: function (id) {
        if (this.state.slides.length === 1) {
          // Emptying the deck entirely leaves nothing to click on; replace
          // rather than delete, the same call the old editor made.
          this.mutate(function (s) { s.slides = [M.newSlide("title-content")]; });
          this.select(this.state.slides[0].id);
          this.refresh();
          return;
        }
        var at = M.indexOfId(this.state.slides, id);
        this.mutate(function (s) { s.slides.splice(at, 1); });
        var next = this.state.slides[Math.min(at, this.state.slides.length - 1)];
        this.select(next.id);
        this.refresh();
      },

      nudgeSlide: function (id, delta) {
        var self = this;
        this.mutate(function (s) { M.nudge(s.slides, id, delta); });
        self.select(id);
      },

      setLayout: function (layout) {
        var id = this.activeSlide.id;
        this.mutate(function (s) { M.setLayout(s, id, layout); });
        // The chosen box may not exist on the new layout; `targetSlot` falls
        // back to the first one, and the blocks fall with it (see columns).
        this.ui.targetSlot = M.slotsFor(layout)[0];
        this.refresh();
      },

      setTheme: function (theme) {
        this.mutate(function (s) { s.theme = theme; });
      },

      setFont: function (font) {
        this.mutate(function (s) { s.font = font; });
      },

      get fontClass() { return M.FONT_CLASS[this.state.font] || "memo-font-sans"; },
      get fontLabel() {
        var found = M.FONTS.filter(function (f) { return f.id === this.state.font; }, this);
        return found.length ? found[0].label : "Sans";
      },

      // ---- blocks ----------------------------------------------------------
      focusBlock: function (id) { this.ui.focusedId = id; },






      onTitleInput: function (el) {
        var slide = this.activeSlide;
        var text = global.MemoSanitize.text(el.innerHTML);
        this.mutate(function () { slide.title = text; }, "title:" + slide.id);
      },



      // ---- the box dialog --------------------------------------------------
      /* One modal per kind of content, opened from the box's Edit button.
       *
       * The canvas is 1280x720 scaled to fit a browser window, which is a fine
       * place to SEE a slide and a hopeless place to write LaTeX or paste an
       * SVG into. So the canvas renders and the dialog writes — and because
       * nothing is edited in place any more, what you see on the slide is
       * always exactly what the file holds.
       */
      openBoxDialog: function (slot) {
        var block = this.boxBlock(slot);
        if (!block) return;
        this.ui.targetSlot = slot;
        this.ui.focusedId = block.id;
        this.ui.dialog = {
          open: true, slot: slot,
          // An svg box opens as the picture dialog it was chosen from.
          type: block.type === "svg" ? "image" : block.type,
          isSvg: block.type === "svg",
          payload: block.payload || "",
          items: (block.items || []).join("\n"),
          attrs: Object.assign({}, block.attrs || {})
        };
        var self = this;
        this.$nextTick(function () {
          self.mountSource();
          self.mountProse();
          self.previewFormula();
        });
      },

      closeDialog: function () {
        this.teardownSource();
        this.teardownProse();
        this.ui.dialog.open = false;
      },

      /* ACE for code and the v1 html escape hatch. Mounted when the dialog
       * opens and destroyed when it closes: Alpine rebuilds this subtree, and an
       * ACE bound to a detached div is a silent no-op that looks like a broken
       * editor.
       *
       * Not for `svg` — an SVG is a picture, chosen in the image dialog, never
       * typed. See openBoxDialog. */
      mountSource: function () {
        var type = this.ui.dialog.type;
        if (["code", "html"].indexOf(type) === -1) return;
        if (typeof ace === "undefined") return;
        var host = document.getElementById("memo-source");
        if (!host) return;
        this.teardownSource();
        this._ace = ace.edit(host);
        this._ace.setTheme(aceTheme());
        // Only five modes are vendored; anything else would 404 and leave the
        // editor with no highlighting at all. Text is the honest fallback.
        var mode = type === "html" ? "ace/mode/xml" : "ace/mode/text";
        var language = (this.ui.dialog.attrs.language || "").toLowerCase();
        if (type === "code" && ["python", "html", "xml", "latex"].indexOf(language) !== -1) {
          mode = "ace/mode/" + language;
        }
        this._ace.session.setMode(mode);
        // No syntax worker. ACE would fetch `worker-<mode>.js` from a base path
        // this page never configures — a 404 and a console error for a validator
        // nobody asked for. The server sanitises what is applied anyway.
        this._ace.session.setUseWorker(false);
        this._ace.setOptions({ fontSize: "13px", showPrintMargin: false,
                               useSoftTabs: true, tabSize: 2, wrap: true });
        this._ace.setValue(this.ui.dialog.payload || "", -1);
      },

      /* Light or dark. See isDarkMode() for where the answer comes from. */
      syncSourceTheme: function () {
        if (this._ace) this._ace.setTheme(aceTheme());
      },

      teardownSource: function () {
        if (this._ace) { this._ace.destroy(); this._ace = null; }
      },

      /* TipTap, for the three prose kinds.
       *
       * Mounted when the dialog opens and destroyed when it closes, for the same
       * reason as ACE above: Alpine rebuilds this subtree, and an editor bound
       * to a detached node is a silent no-op that looks broken.
       *
       * The payload is written into the editor ONCE, at mount, and read back out
       * on every change — never the reverse. Writing into a focused rich-text
       * field moves the caret to the start on every keystroke, which is the
       * single most common way these editors are broken.
       */
      mountProse: function () {
        var type = this.ui.dialog.type;
        if (PROSE_TYPES.indexOf(type) === -1) return;
        if (!global.MemoTipTap) return;      // the module has not landed yet
        var host = document.getElementById("memo-prose");
        if (!host) return;
        this.teardownProse();

        var self = this;
        prose = global.MemoTipTap.create(host, this.ui.dialog.payload || "", {
          kind: type,
          placeholder: PROSE_PLACEHOLDER[type] || "",
          onUpdate: function (html) { self.ui.dialog.payload = html; },
          // A reactive mirror of the marks under the caret, so the toolbar can
          // light up. Reading editor.isActive() from a getter would not work:
          // the editor is a plain object Alpine tracks nothing about, so the
          // effect behind :class would run once and never again — the same bug
          // that left undo and redo disabled for a whole session.
          onSelection: function (editor) { self.syncProse(editor); }
        });
        this.syncProse(prose);
      },

      teardownProse: function () {
        if (prose) { prose.destroy(); prose = null; }
      },

      /* Toolbar state, pushed into reactive `ui` rather than pulled from the
       * editor. See onSelection above. */
      syncProse: function (editor) {
        if (!editor) { this.ui.prose = {}; return; }
        var marks = {};
        PROSE_MARKS.forEach(function (name) {
          marks[name] = editor.isActive(name);
        });
        this.ui.prose = marks;
      },

      /* Run a command against the prose editor. Every toolbar button goes
       * through here so the chain is started and focused in exactly one place. */
      proseCmd: function (fn) {
        if (!prose) return;
        fn(prose.chain().focus());
        this.syncProse(prose);
      },

      toggleProse: function (name) {
        var command = PROSE_COMMANDS[name];
        if (!command) return;
        this.proseCmd(function (chain) { chain[command]().run(); });
      },

      /* A link is the one mark that needs a value, so it asks for one. Clearing
       * the box removes the link, which is what an empty answer means. */
      setProseLink: function () {
        if (!prose) return;
        var current = prose.getAttributes("link").href || "";
        var href = global.prompt(this.config.text.linkPrompt || "Link address", current);
        if (href === null) return;
        var chain = prose.chain().focus().extendMarkRange("link");
        if (!href.trim()) chain.unsetLink().run();
        else chain.setLink({ href: href.trim() }).run();
        this.syncProse(prose);
      },

      previewFormula: function () {
        var target = this.$refs.formulaPreview;
        if (!target || this.ui.dialog.type !== "formula") return;
        this.renderFormula({ payload: this.ui.dialog.payload, render: "" }, target);
      },

      pickMedia: function (pk) {
        var self = this;
        this.ui.mediaBusy = true;
        fetch(this.config.urls.embed + "?file_pk=" + encodeURIComponent(pk))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            self.ui.mediaBusy = false;
            if (!data || data.error) {
              self.ui.status = (data && data.error) || "Could not embed that.";
              return;
            }
            // A picture is a picture: PNG, JPEG or SVG, chosen the same way.
            // What differs is only how it RENDERS — markup through <img src>
            // would show nothing — so the kind is remembered and applied to the
            // block on Insert, rather than swapping the dialog under the user.
            self.ui.dialog.isSvg = data.type === "svg";
            self.ui.dialog.payload = data.payload;
            if (!self.ui.dialog.attrs.alt) self.ui.dialog.attrs.alt = data.alt || "";
          })
          .catch(function () {
            self.ui.mediaBusy = false;
            self.ui.status = "Could not embed that.";
          });
      },

      /* Upload straight into the box being edited. The server resizes and
       * sanitises; this only has to put the payload where the dialog can see
       * it. */
      uploadForDialog: function (files) {
        var self = this;
        var file = (files || [])[0];
        if (!file) return;
        var form = new FormData();
        form.append("file", file);
        this.ui.mediaBusy = true;
        fetch(this.config.urls.upload, {
          method: "POST", headers: { "X-CSRFToken": csrf() }, body: form
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            self.ui.mediaBusy = false;
            if (!data || data.error) {
              self.ui.status = (data && data.error) || "Upload failed.";
              return;
            }
            self.ui.dialog.isSvg = data.type === "svg";
            self.ui.dialog.payload = data.payload;
            if (!self.ui.dialog.attrs.alt) self.ui.dialog.attrs.alt = data.alt || "";
          })
          .catch(function () {
            self.ui.mediaBusy = false;
            self.ui.status = "Upload failed.";
          });
      },

      applyDialog: function () {
        var d = this.ui.dialog;
        var block = this.boxBlock(d.slot);
        if (!block) { this.closeDialog(); return; }
        // Read from whichever editor owns this box's payload. `d.payload` is
        // kept in step by onUpdate, but reading the source of truth directly
        // means a change made in the same tick as Insert cannot be lost.
        var payload = d.payload;
        if (this._ace) payload = this._ace.getValue();
        else if (prose) payload = prose.getHTML();
        var items = d.type === "list"
          ? (d.items || "").split("\n").map(function (line) { return line.trim(); })
              .filter(function (line) { return line.length; })
          : [];
        var attrs = {};
        Object.keys(d.attrs || {}).forEach(function (key) {
          if (d.attrs[key] !== "" && d.attrs[key] !== undefined) {
            attrs[key] = String(d.attrs[key]);
          }
        });
        var sanitize = global.MemoSanitize;
        if (["heading", "quote"].indexOf(d.type) !== -1) payload = sanitize.inline(payload);
        else if (d.type === "text") payload = sanitize.rich(payload);
        if (d.type === "list") {
          items = items.map(function (item) { return sanitize.inline(item); });
        }
        // An image box holds whichever kind of picture was chosen: SVG is
        // markup and renders inline, everything else is a data URI in an <img>.
        var type = d.type === "image" && d.isSvg ? "svg" : d.type;

        this.mutate(function () {
          block.type = type;
          block.payload = payload;
          block.items = items;
          block.attrs = attrs;
          block.render = "";                  // re-rendered from the source
        });
        this.closeDialog();
        this.refresh();
      },

      // ---- formulas --------------------------------------------------------
      /* You write LaTeX. There is no equation builder — a textarea holds the
       * source and KaTeX renders it above, live.
       *
       * The render is also CACHED into the document, because WeasyPrint runs no
       * JavaScript: without a stored rendering an exported deck would lose every
       * formula. The source stays the truth; this is derived from it, and the
       * server re-sanitises it on save because it comes from a browser.
       */
      renderFormula: function (block, target) {
        if (!target) return;
        var katex = global.katex;
        if (!katex) {
          // The vendored asset is missing (download_vendor.py has not run).
          // Show the source rather than an empty box, and cache nothing.
          target.textContent = block.payload || "";
          block.render = "";
          return;
        }
        try {
          katex.render(block.payload || "", target, {
            displayMode: true,
            // A half-typed formula shows the broken fragment in red instead of
            // blanking, so you can see where you are while typing.
            throwOnError: false,
            errorColor: "#ef4444"
          });
          block.render = target.innerHTML;
        } catch (e) {
          target.textContent = block.payload || "";
          block.render = "";
        }
      },


      /* The cheatsheet's previews, rendered from the same source they show. */
      renderHelp: function (el, source) {
        if (!global.katex) { el.textContent = source; return; }
        try {
          global.katex.render(source, el, { throwOnError: false, displayMode: false });
        } catch (e) { el.textContent = source; }
      },

      insertFormulaSnippet: function (snippet) {
        /* Through the model, not the DOM: x-model owns that textarea's value,
         * and writing round it is undone on the next render. */
        var area = document.getElementById("memo-formula-source");
        var value = this.ui.dialog.payload || "";
        var start = area ? (area.selectionStart || 0) : value.length;
        var end = area ? (area.selectionEnd || 0) : value.length;
        this.ui.dialog.payload = value.slice(0, start) + snippet + value.slice(end);
        this.previewFormula();
        if (!area) return;
        var caret = start + snippet.length;
        this.$nextTick(function () {
          area.focus();
          area.selectionStart = area.selectionEnd = caret;
        });
      },


      // ---- undo ------------------------------------------------------------
      undo: function () {
        var restored = history.undo();
        if (!restored) return;
        this.applySnapshot(restored, "Undone.");
      },

      redo: function () {
        var restored = history.redo();
        if (!restored) return;
        this.applySnapshot(restored, "Redone.");
      },

      applySnapshot: function (snapshot, message) {
        this.state = snapshot;
        if (M.indexOfId(this.state.slides, this.ui.activeId) === -1) {
          this.ui.activeId = this.state.slides[0].id;
        }
        this.ui.dirty = true;
        this.ui.status = message;
        this.syncHistory();
        this.refresh();
        this.scheduleSave();
      },

      // ---- saving ----------------------------------------------------------
      scheduleSave: function () {
        var self = this;
        if (!this.config.canEdit) return;
        clearTimeout(this._timer);
        this._timer = setTimeout(function () { self.save(); }, AUTOSAVE_MS);
        // A ceiling as well as a debounce: someone typing continuously for
        // three minutes would otherwise never trigger the trailing edge.
        if (!this._ceiling) {
          this._ceiling = setTimeout(function () { self.save(); }, AUTOSAVE_CEILING_MS);
        }
      },

      save: function (force) {
        var self = this;
        clearTimeout(this._timer);
        clearTimeout(this._ceiling);
        this._ceiling = null;
        if (!this.config.canEdit) return;
        if (this._inflight) { this.scheduleSave(); return; }   // coalesce
        if (!this.ui.dirty && !force) return;

        this._inflight = true;
        this.ui.saving = true;
        this.ui.status = "Saving…";
        var payload = { presentation: M.toPayload(this.state) };
        if (this.baseHash) payload.base_hash = this.baseHash;

        fetch(this.config.urls.save, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify(payload)
        })
          .then(function (r) {
            return r.json().then(function (data) { return { ok: r.ok, status: r.status, data: data }; });
          })
          .then(function (res) {
            self._inflight = false;
            self.ui.saving = false;
            if (res.status === 409) {
              // Do NOT keep retrying: that is how the other tab's work gets
              // overwritten a second later.
              self.ui.conflict = true;
              self.ui.status = res.data.error || "This deck changed elsewhere.";
              return;
            }
            if (!res.ok) { self.ui.status = (res.data && res.data.error) || "Save failed."; return; }
            self.baseHash = res.data.content_hash || self.baseHash;
            self.ui.dirty = false;
            self.ui.status = "Saved";
          })
          .catch(function () {
            self._inflight = false;
            self.ui.saving = false;
            self.ui.status = "Save failed — check your connection.";
          });
      },

      saveOverwriting: function () {
        this.baseHash = "";                 // deliberate: the user chose to win
        this.ui.conflict = false;
        this.save(true);
      },

      // ---- keyboard --------------------------------------------------------
      onKey: function (event) {
        var meta = event.metaKey || event.ctrlKey;
        if (!meta) return;
        var key = event.key.toLowerCase();
        if (key === "s") { event.preventDefault(); this.save(true); return; }
        if (key === "z") {
          // Inside a text field the browser's own undo is the right one — it
          // knows about the caret. Fighting it is how editors lose a cursor.
          if (document.activeElement &&
              document.activeElement.isContentEditable) return;
          event.preventDefault();
          if (event.shiftKey) this.redo(); else this.undo();
          return;
        }
        if (key === "y") {
          if (document.activeElement && document.activeElement.isContentEditable) return;
          event.preventDefault();
          this.redo();
        }
      },

      openMenu: function (event, kind, id) {
        this.ui.menu = { open: true, x: event.clientX, y: event.clientY, kind: kind, id: id };
      }
    };
  };
})(window);

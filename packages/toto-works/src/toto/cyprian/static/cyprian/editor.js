/* The writer: a paginated canvas over CyprianModel.
 *
 * `state` is the only source of truth. Every mutation goes through `mutate`,
 * which commits to history, marks the document dirty and schedules both a save
 * and a re-pagination — so no path changes the document without those happening.
 *
 * The contenteditable rule, inherited from memo and the reason that editor
 * works:
 *
 *   the DOM is written ONCE, when a block element is created, and after that
 *   the model is updated FROM the DOM and never the reverse.
 *
 * Writing back into a focused contenteditable moves the caret to the start on
 * every keystroke. When the model does change underneath — undo, redo, an
 * import — the elements are recreated instead, by bumping `ui.nonce`, which is
 * part of every :key. Stable block ids are what make that safe.
 *
 * Reused wholesale from toto.memo rather than copied: MemoHistory (snapshots
 * with the deferred copy), MemoDrag (pointer-event dragging with the
 * interactive-element guard) and MemoSanitize (the client mirror of the Python
 * allowlist). None of the three has any slide vocabulary in it.
 */
(function (global) {
  "use strict";

  var M = global.CyprianModel;
  var AUTOSAVE_MS = 2000;
  var AUTOSAVE_CEILING_MS = 30000;
  var REPAGINATE_MS = 250;

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

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  global.cyprianEditor = function () {
    var config = readJson("cy-config", {});
    var history = new global.MemoHistory();

    /* The TipTap instance lives HERE, in the closure, and never on the Alpine
     * component — the same place `history` lives, for a sharper reason.
     *
     * Alpine wraps component state in a reactive Proxy. ProseMirror compares
     * `transaction.before` with `state.doc` by IDENTITY, and a proxied doc is
     * never identical to the raw one it was derived from, so every command
     * threw "Applying a mismatched transaction" and the toolbar did nothing.
     * Keeping the editor out of the proxy is the whole fix.
     *
     * `ui.marks` is what the toolbar watches instead: a plain counter, bumped
     * on every selection change, so Alpine still re-evaluates `isActive`.
     */
    var editor = null;
    var editorFor = "";

    return {
      config: config,
      state: M.fromServer(readJson("cy-document", {})),
      baseHash: config.contentHash || "",

      ui: {
        nonce: 0,
        // Reactive mirrors of MemoHistory's state — see canUndo.
        canUndo: false,
        canRedo: false,
        view: "document",
        sourceError: "",
        sourceBusy: false,
        // Bumped on every selection change so Alpine re-evaluates `isActive`:
        // TipTap's state lives outside Alpine and would otherwise never
        // invalidate — the same shape as the canUndo fix above.
        marks: 0,
        dialog: { open: false, kind: "image", payload: "", attrs: {} },
        // What the last "save to the vault" produced, and where it went.
        rendition: { open: false, kind: "", name: "", url: "" },
        // The ask before it: the obligatory file name; for a PDF an optional
        // watermark (text, or a picture from the vault); and the destination —
        // a bucket and folder, defaulting to "beside this document".
        saveDialog: { open: false, kind: "", name: "",
                      watermark: "", watermarkKind: "none",
                      watermarkImage: "", watermarkImageName: "",
                      bucket: 0, directory: 0, imageSearch: "", imageBusy: false },
        saving: false,
        dirty: false,
        status: "",
        conflict: false,
        mediaSearch: "",
        mediaBusy: false,
        pageCount: 1,
        tallCount: 0,
      },

      marginChoices: M.MARGINS,
      fonts: M.FONTS,
      dialogKinds: M.DIALOG_KINDS,
      tones: M.TONES,
      inks: M.INKS,
      sizes: M.SIZES,
      formulaHelp: (global.MemoModel || {}).FORMULA_HELP || [],
      media: readJson("cy-media", []),
      picker: readJson("cy-picker", { buckets: [], directories: [] }),

      _timer: null,
      _ceiling: null,
      _pageTimer: null,
      _inflight: false,

      // ---- boot ------------------------------------------------------------
      boot: function () {
        var self = this;
        history.seed(this.state);
        this.syncHistory();

        global.addEventListener("beforeunload", function (e) {
          if (self.ui.dirty || self._inflight) { e.preventDefault(); e.returnValue = ""; }
        });

        /* Two races to lose gracefully, and `$nextTick` is what settles both.
         *
         * The MODULE may still be loading — it is the only ES module on the
         * page and the browser fetches fifty-odd files for it — so the mount
         * also hangs off `cyprian-tiptap-ready`.
         *
         * The DOM is the other one: boot() runs before Alpine has rendered the
         * x-for over the sections, so the div the editor mounts into does not
         * exist yet. Mounting on the next tick is the difference between an
         * editor and a page that silently has none. */
        var mount = function () {
          global.setTimeout(function () { self.mountEditor(); }, 0);
        };
        if (global.CyprianTipTap) mount();
        global.addEventListener("cyprian-tiptap-ready", mount);

        // The theme toggle reloads the page today, so this is insurance rather
        // than a live requirement — but an ACE stuck in the light theme on a
        // dark page is exactly the mismatch this app keeps fixing elsewhere.
        this.$watch("darkMode", function () { self.syncSourceTheme(); });

      },

      // ---- derived ---------------------------------------------------------
      get fontClass() { return M.FONT_CLASS[this.state.font] || "cy-font-serif"; },
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
      get wordCount() { return M.wordCount(this.state); },
      get readingMinutes() { return Math.max(1, Math.round(this.wordCount / 220)); },
      /* The headings in the body, for the outline strip. Derived from the
       * live HTML rather than stored, because a heading IS the structure now. */
      get outlineEntries() {
        var out = [];
        var re = /<h([1-3])[^>]*>([\s\S]*?)<\/h\1>/gi;
        var match;
        while ((match = re.exec(this.state.content || "")) !== null) {
          var text = match[2].replace(/<[^>]*>/g, "").trim();
          if (text) out.push({ level: parseInt(match[1], 10), text: text });
        }
        return out;
      },
      get filteredMedia() {
        var q = (this.ui.mediaSearch || "").toLowerCase();
        if (!q) return this.media;
        return this.media.filter(function (m) {
          return (m.title + " " + m.location).toLowerCase().indexOf(q) !== -1;
        });
      },


      /* Redraw the editable DOM from the model. Only for changes the DOM did
       * not make itself — see the header. */
      // ---- the one mutation path -------------------------------------------
      /* Everything that changes the document goes through here, so no path can
       * change it without committing to history, marking it dirty and
       * scheduling both a save and a re-pagination. */
      mutate: function (fn, coalesceKey) {
        fn(this.state);
        history.commit(this.state, coalesceKey);
        this.syncHistory();
        this.ui.dirty = true;
        this.scheduleSave();
      },

      /* Publish the history's state into `ui` so the toolbar can see it.
       *
       * Called after everything that can change it. `history` is a plain object
       * Alpine knows nothing about, so a getter calling into it has no reactive
       * dependency — that is the bug that left both buttons disabled for a whole
       * session. */
      syncHistory: function () {
        this.ui.canUndo = history.canUndo();
        this.ui.canRedo = history.canRedo();
      },

      /* Redraw the section DOM from the model, and put the editor back.
       *
       * Only for changes the editor did not make itself — undo, redo, applying
       * the source view, adding or removing a section. Bumping the nonce
       * recreates every `:key`ed element, which destroys the div TipTap was
       * mounted on, so the mount has to follow. */
      refresh: function () {
        var self = this;
        this.ui.nonce += 1;
        this.teardownEditor();
        // setTimeout, not $nextTick: the host div is keyed on `ui.nonce`, so
        // Alpine has to tear the old one down and build the new one before
        // there is anything to mount into.
        global.setTimeout(function () { self.mountEditor(); }, 0);
      },

      // ---- pagination ------------------------------------------------------
      /* The usable height of one page, read off the real element.
       *
       * Measured rather than computed from the margin name: the stylesheet owns
       * those numbers, and duplicating them here is how the editor's pages and
       * the PDF's stop agreeing.
       */
      pageHeight: function () {
        var doc = this.$refs.canvas;
        if (!doc) return 0;
        // The stylesheet's own numbers, read as custom properties. NOT the
        // element's height: `.cy-page` grows with the document, so measuring it
        // would say "one page" however long the text got.
        var styles = global.getComputedStyle(doc);
        var height = parseFloat(styles.getPropertyValue("--cy-page-h"));
        var margin = parseFloat(styles.getPropertyValue("--cy-margin-y"));
        if (!height || isNaN(margin)) return 0;
        return height - (margin * 2);
      },

      setMeta: function (name, value) {
        this.mutate(function (s) { s.meta[name] = value; }, "meta:" + name);
      },

      // ---- page setup ------------------------------------------------------
      // Everything here travels IN THE FILE, so the reader and the PDF agree
      // with the writer. Margins re-paginate; the rest just re-renders.
      setMargins: function (id) {
        // No explicit re-paginate: the decoration plugin re-measures on its
        // next frame, against the CSS variables the new margins just changed.
        this.mutate(function (s) { s.margins = id; }, "margins");
      },
      setFont: function (id) {
        this.mutate(function (s) { s.font = id; }, "font");
      },
      toggleToc: function () {
        this.mutate(function (s) { s.toc = !s.toc; }, "toc");
      },
      /* The cover sheet is opt-in: the ordinary document here is a one-page
       * handout, and a title page would double its length in print. */
      toggleCover: function () {
        this.mutate(function (s) { s.cover = !s.cover; }, "cover");
      },

      // ---- the editor ------------------------------------------------------
      /* ONE TipTap instance, on the section being written.
       *
       * Fifty sections must not mean fifty ProseMirror instances: the active
       * section mounts an editor, every other one renders its stored HTML, and
       * clicking a section moves the editor to it. The cost of a mount is a
       * parse of one section's HTML; the cost of not doing this is a document
       * that takes seconds to open and megabytes of live DOM.
       */
      mountEditor: function (attempt) {
        var self = this;
        if (!global.CyprianTipTap) return;
        var host = document.getElementById("cy-editor");
        if (!host) {
          // Alpine may not have rendered yet — boot() runs before the template
          // does. Try again on the next frame rather than leaving a page with
          // no editor on it; give up after a few so a genuinely missing host
          // cannot spin forever.
          if ((attempt || 0) < 20) {
            global.requestAnimationFrame(function () {
              self.mountEditor((attempt || 0) + 1);
            });
          }
          return;
        }
        if (editor) return;

        editor = global.CyprianTipTap.create(host, this.state.content, {
          onUpdate: function (html) {
            // Read FROM the editor, never written back into a focused one —
            // memo's rule, and the reason the caret stays where it was put.
            self.mutate(function (state) { state.content = html; }, "content");
          },
          onSelection: function () { self.ui.marks += 1; },
          // The A4 content box, measured from the real page element so a margin
          // change is picked up without rebuilding anything.
          pageHeight: function () { return self.pageHeight(); },
          /* A pasted or dropped picture. Uploaded through the same endpoint as
           * the image dialog — one resize policy, one sanitiser — and inserted
           * at the caret as the data URI the server answers with. */
          onImageFile: function (file) {
            var body = new FormData();
            body.append("file", file, file.name || "pasted-image.png");
            self.ui.status = "Embedding…";
            fetch(self.config.urls.upload, {
              method: "POST", headers: { "X-CSRFToken": csrf() }, body: body
            }).then(function (r) { return r.json(); })
              .then(function (data) {
                if (!data || data.error || data.kind === "svg") {
                  self.ui.status = (data && data.error) || "Could not embed that.";
                  return;
                }
                self.ui.status = "";
                self.cmd(function (c) { c.setImage({ src: data.payload,
                                                     alt: data.alt || "" }).run(); });
              })
              .catch(function () { self.ui.status = "Could not embed that."; });
          },
          onPages: function (pages, tall) {
            self.ui.pageCount = pages;
            self.ui.tallCount = tall;
          }
        });
        editorFor = "document";
        this.ui.marks += 1;
        this.renderFormulas(host);
      },

      teardownEditor: function () {
        if (editor) {
          // Take the HTML back before the instance goes: a destroy mid-keystroke
          // would otherwise lose whatever the debounce had not committed.
          var html = editor.getHTML();
          var state = this.state;
          if (html !== state.content) {
            this.mutate(function () { state.content = html; });
          }
          editor.destroy();
        }
        editor = null;
        editorFor = "";
      },

      /* ---- toolbar ---      /* ---- toolbar -------------------------------------------------------
       * Every button runs a TipTap command through the same two helpers, so
       * "is this on" and "turn this on" cannot disagree — which is exactly how
       * a toolbar ends up lying about the caret's state.
       */
      cmd: function (run) {
        if (!editor) return;
        run(editor.chain().focus());
        this.ui.marks += 1;
      },

      isActive: function (name, attrs) {
        // `ui.marks` is read so Alpine re-evaluates on every selection change;
        // TipTap's state lives outside Alpine and would otherwise never
        // invalidate. Same shape as the canUndo fix.
        return this.ui.marks >= 0 && !!editor
               && editor.isActive(name, attrs || undefined);
      },

      toggleMark: function (name) { this.cmd(function (c) { c.toggleMark(name).run(); }); },
      toggleHeading: function (level) {
        this.cmd(function (c) { c.toggleHeading({ level: level }).run(); });
      },
      toggleBulletList: function () { this.cmd(function (c) { c.toggleBulletList().run(); }); },
      toggleOrderedList: function () { this.cmd(function (c) { c.toggleOrderedList().run(); }); },
      toggleQuote: function () { this.cmd(function (c) { c.toggleBlockquote().run(); }); },
      toggleCodeBlock: function () { this.cmd(function (c) { c.toggleCodeBlock().run(); }); },
      setInk: function (ink) { this.cmd(function (c) { c.setCyInk(ink).run(); }); },
      setSize: function (size) { this.cmd(function (c) { c.setCySize(size).run(); }); },
      setCallout: function (tone) { this.cmd(function (c) { c.setCallout(tone).run(); }); },
      addPageBreak: function () { this.cmd(function (c) { c.setPageBreak().run(); }); },
      addDivider: function () { this.cmd(function (c) { c.setHorizontalRule().run(); }); },
      /* An explicit paragraph break. Enter already makes one in prose, but
       * inside a code block, a quote or a table Enter means something else and
       * there is no obvious way OUT — this ends whatever block the caret is in
       * and starts a plain paragraph after it. The visible tool matters in a
       * canvas with no per-block chrome: structure is the writer's act, and
       * this is the act. */
      addParagraph: function () {
        this.cmd(function (c) { c.createParagraphNear().setParagraph().run(); });
      },

      setLink: function () {
        if (!editor) return;
        var current = editor.getAttributes("link").href || "";
        var href = prompt("Link to", current);
        if (href === null) return;
        this.cmd(function (c) {
          if (!href) { c.unsetLink().run(); return; }
          c.extendMarkRange("link").setLink({ href: href }).run();
        });
      },

      // ---- tables ---------------------------------------------------------
      insertTable: function () {
        this.cmd(function (c) {
          c.insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run();
        });
      },
      get inTable() { return this.isActive("table"); },
      tableCmd: function (name) {
        var self = this;
        this.cmd(function (c) { c[name]().run(); });
        this.$nextTick(function () { self.ui.marks += 1; });
      },










      // ---- formulas --------------------------------------------------------
      renderFormula: function (latex, target) {
        if (!target) return;
        var katex = global.katex;
        if (!katex) { target.textContent = latex || ""; return; }
        try {
          katex.render(latex || "", target, {
            displayMode: true, throwOnError: false, errorColor: "#ef4444"
          });
        } catch (e) {
          target.textContent = latex || "";
        }
      },

      renderHelp: function (el, source) {
        if (!global.katex) { el.textContent = source; return; }
        try {
          global.katex.render(source, el, { throwOnError: false, displayMode: false });
        } catch (e) { el.textContent = source; }
      },

      /* The cheatsheet drops LaTeX at the caret in the dialog's source box.
       * Through the model rather than the DOM, because x-model owns that
       * textarea's value and writing round it would be undone on the next
       * render. */
      insertFormulaSnippet: function (snippet) {
        var area = document.getElementById("cy-formula-source");
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

      // ---- the block dialog ------------------------------------------------
      /* Insert and edit are the same form with a different starting value, so
       * they are the same dialog. Everything that needs more than typing lives
       * here rather than inline: three little forms scattered through a page is
       * what made the canvas read as a settings screen. */
      /* The dialog, for the two things that need a form: a picture has to be
       * chosen from somewhere, and a formula is LaTeX you want to see rendered
       * before you commit to it. Everything else on the toolbar is one click.
       */
      openDialog: function (kind) {
        if (!editor) return;
        var current = kind === "formula"
          ? (editor.getAttributes("formula").latex || "")
          : "";
        this.ui.dialog = {
          open: true, kind: kind, payload: current,
          attrs: { alt: "", width: "100", align: "center" }
        };
        var self = this;
        this.$nextTick(function () { self.previewFormula(); });
      },

      closeDialog: function () { this.ui.dialog.open = false; },

      /* The formula preview. Rendered on every keystroke — KaTeX on a single
       * expression is microseconds, and a formula you cannot see until you
       * press Insert is a formula you get wrong. */
      previewFormula: function () {
        var target = this.$refs.formulaPreview;
        if (!target || this.ui.dialog.kind !== "formula") return;
        this.renderFormula(this.ui.dialog.payload, target);
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
            self.ui.dialog.payload = data.payload;
            if (!self.ui.dialog.attrs.alt) self.ui.dialog.attrs.alt = data.alt || "";
          })
          .catch(function () {
            self.ui.mediaBusy = false;
            self.ui.status = "Could not embed that.";
          });
      },

      /* Upload straight into the dialog. The server resizes and sanitises; this
       * only has to put the data URI where Insert can see it. */
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
        if (!editor || !d.payload) { this.closeDialog(); return; }

        if (d.kind === "image") {
          var attrs = d.attrs || {};
          this.cmd(function (c) {
            c.setImage({ src: d.payload, alt: attrs.alt || "" }).run();
          });
          // Width and alignment are data attributes on the img, which is how
          // the stylesheet has always sized a picture — and what the sanitiser
          // allows. TipTap's Image node does not carry them, so they are set on
          // the DOM node it just created.
          var self = this;
          this.$nextTick(function () { self.decorateLastImage(attrs); });
        } else if (d.kind === "formula") {
          var latex = d.payload;
          var rendered = this.$refs.formulaPreview
            ? this.$refs.formulaPreview.innerHTML : "";
          this.cmd(function (c) {
            c.setFormula({ latex: latex, rendered: rendered }).run();
          });
        }
        this.closeDialog();
      },

      decorateLastImage: function (attrs) {
        if (!editor) return;
        var host = editor.view.dom;
        var images = host.querySelectorAll("img");
        var last = images[images.length - 1];
        if (!last) return;
        if (attrs.width) last.setAttribute("data-width", attrs.width);
        if (attrs.align) last.setAttribute("data-align", attrs.align);
        // The DOM is the source of truth while the editor is live, so read it
        // back through the ordinary path rather than patching the model.
        var html = editor.getHTML();
        this.mutate(function (state) { state.content = html; });
      },

      /* KaTeX over every formula node in a mounted editor — the node stores its
       * rendering, but a document written on a build without KaTeX, or edited
       * by hand, may have only the source. */
      renderFormulas: function (host) {
        if (!host || !global.katex) return;
        var nodes = host.querySelectorAll(".cy-formula[data-latex]");
        Array.prototype.forEach.call(nodes, function (node) {
          if (node.querySelector(".katex")) return;
          try {
            global.katex.render(node.getAttribute("data-latex") || "", node, {
              displayMode: true, throwOnError: false, errorColor: "#ef4444"
            });
          } catch (e) { /* leave the source showing */ }
        });
      },

      /* Render the document into the vault, beside itself.
       *
       * Saves first: a rendition of what is on screen but not on disk would be
       * a file that does not match the document it claims to be. Then shows the
       * link, because "saved to your bucket" without one means hunting for it.
       */
      /* Open the save dialog. The one moment a file name is asked for — there
       * is no name field in the header, because naming is part of SAVING a
       * product of the document, not of writing it. The name is obligatory (the
       * dialog's button stays disabled without one); the watermark is offered
       * for PDF only, where there are pages to stamp. */
      saveRendition: function (kind) {
        var home = this.config.home || {};
        this.ui.saveDialog = {
          open: true, kind: kind,
          name: (this.config.renditionBase || "document") + "." + kind,
          watermark: "", watermarkKind: "none",
          watermarkImage: "", watermarkImageName: "",
          bucket: home.bucket || 0, directory: home.directory || 0,
          imageSearch: "", imageBusy: false
        };
      },

      /* The destination tree: the chosen bucket's folders, root first, indented
       * by depth. From the same picker data the New Document flow uses, so
       * what is offered is exactly what the user may write into. */
      get saveDirs() {
        var picker = this.picker || { directories: [] };
        var bucket = Number(this.ui.saveDialog.bucket);
        return (picker.directories || [])
          .filter(function (d) { return d.bucket_id === bucket; })
          .map(function (d) {
            return { id: d.id, path: d.path,
                     depth: (d.path.match(/\//g) || []).length };
          });
      },

      /* Watermark pictures come from the vault like every other image here:
       * the embed endpoint answers with a data URI, so the PDF renderer never
       * fetches anything. */
      get watermarkMedia() {
        var q = (this.ui.saveDialog.imageSearch || "").toLowerCase();
        return this.media.filter(function (m) {
          return !q || (m.title || "").toLowerCase().indexOf(q) !== -1;
        }).slice(0, 40);
      },

      pickWatermark: function (pk, title) {
        var self = this;
        this.ui.saveDialog.imageBusy = true;
        fetch(this.config.urls.embed + "?file_pk=" + encodeURIComponent(pk))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            self.ui.saveDialog.imageBusy = false;
            if (!data || data.error || data.type === "svg") {
              // an svg embed is markup, not a data URI — no stamp from it
              self.ui.status = (data && data.error) || "Pick a bitmap image.";
              return;
            }
            self.ui.saveDialog.watermarkImage = data.payload;
            self.ui.saveDialog.watermarkImageName = title || "";
          })
          .catch(function () {
            self.ui.saveDialog.imageBusy = false;
            self.ui.status = "Could not load that image.";
          });
      },

      confirmRendition: function () {
        var d = this.ui.saveDialog;
        if (!d.open || !(d.name || "").trim()) return;
        var self = this;
        var kind = d.kind;
        this.ui.saveDialog.open = false;
        this.ui.status = "Rendering…";
        var url = kind === "pdf" ? this.config.urls.savePdf : this.config.urls.saveHtml;
        var body = new FormData();
        body.append("name", d.name.trim());
        if (kind === "pdf" && d.watermarkKind === "text" && (d.watermark || "").trim()) {
          body.append("watermark", d.watermark.trim());
        }
        if (kind === "pdf" && d.watermarkKind === "image" && d.watermarkImage) {
          body.append("watermark_image", d.watermarkImage);
        }
        if (d.bucket) {
          body.append("bucket", String(d.bucket));
          if (d.directory) body.append("directory", String(d.directory));
        }
        Promise.resolve(this.ui.dirty ? this.save(true) : null)
          .then(function () {
            return fetch(url, {
              method: "POST", headers: { "X-CSRFToken": csrf() }, body: body
            });
          })
          .then(function (r) {
            return r.json().then(function (d) { return { ok: r.ok, data: d }; });
          })
          .then(function (res) {
            if (!res.ok) {
              // A missing WeasyPrint is a deployment fact, not something to
              // retry — it comes back with the flag to build.
              self.ui.status = (res.data && res.data.error) || "Could not save that.";
              return;
            }
            self.ui.status = "Saved " + res.data.name;
            self.ui.rendition = {
              open: true, kind: res.data.kind,
              name: res.data.name, url: res.data.url
            };
          })
          .catch(function () { self.ui.status = "Could not save that."; });
      },

      // ---- the source view -------------------------------------------------
      setView: function (view) {
        this.ui.view = view;
        if (view === "source") this.loadSource();
      },

      /* Mount ACE lazily: the source view is a second surface, and building an
       * editor for it at boot costs every writer who never opens it. */
      loadSource: function (force) {
        var self = this;
        if (typeof ace === "undefined") {
          this.ui.sourceError = "The source editor did not load.";
          return;
        }
        if (!this._ace) {
          this._ace = ace.edit("cy-source");
          this._ace.session.setMode("ace/mode/xml");
          // No syntax worker. ACE would fetch `worker-xml.js` from a base path
          // this page never configures — a 404 and a console error for a
          // validator nobody asked for. The server's parser is the one that
          // decides whether the source is good, and it says so on Apply.
          this._ace.session.setUseWorker(false);
          this._ace.setOptions({ fontSize: "13px", showPrintMargin: false,
                                 useSoftTabs: true, tabSize: 2, wrap: true });
        }
        // Every time it is shown, not once at construction: the theme toggle is
        // a live Alpine value, and a source view stuck in the light theme on a
        // dark page is exactly the mismatch this app keeps fixing elsewhere.
        this.syncSourceTheme();
        if (this._sourceLoaded && !force) return;

        // Save first, so what the source view shows is the document as it
        // WOULD be stored — not a version two keystrokes behind it.
        var start = this.ui.dirty ? this.save(true) : Promise.resolve();
        Promise.resolve(start).then(function () {
          return fetch(self.config.urls.source, { headers: { "Accept": "text/plain" } });
        }).then(function (r) { return r.text(); })
          .then(function (text) {
            self._ace.setValue(text, -1);
            self._sourceLoaded = true;
            self.ui.sourceError = "";
          })
          .catch(function () { self.ui.sourceError = "Could not read the file."; });
      },

      /* Light or dark. See isDarkMode() for where the answer comes from. */
      syncSourceTheme: function () {
        if (this._ace) this._ace.setTheme(aceTheme());
      },

      /* Applying goes through the SERVER's parser rather than a second one
       * written here. One parser, one set of rules, and a malformed file is
       * explained in the same sentence the save endpoint would use. */
      applySource: function () {
        var self = this;
        if (!this._ace) return;
        this.ui.sourceBusy = true;
        this.ui.sourceError = "";
        fetch(this.config.urls.source, {
          method: "POST",
          headers: { "Content-Type": "application/xml", "X-CSRFToken": csrf() },
          body: this._ace.getValue()
        })
          .then(function (r) {
            return r.json().then(function (d) { return { ok: r.ok, data: d }; });
          })
          .then(function (res) {
            self.ui.sourceBusy = false;
            if (!res.ok || !res.data.document) {
              self.ui.sourceError = (res.data && res.data.error) || "That is not a document.";
              return;
            }
            self.teardownEditor();
            self.state = M.fromServer(res.data.document);
            history.seed(self.state);
            self.syncHistory();
            self.ui.dirty = true;
            self.ui.status = "Applied from source.";
            self.ui.view = "document";
            self.refresh();
            self.scheduleSave();
          })
          .catch(function () {
            self.ui.sourceBusy = false;
            self.ui.sourceError = "Could not apply that.";
          });
      },

      // ---- undo ------------------------------------------------------------
      undo: function () {
        var restored = history.undo();
        if (restored) this.applySnapshot(restored, "Undone.");
      },

      redo: function () {
        var restored = history.redo();
        if (restored) this.applySnapshot(restored, "Redone.");
      },

      applySnapshot: function (snapshot, message) {
        // The model changed underneath a live editor, so the instance holding
        // the old text has to go before the DOM is recreated.
        this.teardownEditor();
        this.state = snapshot;
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
        // A ceiling as well as a debounce: writing continuously for three
        // minutes would otherwise never reach the trailing edge.
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
        var payload = { document: M.toPayload(this.state) };
        if (this.baseHash) payload.base_hash = this.baseHash;

        fetch(this.config.urls.save, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify(payload)
        })
          .then(function (r) {
            return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
          })
          .then(function (res) {
            self._inflight = false;
            self.ui.saving = false;
            if (res.status === 409) {
              // Do NOT retry: that is how the other tab's work gets overwritten
              // a second later.
              self.ui.conflict = true;
              self.ui.status = res.data.error || "This document changed elsewhere.";
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
        if (key === "z" || key === "y") {
          // Inside a text field the browser's own undo is the right one — it
          // knows about the caret. Fighting it is how editors lose a cursor.
          if (document.activeElement && document.activeElement.isContentEditable) return;
          event.preventDefault();
          if (key === "y" || event.shiftKey) this.redo(); else this.undo();
        }
      }
    };
  };
})(window);

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

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  global.memoEditor = function () {
    var config = readJson("memo-config", {});
    var history = new global.MemoHistory();

    return {
      config: config,
      state: M.fromServer(readJson("memo-data", {})),
      baseHash: config.contentHash || "",

      ui: {
        activeId: "",
        focusedId: "",
        nonce: 0,
        filmstrip: true,
        saving: false,
        dirty: false,
        status: "",
        conflict: false,
        showMedia: false,
        mediaSearch: "",
        mediaBusy: false,
        menu: { open: false, x: 0, y: 0, id: "", kind: "" },
        popover: { open: false, x: 0, y: 0 }
      },

      layouts: M.LAYOUTS,
      fonts: M.FONTS,
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

        this._stopCanvas = global.MemoCanvas.observe(this.$refs.stage);
        this.scheduleAutofit();
        global.MemoDrag.init(this.$el, {
          onDrop: function (kind, id, zone, before) { self.onDrop(kind, id, zone, before); },
          onClick: function (kind, id) {
            if (kind === "slide") self.select(id);
            else self.focusBlock(id);
          }
        });

        document.addEventListener("selectionchange", function () {
          self.syncPopover();
        });

        global.addEventListener("beforeunload", function (e) {
          if (self.ui.dirty || self._inflight) { e.preventDefault(); e.returnValue = ""; }
        });
      },

      destroy: function () { if (this._stopCanvas) this._stopCanvas(); },

      // ---- derived ---------------------------------------------------------
      get activeSlide() {
        var at = M.indexOfId(this.state.slides, this.ui.activeId);
        return this.state.slides[at === -1 ? 0 : at];
      },
      get activeIndex() { return M.indexOfId(this.state.slides, this.ui.activeId); },
      get columns() { return M.columns(this.activeSlide); },
      get canUndo() { return history.canUndo(); },
      get canRedo() { return history.canRedo(); },
      get filteredMedia() {
        var q = (this.ui.mediaSearch || "").toLowerCase();
        if (!q) return this.media;
        return this.media.filter(function (m) {
          return (m.title + " " + m.location).toLowerCase().indexOf(q) !== -1;
        });
      },

      placeholderFor: function (block) { return M.PLACEHOLDER[block.type] || ""; },
      isFocused: function (id) { return this.ui.focusedId === id; },

      // ---- the one mutation path -------------------------------------------
      mutate: function (fn, coalesceKey) {
        fn(this.state);
        history.commit(this.state, coalesceKey);
        this.ui.dirty = true;
        this.scheduleSave();
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

      /* The manual override pins a number and stops auto-fitting that block. */
      nudgeScale: function (block, delta) {
        var C = global.MemoCanvas;
        var current = M.blockScale(block);
        var next = Math.min(C.MAX_SCALE, Math.max(C.MIN_SCALE,
                            +(current + delta).toFixed(2)));
        this.mutate(function () { block.attrs.scale = next.toFixed(2); },
                    "scale:" + block.id);
        this.refresh();
      },

      resetScale: function (block) {
        this.mutate(function () { block.attrs.scale = "auto"; });
        this.refresh();
      },

      scaleLabel: function (block) {
        return Math.round(M.blockScale(block) * 100) + "%";
      },

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

      addBlock: function (type, slot) {
        var slideId = this.activeSlide.id;
        var block = M.newBlock(type);
        this.mutate(function (s) { M.insertBlock(s, slideId, block, slot || "", null); });
        this.ui.focusedId = block.id;
        this.refresh();
      },

      removeBlock: function (id) {
        this.mutate(function (s) { M.deleteBlock(s, id); });
        if (this.ui.focusedId === id) this.ui.focusedId = "";
        this.refresh();
      },

      nudgeBlock: function (id, delta) {
        // The keyboard equivalent of dragging, calling the same model function.
        var slide = this.activeSlide;
        this.mutate(function () { M.nudge(slide.blocks, id, delta); });
        this.refresh();
      },

      moveBlockToSlot: function (id, slot) {
        this.mutate(function (s) { M.moveBlock(s, id, slot, null); });
        this.refresh();
      },

      setBlockAttr: function (block, name, value) {
        this.mutate(function () { block.attrs[name] = String(value); },
                    "attr:" + block.id + ":" + name);
      },

      // ---- text ------------------------------------------------------------
      /* Called on input from a contenteditable. Reads the DOM into the model
       * and does NOT write back — see the header. */
      onInput: function (block, el, rich) {
        var html = rich ? global.MemoSanitize.rich(el.innerHTML)
                        : global.MemoSanitize.inline(el.innerHTML);
        this.mutate(function () { block.payload = html; }, "text:" + block.id);
      },

      onTitleInput: function (el) {
        var slide = this.activeSlide;
        var text = global.MemoSanitize.text(el.innerHTML);
        this.mutate(function () { slide.title = text; }, "title:" + slide.id);
      },

      onCodeInput: function (block, el) {
        // textContent, not innerHTML: a code block is literal text, and the
        // server keeps it literal for the same reason.
        var text = el.textContent || "";
        this.mutate(function () { block.payload = text; }, "code:" + block.id);
      },

      onItemInput: function (block, index, el) {
        var html = global.MemoSanitize.inline(el.innerHTML);
        this.mutate(function () { block.items[index] = html; },
                    "item:" + block.id + ":" + index);
      },

      onItemKey: function (event, block, index) {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          this.mutate(function () { block.items.splice(index + 1, 0, ""); });
          this.refresh();
        } else if (event.key === "Backspace" && !(event.target.textContent || "").length
                   && block.items.length > 1) {
          event.preventDefault();
          this.mutate(function () { block.items.splice(index, 1); });
          this.refresh();
        }
      },

      // ---- the selection popover -------------------------------------------
      syncPopover: function () {
        var sel = document.getSelection();
        if (!sel || sel.isCollapsed || !sel.rangeCount) {
          this.ui.popover.open = false;
          return;
        }
        var node = sel.anchorNode;
        var host = node && (node.nodeType === 1 ? node : node.parentNode);
        if (!host || !host.closest || !host.closest("[data-rich]")) {
          this.ui.popover.open = false;
          return;
        }
        var rect = sel.getRangeAt(0).getBoundingClientRect();
        this.ui.popover.x = rect.left + rect.width / 2;
        this.ui.popover.y = rect.top;
        this.ui.popover.open = true;
      },

      /* execCommand is deprecated and still the only thing that applies a mark
       * to a selection inside contenteditable without hand-writing range
       * surgery. Every browser this platform supports implements it; the
       * replacement (the Highlight API) cannot edit content at all. */
      mark: function (command) {
        document.execCommand(command, false, null);
        this.commitFocused();
      },

      link: function () {
        var href = prompt("Link to");
        if (href === null) return;
        document.execCommand(href ? "createLink" : "unlink", false, href || null);
        this.commitFocused();
      },

      commitFocused: function () {
        var el = document.querySelector('[data-rich][data-block="' + this.ui.focusedId + '"]');
        if (!el) return;
        var found = M.findBlock(this.state, this.ui.focusedId);
        if (found) this.onInput(found.block, el, found.block.type === "text");
      },

      // ---- drag ------------------------------------------------------------
      onDrop: function (kind, id, zone, before) {
        var self = this;
        if (kind === "slide") {
          var to = before ? M.indexOfId(this.state.slides, before)
                          : this.state.slides.length;
          this.mutate(function (s) { M.moveSlide(s, id, to); });
          this.select(id);
          return;
        }
        if (kind === "block") {
          var slot = zone.getAttribute("data-slot") || "";
          this.mutate(function (s) { M.moveBlock(s, id, slot, before); });
          this.refresh();
          return;
        }
        if (kind === "media") {
          this.insertMedia(id, zone.getAttribute("data-slot") || "", before);
        }
      },

      // ---- media -----------------------------------------------------------
      insertMedia: function (pk, slot, before) {
        var self = this;
        this.ui.mediaBusy = true;
        fetch(this.config.urls.embed + "?file_pk=" + encodeURIComponent(pk))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            self.ui.mediaBusy = false;
            if (!data || data.error) { self.ui.status = (data && data.error) || "Could not embed that."; return; }
            var kind = data.kind === "svg" ? "svg" : "image";
            var block = M.newBlock(kind);
            block.payload = data.kind === "svg" ? (data.markup || data.payload)
                                                : (data.data_uri || data.payload);
            block.attrs.alt = data.alt || "";
            var slideId = self.activeSlide.id;
            self.mutate(function (s) { M.insertBlock(s, slideId, block, slot, before); });
            self.ui.showMedia = false;
            self.refresh();
          })
          .catch(function () { self.ui.mediaBusy = false; self.ui.status = "Could not embed that."; });
      },

      /* Files dropped from the desktop, or picked with the file input.
       *
       * The browser owns this gesture, so it is a native drop rather than the
       * pointer-event engine above — what the two share is the insertion path,
       * not the interaction. Bytes go to the server, which resizes and
       * sanitises them: doing it in a canvas here would mean two resize
       * policies that drift, and an SVG sanitiser that can be skipped.
       */
      onFiles: function (files, slot, before) {
        var self = this;
        Array.prototype.slice.call(files || []).forEach(function (file) {
          var form = new FormData();
          form.append("file", file);
          self.ui.mediaBusy = true;
          fetch(self.config.urls.upload, {
            method: "POST", headers: { "X-CSRFToken": csrf() }, body: form
          })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              self.ui.mediaBusy = false;
              if (!data || data.error) { self.ui.status = (data && data.error) || "Upload failed."; return; }
              var kind = data.kind === "svg" ? "svg" : "image";
              var block = M.newBlock(kind);
              block.payload = data.payload;
              block.attrs.alt = data.alt || "";
              var slideId = self.activeSlide.id;
              self.mutate(function (s) { M.insertBlock(s, slideId, block, slot || "", before || null); });
              self.refresh();
            })
            .catch(function () { self.ui.mediaBusy = false; self.ui.status = "Upload failed."; });
        });
      },

      onFileDrop: function (event, slot) {
        var files = event.dataTransfer && event.dataTransfer.files;
        if (files && files.length) this.onFiles(files, slot, null);
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

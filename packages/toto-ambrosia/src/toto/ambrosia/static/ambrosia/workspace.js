/* The workspace room: tree, tabs, editor, and the wiring to the console.
 *
 * The tree is a FLAT list of rows each carrying a `depth` and a parent id —
 * vault's idiom, and the good part of it. Indentation is padding and visibility
 * is a parent walk, so arbitrary nesting costs nothing and filtering does not
 * have to rebuild a tree.
 */
(function (global) {
  "use strict";

  /* ACE modes, and the reason the list is this short.
   *
   * Exactly five mode files are vendored under core/static/vendor/ace —
   * bibtex, html, latex, python, xml. Anything else 404s: `ace/mode/json` and
   * `ace/mode/yaml` are NOT there, and asking for one leaves the editor with no
   * highlighting and an error in the console. `ace/mode/text` is the exception,
   * because it lives inside ace.js itself.
   *
   * So json, yaml, csv and log files are deliberately mapped to text rather
   * than to the mode they deserve. A wrong or missing highlighter is worse than
   * a plain one.
   */
  var MODES = {
    python: "ace/mode/python",
    latex: "ace/mode/latex",
    bib: "ace/mode/bibtex",
    html: "ace/mode/html",
    xml: "ace/mode/xml",
    text: "ace/mode/text",
    json: "ace/mode/text",
    yaml: "ace/mode/text",
    csv: "ace/mode/text"
  };

  // How often a queued compile is asked about, and how long before we stop.
  var POLL_MS = 1500;
  var POLL_LIMIT = 240;                            // 6 minutes

  function readJson(id, fallback) {
    var el = document.getElementById(id);
    if (!el) return fallback;
    try { return JSON.parse(el.textContent); } catch (e) { return fallback; }
  }

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "Bad response" }; });
    });
  }

  function get(url) {
    return fetch(url).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "Bad response" }; });
    });
  }

  global.ambrosiaRoom = function () {
    var config = readJson("ambrosia-config", { urls: {} });
    var transcript = new global.AmbrosiaTranscript();

    return {
      // ---- state ----
      config: config,
      readonly: !!config.readonly,
      items: readJson("ambrosia-tree", []),
      openDirs: {},
      filter: "",
      tabs: [],
      activePk: null,
      editor: null,
      statusLine: "",
      menu: { open: false, x: 0, y: 0, item: null },

      // console
      // A COPY of the transcript, never the transcript's own array. Alpine 3
      // wraps this object in a reactive Proxy, but `transcript` closes over the
      // raw array it built before Alpine ever saw it: pushing there mutates the
      // raw array, the proxy's set trap never fires, and nothing re-renders.
      // Re-assigning `transcript.entries` afterwards does not help either — it
      // is the same reference, so Vue's hasChanged() sees no change. Every
      // update therefore goes through syncConsole(), which assigns a NEW array.
      entries: transcript.entries.slice(),
      autoScroll: true,
      // NAMED FOR A KERNEL, MEANS "a job is in flight" since 2026-09-10.
      // `kernelState` and `prompt` sat beside it and are gone with the kernel;
      // this one is still set by runFile() and read by the LaTeX toolbar's
      // `busy` getter, so it is a live flag with a stale name. Left alone
      // deliberately: renaming it touches ~10 sites across a file that is
      // about to be ported to enigma, and a rename is not worth a merge
      // conflict there. See zenobia/limbo/workspace_ui/.
      kernelBusy: false,

      // latex
      isLatex: !!config.isLatex,
      // Which assistant surface this room offers, or "" for none. From the
      // server, because only it knows whether toto.steven is installed.
      surfaceKey: config.stevenSurface || "",
      mainPk: config.mainPk || null,
      mainName: "",

      // The compile in flight, if any. `runId` is what pollRun follows and
      // `runState` is what the toolbar badge shows.
      runId: null,
      runState: "",

      // ---- settings drawer ----
      // `settingsFields` is the lab's own description of its knobs (kind,
      // bounds, choices, help), built server-side — the room renders controls
      // from it without knowing what any key means. `settingsDraft` is what
      // the form edits; it only reaches the server on Save.
      settingsOpen: false,
      settingsFields: config.settingsFields || [],
      settingsDraft: Object.assign({}, config.settings || {}),
      settingsEnvRows: [],
      settingsFieldErrors: {},
      settingsError: "",
      settingsSaving: false,
      settingsRestartHint: false,

      // ---- boot ----
      boot: function () {
        var self = this;
        // The git toolbar's commit flush: land the active buffer before the
        // commit modal reads repo status, so the commit sees what is on screen.
        window.repoFlushSave = function () { return self.saveActive(); };
        this.items.forEach(function (i) {
          if (i.t === "dir") self.openDirs[i.id] = true;   // start expanded
        });
        // ?destroy=1 used to open the disclosure at the foot of the page; the
        // drawer absorbed it, so the same link opens the drawer instead. A
        // link people have bookmarked should not stop working.
        if (config.openSettings) this.openSettings();
        this.mountEditor();
        // NOTHING TO POLL since 2026-09-10. A Python workspace used to ask
        // `dracena:kernel_status` on load; the route is deleted and there is no
        // state to report — a Run starts a process and it exits. A LaTeX
        // workspace never polled, for the same reason it does now: no kernel.
        this.syncLatexNames();

        // ?file=<pk> — how the vault browser's Edit button lands here.
        var wanted = new URLSearchParams(location.search).get("file");
        var first = null, main = null;
        this.items.forEach(function (i) {
          if (i.t !== "file" || !i.editable) return;
          if (i.main) main = i;
          if (wanted && String(i.id) === String(wanted)) first = i;
          else if (!wanted && !first) first = i;
        });
        if (!wanted && main) first = main;
        if (first) this.openFile(first);

        window.addEventListener("beforeunload", function (e) {
          if (self.tabs.some(function (t) { return t.dirty; })) {
            e.preventDefault();
            e.returnValue = "";
          }
        });
      },

      mountEditor: function () {
        if (typeof ace === "undefined") return;
        // Read dark mode from localStorage, which is Oya's actual source of
        // truth (base.html seeds x-data from it) and is readable before Alpine
        // has booted. The theme toggle reloads the page, so this is read once.
        var dark = false;
        try { dark = localStorage.getItem("darkMode") === "true"; } catch (e) {}
        this.editor = ace.edit("ambrosia-editor");
        this.editor.setTheme(dark ? "ace/theme/twilight" : "ace/theme/textmate");
        this.editor.session.setMode(
          this.isLatex ? "ace/mode/latex" : "ace/mode/python");
        this.editor.setOptions({
          fontSize: "13px", showPrintMargin: false, useSoftTabs: true, tabSize: 4
        });
        this.editor.setReadOnly(this.readonly);
        this.registerAssistant();

        var self = this;
        this.editor.session.on("change", function () {
          var tab = self.activeTab;
          if (!tab || tab.loading) return;
          tab.content = self.editor.getValue();
          tab.dirty = tab.content !== tab.saved;
        });
        // Ctrl/Cmd+S saves, because everyone tries it.
        this.editor.commands.addCommand({
          name: "ambrosiaSave",
          bindKey: { win: "Ctrl-S", mac: "Command-S" },
          exec: function () { self.saveActive(); }
        });
      },

      // ---- tree ----
      get dirMap() {
        var map = {};
        this.items.forEach(function (i) { if (i.t === "dir") map[i.id] = i; });
        return map;
      },

      isOpen: function (id) { return !!this.openDirs[id]; },

      toggle: function (id) {
        this.openDirs[id] = !this.openDirs[id];
      },

      /* A row shows when every ancestor is expanded. Filtering bypasses that —
       * a match you cannot see is not a match. */
      get visibleItems() {
        var self = this;
        var needle = this.filter.trim().toLowerCase();
        if (needle) {
          return this.items.filter(function (i) {
            return i.t === "file" && i.name.toLowerCase().indexOf(needle) !== -1;
          });
        }
        var dirs = this.dirMap;
        function visible(item) {
          if (item.pid === null || item.pid === undefined) return true;
          if (!self.openDirs[item.pid]) return false;
          var parent = dirs[item.pid];
          return parent ? visible(parent) : true;
        }
        return this.items.filter(visible);
      },

      isActive: function (item) {
        return item.t === "file" && item.id === this.activePk;
      },

      refreshTree: function () {
        var self = this;
        get(config.urls.tree).then(function (data) {
          if (data.ok) self.items = data.items;
        });
      },

      openMenu: function (event, item) {
        this.menu = {
          open: true, item: item,
          x: Math.min(event.clientX, window.innerWidth - 200),
          y: Math.min(event.clientY, window.innerHeight - 200)
        };
      },

      // ---- tabs ----
      get activeTab() {
        var pk = this.activePk;
        return this.tabs.filter(function (t) { return t.pk === pk; })[0] || null;
      },

      openFile: function (item) {
        if (!item || item.t !== "file") return;
        if (!item.editable) {
          if (item.viewable) { this.openRaw(item); return; }
          this.statusLine = item.encrypted
            ? "That file is encrypted."
            : "That file type does not open here.";
          return;
        }
        var existing = this.tabs.filter(function (t) { return t.pk === item.id; })[0];
        if (existing) { this.activate(item.id); return; }

        var self = this;
        var url = config.urls.content.replace(/\/0\/$/, "/" + item.id + "/");
        get(url).then(function (data) {
          if (!data.ok) { self.statusLine = data.error || "Could not open that file."; return; }
          self.tabs.push({
            pk: data.pk, name: data.name, file_type: data.file_type,
            content: data.content, saved: data.content, dirty: false,
            loading: false, readonly: !!data.readonly,
            truncated: !!data.truncated
          });
          self.activate(data.pk);
        });
      },

      activate: function (pk) {
        var tab = this.tabs.filter(function (t) { return t.pk === pk; })[0];
        if (!tab || !this.editor) return;
        this.activePk = pk;
        tab.loading = true;                       // suppress the change handler
        this.editor.session.setMode(MODES[tab.file_type] || "ace/mode/text");
        this.editor.setValue(tab.content, -1);
        // Server-side `file_save` refuses these too — this is only so the caret
        // does not invite an edit that would be rejected.
        this.editor.setReadOnly(!!tab.readonly);
        tab.loading = false;
        this.editor.focus();
      },

      closeTab: function (pk) {
        var tab = this.tabs.filter(function (t) { return t.pk === pk; })[0];
        if (tab && tab.dirty
            && !confirm("“" + tab.name + "” has unsaved changes. Close it anyway?")) {
          return;
        }
        this.tabs = this.tabs.filter(function (t) { return t.pk !== pk; });
        if (this.activePk === pk) {
          if (this.tabs.length) this.activate(this.tabs[this.tabs.length - 1].pk);
          else { this.activePk = null; if (this.editor) this.editor.setValue("", -1); }
        }
      },

      // Returns the save promise (or a resolved one when there is nothing to
      // do) so the git commit flush can await the buffer actually landing.
      saveActive: function () {
        var tab = this.activeTab;
        if (!tab || this.readonly) return Promise.resolve();
        if (tab.readonly) {
          this.statusLine = "“" + tab.name + "” is generated — edit the source.";
          return Promise.resolve();
        }
        var self = this;
        var url = config.urls.save.replace(/\/0\/save\/$/, "/" + tab.pk + "/save/");
        this.statusLine = "Saving…";
        return post(url, { content: tab.content }).then(function (data) {
          if (!data.ok) { self.statusLine = data.error || "Save failed."; return; }
          tab.saved = tab.content;
          tab.dirty = false;
          self.statusLine = "Saved " + tab.name;
        });
      },

      // ---- file operations ----
      promptNewFile: function (dirId) {
        var name = prompt("New file name", "untitled.py");
        if (!name) return;
        var self = this;
        post(config.urls.createFile, { name: name, directory: dirId })
          .then(function (data) {
            if (!data.ok) { self.statusLine = data.error; return; }
            self.items = data.items;
            var made = data.items.filter(function (i) {
              return i.t === "file" && i.id === data.pk;
            })[0];
            if (made) self.openFile(made);
          });
      },

      promptNewDir: function (parentId) {
        var name = prompt("New folder name", "");
        if (!name) return;
        var self = this;
        post(config.urls.createDir, { name: name, parent: parentId })
          .then(function (data) {
            if (!data.ok) { self.statusLine = data.error; return; }
            self.items = data.items;
          });
      },

      promptRename: function (item) {
        var name = prompt("Rename to", item.name);
        if (!name || name === item.name) return;
        var self = this;
        var url = config.urls.rename.replace(/\/0\/rename\/$/, "/" + item.id + "/rename/");
        post(url, { name: name }).then(function (data) {
          if (!data.ok) { self.statusLine = data.error; return; }
          self.items = data.items;
          var tab = self.tabs.filter(function (t) { return t.pk === item.id; })[0];
          if (tab) tab.name = name;
        });
      },

      confirmDelete: function (item) {
        if (!confirm("Delete “" + item.name + "”? This cannot be undone.")) return;
        var self = this;
        var url = config.urls.delete.replace(/\/0\/delete\/$/, "/" + item.id + "/delete/");
        post(url, {}).then(function (data) {
          if (!data.ok) { self.statusLine = data.error; return; }
          self.items = data.items;
          self.closeTabSilently(item.id);
        });
      },

      closeTabSilently: function (pk) {
        this.tabs = this.tabs.filter(function (t) { return t.pk !== pk; });
        if (this.activePk === pk) {
          this.activePk = this.tabs.length ? this.tabs[this.tabs.length - 1].pk : null;
          if (this.activePk) this.activate(this.activePk);
          else if (this.editor) this.editor.setValue("", -1);
        }
      },

      // ---- execution ----
      //
      // FOUR MEMBERS WENT ON 2026-09-10 with the kernel they drove:
      // `kernelReady` and `kernelLabel` (the toolbar pip and its caption),
      // `pollKernel` (GET dracena:kernel_status) and `kernelAction` (POST
      // dracena:kernel_action, for Restart and Stop). Both routes are deleted.
      //
      // `canRun` REPLACES `kernelReady` as the Run button's guard, and the
      // change of meaning is the point: it used to ask "is there a live
      // interpreter", which after the removal would have been false forever
      // and left the button permanently greyed out. It now asks the only
      // question left — is something already running.
      get canRun() { return !this.kernelBusy; },

      runFile: function () {
        var tab = this.activeTab;
        if (!tab) return;
        // Save first: running a buffer that differs from the file on disk is the
        // kind of confusion that costs an afternoon.
        var self = this;
        if (tab.dirty && !this.readonly) {
          var url = config.urls.save.replace(/\/0\/save\/$/, "/" + tab.pk + "/save/");
          post(url, { content: tab.content }).then(function () {
            tab.saved = tab.content; tab.dirty = false;
            self.execute(tab.content, tab.name);
          });
        } else {
          this.execute(tab.content, tab.name);
        }
      },

      /* ---- the assistant --------------------------------------------------
       * This hands read and write to the shared client, so a Python or LaTeX
       * file open in a lab gets the same toolbar as one open in the plain
       * editor. It uses the ACE selection API directly and does NOT depend on
       * the room's own run-a-selection button, which was removed on
       * 2026-09-10 — the assistant kept working precisely because it never
       * borrowed it.
       *
       * The surface is chosen from the workspace KIND, because that is what
       * decides the language here: a .py inside a LaTeX project opens in the
       * texlab room, and asking a prose question about it would be wrong.
       */
      registerAssistant: function () {
        var self = this;
        if (!window.StevenActions || !this.surfaceKey) return;
        window.StevenActions.register(this.surfaceKey, {
          read: function () {
            return self.editor ? (self.editor.getSelectedText() || "") : "";
          },
          write: function (text) {
            if (!self.editor) return;
            var range = self.editor.getSelectionRange();
            if (range.isEmpty()) return;
            self.editor.session.replace(range, text);
            self.editor.focus();
          },
          document: function () {
            return self.editor ? self.editor.getValue() : "";
          },
          /* Raw source, so asking and regenerating are the same bytes — a
             .tex or a .py file is exactly what "new code in the encoding
             language" means. */
          source: function () {
            return self.editor ? self.editor.getValue() : "";
          },
          writeDocument: function (text) {
            if (!self.editor) return;
            /* -1 leaves the cursor at the start rather than selecting the whole
               new file; one undo step, and the change event the dirty flag and
               autosave already watch. */
            self.editor.session.setValue(text, -1);
            self.editor.focus();
          },
          /* ACE paints its own selection, so window.getSelection() is empty
             here and the shared fallback would never place the button. */
          anchor: function () {
            if (!self.editor) return null;
            var range = self.editor.getSelectionRange();
            if (range.isEmpty()) return null;
            var c = self.editor.renderer.textToScreenCoordinates(
              range.end.row, range.end.column);
            return { top: c.pageY - window.scrollY,
                     left: c.pageX - window.scrollX,
                     right: c.pageX - window.scrollX,
                     height: self.editor.renderer.lineHeight || 16, width: 0 };
          },
        });
      },

      execute: function (code, source) {
        var self = this;
        this.kernelBusy = true;
        this.statusLine = "Running…";
        post(config.urls.execute, { code: code }).then(function (data) {
          self.kernelBusy = false;
          if (!data.ok) {
            transcript.pushError(data.error || "Execution failed.", source);
            self.statusLine = data.error || "Execution failed.";
          } else {
            transcript.pushResult(data, source);
            self.statusLine = data.status === "ok" ? "Done." : data.status;
          }
          self.syncConsole();
        });
      },

      // ---- latex ----
      get busy() { return this.kernelBusy; },

      /* The raw bytes of a file, for the browser to show itself. A version
         parameter beats the cache: a compile overwrites build/main.pdf in
         place, and without it a second open shows the previous run. */
      rawUrl: function (pk) {
        return config.urls.raw.replace(/\/0\/raw\/$/, "/" + pk + "/raw/")
               + "?v=" + Date.now();
      },

      /* The main document's name for the toolbar, read off the tree so it
         stays right after a rename or a change of main document. */
      syncLatexNames: function () {
        var self = this;
        this.mainName = "";
        this.items.forEach(function (i) {
          if (i.t === "file" && i.main) { self.mainName = i.name; self.mainPk = i.id; }
        });
      },

      fileIcon: function (item) {
        if (item.file_type === "python") return "fa-file-code";
        if (item.file_type === "latex") return "fa-file-lines";
        if (item.file_type === "pdf") return "fa-file-pdf";
        if (item.file_type === "image") return "fa-file-image";
        if (item.file_type === "bib") return "fa-book";
        return "fa-file-lines";
      },

      /* A PDF or an image from the tree opens in a new tab, in the browser's
         own viewer. There is no preview pane any more: a compile files its
         output into build/ and the tree is where you find it, like any
         other file in the bucket. */
      openRaw: function (item) {
        if (!item || !item.viewable) return;
        window.open(this.rawUrl(item.id), "_blank", "noopener");
      },

      setMain: function (item) {
        var self = this;
        var url = config.urls.setMain.replace(/\/0\/main\/$/, "/" + item.id + "/main/");
        post(url, {}).then(function (data) {
          if (!data.ok) { self.statusLine = data.error; return; }
          self.items = data.items;
          self.syncLatexNames();
          self.statusLine = data.main + " is now the main document.";
        });
      },

      // ---- settings drawer ----

      /** The .tex files a main document can be chosen from. */
      latexFiles: function () {
        return this.items.filter(function (i) {
          return i.t === "file" && i.file_type === "latex";
        });
      },

      setMainByPk: function (pk) {
        if (!pk) return;
        var item = this.items.filter(function (i) {
          return String(i.id) === String(pk);
        })[0];
        if (item) this.setMain(item);
      },

      /** Whether a control is disabled: readonly rooms, and execution knobs
       *  for someone this platform does not let run code. */
      settingsLocked: function (field) {
        return this.readonly || (field.needsExecute && !config.canExecute);
      },

      openSettings: function () {
        // Start from what is in force, so cancelling by closing the drawer
        // leaves nothing half-edited behind.
        this.settingsDraft = Object.assign({}, config.settings || {});
        this.settingsEnvRows = this.envToRows(this.settingsDraft);
        this.settingsFieldErrors = {};
        this.settingsError = "";
        this.settingsRestartHint = false;
        this.settingsOpen = true;
      },

      closeSettings: function () {
        this.settingsOpen = false;
      },

      /** The env field, as editable rows. Object order is insertion order in
       *  every engine that matters here, so the rows come back stable. */
      envToRows: function (draft) {
        var rows = [];
        var env = (draft && draft.env) || {};
        Object.keys(env).forEach(function (name) {
          rows.push({ name: name, value: String(env[name]) });
        });
        return rows;
      },

      /** Rows back to an object, dropping the blank ones a user left behind. */
      rowsToEnv: function () {
        var env = {};
        this.settingsEnvRows.forEach(function (row) {
          var name = (row.name || "").trim();
          if (name) env[name] = row.value === undefined ? "" : String(row.value);
        });
        return env;
      },

      saveSettings: function () {
        var self = this;
        if (this.settingsSaving) return;
        this.settingsSaving = true;
        this.settingsError = "";
        this.settingsFieldErrors = {};

        // Only what CHANGED. Posting every field would write today's defaults
        // into the row as overrides, so a later change to a host default could
        // never reach this workspace again — and it would fire the restart
        // hint for a save that touched nothing.
        var current = config.settings || {};
        var payload = {};
        this.settingsFields.forEach(function (field) {
          if (self.settingsLocked(field)) return;   // never send what is disabled
          var value = field.kind === "env"
            ? self.rowsToEnv()
            : self.settingsDraft[field.key];
          if (value === undefined) return;
          if (JSON.stringify(value) === JSON.stringify(current[field.key])) return;
          payload[field.key] = value;
        });

        if (!Object.keys(payload).length) {
          this.settingsSaving = false;
          this.statusLine = "Nothing to save.";
          return;
        }

        post(config.urls.settings, { settings: payload }).then(function (data) {
          self.settingsSaving = false;
          if (!data.ok) {
            self.settingsError = data.error || "Could not save the settings.";
            self.settingsFieldErrors = data.fields || {};
            return;
          }
          self.applySettings(data);
          self.settingsRestartHint = !!data.restartRequired;
          self.statusLine = "Settings saved.";
        });
      },

      resetSettings: function () {
        var self = this;
        if (this.settingsSaving) return;
        if (!window.confirm("Drop every setting on this workspace and use the host defaults?")) return;
        this.settingsSaving = true;
        post(config.urls.settings, { reset: true }).then(function (data) {
          self.settingsSaving = false;
          if (!data.ok) { self.settingsError = data.error; return; }
          self.applySettings(data);
          self.settingsFieldErrors = {};
          self.settingsError = "";
          self.settingsRestartHint = false;
          self.statusLine = "Settings reset to the host defaults.";
        });
      },

      /** Adopt what the server says is in force — it re-clamps, so this is
       *  not always what was typed. */
      applySettings: function (data) {
        config.settings = data.settings || {};
        if (data.fields) this.settingsFields = data.fields;
        this.settingsDraft = Object.assign({}, config.settings);
        this.settingsEnvRows = this.envToRows(this.settingsDraft);
      },

      compile: function () {
        var self = this;
        if (this.kernelBusy) return;
        // Save first. Compiling a buffer that differs from the file on disk
        // produces a PDF of code nobody wrote, which is worse than an error.
        var dirty = this.tabs.filter(function (t) { return t.dirty && !t.readonly; });
        var saves = this.readonly ? [] : dirty.map(function (tab) {
          var url = config.urls.save.replace(/\/0\/save\/$/, "/" + tab.pk + "/save/");
          return post(url, { content: tab.content }).then(function () {
            tab.saved = tab.content; tab.dirty = false;
          });
        });

        this.kernelBusy = true;
        this.runState = "queued";
        this.statusLine = "Queueing…";
        Promise.all(saves).then(function () {
          return post(config.urls.compile, {});
        }).then(function (data) {
          if (!data.ok) { self.compileFailed(data.error || "Compile failed."); return; }
          self.runId = data.run ? data.run.id : null;
          self.statusLine = "Compiling " + (data.main || "") + "…";
          if (!self.runId) { self.compileFailed("The compile was not queued."); return; }
          self.pollRun(0);
        });
      },

      /* Ask about the run until it finishes.
       *
       * A fixed interval rather than a backoff: a compile is seconds, not
       * hours, and the useful thing is that the log appears promptly. The limit
       * exists so a worker that dies mid-run leaves a message rather than a
       * page that polls until the tab is closed. */
      pollRun: function (attempt) {
        var self = this;
        if (!this.runId) return;
        if (attempt > POLL_LIMIT) {
          this.compileFailed(
            "The compile is still running after several minutes — check the "
            + "workflow run.");
          return;
        }
        var url = config.urls.run.replace(/\/0\/$/, "/" + this.runId + "/");
        get(url).then(function (data) {
          if (!data.ok || !data.run) {
            self.compileFailed(data.error || "Lost track of the compile.");
            return;
          }
          var run = data.run;
          self.runState = run.state;
          if (!run.finished) {
            self.statusLine = run.state === "running" ? "Compiling…" : "Queued…";
            global.setTimeout(function () { self.pollRun(attempt + 1); }, POLL_MS);
            return;
          }
          self.finishRun(run);
        });
      },

      finishRun: function (run) {
        this.kernelBusy = false;
        this.runId = null;
        if (run.items) this.items = run.items;

        transcript.push({
          label: "pdflatex",
          source: run.main + (run.passes ? " · " + run.passes + " pass(es)" : "")
                  + (run.duration ? " · " + run.duration.toFixed(1) + "s" : ""),
          stdout: run.stdout,
          stderr: run.error,
          status: run.status
        });
        this.syncConsole();

        if (run.state === "success" && run.pdf_pk) {
          // The tree was just re-sent with the run, so the PDF is already
          // in build/ on the left. Say where, rather than showing it here.
          this.statusLine = "Compiled " + run.main + " — the PDF is in build/ in the file tree.";
        } else {
          this.statusLine = run.error || "Compile failed — read the log.";
        }
        this.syncLatexNames();
        this.reloadOpenArtifacts();
      },

      compileFailed: function (message) {
        this.kernelBusy = false;
        this.runId = null;
        this.runState = "failed";
        transcript.pushError(message, "pdflatex");
        this.statusLine = message;
        this.syncConsole();
      },

      /* A `build/` file that is open is now stale — the run just overwrote it.
       * Re-fetching is what makes "compile, then read the log" work without the
       * user closing and reopening the tab. */
      reloadOpenArtifacts: function () {
        var self = this;
        this.tabs.filter(function (t) { return t.readonly; }).forEach(function (tab) {
          var url = config.urls.content.replace(/\/0\/$/, "/" + tab.pk + "/");
          get(url).then(function (data) {
            if (!data.ok) return;
            tab.content = data.content;
            tab.saved = data.content;
            tab.dirty = false;
            tab.truncated = !!data.truncated;
            if (self.activePk === tab.pk && self.editor) {
              tab.loading = true;
              self.editor.setValue(data.content, -1);
              tab.loading = false;
            }
          });
        });
      },

      clearConsole: function () {
        transcript.clear();
        this.syncConsole();
        this.statusLine = "Console cleared — variables kept.";
      },

      /* Publish the transcript to the reactive component. The copy is the
       * whole point: Alpine only re-renders when the array IDENTITY changes,
       * because the transcript mutates a raw array it owns outside the proxy. */
      syncConsole: function () {
        this.entries = transcript.entries.slice();
        this.scrollConsole();
      },

      scrollConsole: function () {
        if (!this.autoScroll) return;
        var self = this;
        this.$nextTick(function () {
          var el = self.$refs.transcript;
          if (el) el.scrollTop = el.scrollHeight;
        });
      }
    };
  };
})(window);

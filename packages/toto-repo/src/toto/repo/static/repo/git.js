/* toto.repo UI — one Alpine component per page (included via
 * repo/_git_ui.html), driving three modals: commit, history (Cytoscape
 * DAG), and the repo panel. Buttons anywhere on the page open it through the global
 * `GitUI` handle with a per-repo context {repoPk, repoName, urls} — so a page
 * may host many repos (vault browser) or one (editors).
 *
 * Conventions match the oya stack: fetch + X-CSRFToken FormData POSTs,
 * Alpine x-show modals, Tailwind theme tokens, poll-until-done for runs.
 */

function gitUI(csrfToken) {
  return {
    csrf: csrfToken,
    ctx: null,          // {repoPk, repoName, urls}
    modal: null,        // 'commit' | 'history' | 'panel'
    busy: false,
    error: "",
    // commit modal
    status: null,
    message: "",
    // panel modal
    branches: null,
    newBranch: "",
    mergeBranch: "",
    conflicts: [],
    conflictDetails: [],   // [{path, ours, theirs, merged, editable}]
    resolutions: {},       // path -> "ours" | "theirs" | "edit"
    editedContent: {},     // path -> textarea text (used when resolution is "edit")
    conflictBranch: "",    // the branch the failed merge was merging
    importSummary: null,
    runState: null,     // {op, status, stdout, stderr}
    // history modal
    commitDetail: null,
    _cy: null,

    init() {
      window.GitUI = this;
      // Editors registered a context before Alpine booted?
      if (window.GitUIPendingCtx) this.ctx = window.GitUIPendingCtx;
    },

    // ---- plumbing --------------------------------------------------------
    async _post(url, data) {
      const fd = new FormData();
      Object.entries(data || {}).forEach(([k, v]) => fd.append(k, v));
      const resp = await fetch(url, {
        method: "POST",
        headers: { "X-CSRFToken": this.csrf },
        credentials: "same-origin",
        body: fd,
      });
      const json = await resp.json().catch(() => ({}));
      if (!resp.ok) throw json;
      return json;
    },
    async _get(url) {
      const resp = await fetch(url, { credentials: "same-origin" });
      const json = await resp.json().catch(() => ({}));
      if (!resp.ok) throw json;
      return json;
    },
    _fail(err) {
      this.error = (err && err.error) || "operation failed";
      if (err && err.conflicts) {
        this.conflicts = err.conflicts;
        this.conflictDetails = err.details || [];
        this.resolutions = {};
        this.editedContent = {};
        (this.conflictDetails).forEach((d) => {
          this.resolutions[d.path] = "ours";
          this.editedContent[d.path] = d.merged || "";
        });
      }
      this.busy = false;
    },
    _reset() {
      this.error = "";
      this.conflicts = [];
      this.importSummary = null;
      this.commitDetail = null;
      this.runState = null;
    },

    close() {
      this.modal = null;
      if (this._cy) { this._cy.destroy(); this._cy = null; }
    },

    // ---- init repo -------------------------------------------------------
    // A repository has exactly one origin and it is a URL, so the init modal
    // asks for exactly that. `pickable` is an OPTIONAL convenience: another app
    // (toto.gitea is the only one today) may publish a picker endpoint at
    // window.REPO_REMOTE_PICKER_URL, and choosing from it just fills the URL
    // field in. Nothing here knows what a Gitea is.
    initName: "",
    initAdvanced: false,
    initBranch: "main",
    initRemoteUrl: "",   // the origin, typed or picked
    panelRemoteUrl: "",  // the repo panel's remote-address editor
    pickable: [],        // [{label, url}] from the picker endpoint, if any
    _initUrl: "",

    async openInit(initUrl, dirName) {
      this._reset();
      this._initUrl = initUrl;
      this.initName = (dirName || "repo").toLowerCase()
        .replace(/[^a-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "") || "repo";
      this.initAdvanced = false;
      this.initBranch = "main";
      this.initRemoteUrl = "";
      this.pickable = [];
      this.modal = "init";
      this.busy = true;
      await this._loadPickable();
      this.busy = false;
    },
    async _loadPickable() {
      // Absent endpoint, disabled provider, network error: all the same
      // outcome — no picker, and the URL field still works. A remote helper
      // that is down must never block making a local repository.
      if (!window.REPO_REMOTE_PICKER_URL) return;
      try {
        const data = await this._get(window.REPO_REMOTE_PICKER_URL);
        this.pickable = data.remotes || [];
      } catch (e) { /* type it in */ }
    },
    async doInit() {
      // Init is a background GitRun (worktree export + initial commit); when a
      // remote URL is given we connect it right after, so the repo is
      // push-ready. Then reload to show it.
      this.busy = true;
      this.error = "";
      try {
        const initBody = {};
        if (this.initBranch.trim() && this.initBranch.trim() !== "main") {
          initBody.default_branch = this.initBranch.trim();
        }
        const res = await this._post(this._initUrl, initBody);
        this.ctx = { repoPk: res.repo_pk, repoName: this.initName, urls: res.urls };
        const run = await this._waitForRun(res.run_id);
        if (run.status !== "success") {
          this.error = run.stderr || "init failed";
          this.busy = false;
          return;
        }
        if (this.initRemoteUrl.trim()) {
          await this._post(res.urls.connect, { url: this.initRemoteUrl.trim() });
        }
        window.location.reload();
      } catch (err) { this._fail(err); }
    },
    async _waitForRun(runId) {
      const url = this.ctx.urls.run_status_base.replace("/0/", `/${runId}/`);
      for (;;) {
        const run = await this._get(url);
        if (run.status === "success" || run.status === "failed") return run;
        await new Promise((r) => setTimeout(r, 1500));
      }
    },

    // ---- commit ----------------------------------------------------------
    async openCommit(ctx) {
      this.ctx = ctx || this.ctx;
      this._reset();
      this.modal = "commit";
      this.busy = true;
      // Editors expose an async pre-save hook (flush the buffer first).
      try { if (window.GitUIBeforeCommit) await window.GitUIBeforeCommit(); } catch (e) {}
      try {
        this.status = await this._get(this.ctx.urls.status);
        this.busy = false;
      } catch (err) { this._fail(err); }
    },
    async doCommit() {
      this.busy = true;
      this.error = "";
      try {
        await this._post(this.ctx.urls.commit, { message: this.message });
        this.message = "";
        this.close();
      } catch (err) { this._fail(err); }
    },

    // ---- repo panel ------------------------------------------------------
    async openPanel(ctx) {
      this.ctx = ctx || this.ctx;
      this._reset();
      this.modal = "panel";
      this.busy = true;
      this.pickable = [];
      try {
        [this.status, this.branches] = await Promise.all([
          this._get(this.ctx.urls.status),
          this._get(this.ctx.urls.branches),
        ]);
        this.panelRemoteUrl = this.status.remote.url || "";
        this.mergeBranch = "";
        this.busy = false;
      } catch (err) { this._fail(err); }
      // After the panel is usable, not before: the picker is a convenience and
      // must not delay showing branches and status.
      await this._loadPickable();
    },
    async refreshPanel() { await this.openPanel(this.ctx); },
    async openAndRun(ctx, op) {
      // Editor toolbar Push/Pull: open the panel (for feedback) and start the run.
      await this.openPanel(ctx);
      if (!this.error) await this.doRun(op);
    },
    async doBranchCreate(checkout) {
      if (!this.newBranch.trim()) return;
      this.busy = true;
      try {
        await this._post(this.ctx.urls.branch_create,
                         { name: this.newBranch.trim(), checkout: checkout ? "1" : "0" });
        this.newBranch = "";
        await this.refreshPanel();
      } catch (err) { this._fail(err); }
    },
    async doBranchDelete(branch) {
      if (!confirm("Delete branch " + branch + "? Unmerged work is refused.")) return;
      this.busy = true;
      try {
        await this._post(this.ctx.urls.branch_delete, { name: branch });
        await this.refreshPanel();
      } catch (err) { this._fail(err); }
    },
    async doCheckout(branch) {
      this.busy = true;
      try {
        const res = await this._post(this.ctx.urls.checkout, { branch });
        this.importSummary = res.import_summary;
        await this.refreshPanel();
        if (res.import_summary) this.importSummary = res.import_summary;
      } catch (err) { this._fail(err); }
    },
    async doMerge() {
      if (!this.mergeBranch) return;
      this.busy = true;
      this.conflicts = [];
      this.conflictDetails = [];
      this.conflictBranch = this.mergeBranch;
      try {
        const res = await this._post(this.ctx.urls.merge, { branch: this.mergeBranch });
        this.importSummary = res.import_summary;
        await this.refreshPanel();
        if (res.import_summary) this.importSummary = res.import_summary;
      } catch (err) { this._fail(err); }
    },
    /* Re-run the failed merge with the user's per-file answers. Stateless on
     * the server: the merge replays and the choices settle it in one request,
     * so nothing ever sits half-merged between two clicks. */
    async doResolveMerge() {
      const resolutions = {};
      for (const d of this.conflictDetails) {
        const pick = this.resolutions[d.path] || "ours";
        resolutions[d.path] = pick === "edit"
          ? { content: this.editedContent[d.path] || "" }
          : pick;
      }
      this.busy = true;
      try {
        const res = await this._post(this.ctx.urls.merge, {
          branch: this.conflictBranch,
          resolutions: JSON.stringify(resolutions),
        });
        this.conflicts = [];
        this.conflictDetails = [];
        this.importSummary = res.import_summary;
        await this.refreshPanel();
        if (res.import_summary) this.importSummary = res.import_summary;
      } catch (err) { this._fail(err); }
    },
    /* The origin URL — replaces whatever remote was set before. */
    async doSetRemoteUrl() {
      const url = this.panelRemoteUrl.trim();
      if (!url) return;
      this.busy = true;
      try {
        await this._post(this.ctx.urls.connect, { url: url });
        this.panelRemoteUrl = "";
        await this.refreshPanel();
      } catch (err) { this._fail(err); }
    },
    async doRun(op) {
      this.busy = true;
      this.error = "";
      this.runState = { op: op, status: "pending", stdout: "", stderr: "" };
      try {
        const res = await this._post(this.ctx.urls[op], {});
        await this._pollRun(res.run_id, op);
      } catch (err) { this._fail(err); this.runState = null; }
    },
    async _pollRun(runId, op) {
      const url = this.ctx.urls.run_status_base.replace("/0/", `/${runId}/`);
      for (;;) {
        let run;
        try { run = await this._get(url); } catch (err) { this._fail(err); return; }
        this.runState = { op: op, ...run };
        if (run.status === "success" || run.status === "failed") break;
        await new Promise((r) => setTimeout(r, 1500));
      }
      this.busy = false;
      if (this.runState.status === "success") {
        this.importSummary = this.runState.import_summary;
        const summary = this.importSummary;
        await this.refreshPanel();
        this.importSummary = summary;
        this.runState.status = "success";
      } else if (this.runState.import_summary && this.runState.import_summary.conflicts) {
        this.conflicts = this.runState.import_summary.conflicts;
      }
    },

    // ---- history (Cytoscape) ---------------------------------------------
    async openHistory(ctx) {
      this.ctx = ctx || this.ctx;
      this._reset();
      this.modal = "history";
      this.busy = true;
      try {
        const data = await this._get(this.ctx.urls.history);
        this.busy = false;
        this.$nextTick(() => this._renderHistory(data));
      } catch (err) { this._fail(err); }
    },
    _laneColors() {
      // Lane palette — readable on both themes.
      return ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#06b6d4",
              "#a855f7", "#84cc16", "#f97316"];
    },
    _renderHistory(data) {
      const container = document.getElementById("repo-graph");
      if (!container || typeof cytoscape === "undefined") return;
      if (this._cy) { this._cy.destroy(); this._cy = null; }
      const tw = (window.tailwind && tailwind.config && tailwind.config.theme.extend.colors) || {};
      const dark = localStorage.getItem("darkMode") === "true";
      const tone = (name, fb) => tw[name + (dark ? "-dark" : "-light")] || fb;
      const palette = this._laneColors();

      const nodes = data.nodes.map((n) => {
        // Branch names ride the label; HEAD gets the arrow and a size bump so
        // "where am I" is answerable without reading anything else.
        const names = n.is_head && data.branches
          ? n.refs.map((r) => (r === data.head ? "HEAD \u2192 " + r : r))
          : n.refs;
        const refs = names.length ? `  [${names.join(", ")}]` : "";
        return {
          data: {
            id: n.id,
            label: `${n.label}  ${n.message.slice(0, 48)}${refs}`,
            color: palette[n.lane % palette.length],
            size: n.is_head ? 18 : n.refs.length ? 13 : 10,
          },
          // history.py's gitk lanes: x = lane, y = -row (newest row on top,
          // y growing up). Cytoscape's y grows down — flip it back to rows.
          position: { x: n.x * 70, y: -n.y * 44 },
        };
      });
      const edges = data.edges.map((e, i) => ({
        data: { id: `e${i}`, source: e.source, target: e.target },
      }));

      this._cy = cytoscape({
        container,
        elements: [...nodes, ...edges],
        style: [
          { selector: "node", style: {
            "background-color": "data(color)",
            "width": "data(size)", "height": "data(size)",
            "label": "data(label)",
            "color": tone("text-main", dark ? "#e5e7eb" : "#1f2937"),
            "font-size": "11px",
            "text-valign": "center", "text-halign": "right",
            "text-margin-x": 8,
          }},
          { selector: "edge", style: {
            "width": 1.5,
            "line-color": dark ? "#4b5563" : "#d1d5db",
            "curve-style": "straight",
            "target-arrow-shape": "none",
          }},
        ],
        layout: { name: "preset" },  // history.py already placed every node
        wheelSensitivity: 0.3,
      });
      this._cy.on("tap", "node", (evt) => this._loadCommit(evt.target.id()));
      this._cy.on("tap", (evt) => {
        if (evt.target === this._cy) this.commitDetail = null;
      });
      // The modal is x-show'n this same tick — the container has no size
      // until it paints, so fit after a beat.
      setTimeout(() => {
        try { this._cy.resize(); this._cy.fit(undefined, 30); } catch (_) {}
      }, 80);
    },
    async _loadCommit(sha) {
      try {
        this.commitDetail = await this._get(
          this.ctx.urls.commit_detail_base + sha + "/");
      } catch (err) { this._fail(err); }
    },
    /* A NEW commit whose tree equals `sha` — history preserved, push intact.
     * The confirm names the sha because there is no second look after it. */
    async doRestore(sha) {
      if (!confirm("Restore the repository to " + sha.slice(0, 7) +
                   "? Later commits stay in history; the files change now.")) return;
      this.busy = true; this.error = "";
      try {
        await this._post(this.ctx.urls.restore_base + sha + "/", {});
        // The page's open buffers are stale the moment the tree changed;
        // reload is the honest refresh, same as init does.
        window.location.reload();
      } catch (err) { this._fail(err); }
      finally { this.busy = false; }
    },
  };
}

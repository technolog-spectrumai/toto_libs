/* gitvault UI — one Alpine component per page (included via
 * gitvault/_git_ui.html), driving three modals: commit, history (Sigma.js DAG),
 * and the repo panel. Buttons anywhere on the page open it through the global
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
    importSummary: null,
    runState: null,     // {op, status, stdout, stderr}
    // history modal
    commitDetail: null,
    _sigma: null,

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
      if (err && err.conflicts) this.conflicts = err.conflicts;
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
      if (this._sigma) { this._sigma.kill(); this._sigma = null; }
    },

    // ---- init repo (vault browser) --------------------------------------
    async initRepo(initUrl) {
      // Init is a background GitRun (worktree export + initial commit can be
      // slow on big directories) — poll it, then reload to show the repo.
      try {
        const res = await this._post(initUrl, {});
        this.ctx = { repoPk: res.repo_pk, repoName: "", urls: res.urls };
        const run = await this._waitForRun(res.run_id);
        if (run.status === "success") { window.location.reload(); return; }
        alert(run.stderr || "init failed");
      } catch (err) { alert((err && err.error) || "init failed"); }
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
      try {
        [this.status, this.branches] = await Promise.all([
          this._get(this.ctx.urls.status),
          this._get(this.ctx.urls.branches),
        ]);
        this.mergeBranch = "";
        this.busy = false;
      } catch (err) { this._fail(err); }
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
      try {
        const res = await this._post(this.ctx.urls.merge, { branch: this.mergeBranch });
        this.importSummary = res.import_summary;
        await this.refreshPanel();
        if (res.import_summary) this.importSummary = res.import_summary;
      } catch (err) { this._fail(err); }
    },
    async doConnect() {
      this.busy = true;
      try {
        await this._post(this.ctx.urls.connect, {});
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

    // ---- history (Sigma.js) ----------------------------------------------
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
      const container = document.getElementById("gitvault-sigma");
      if (!container) return;
      if (this._sigma) { this._sigma.kill(); this._sigma = null; }
      const dark = localStorage.getItem("darkMode") === "true";
      const palette = this._laneColors();
      const graph = new graphology();

      data.nodes.forEach((n) => {
        const refs = n.refs.length ? `  [${n.refs.join(", ")}]` : "";
        graph.addNode(n.id, {
          x: n.x,
          y: n.y,
          size: n.is_head ? 8 : n.refs.length ? 6.5 : 5,
          label: `${n.label}  ${n.message.slice(0, 48)}${refs}`,
          color: palette[n.lane % palette.length],
        });
      });
      data.edges.forEach((e, i) => {
        graph.addEdgeWithKey(`e${i}`, e.source, e.target, {
          size: 1.5,
          color: dark ? "#4b5563" : "#d1d5db",
        });
      });

      this._sigma = new Sigma(graph, container, {
        renderLabels: true,
        labelColor: { color: dark ? "#e5e7eb" : "#1f2937" },
        labelSize: 12,
        labelRenderedSizeThreshold: 0,
        defaultEdgeType: "line",
        minCameraRatio: 0.1,
        maxCameraRatio: 4,
      });
      this._sigma.on("clickNode", ({ node }) => this._loadCommit(node));
    },
    async _loadCommit(sha) {
      try {
        this.commitDetail = await this._get(
          this.ctx.urls.commit_detail_base + sha + "/");
      } catch (err) { this._fail(err); }
    },
  };
}

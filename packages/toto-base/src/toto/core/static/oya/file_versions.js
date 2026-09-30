/* Versions and the editing lock, for every vault editor.
 *
 * Companion to oya/_file_versions.html. Lives in toto-base beside it, and for
 * the same reason: three editors in two packages drive this, and a copy in each
 * would drift — primula's separate versioning is exactly what that looks like
 * after a year.
 *
 * Two jobs:
 *
 *   1. Hold the lock while the editor is open. Beat at the interval the SERVER
 *      names, so the period and the TTL it must stay under live in one place
 *      (toto/vault/locks.py) rather than being a number copied into three files.
 *
 *   2. Cut and restore versions.
 *
 * Both only for somebody who may WRITE the file (2026-09-30). The page says so
 * first (`can_write` on oya/_file_versions.html, true unless it says false) and
 * the server narrows it on every answer: `can_write: false` from the history,
 * or a 403/404 from the lock, turns the panel into a reader's — the history
 * and who is editing, and no claim, heartbeat, beacon or button that the
 * vault's doors would refuse.
 *
 * Releasing on pagehide is best-effort by design: sendBeacon cannot report a
 * failure and nothing here waits for one. Expiry is the real guarantee, so a
 * lost beacon costs a couple of minutes, never correctness.
 */
(function (global) {
  "use strict";

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {}),
    });
  }

  // 403: they may read the file but not change it. 404: not a file they can
  // see (any more). Either way there is no lock to hold and nothing to save.
  function refused(r) { return r.status === 403 || r.status === 404; }

  global.fileVersions = function (filePk, canWrite) {
    var base = "/vault/file/" + filePk;
    return {
      items: [],
      lock: { locked: false, mine: false, holder: "", heartbeat_seconds: 30 },
      label: "",
      error: "",
      busy: false,
      canWrite: canWrite !== false,
      _timer: null,

      // Once read-only, always read-only for this page: the right to write
      // does not come back mid-session, and a reader's page is a reader's.
      _readOnly: function () {
        this.canWrite = false;
        if (this._timer) { clearInterval(this._timer); this._timer = null; }
      },

      // Tell the page who holds the document. The banner that says so sits up
      // by the toolbar, outside this component's scope, because the panel it
      // used to live in is collapsed by default — so the one message a second
      // editor needs was the one nobody saw. A CustomEvent rather than a
      // shared scope: four editors in three packages include this, and none of
      // them can be assumed to have a particular parent component. Instantiating
      // fileVersions twice would claim the lock and beat it twice, which is why
      // the banner listens instead of asking.
      _publish: function () {
        try {
          global.dispatchEvent(new CustomEvent("vault-lock", {
            detail: Object.assign({}, this.lock, { can_write: this.canWrite }),
          }));
        } catch (e) { /* nothing here is worth breaking an editor over */ }
      },

      init: function () {
        var self = this;
        if (!this.canWrite) { this.refresh(); return; }
        this.claim().then(function () { self.refresh(); });

        // Give the lock back the moment the tab goes away. pagehide fires where
        // beforeunload does not — bfcache, mobile task switching — which is the
        // case that would otherwise hold a document for the full TTL.
        global.addEventListener("pagehide", function () {
          if (!self.canWrite) { return; }
          if (global.navigator && navigator.sendBeacon) {
            navigator.sendBeacon(base + "/lock/release/", new Blob([], {
              type: "application/json",
            }));
          }
        });
      },

      claim: function () {
        var self = this;
        return post(base + "/lock/")
          .then(function (r) {
            if (refused(r)) { self._readOnly(); return; }
            return r.json().then(function (d) {
              self.lock = Object.assign(self.lock, d);
              self._publish();
              if (r.status === 423) { self.error = d.error || ""; }
              else { self.error = ""; self.beat(); }
            });
          })
          .catch(function () { /* offline: the editor still works, unlocked */ });
      },

      beat: function () {
        var self = this;
        if (this._timer) { clearInterval(this._timer); }
        var every = (this.lock.heartbeat_seconds || 30) * 1000;
        this._timer = setInterval(function () {
          post(base + "/lock/beat/")
            .then(function (r) {
              if (refused(r)) { self._readOnly(); self._publish(); return null; }
              return r.json();
            })
            .then(function (d) {
              if (!d) { return; }
              self.lock = Object.assign(self.lock, d);
              self._publish();
              // Lost it while asleep. Say so rather than letting them keep
              // typing into a document somebody else now owns.
              if (d.held === false && !d.mine && d.locked) {
                self.error = d.holder + " " + "took over editing this document.";
              }
            })
            .catch(function () { /* a missed beat is survivable; TTL has slack */ });
        }, every);
      },

      refresh: function () {
        var self = this;
        return fetch(base + "/versions/", { credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            self.items = d.versions || [];
            self.lock = Object.assign(self.lock, d);
            if (d.can_write === false) { self._readOnly(); }
            self._publish();
          })
          .catch(function () { /* the panel is not the document; stay quiet */ });
      },

      saveVersion: function () {
        var self = this;
        if (this.busy || !this.canWrite) { return; }
        this.busy = true;
        this.error = "";
        // The document's own autosave owns the bytes; this only names the
        // state it has already written. Let it flush first if it offers to.
        var flushed = (typeof this.beforeSaveVersion === "function")
          ? Promise.resolve(this.beforeSaveVersion()) : Promise.resolve();
        return flushed
          .then(function () { return post(base + "/versions/save/", { label: self.label }); })
          .then(function (r) { return r.json().then(function (d) {
            if (!r.ok) { self.error = d.error || "Could not save a version."; return; }
            self.label = "";
            return self.refresh();
          }); })
          .catch(function () { self.error = "Could not save a version."; })
          .then(function () { self.busy = false; });
      },

      restore: function (item) {
        var self = this;
        if (this.busy || !this.canWrite) { return; }
        var ok = global.confirm(
          "Restore v" + item.number + "? Your current text is kept as a new " +
          "version first, so nothing is lost.");
        if (!ok) { return; }
        this.busy = true;
        this.error = "";
        return post(base + "/versions/" + item.id + "/restore/")
          .then(function (r) { return r.json().then(function (d) {
            if (!r.ok) { self.error = d.error || "Could not restore."; return; }
            // The file on the server changed underneath the open editor, so a
            // reload is the only honest way to show it.
            global.location.reload();
          }); })
          .catch(function () { self.error = "Could not restore."; })
          .then(function () { self.busy = false; });
      },

      when: function (iso) {
        try { return new Date(iso).toLocaleString(); } catch (e) { return iso; }
      },
    };
  };
})(window);

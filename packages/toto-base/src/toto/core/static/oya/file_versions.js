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

  global.fileVersions = function (filePk) {
    var base = "/vault/file/" + filePk;
    return {
      items: [],
      lock: { locked: false, mine: false, holder: "", heartbeat_seconds: 30 },
      label: "",
      error: "",
      busy: false,
      _timer: null,

      init: function () {
        var self = this;
        this.claim().then(function () { self.refresh(); });

        // Give the lock back the moment the tab goes away. pagehide fires where
        // beforeunload does not — bfcache, mobile task switching — which is the
        // case that would otherwise hold a document for the full TTL.
        global.addEventListener("pagehide", function () {
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
          .then(function (r) { return r.json().then(function (d) {
            self.lock = Object.assign(self.lock, d);
            if (r.status === 423) { self.error = d.error || ""; }
            else { self.error = ""; self.beat(); }
          }); })
          .catch(function () { /* offline: the editor still works, unlocked */ });
      },

      beat: function () {
        var self = this;
        if (this._timer) { clearInterval(this._timer); }
        var every = (this.lock.heartbeat_seconds || 30) * 1000;
        this._timer = setInterval(function () {
          post(base + "/lock/beat/")
            .then(function (r) { return r.json(); })
            .then(function (d) {
              self.lock = Object.assign(self.lock, d);
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
          })
          .catch(function () { /* the panel is not the document; stay quiet */ });
      },

      saveVersion: function () {
        var self = this;
        if (this.busy) { return; }
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
        if (this.busy) { return; }
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

/* The upload queue behind the vault list's Upload (2026-10-04).
 *
 * Plain browser upload events, no socket: each file is one XMLHttpRequest to
 * the vault's own upload door (api/files/upload/), which applies the same
 * refusals, quota and charge as before, and `upload.onprogress` says how far
 * it is. Two files travel at a time; the rest wait their turn. Each file can
 * be cancelled, and each is answered on its own: what the door refuses — an
 * Office file, a spent quota, a full bucket, a file too large — is shown on
 * that file's line in the door's own sentence, and the others still land.
 *
 * Nothing the server knows is written into this file: the page hands over
 * the door's address, the CSRF token and its sentences (the list's data-
 * attributes). The page draws what `onChange` hands it; this file touches no
 * element.
 */
(function (root) {
  "use strict";

  var UNITS = ["B", "KB", "MB", "GB"];

  function formatBytes(bytes) {
    var value = Math.max(0, Number(bytes) || 0), unit = 0;
    while (value >= 1024 && unit < UNITS.length - 1) { value /= 1024; unit += 1; }
    return (unit === 0 || value >= 100 ? Math.round(value) : value.toFixed(1)) + " " + UNITS[unit];
  }

  function formatSpeed(bytesPerSecond) {
    return bytesPerSecond > 0 ? formatBytes(bytesPerSecond) + "/s" : "";
  }

  /* What a refused upload says: the door's own sentence when it sent one
   * (JSON `detail` or `error`), else a sentence of the page's for the status. */
  function refusalText(status, body, messages) {
    messages = messages || {};
    var data = null;
    try { data = JSON.parse(body || ""); } catch (error) { data = null; }
    if (data && typeof data === "object") {
      var said = data.detail || data.error;
      if (typeof said === "string" && said) return said;
    }
    if (!status) return messages.network || "";
    if (status === 413) return messages.tooLarge || "";
    return (messages.refused || "") + " (" + status + ")";
  }

  function createUploadQueue(options) {
    var concurrency = options.concurrency || 2;
    var now = options.now || function () { return Date.now(); };
    var messages = options.messages || {};
    var items = [], active = 0, nextId = 1, lastDrawn = 0;

    function snapshot() {
      return items.map(function (item) {
        return {id: item.id, name: item.name, size: item.size, loaded: item.loaded,
                percent: item.percent, speed: item.speed, speedText: formatSpeed(item.speed),
                status: item.status, message: item.message, fileId: item.fileId};
      });
    }

    function changed(force) {
      var at = now();
      if (!force && at - lastDrawn < 100) return;
      lastDrawn = at;
      if (options.onChange) options.onChange(snapshot());
    }

    function waiting(item) { return item.status === "queued" || item.status === "uploading"; }

    function finish(item, status, xhr) {
      if (!waiting(item)) return;
      if (item.status === "uploading") active -= 1;
      item.xhr = null;
      item.speed = 0;
      item.status = status;
      var data = null;
      if (status === "done") {
        item.percent = 100;
        item.loaded = item.size;
        try { data = JSON.parse(xhr.responseText || ""); } catch (error) { data = null; }
        item.fileId = data && data.id ? data.id : null;
      } else if (status === "refused") {
        item.message = refusalText(xhr ? xhr.status : 0, xhr ? xhr.responseText : "", messages);
      } else {
        item.message = messages.cancelled || "";
      }
      pump();
      changed(true);
      if (status === "done" && options.onDone) options.onDone(item.fileId, item.target);
    }

    function progress(item, event) {
      var at = now();
      var total = event.lengthComputable && event.total ? event.total : item.size;
      item.loaded = event.loaded;
      // 100 is the door's answer, not the last byte leaving.
      item.percent = total ? Math.min(99, Math.floor(100 * event.loaded / total)) : 0;
      var elapsed = at - item.lastAt;
      if (elapsed >= 200) {
        var speed = 1000 * (event.loaded - item.lastLoaded) / elapsed;
        item.speed = item.speed ? 0.6 * item.speed + 0.4 * speed : speed;
        item.lastAt = at;
        item.lastLoaded = event.loaded;
      }
      changed(false);
    }

    function start(item) {
      active += 1;
      item.status = "uploading";
      item.lastAt = now();
      item.lastLoaded = 0;
      var xhr = new options.XHR();
      item.xhr = xhr;
      xhr.open("POST", options.url);
      xhr.setRequestHeader("X-CSRFToken", options.csrf());
      xhr.upload.onprogress = function (event) { progress(item, event); };
      xhr.onload = function () { finish(item, xhr.status === 201 ? "done" : "refused", xhr); };
      xhr.onerror = function () { finish(item, "refused", null); };
      xhr.onabort = function () { finish(item, "cancelled", null); };
      var form = new options.FormData();
      form.append("file", item.file);
      form.append("title", item.name);
      form.append("bucket_slug", item.target.bucketSlug);
      form.append("directory_id", item.target.directoryId);
      xhr.send(form);
    }

    function pump() {
      while (active < concurrency) {
        var next = null;
        for (var i = 0; i < items.length; i += 1) {
          if (items[i].status === "queued") { next = items[i]; break; }
        }
        if (!next) return;
        start(next);
      }
    }

    return {
      add: function (file, target) {
        var item = {id: nextId, file: file, name: file.name, size: file.size || 0, target: target,
                    loaded: 0, percent: 0, speed: 0, status: "queued", message: "",
                    fileId: null, xhr: null, lastAt: 0, lastLoaded: 0};
        nextId += 1;
        items.push(item);
        pump();
        changed(true);
        return item.id;
      },
      cancel: function (id) {
        for (var i = 0; i < items.length; i += 1) {
          var item = items[i];
          if (item.id !== id || !waiting(item)) continue;
          if (item.status === "uploading" && item.xhr) item.xhr.abort();
          else finish(item, "cancelled", null);
        }
      },
      clearFinished: function () {
        items = items.filter(waiting);
        changed(true);
      },
      busy: function () { return items.some(waiting); },
      snapshot: snapshot,
    };
  }

  var api = {createUploadQueue: createUploadQueue, refusalText: refusalText,
             formatBytes: formatBytes, formatSpeed: formatSpeed};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.totoUploads = api;
})(typeof window !== "undefined" ? window : globalThis);

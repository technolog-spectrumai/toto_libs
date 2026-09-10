/* The console transcript.
 *
 * Append-only by construction: `push` adds an entry and nothing ever mutates one
 * that is already there. That is the whole requirement — output from an earlier
 * run stays exactly as it was, and the only way to lose it is the Clear button.
 *
 * Kept apart from workspace.js so the transcript can be tested and reasoned about
 * without an editor or a tree anywhere near it.
 */
(function (global) {
  "use strict";

  // A transcript is cheap but not free. It used to hold a few hundred inline
  // matplotlib figures as base64; there is no rich output any more, so the cap
  // now bounds text — a Run that prints in a loop. Oldest entries fall off the
  // top either way.
  var MAX_ENTRIES = 200;

  function Transcript() {
    this.entries = [];
    this._seq = 0;
  }

  Transcript.prototype.push = function (entry) {
    this._seq += 1;
    var row = {
      id: this._seq,
      label: entry.label || ("[" + this._seq + "]"),
      source: entry.source || "",
      stdout: entry.stdout || "",
      stderr: entry.stderr || "",
      // NO `rich` since 2026-09-10. It carried Jupyter `display_data` payloads
      // — image/png, image/svg+xml, text/html — and nothing produces them
      // without a kernel. Dropped from the row rather than defaulted to []:
      // a field that is always empty is one a reader assumes might not be.
      status: entry.status || "ok"
    };
    this.entries.push(row);
    while (this.entries.length > MAX_ENTRIES) this.entries.shift();
    return row;
  };

  /* A server execution result becomes one entry.
   *
   * The label was "In [n]" from `execution_count` — a kernel's own counter,
   * which the response no longer carries because there is no session to count
   * within. `push` falls back to its own sequence number, which is what the
   * label meant to a reader anyway: the nth thing in this transcript. */
  Transcript.prototype.pushResult = function (result, source) {
    return this.push({
      source: summarise(source),
      stdout: result.stdout,
      stderr: result.stderr,
      status: result.status
    });
  };

  /* Something went wrong before the code was ever run. */
  Transcript.prototype.pushError = function (message, source) {
    return this.push({
      label: "!",
      source: summarise(source),
      stderr: message,
      status: "error"
    });
  };

  Transcript.prototype.clear = function () {
    // The screen, and only the screen. It used to be worth saying that this
    // does not touch the kernel — clearing the screen and losing your
    // variables were different actions. Nothing survives a Run now, so there
    // is nothing left for Clear to accidentally destroy.
    this.entries.length = 0;
  };

  /* One readable line describing what was run, for the entry header. */
  function summarise(source) {
    if (!source) return "";
    var text = String(source).trim();
    var firstLine = text.split("\n")[0];
    var extraLines = text.split("\n").length - 1;
    if (firstLine.length > 70) firstLine = firstLine.slice(0, 67) + "…";
    return extraLines > 0 ? firstLine + " … (+" + extraLines + " lines)" : firstLine;
  }

  global.AmbrosiaTranscript = Transcript;
  global.AmbrosiaTranscript.summarise = summarise;
})(window);

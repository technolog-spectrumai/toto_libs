/* The console transcript.
 *
 * Append-only by construction: `push` adds an entry and nothing ever mutates one
 * that is already there. That is the whole requirement — output from an earlier
 * run stays exactly as it was, and the only way to lose it is the Clear button.
 *
 * Kept apart from workspace.js so the transcript can be tested and reasoned about
 * without an editor, a tree or a kernel anywhere near it.
 */
(function (global) {
  "use strict";

  // A transcript is cheap but not free — a few hundred matplotlib figures is
  // real memory. Oldest entries fall off the top.
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
      rich: entry.rich || [],
      status: entry.status || "ok"
    };
    this.entries.push(row);
    while (this.entries.length > MAX_ENTRIES) this.entries.shift();
    return row;
  };

  /* A server execution result becomes one entry. */
  Transcript.prototype.pushResult = function (result, source) {
    return this.push({
      label: result.execution_count ? "In [" + result.execution_count + "]" : "In [ ]",
      source: summarise(source),
      stdout: result.stdout,
      stderr: result.stderr,
      rich: result.rich,
      status: result.status
    });
  };

  /* Something went wrong before the kernel even saw the code. */
  Transcript.prototype.pushError = function (message, source) {
    return this.push({
      label: "!",
      source: summarise(source),
      stderr: message,
      status: "error"
    });
  };

  Transcript.prototype.clear = function () {
    // Deliberately does NOT touch the kernel: clearing the screen and losing
    // your variables are different actions.
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

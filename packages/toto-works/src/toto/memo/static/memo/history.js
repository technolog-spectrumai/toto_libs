/* Undo and redo, as whole-document snapshots.
 *
 * Snapshots rather than command objects, and the reason is contenteditable: it
 * produces mutations this app never observes — a paste, a drag inside a field,
 * an autocorrect on a phone. A command log can only replay what it was told
 * about, so it would drift out of step with the document within a session. A
 * snapshot cannot: it is whatever the document actually is.
 *
 * The cost is copying the deck on every commit. A deck is at most a few hundred
 * KB of JSON and the cap is 50 entries, so this is worth no further thought.
 *
 * Focus travels WITH the snapshot. Undoing a change on slide 7 while looking at
 * slide 2 and being left on slide 2 is disorienting — you cannot see what the
 * undo did, so you press it again.
 */
(function (global) {
  "use strict";

  var LIMIT = 50;
  var COALESCE_MS = 600;

  function clone(value) {
    try { return structuredClone(value); }
    catch (e) { return JSON.parse(JSON.stringify(value)); }
  }

  function History() {
    this.past = [];
    this.future = [];
    this._lastKey = null;
    this._lastAt = 0;
  }

  /* Record the state as it is now.
   *
   * `key` groups edits that should undo together. Typing a sentence into one
   * block commits on every keystroke, and without coalescing that is forty
   * undo steps to get back across one sentence.
   */
  History.prototype.commit = function (state, key, now) {
    now = now || Date.now();
    var coalesce = key && key === this._lastKey && (now - this._lastAt) < COALESCE_MS;
    this._lastKey = key || null;
    this._lastAt = now;

    if (coalesce && this.past.length) {
      this.past[this.past.length - 1] = clone(state);
    } else {
      this.past.push(clone(state));
      while (this.past.length > LIMIT) this.past.shift();
    }
    // Any new edit abandons the redo branch — the standard, expected behaviour.
    this.future.length = 0;
  };

  /* The baseline, recorded once when the editor boots. Without it the first
   * undo has nothing to go back to and the first edit is unrepeatable. */
  History.prototype.seed = function (state) {
    this.past = [clone(state)];
    this.future = [];
    this._lastKey = null;
  };

  History.prototype.canUndo = function () { return this.past.length > 1; };
  History.prototype.canRedo = function () { return this.future.length > 0; };

  History.prototype.undo = function (current) {
    if (!this.canUndo()) return null;
    this.future.push(this.past.pop());
    this._lastKey = null;
    return clone(this.past[this.past.length - 1]);
  };

  History.prototype.redo = function () {
    if (!this.canRedo()) return null;
    var next = this.future.pop();
    this.past.push(next);
    this._lastKey = null;
    return clone(next);
  };

  global.MemoHistory = History;
})(window);

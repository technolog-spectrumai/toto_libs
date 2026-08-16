/* Undo and redo, as whole-document snapshots.
 *
 * Snapshots rather than command objects, and the reason is contenteditable: it
 * produces mutations this app never observes — a paste, a drag inside a field,
 * an autocorrect on a phone. A command log can only replay what it was told
 * about, so it would drift out of step with the document within a session. A
 * snapshot cannot: it is whatever the document actually is.
 *
 * The cost is copying the deck on every commit — and a deck with twenty embedded
 * images is several MB, so "every commit" cannot mean "every keystroke". A
 * commit carrying a coalesce key therefore only SCHEDULES the snapshot, on the
 * same 600 ms window that already groups typing into one undo step. The model
 * itself is updated synchronously by the caller, so nothing is ever at risk;
 * what is deferred is only the copy.
 *
 * The editor moves focus to follow a restored snapshot (see `applySnapshot`):
 * undoing a change on slide 7 while looking at slide 2 and being left on slide 2
 * is disorienting — you cannot see what the undo did, so you press it again.
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
    this._pending = null;
    this._coalesce = false;
    this._timer = null;
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

    // Any new edit abandons the redo branch — the standard, expected behaviour.
    this.future.length = 0;

    if (!key) {
      // A discrete action: add, delete, reorder, layout. Snapshot it now, so
      // undo is exact even if the next thing that happens is a reload.
      this._flush(state);
      return;
    }

    // Typing. Defer the copy — several MB per character is not a cost worth
    // paying to get an undo step you would immediately coalesce away.
    var self = this;
    // The coalesce decision belongs to the FIRST commit of a burst. Taking it
    // from the last one would let a burst that began a new undo step overwrite
    // the previous entry instead of pushing — silently losing a step.
    if (this._pending === null) this._coalesce = coalesce;
    this._pending = state;
    clearTimeout(this._timer);
    this._timer = setTimeout(function () { self.flushPending(); }, COALESCE_MS);
  };

  History.prototype._flush = function (state, coalesce) {
    if (coalesce && this.past.length > 1) {
      this.past[this.past.length - 1] = clone(state);
    } else {
      this.past.push(clone(state));
      while (this.past.length > LIMIT) this.past.shift();
    }
  };

  /* Take any deferred snapshot now. Called on the timer, and by anything that
   * has to see history settled — undo, redo, and saving. */
  History.prototype.flushPending = function () {
    clearTimeout(this._timer);
    if (!this._pending) return;
    this._flush(this._pending, this._coalesce);
    this._pending = null;
  };

  /* The baseline, recorded once when the editor boots. Without it the first
   * undo has nothing to go back to and the first edit is unrepeatable. */
  History.prototype.seed = function (state) {
    clearTimeout(this._timer);
    this.past = [clone(state)];
    this.future = [];
    this._lastKey = null;
    this._pending = null;
  };

  History.prototype.canUndo = function () {
    return this.past.length > 1 || this._pending !== null;
  };
  History.prototype.canRedo = function () { return this.future.length > 0; };

  History.prototype.undo = function (current) {
    this.flushPending();          // the deferred keystroke is a step too
    if (this.past.length <= 1) return null;
    this.future.push(this.past.pop());
    this._lastKey = null;
    return clone(this.past[this.past.length - 1]);
  };

  History.prototype.redo = function () {
    this.flushPending();
    if (!this.canRedo()) return null;
    var next = this.future.pop();
    this.past.push(next);
    this._lastKey = null;
    return clone(next);
  };

  global.MemoHistory = History;
})(window);

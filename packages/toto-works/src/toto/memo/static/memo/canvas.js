/* The 16:9 stage.
 *
 * A slide is laid out at its true 1280x720 size and then scaled to fit the
 * pane, rather than being laid out at whatever size the pane happens to be.
 * That is what makes the editor honest: where a line breaks, whether content
 * overflows, the point at which a heading wraps — all of it is what will happen
 * when presenting, not an approximation that changes with the browser window.
 *
 * The scale goes out as an inline custom property, never as a class. A class
 * name assembled in JS does not exist under the Tailwind JIT build, which
 * generates from what is in the DOM at first paint — so anything computed has
 * to travel as a style or a data- attribute.
 */
(function (global) {
  "use strict";

  var SLIDE_W = 1280;

  function fit(stage) {
    if (!stage) return;
    var width = stage.clientWidth;
    if (!width) return;
    stage.style.setProperty("--memo-scale", String(width / SLIDE_W));
  }

  function observe(stage) {
    if (!stage) return function () {};
    fit(stage);
    if (typeof ResizeObserver === "undefined") {
      var onResize = function () { fit(stage); };
      global.addEventListener("resize", onResize);
      return function () { global.removeEventListener("resize", onResize); };
    }
    var ro = new ResizeObserver(function () { fit(stage); });
    ro.observe(stage);
    return function () { ro.disconnect(); };
  }

  /* ---- auto-fit -----------------------------------------------------------
   *
   * A block marked scale="auto" shrinks until its content fits the flow it is
   * in, and the resolved number is written back into the document. That last
   * part is the whole point: the player and the PDF cannot measure anything, so
   * they can only render what the editor already decided.
   *
   * Measured in slide coordinates, not screen ones. The stage is CSS-scaled, so
   * scrollHeight and clientHeight are both in the untransformed 1280x720 space
   * and the comparison is unaffected by the zoom — which is also why the block
   * scale had to stop sharing a custom-property name with the stage zoom.
   */
  var MIN_SCALE = 0.5;
  var MAX_SCALE = 1.6;
  var STEP = 0.05;

  function overflowAmount(flow) {
    return flow.scrollHeight - flow.clientHeight;
  }

  /* Fit every auto block in `root`. `onResolved(blockId, scale, overflowPx)` is
   * called for each one that changed, so the caller can store it. */
  function autofit(root, onResolved) {
    if (!root) return;
    var flows = root.querySelectorAll(".memo-flow");
    Array.prototype.forEach.call(flows, function (flow) {
      var blocks = Array.prototype.filter.call(
        flow.querySelectorAll("[data-block-id]"),
        function (el) { return el.getAttribute("data-autofit") === "1"; });
      if (!blocks.length) return;

      // Start from full size, then step the whole flow down together. Scaling
      // blocks individually would make one paragraph smaller than its
      // neighbour for no reason a reader could see.
      var scale = MAX_SCALE > 1 ? 1 : MAX_SCALE;
      blocks.forEach(function (el) {
        el.style.setProperty("--memo-block-scale", "1");
      });

      var guard = 0;
      while (overflowAmount(flow) > 1 && scale > MIN_SCALE && guard < 40) {
        scale = Math.max(MIN_SCALE, +(scale - STEP).toFixed(2));
        blocks.forEach(function (el) {
          el.style.setProperty("--memo-block-scale", String(scale));
        });
        guard += 1;
      }

      var spill = Math.max(0, overflowAmount(flow));
      blocks.forEach(function (el) {
        if (spill > 1) {
          // At the floor and still too big. The editor lets it spill rather
          // than clipping, because clipping hides the thing you need to fix.
          el.setAttribute("data-overflow", "overflows by " + Math.round(spill) + "px");
        } else {
          el.removeAttribute("data-overflow");
        }
        if (onResolved) {
          onResolved(el.getAttribute("data-block-id"), scale, spill);
        }
      });
    });
  }

  /* ---- the filmstrip ------------------------------------------------------
   *
   * A thumbnail is a real slide — same markup, same stylesheet — which is what
   * makes it honest, and also what makes it expensive: a forty-slide deck was
   * building forty full slide DOMs and rebuilding them on every render.
   *
   * So only rows near the viewport get their contents. One observer for the
   * whole list, not one per row, and a generous margin so scrolling never shows
   * an empty box. Rows out of view keep their number and title, which is all
   * you navigate by anyway.
   */
  function watchFilmstrip(list, onVisible) {
    if (!list || !onVisible) return function () {};
    if (typeof IntersectionObserver === "undefined") {
      // No observer: render everything, as before. Correct, just not cheap.
      Array.prototype.forEach.call(list.querySelectorAll("[data-drag-id]"),
        function (row) { onVisible(row.getAttribute("data-drag-id"), true); });
      return function () {};
    }

    // Reports into component state rather than setting a DOM attribute: Alpine
    // tracks its own reactive data, and would never re-evaluate an x-if that
    // read an attribute an observer had changed behind its back.
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        onVisible(entry.target.getAttribute("data-drag-id"), entry.isIntersecting);
      });
    }, { root: list, rootMargin: "300px 0px" });

    var observed = [];
    function sync() {
      observed.forEach(function (row) { io.unobserve(row); });
      observed = Array.prototype.slice.call(list.querySelectorAll("[data-drag-id]"));
      observed.forEach(function (row) { io.observe(row); });
    }
    sync();

    // Alpine replaces these rows whenever the deck changes, so re-observe.
    var mo = new MutationObserver(sync);
    mo.observe(list, { childList: true });

    return function () { io.disconnect(); mo.disconnect(); };
  }

  global.MemoCanvas = {
    SLIDE_W: SLIDE_W, SLIDE_H: 720,
    watchFilmstrip: watchFilmstrip,
    MIN_SCALE: MIN_SCALE, MAX_SCALE: MAX_SCALE, STEP: STEP,
    fit: fit, observe: observe, autofit: autofit
  };
})(window);

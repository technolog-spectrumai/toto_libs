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

  global.MemoCanvas = { SLIDE_W: SLIDE_W, SLIDE_H: 720, fit: fit, observe: observe };
})(window);

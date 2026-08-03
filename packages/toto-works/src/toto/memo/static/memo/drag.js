/* Dragging: slides, blocks, and media dropped onto a slide.
 *
 * Pointer events, hand-rolled. No drag-and-drop library is vendored in this
 * repo, and adding one means a new file under core/static/vendor plus an entry
 * in three hosts' download_vendor.py manifests — for behaviour that is about
 * 200 lines. The shape here is lifted from canasta's drag.js, which is the
 * house reference and already solved the parts that are easy to get wrong.
 *
 * Why the details matter, each one a bug that would otherwise be found by a
 * user rather than by us:
 *
 *   * DRAG_PX / DRAG_MS — below these a press is still ambiguous. Without a
 *     threshold, every tap on a touch screen becomes a one-pixel drag and a
 *     block can never simply be selected for editing.
 *   * one pointerId — ignoring the others is what kills the classic two-finger
 *     bug, where a second touch retargets a drag already in flight.
 *   * setPointerCapture — the drag has to keep following the finger after it
 *     leaves the element it started on, which is most of the time.
 *   * a proxy on a top layer — the list does NOT reflow while dragging. Content
 *     shifting under the pointer is disorienting; only a thin line moves.
 *   * delegation from a root, not listeners per element — Alpine replaces these
 *     nodes on every render, so per-element listeners would be attached to
 *     detached DOM within one keystroke.
 *
 * Every drag here has a click or keyboard equivalent that calls the same model
 * function. That is the house rule, and it is also what makes the feature
 * usable without a pointing device.
 */
(function (global) {
  "use strict";

  var DRAG_PX = 8;
  var DRAG_MS = 400;

  var state = null;
  var line = null;

  function dropLine() {
    if (!line) {
      line = document.createElement("div");
      line.className = "memo-drop-line";
    }
    return line;
  }

  function clearLine() {
    if (line && line.parentNode) line.parentNode.removeChild(line);
  }

  /* Strip everything that makes a node PARTICIPATE, so a clone is only a
   * picture of one.
   *
   * Two bugs live here, and both were found by users rather than by us:
   *
   *   * Alpine initialises any node added to the document. A clone of a row
   *     inside an `x-for` is outside that loop's scope, so every binding on it
   *     throws "block is not defined" — a console full of errors on every drag.
   *   * `zoneAt()` scans the whole document for drop zones and the LAST match
   *     wins. A dragged section carries its own zone, and the clone is appended
   *     to <body>, so the ghost would out-rank the real zones and the drop
   *     would land nowhere.
   */
  function neutralise(root) {
    var nodes = [root].concat(Array.prototype.slice.call(root.querySelectorAll("*")));
    nodes.forEach(function (node) {
      var attrs = Array.prototype.slice.call(node.attributes || []);
      attrs.forEach(function (attr) {
        var name = attr.name;
        if (name.charAt(0) === ":" || name.charAt(0) === "@"
            || name.indexOf("x-") === 0
            || name === "id" || name === "contenteditable"
            || name === "data-drop-kind" || name === "data-drag-id"
            || name === "data-drag-handle" || name === "data-drag-kind") {
          node.removeAttribute(name);
        }
      });
    });
    return root;
  }

  function makeProxy(el) {
    var rect = el.getBoundingClientRect();
    var proxy = neutralise(el.cloneNode(true));
    proxy.classList.add("memo-drag-proxy");
    proxy.style.position = "fixed";
    proxy.style.left = "0";
    proxy.style.top = "0";
    proxy.style.width = rect.width + "px";
    proxy.style.height = rect.height + "px";
    proxy.style.pointerEvents = "none";
    proxy.style.zIndex = "9000";
    proxy.style.opacity = "0.85";
    document.body.appendChild(proxy);
    return { el: proxy, dx: rect.left, dy: rect.top };
  }

  function moveProxy(proxy, x, y) {
    proxy.el.style.transform =
      "translate(" + (proxy.dx + x) + "px," + (proxy.dy + y) + "px)";
  }

  /* Which zone is under the pointer, and where in it the thing would land.
   *
   * Rectangles and a loop, deliberately. Hit-testing scaled, transformed,
   * overlapping containers is where drag implementations rot;
   * getBoundingClientRect already reports post-transform coordinates, so the
   * canvas being CSS-scaled needs no correction anywhere.
   */
  function zoneAt(x, y, kind) {
    var zones = document.querySelectorAll("[data-drop-kind]");
    for (var i = zones.length - 1; i >= 0; i--) {   // later registrations win
      var zone = zones[i];
      // A space-separated list: one flow region accepts both a block being
      // reordered and a picture being dragged in from the vault, and they land
      // in the same place by the same insertion rule.
      var accepts = (zone.getAttribute("data-drop-kind") || "").split(/\s+/);
      if (accepts.indexOf(kind) === -1) continue;
      var rect = zone.getBoundingClientRect();
      if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
        return zone;
      }
    }
    return null;
  }

  /* The id of the item to insert BEFORE, or null for the end of the zone. */
  function beforeIn(zone, x, y, draggedId) {
    var items = zone.querySelectorAll("[data-drag-id]");
    var horizontal = zone.getAttribute("data-drop-axis") === "x";
    for (var i = 0; i < items.length; i++) {
      var id = items[i].getAttribute("data-drag-id");
      if (id === draggedId) continue;
      var rect = items[i].getBoundingClientRect();
      var mid = horizontal ? rect.left + rect.width / 2 : rect.top + rect.height / 2;
      if ((horizontal ? x : y) < mid) return id;
    }
    return null;
  }

  function showLine(zone, before) {
    var el = dropLine();
    if (before) {
      var target = zone.querySelector('[data-drag-id="' + before + '"]');
      if (target) { zone.insertBefore(el, target); return; }
    }
    zone.appendChild(el);
  }

  function begin(event, handle) {
    var source = handle.closest("[data-drag-id]");
    if (!source) return;
    state.dragging = true;
    state.source = source;
    state.proxy = makeProxy(source);
    source.style.opacity = "0.35";
    document.body.classList.add("memo-dragging");
    moveProxy(state.proxy, event.clientX - state.startX, event.clientY - state.startY);
  }

  /* Undo everything `begin` did. Safe to call at any time, including twice.
   *
   * The body class is removed FIRST and unconditionally, before the early
   * return. `.memo-dragging` carries `user-select: none`, and in Firefox a
   * contenteditable inside an unselectable subtree cannot be clicked into at
   * all — so a drag that failed to clean up did not leave a cosmetic cursor
   * behind, it left an editor nobody could type in until the page was
   * reloaded. That is worth being paranoid about.
   */
  function teardown() {
    document.body.classList.remove("memo-dragging");
    clearLine();
    if (!state) return;
    if (state.proxy && state.proxy.el.parentNode) {
      state.proxy.el.parentNode.removeChild(state.proxy.el);
    }
    if (state.source) state.source.style.opacity = "";
    state = null;
  }

  /* Every way a drag can end without the pointerup we are listening for.
   *
   * `setPointerCapture` normally guarantees the release comes back to us — but
   * capture is lost the moment the captured element is removed from the DOM,
   * and Alpine replaces these nodes on every render. Add a window that loses
   * focus, a tab that is hidden, a release outside the browser entirely, and
   * "the pointerup always arrives" is simply not true.
   *
   * Note the phases. These are BUBBLE-phase listeners on purpose: a
   * capture-phase pointerup on window would run before `finish` and cancel
   * every drop in the app. By the time these see it, `finish` has already
   * cleared the state and they do nothing.
   */
  function armFailsafes() {
    if (armFailsafes.armed) return;
    armFailsafes.armed = true;

    global.addEventListener("pointercancel", function () { teardown(); });
    global.addEventListener("blur", function () { teardown(); });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) teardown();
    });
    global.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && state) teardown();
    });
    // The net under the net: whatever happened, the next press anywhere clears
    // it. A stuck drag then costs one click rather than a page reload.
    global.addEventListener("pointerdown", function () {
      if (state) teardown();
      else document.body.classList.remove("memo-dragging");
    }, true);
  }

  /* `options.onDrop(kind, id, zoneEl, beforeId)` — the one place a drag turns
   * into a model change. `options.onClick(kind, id)` gets the presses that
   * never became drags, so a tap still selects. */
  function init(root, options) {
    options = options || {};
    armFailsafes();

    root.addEventListener("pointerdown", function (event) {
      if (event.button !== 0 && event.pointerType === "mouse") return;

      // A press that lands on a control is that control's, never a drag.
      //
      // Without this, any button nested inside a drag handle is dead: the
      // handle lookup below finds the ancestor, setPointerCapture retargets the
      // pointerup to it, and the browser never synthesises a `click` on the
      // button. That is exactly what killed Delete, Duplicate and the reorder
      // arrows in the filmstrip, where the whole row is the handle. One guard
      // fixes every such control rather than each one separately.
      if (event.target.closest(
            "button, a, input, select, textarea, label, [contenteditable], [data-no-drag]")) {
        return;
      }

      var handle = event.target.closest("[data-drag-handle]");
      if (!handle || !root.contains(handle)) return;
      var owner = handle.closest("[data-drag-id]");
      if (!owner) return;

      state = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startedAt: Date.now(),
        kind: owner.getAttribute("data-drag-kind"),
        id: owner.getAttribute("data-drag-id"),
        dragging: false,
        source: null,
        proxy: null,
        zone: null,
        before: null
      };
      try { handle.setPointerCapture(event.pointerId); } catch (e) {}
    });

    root.addEventListener("pointermove", function (event) {
      if (!state || event.pointerId !== state.pointerId) return;
      var dx = event.clientX - state.startX;
      var dy = event.clientY - state.startY;

      if (!state.dragging) {
        var far = Math.abs(dx) > DRAG_PX || Math.abs(dy) > DRAG_PX;
        var slow = (Date.now() - state.startedAt) > DRAG_MS;
        if (!far && !slow) return;
        begin(event, event.target.closest("[data-drag-handle]") || event.target);
        if (!state.dragging) return;
      }

      event.preventDefault();
      moveProxy(state.proxy, dx, dy);

      var zone = zoneAt(event.clientX, event.clientY, state.kind);
      state.zone = zone;
      if (!zone) { clearLine(); state.before = null; return; }
      state.before = beforeIn(zone, event.clientX, event.clientY, state.id);
      showLine(zone, state.before);
    });

    function finish(event) {
      if (!state || event.pointerId !== state.pointerId) return;
      var was = state;
      var dragged = state.dragging;
      var zone = state.zone;
      var before = state.before;
      teardown();
      if (!dragged) {
        if (options.onClick) options.onClick(was.kind, was.id);
        return;
      }
      if (zone && options.onDrop) options.onDrop(was.kind, was.id, zone, before);
    }

    root.addEventListener("pointerup", finish);
    root.addEventListener("pointercancel", function (event) {
      if (state && event.pointerId === state.pointerId) teardown();
    });
    // A drag that ends outside the root still has to clean up, or the proxy
    // stays on screen forever.
    global.addEventListener("pointerup", function (event) {
      if (state && event.pointerId === state.pointerId) finish(event);
    });
  }

  global.MemoDrag = { init: init, zoneAt: zoneAt, beforeIn: beforeIn };
})(window);

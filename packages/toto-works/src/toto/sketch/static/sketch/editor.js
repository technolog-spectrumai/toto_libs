/* The sketch editor: engine + canvas + toolbar wiring, one page-singleton.
 *
 * Ported from enigma's useSketch.ts, SketchCanvas.vue, SketchToolbar.vue and
 * SvgEditor.vue, re-expressed for Alpine + plain SVG DOM. The load-bearing
 * design constraint: the DOCUMENT (up to 1000 shapes) and the undo stacks
 * live in a closure and are NEVER Alpine-proxied — Alpine only sees cheap
 * scalar mirrors (tool, dirty, hasSelection…). Pointer events attach
 * natively to the <svg>, and rendering is an explicit render() over a
 * Map(id → node) cache: renderShape() updates one node during a drag,
 * renderAll() rebuilds paint order on commits. A proxied shape array
 * re-rendered per pointermove would be unusable.
 */
(function (global) {
  "use strict";

  var M = global.SketchModel;
  var D = global.SketchSvgDoc;
  var X = global.SketchExport;
  var SVG_NS = "http://www.w3.org/2000/svg";

  var UNDO_LIMIT = 100;
  var ERASE_STEP = 4;
  var ERASER_RADIUS = 10;
  var SELECT_RADIUS = 8;
  var HANDLE = 14;
  var HANDLE_HIT = HANDLE / 2 + 3;
  var ROTATE_OFFSET = 34;
  var PALETTE_LS_KEY = "sketch.palette";
  var GRID_LS_KEY = "sketch.grid";
  var GRID_STEP = 20;
  var POLY_CLOSE_RADIUS = 12;
  var POLY_MIN_VERTEX_DIST = 1;

  /* Where the page's theme actually lives. oya/base.html keeps it in
     localStorage and Alpine mirrors it; ACE is outside Alpine, so it has to
     read the same place rather than guess. Lifted verbatim from cyprian so the
     two source views cannot disagree about what "dark" means. */
  function isDarkMode() {
    try { return localStorage.getItem("darkMode") === "true"; }
    catch (e) { return false; }
  }

  function aceTheme() {
    return isDarkMode() ? "ace/theme/twilight" : "ace/theme/textmate";
  }

  // ── engine: one editing session over one parsed document ────────────────

  function createSketch(doc) {
    var caps = M.DEFAULT_CAPS;
    var engine = {
      doc: doc,                    // {rootAttrs, prologue, shapes, width, height, background, backgroundImage}
      selectedId: null,
      live: null,                  // in-progress freehand stroke
      boardFull: doc.shapes.length >= caps.maxShapes,
      undoStack: [],
      redoStack: [],
      dragBefore: null,
      eraseSnapshotTaken: false,
      onChange: function () {},
    };

    function currentState() {
      return {
        shapes: engine.doc.shapes.slice(),
        background: engine.doc.background,
        image: engine.doc.backgroundImage,   // reference — strings are immutable
      };
    }

    function snapshot() {
      engine.undoStack.push(currentState());
      if (engine.undoStack.length > UNDO_LIMIT) engine.undoStack.shift();
      engine.redoStack = [];       // a fresh action forks the timeline
    }

    function applyShapes(next) {
      engine.doc.shapes = next;
      if (engine.selectedId && !next.some(function (s) { return s.id === engine.selectedId; })) {
        engine.selectedId = null;
      }
      engine.boardFull = next.length >= caps.maxShapes;
    }

    function applyState(state) {
      engine.doc.background = state.background;
      engine.doc.backgroundImage = state.image;
      applyShapes(state.shapes);
    }

    engine.selected = function () {
      var id = engine.selectedId;
      if (!id) return null;
      for (var i = 0; i < engine.doc.shapes.length; i++) {
        if (engine.doc.shapes[i].id === id) return engine.doc.shapes[i];
      }
      return null;
    };

    engine.canUndo = function () { return engine.undoStack.length > 0; };
    engine.canRedo = function () { return engine.redoStack.length > 0; };

    engine.undo = function () {
      var prev = engine.undoStack.pop();
      if (!prev) return;
      engine.redoStack.push(currentState());
      applyState(prev);
      engine.onChange("all");
    };

    engine.redo = function () {
      var next = engine.redoStack.pop();
      if (!next) return;
      engine.undoStack.push(currentState());
      applyState(next);
      engine.onChange("all");
    };

    function atCapacity() {
      if (engine.doc.shapes.length < caps.maxShapes) return false;
      engine.boardFull = true;
      engine.onChange("meta");
      return true;
    }

    function commit(shape) {
      snapshot();
      applyShapes(engine.doc.shapes.concat([shape]));
      engine.onChange("all");
    }

    // — freehand —
    engine.beginStroke = function (x, y, style) {
      if (atCapacity()) return;
      engine.live = {
        id: crypto.randomUUID(),
        pts: [M.quant(x), M.quant(y)],
        color: style.color, sw: style.sw, opacity: style.opacity,
      };
    };
    engine.extendStroke = function (x, y) {
      var live = engine.live;
      if (!live) return;
      var n = live.pts.length;
      if (n >= 2 && Math.hypot(x - live.pts[n - 2], y - live.pts[n - 1]) < M.MIN_POINT_DIST) return;
      live.pts.push(M.quant(x), M.quant(y));
    };
    engine.commitStroke = function () {
      var live = engine.live;
      engine.live = null;
      if (!live || live.pts.length < 2) return;
      commit({ id: live.id, kind: "path", color: live.color, sw: live.sw,
               opacity: live.opacity, fill: null, rot: 0, pts: live.pts.slice() });
    };
    engine.cancelStroke = function () { engine.live = null; };

    // — dragged-out shapes —
    engine.addRect = function (x, y, w, h, base) {
      if (atCapacity() || Math.abs(w) < 1 || Math.abs(h) < 1) return;
      commit(Object.assign(base, {
        kind: "rect",
        x: M.quant(Math.min(x, x + w)), y: M.quant(Math.min(y, y + h)),
        w: M.quant(Math.abs(w)), h: M.quant(Math.abs(h)) }));
    };
    engine.addTriangle = function (x, y, w, h, base) {
      if (atCapacity() || Math.abs(w) < 1 || Math.abs(h) < 1) return;
      commit(Object.assign(base, {
        kind: "triangle",
        x: M.quant(Math.min(x, x + w)), y: M.quant(Math.min(y, y + h)),
        w: M.quant(Math.abs(w)), h: M.quant(Math.abs(h)) }));
    };
    engine.addEllipse = function (cx, cy, rx, ry, base) {
      if (atCapacity() || rx < 0.5 || ry < 0.5) return;
      commit(Object.assign(base, {
        kind: "ellipse", cx: M.quant(cx), cy: M.quant(cy), rx: M.quant(rx), ry: M.quant(ry) }));
    };
    engine.addArrow = function (x1, y1, x2, y2, base) {
      if (atCapacity() || Math.hypot(x2 - x1, y2 - y1) < 2) return;
      commit(Object.assign(base, {
        kind: "arrow", x1: M.quant(x1), y1: M.quant(y1), x2: M.quant(x2), y2: M.quant(y2) }));
    };
    engine.addLine = function (x1, y1, x2, y2, base) {
      if (atCapacity() || Math.hypot(x2 - x1, y2 - y1) < 2) return;
      commit(Object.assign(base, {
        kind: "line", x1: M.quant(x1), y1: M.quant(y1), x2: M.quant(x2), y2: M.quant(y2) }));
    };
    engine.addArc = function (x1, y1, x2, y2, h, base) {
      if (atCapacity() || Math.hypot(x2 - x1, y2 - y1) < 2 || !isFinite(h)) return;
      commit(Object.assign(base, {
        kind: "arc", x1: M.quant(x1), y1: M.quant(y1),
        x2: M.quant(x2), y2: M.quant(y2), h: M.quant(h) }));
    };
    engine.addPolygon = function (pts, base) {
      if (atCapacity() || !Array.isArray(pts) || pts.length < 6) return;
      commit(Object.assign(base, { kind: "polygon", pts: pts.map(M.quant) }));
    };
    engine.addText = function (x, y, text, size, tw, th, base, style) {
      var clean = text.trim().slice(0, caps.maxTextLen);
      if (!clean || atCapacity()) return;
      var font = style || {};
      commit(Object.assign(base, {
        kind: "text", fill: null, x: M.quant(x), y: M.quant(y),
        text: clean, size: size, tw: M.quant(tw), th: M.quant(th),
        // Normalised here rather than trusted from the caller: `font` reaches
        // the emitter and becomes a font-family in a file this platform
        // renders, so an unknown key falls back to the default.
        font: M.fontKey(font.font),
        bold: !!font.bold, italic: !!font.italic, underline: !!font.underline,
        tracking: Number(font.tracking) || 0 }));
    };

    // — selection + transforms —
    engine.selectAt = function (x, y, radius) {
      engine.selectedId = M.hitTest(engine.doc.shapes, x, y, radius);
      engine.onChange("meta");
      return engine.selectedId;
    };
    engine.clearSelection = function () {
      if (!engine.selectedId) return;
      engine.selectedId = null;
      engine.onChange("meta");
    };

    function replaceSelected(next) {
      for (var i = 0; i < engine.doc.shapes.length; i++) {
        if (engine.doc.shapes[i].id === next.id) {
          engine.doc.shapes[i] = next;
          return;
        }
      }
    }

    engine.beginTransform = function () {
      engine.dragBefore = engine.doc.shapes.slice();
    };
    engine.moveSelected = function (dx, dy) {
      var s = engine.selected();
      if (!s) return;
      replaceSelected(M.translateShape(s, dx, dy));
      engine.onChange("shape");
    };
    /* Scale about (ox,oy) in the shape's UNROTATED frame. Rotation makes this
     * more than a plain scale: scaling moves the pivot, so compensate by
     * translating until the anchor renders where it did — exact, because a
     * translation moves point and pivot together. */
    engine.resizeSelected = function (fx, fy, ox, oy) {
      var s = engine.selected();
      if (!s) return;
      var scaled = M.scaleShape(s, ox, oy, fx, fy);
      if (!s.rot) {
        replaceSelected(scaled);
        engine.onChange("shape");
        return;
      }
      var p0 = M.shapePivot(s);
      var p1 = M.shapePivot(scaled);
      var a0 = M.rotatePoint(ox, oy, p0.cx, p0.cy, s.rot);
      var a1 = M.rotatePoint(ox, oy, p1.cx, p1.cy, s.rot);
      replaceSelected(M.translateShape(scaled, a0.x - a1.x, a0.y - a1.y));
      engine.onChange("shape");
    };
    engine.rotateSelected = function (deg) {
      var s = engine.selected();
      if (!s) return;
      replaceSelected(Object.assign({}, s, { rot: M.normDeg(deg) }));
      engine.onChange("shape");
    };
    /* Commit a drag: ONE undo entry for the whole gesture. */
    engine.endTransform = function () {
      var before = engine.dragBefore;
      engine.dragBefore = null;
      if (!before) return;
      if (JSON.stringify(before) === JSON.stringify(engine.doc.shapes)) return;
      var after = engine.doc.shapes.slice();
      engine.doc.shapes = before;
      snapshot();
      applyShapes(after);
      engine.onChange("all");
    };

    function commitSelectedChange(mutate) {
      var s = engine.selected();
      if (!s) return;
      var next = mutate(s);
      if (JSON.stringify(next) === JSON.stringify(s)) return;
      snapshot();
      applyShapes(engine.doc.shapes.map(function (x) { return x.id === next.id ? next : x; }));
      engine.onChange("all");
    }
    engine.restyleSelected = function (style) {
      commitSelectedChange(function (s) {
        // An opaque import has no restylable stroke; text takes no fill.
        if (s.kind === "opaque") return s;
        var next = Object.assign({}, s, style);
        if (!M.canFill(s.kind)) next.fill = s.kind === "text" ? null : next.fill;
        return next;
      });
    };
    engine.setSelectedRotation = function (deg) {
      if (!isFinite(deg)) return;
      commitSelectedChange(function (s) { return Object.assign({}, s, { rot: M.normDeg(deg) }); });
    };
    engine.deleteSelected = function () {
      var s = engine.selected();
      if (!s) return;
      snapshot();
      applyShapes(engine.doc.shapes.filter(function (x) { return x.id !== s.id; }));
      engine.onChange("all");
    };

    // — erase / clear / background —
    engine.beginErase = function () { engine.eraseSnapshotTaken = false; };
    /* Sampled along the segment so a fast flick can't skip shapes; the whole
     * press-drag-release is one undo step, taken lazily at the first hit. */
    engine.eraseAlong = function (fromX, fromY, toX, toY, radius) {
      var dist = Math.hypot(toX - fromX, toY - fromY);
      var steps = Math.max(1, Math.ceil(dist / ERASE_STEP));
      var hits = {};
      var any = false;
      for (var i = 0; i <= steps; i++) {
        var t = i / steps;
        var id = M.hitTest(engine.doc.shapes,
          fromX + (toX - fromX) * t, fromY + (toY - fromY) * t, radius);
        if (id) { hits[id] = true; any = true; }
      }
      if (!any) return;
      if (!engine.eraseSnapshotTaken) {
        snapshot();
        engine.eraseSnapshotTaken = true;
      }
      applyShapes(engine.doc.shapes.filter(function (s) { return !hits[s.id]; }));
      engine.onChange("all");
    };
    engine.setBackground = function (color) {
      if (engine.doc.background === color) return;
      snapshot();
      engine.doc.background = color;
      engine.onChange("all");
    };
    engine.setBackgroundImage = function (dataUrl) {
      var next = dataUrl === null ? null : M.validateBackgroundImage(dataUrl);
      if (engine.doc.backgroundImage === next) return;
      snapshot();
      engine.doc.backgroundImage = next;
      engine.onChange("all");
    };
    engine.clearBoard = function () {
      if (!engine.doc.shapes.length) return;
      snapshot();
      applyShapes([]);
      engine.boardFull = false;
      engine.onChange("all");
    };

    /* Replace the whole document — the source view's Apply, and nothing else.
       The undo stack is CLEARED rather than pushed to: a snapshot holds shapes,
       background and image, not page size, rootAttrs or prologue, so an "undo"
       across a document swap would restore half of it and quietly keep the
       other half. Applying is therefore a new starting point, exactly as
       cyprian re-seeds its history. Nothing has been written to disk at this
       point, so leaving without saving is still the way back. */
    engine.loadDoc = function (next) {
      engine.doc = next;
      engine.selectedId = null;
      engine.live = null;
      engine.undoStack = [];
      engine.redoStack = [];
      engine.boardFull = next.shapes.length >= caps.maxShapes;
      engine.onChange("all");
    };

    engine.toText = function () { return D.serialize(engine.doc); };
    engine.opaqueCount = function () {
      return engine.doc.shapes.filter(function (s) { return s.kind === "opaque"; }).length;
    };

    return engine;
  }

  // ── palette (device preference, localStorage) ───────────────────────────

  function loadPalette() {
    try {
      return M.normalizeSlots(JSON.parse(global.localStorage.getItem(PALETTE_LS_KEY)));
    } catch (e) {
      return M.DEFAULT_SLOT_COLORS.slice();
    }
  }

  function persistPalette(slots) {
    try {
      global.localStorage.setItem(PALETTE_LS_KEY, JSON.stringify(slots));
    } catch (e) { /* private mode: the palette is a nicety */ }
  }

  // ── canvas renderer: imperative SVG DOM over a node cache ───────────────

  function createRenderer(svg, engine) {
    var layers = {
      bg: document.createElementNS(SVG_NS, "rect"),
      bgImage: document.createElementNS(SVG_NS, "image"),
      grid: document.createElementNS(SVG_NS, "path"),
      shapes: document.createElementNS(SVG_NS, "g"),
      liveStroke: document.createElementNS(SVG_NS, "path"),
      preview: document.createElementNS(SVG_NS, "g"),
      selection: document.createElementNS(SVG_NS, "g"),
      eraser: document.createElementNS(SVG_NS, "circle"),
    };
    Object.keys(layers).forEach(function (k) { svg.appendChild(layers[k]); });
    // Editor chrome only — the grid draws under the shapes and is NEVER part
    // of the serialized document.
    layers.grid.setAttribute("stroke", "#3b82f6");
    layers.grid.setAttribute("stroke-width", "0.5");
    layers.grid.setAttribute("opacity", "0.35");
    layers.grid.setAttribute("fill", "none");
    layers.grid.setAttribute("pointer-events", "none");
    layers.grid.style.display = "none";
    layers.liveStroke.setAttribute("fill", "none");
    layers.liveStroke.setAttribute("stroke-linecap", "round");
    layers.liveStroke.setAttribute("stroke-linejoin", "round");
    layers.selection.setAttribute("pointer-events", "none");
    layers.preview.setAttribute("opacity", "0.8");
    layers.eraser.setAttribute("fill", "none");
    layers.eraser.setAttribute("stroke", "#ef4444");
    layers.eraser.setAttribute("stroke-width", "2");
    layers.eraser.setAttribute("pointer-events", "none");
    layers.eraser.setAttribute("r", String(ERASER_RADIUS));
    layers.eraser.style.display = "none";

    var nodeCache = {};        // shape id → element

    function setAttrs(el, attrs) {
      for (var k in attrs) {
        if (attrs[k] === null || attrs[k] === undefined || attrs[k] === "") el.removeAttribute(k);
        else el.setAttribute(k, String(attrs[k]));
      }
    }

    function rotOf(s) {
      if (!s.rot) return null;
      var p = M.shapePivot(s);
      return "rotate(" + s.rot + " " + p.cx + " " + p.cy + ")";
    }

    function polyStr(pts) {
      return pts.map(function (p) { return p[0] + "," + p[1]; }).join(" ");
    }

    function buildNode(s) {
      var el;
      switch (s.kind) {
        case "path": el = document.createElementNS(SVG_NS, "path"); break;
        case "arc": el = document.createElementNS(SVG_NS, "path"); break;
        case "rect": el = document.createElementNS(SVG_NS, "rect"); break;
        case "ellipse": el = document.createElementNS(SVG_NS, "ellipse"); break;
        case "triangle": el = document.createElementNS(SVG_NS, "polygon"); break;
        case "polygon": el = document.createElementNS(SVG_NS, "polygon"); break;
        case "line": el = document.createElementNS(SVG_NS, "line"); break;
        case "arrow": el = document.createElementNS(SVG_NS, "g"); break;
        case "text": el = document.createElementNS(SVG_NS, "text"); break;
        case "opaque": el = document.createElementNS(SVG_NS, "g"); break;
        default: el = document.createElementNS(SVG_NS, "g");
      }
      if (s.kind === "arrow") {
        el.appendChild(document.createElementNS(SVG_NS, "line"));
        el.appendChild(document.createElementNS(SVG_NS, "polygon"));
      }
      if (s.kind === "opaque") {
        // The interior is the verbatim import; guard rules already ran on it.
        el.innerHTML = s.markup;
      }
      return el;
    }

    function updateNode(el, s) {
      var fill = M.canFill(s.kind) && s.fill ? s.fill : "none";
      switch (s.kind) {
        case "path":
          setAttrs(el, { d: M.pathD(s.pts), fill: "none", stroke: s.color,
            "stroke-width": s.sw, opacity: s.opacity,
            "stroke-linecap": "round", "stroke-linejoin": "round", transform: rotOf(s) });
          break;
        case "rect":
          setAttrs(el, { x: s.x, y: s.y, width: s.w, height: s.h, fill: fill,
            stroke: s.color, "stroke-width": s.sw, opacity: s.opacity, transform: rotOf(s) });
          break;
        case "ellipse":
          setAttrs(el, { cx: s.cx, cy: s.cy, rx: s.rx, ry: s.ry, fill: fill,
            stroke: s.color, "stroke-width": s.sw, opacity: s.opacity, transform: rotOf(s) });
          break;
        case "triangle":
          setAttrs(el, { points: polyStr(M.trianglePoints(s)), fill: fill,
            stroke: s.color, "stroke-width": s.sw, opacity: s.opacity,
            "stroke-linejoin": "round", transform: rotOf(s) });
          break;
        case "polygon":
          setAttrs(el, { points: polyStr(M.polyPairs(s.pts)), fill: fill,
            stroke: s.color, "stroke-width": s.sw, opacity: s.opacity,
            "stroke-linejoin": "round", transform: rotOf(s) });
          break;
        case "line":
          setAttrs(el, { x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2, fill: "none",
            stroke: s.color, "stroke-width": s.sw, opacity: s.opacity,
            "stroke-linecap": "round", transform: rotOf(s) });
          break;
        case "arc":
          setAttrs(el, { d: M.arcPathD(s), fill: "none",
            stroke: s.color, "stroke-width": s.sw, opacity: s.opacity,
            "stroke-linecap": "round", transform: rotOf(s) });
          break;
        case "arrow":
          setAttrs(el, { opacity: s.opacity, transform: rotOf(s) });
          setAttrs(el.firstChild, { x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2,
            stroke: s.color, "stroke-width": s.sw, "stroke-linecap": "round" });
          setAttrs(el.lastChild, { points: polyStr(M.arrowHead(s)), fill: s.color });
          break;
        case "text":
          setAttrs(el, { x: s.x, y: s.y, fill: s.color, opacity: s.opacity,
            "font-size": s.size, "font-family": M.fontStack(s.font),
            "font-weight": s.bold ? "bold" : null,
            "font-style": s.italic ? "italic" : null,
            "text-decoration": s.underline ? "underline" : null,
            "letter-spacing": s.tracking || null,
            transform: rotOf(s) });
          el.textContent = s.text;
          break;
        case "opaque": {
          var t = M.opaqueTransform(s);
          setAttrs(el, { transform: t || null });
          // First render: measure the import's untransformed box so
          // hit-testing and the selection frame know where it is. Detached
          // nodes (renderAll builds into a fragment) DON'T throw in
          // Chromium — getBBox returns a zero box — so only trust a
          // measurement taken while connected; renderAll re-runs us after
          // attach.
          if (!s.measured && el.isConnected) {
            try {
              var box = el.getBBox();
              s.bw = box.width; s.bh = box.height;
              if (s.hintCx !== null && s.hintCy !== null) {
                // A re-opened wrapped import keeps its SAVED pivot, so the
                // stored placement means exactly what it meant.
                s.bx = s.hintCx - box.width / 2;
                s.by = s.hintCy - box.height / 2;
              } else {
                s.bx = box.x; s.by = box.y;
              }
              s.measured = true;
            } catch (e) { /* detached — measured next render */ }
          }
          break;
        }
      }
    }

    return {
      layers: layers,
      renderAll: function () {
        var doc = engine.doc;
        setAttrs(layers.bg, doc.background
          ? { width: doc.width, height: doc.height, fill: doc.background }
          : { width: 0, height: 0 });
        layers.bg.style.display = doc.background ? "" : "none";
        if (doc.backgroundImage) {
          setAttrs(layers.bgImage, { href: doc.backgroundImage, x: 0, y: 0,
            width: doc.width, height: doc.height, preserveAspectRatio: "xMidYMid slice" });
          layers.bgImage.style.display = "";
        } else {
          layers.bgImage.style.display = "none";
        }

        var seen = {};
        var frag = document.createDocumentFragment();
        doc.shapes.forEach(function (s) {
          var el = nodeCache[s.id];
          if (!el) {
            el = buildNode(s);
            nodeCache[s.id] = el;
          }
          updateNode(el, s);
          frag.appendChild(el);      // appending re-orders to paint order
          seen[s.id] = true;
        });
        Object.keys(nodeCache).forEach(function (id) {
          if (!seen[id]) delete nodeCache[id];   // dropped with the fragment swap
        });
        layers.shapes.textContent = "";
        layers.shapes.appendChild(frag);
        // Imports measure via getBBox(), which THROWS inside the detached
        // fragment above — re-run their update now that the nodes are live,
        // or hit-testing never learns where they are.
        doc.shapes.forEach(function (s) {
          if (s.kind === "opaque" && !s.measured) updateNode(nodeCache[s.id], s);
        });
      },
      renderShape: function (s) {
        var el = nodeCache[s.id];
        if (el) updateNode(el, s);
      },
      renderLiveStroke: function () {
        var live = engine.live;
        if (!live) {
          layers.liveStroke.style.display = "none";
          return;
        }
        layers.liveStroke.style.display = "";
        setAttrs(layers.liveStroke, { d: M.pathD(live.pts), stroke: live.color,
          "stroke-width": live.sw, opacity: live.opacity });
      },
      renderPreview: function (tool, box, style) {
        var g = layers.preview;
        g.textContent = "";
        if (!box) return;
        var el;
        if (tool === "rect") {
          el = document.createElementNS(SVG_NS, "rect");
          setAttrs(el, { x: box.x, y: box.y, width: box.w, height: box.h });
        } else if (tool === "ellipse") {
          el = document.createElementNS(SVG_NS, "ellipse");
          setAttrs(el, { cx: box.x + box.w / 2, cy: box.y + box.h / 2,
            rx: box.w / 2, ry: box.h / 2 });
        } else if (tool === "triangle") {
          el = document.createElementNS(SVG_NS, "polygon");
          setAttrs(el, { points: polyStr([
            [box.x + box.w / 2, box.y],
            [box.x + box.w, box.y + box.h],
            [box.x, box.y + box.h]]) });
        } else if (tool === "line" || tool === "arc") {
          // The arc's drag phase lays down its chord; the bow comes after.
          el = document.createElementNS(SVG_NS, "line");
          setAttrs(el, { x1: box.x1, y1: box.y1, x2: box.x2, y2: box.y2,
            stroke: style.color, "stroke-width": style.sw, "stroke-linecap": "round" });
          g.appendChild(el);
          return;
        } else if (tool === "arrow") {
          el = document.createElementNS(SVG_NS, "g");
          var line = document.createElementNS(SVG_NS, "line");
          setAttrs(line, { x1: box.x1, y1: box.y1, x2: box.x2, y2: box.y2,
            stroke: style.color, "stroke-width": style.sw, "stroke-linecap": "round" });
          var head = document.createElementNS(SVG_NS, "polygon");
          setAttrs(head, { points: polyStr(M.arrowHead({
            x1: box.x1, y1: box.y1, x2: box.x2, y2: box.y2, sw: style.sw })),
            fill: style.color });
          el.appendChild(line);
          el.appendChild(head);
          g.appendChild(el);
          return;
        } else {
          return;
        }
        setAttrs(el, { fill: style.fill || "none", stroke: style.color,
          "stroke-width": style.sw });
        g.appendChild(el);
      },
      renderSelection: function (tool) {
        var g = layers.selection;
        g.textContent = "";
        var s = engine.selected();
        if (tool !== "select" || !s) return;
        var b = M.shapeBounds(s);
        var rot = rotOf(s);
        if (rot) g.setAttribute("transform", rot);
        else g.removeAttribute("transform");
        var box = document.createElementNS(SVG_NS, "rect");
        setAttrs(box, { x: b.x - 4, y: b.y - 4, width: b.w + 8, height: b.h + 8,
          fill: "none", stroke: "#3b82f6", "stroke-width": 2, "stroke-dasharray": "8 6" });
        var stalk = document.createElementNS(SVG_NS, "line");
        setAttrs(stalk, { x1: b.x + b.w / 2, y1: b.y - 4, x2: b.x + b.w / 2,
          y2: b.y - ROTATE_OFFSET, stroke: "#3b82f6", "stroke-width": 2 });
        var knob = document.createElementNS(SVG_NS, "circle");
        setAttrs(knob, { cx: b.x + b.w / 2, cy: b.y - ROTATE_OFFSET, r: HANDLE / 2,
          fill: "#3b82f6", stroke: "#ffffff", "stroke-width": 2 });
        var handle = document.createElementNS(SVG_NS, "rect");
        setAttrs(handle, { x: b.x + b.w - HANDLE / 2, y: b.y + b.h - HANDLE / 2,
          width: HANDLE, height: HANDLE, fill: "#3b82f6",
          stroke: "#ffffff", "stroke-width": 2 });
        g.appendChild(box);
        g.appendChild(stalk);
        g.appendChild(knob);
        g.appendChild(handle);
      },
      renderEraser: function (tool, cursor) {
        if (tool !== "eraser" || !cursor) {
          layers.eraser.style.display = "none";
          return;
        }
        layers.eraser.style.display = "";
        setAttrs(layers.eraser, { cx: cursor.x, cy: cursor.y });
      },
      renderGrid: function (on, step) {
        if (!on) {
          layers.grid.style.display = "none";
          return;
        }
        var w = engine.doc.width;
        var h = engine.doc.height;
        var d = [];
        for (var x = step; x < w; x += step) d.push("M" + x + " 0V" + h);
        for (var y = step; y < h; y += step) d.push("M0 " + y + "H" + w);
        layers.grid.setAttribute("d", d.join(""));
        layers.grid.style.display = "";
      },
      /* In-progress polygon: placed edges, rubber band to the cursor, and a
       * ring on the first vertex once it is closeable. */
      renderPolyDraft: function (pts, cursor, style, closeable) {
        var g = layers.preview;
        g.textContent = "";
        if (!pts || !pts.length) return;
        var line = document.createElementNS(SVG_NS, "polyline");
        var coords = [];
        for (var i = 0; i + 1 < pts.length; i += 2) coords.push(pts[i] + "," + pts[i + 1]);
        if (cursor) coords.push(cursor.x + "," + cursor.y);
        setAttrs(line, { points: coords.join(" "), fill: style.fill || "none",
          stroke: style.color, "stroke-width": style.sw, "stroke-linejoin": "round" });
        g.appendChild(line);
        for (var v = 0; v + 1 < pts.length; v += 2) {
          var dot = document.createElementNS(SVG_NS, "circle");
          setAttrs(dot, { cx: pts[v], cy: pts[v + 1], r: 3, fill: "#3b82f6" });
          g.appendChild(dot);
        }
        if (closeable) {
          var ring = document.createElementNS(SVG_NS, "circle");
          setAttrs(ring, { cx: pts[0], cy: pts[1], r: 9, fill: "none",
            stroke: "#3b82f6", "stroke-width": 2 });
          g.appendChild(ring);
        }
      },
      /* In-progress arc: the chord dashed, the arc itself live. */
      renderArcDraft: function (draft, style) {
        var g = layers.preview;
        g.textContent = "";
        if (!draft) return;
        var chord = document.createElementNS(SVG_NS, "line");
        setAttrs(chord, { x1: draft.x1, y1: draft.y1, x2: draft.x2, y2: draft.y2,
          stroke: "#3b82f6", "stroke-width": 1, "stroke-dasharray": "6 5" });
        g.appendChild(chord);
        var arc = document.createElementNS(SVG_NS, "path");
        setAttrs(arc, { d: M.arcPathD(draft), fill: "none", stroke: style.color,
          "stroke-width": style.sw, "stroke-linecap": "round" });
        g.appendChild(arc);
      },
    };
  }

  // ── the Alpine component ────────────────────────────────────────────────

  global.sketchEditor = function () {
    // Closure-held, never proxied.
    var engine = null;
    var renderer = null;
    var svg = null;
    var config = { saveUrl: "", canEdit: false, title: "drawing.svg", strings: {} };
    var baselineText = "";
    var baseHash = "";               // what the server last agreed the file was
    var rectCache = null;
    var drag = { mode: "none" };
    var polyDraft = null;            // { pts: [x0,y0,…] } while placing vertices
    var arcDraft = null;             // { x1,y1,x2,y2,h } while pulling the bow
    var snapGrid = false;

    function refreshRect() {
      rectCache = svg ? svg.getBoundingClientRect() : null;
    }

    function snapV(v) { return Math.round(v / GRID_STEP) * GRID_STEP; }
    function snapPt(p) { return snapGrid ? { x: snapV(p.x), y: snapV(p.y) } : p; }

    /* Signed sagitta of the arc whose apex tracks the cursor: distance of
     * the cursor from the chord along its left-hand normal. */
    function arcHFor(draft, p) {
      var dx = draft.x2 - draft.x1;
      var dy = draft.y2 - draft.y1;
      var c = Math.hypot(dx, dy);
      if (c < 1e-6) return 0;
      var mx = (draft.x1 + draft.x2) / 2;
      var my = (draft.y1 + draft.y2) / 2;
      return M.quant(((p.x - mx) * -dy + (p.y - my) * dx) / c);
    }

    function toBoard(e) {
      if (!rectCache) refreshRect();
      var r = rectCache;
      if (!r || r.width === 0 || r.height === 0) return { x: 0, y: 0 };
      // General `meet` maths retained even though the aspect usually matches —
      // the two can disagree for a frame after a resize.
      var scale = Math.min(r.width / engine.doc.width, r.height / engine.doc.height);
      var offX = (r.width - engine.doc.width * scale) / 2;
      var offY = (r.height - engine.doc.height * scale) / 2;
      return {
        x: M.quant((e.clientX - r.left - offX) / scale),
        y: M.quant((e.clientY - r.top - offY) / scale),
      };
    }

    function angleTo(pivot, p) {
      return (Math.atan2(p.y - pivot.cy, p.x - pivot.cx) * 180) / Math.PI;
    }

    /* Polygon and arc are absent from this palette ON PURPOSE, and their model,
       rendering and import code below is INTENTIONALLY kept.

       Dropping the tools makes the editor simpler to learn; dropping the kinds
       would make it destructive. `svgdoc.js` decomposes an imported drawing into
       these kinds — an Inkscape file full of <polygon> elements is read as
       editable polygons, and a file whose shapes the model cannot name is
       carried opaquely instead. Delete the kinds and those files stop
       round-tripping. So: no way to draw a new one, every way to keep an old
       one. Re-adding a tool is two lines here.
    */
    var component = {
      // — reactive UI mirrors only —
      tool: "draw",
      tools: [
        { id: "select", icon: "fa-arrow-pointer", label: "Select" },
        { id: "draw", icon: "fa-pencil", label: "Draw" },
        { id: "rect", icon: "fa-square", label: "Rectangle" },
        { id: "ellipse", icon: "fa-circle", label: "Ellipse" },
        { id: "triangle", icon: "fa-caret-up", label: "Triangle" },
        { id: "line", icon: "fa-slash", label: "Line" },
        { id: "arrow", icon: "fa-arrow-right", label: "Arrow" },
        { id: "text", icon: "fa-font", label: "Text" },
        { id: "eraser", icon: "fa-eraser", label: "Eraser" },
      ],
      strokeSizes: M.STROKE_SIZES,
      opacitySteps: M.OPACITY_STEPS,
      textSizes: M.TEXT_SIZES,
      pickerColors: M.PICKER_COLORS,
      slots: M.DEFAULT_SLOT_COLORS.slice(),
      currentColor: "#111827",
      currentWidth: 4,
      currentOpacity: 1,
      currentFill: null,
      paletteEditing: false,
      openSlot: null,
      textSize: 24,
      // Text styling, remembered between labels: somebody titling a diagram
      // wants the next heading to match the last one, and re-picking a font
      // for every word is the kind of thing that makes a tool feel hostile.
      textFont: M.DEFAULT_FONT,
      textBold: false,
      textItalic: false,
      textUnderline: false,
      textTracking: 0,
      fontChoices: M.FONT_CHOICES,
      trackingSteps: M.TRACKING_STEPS,
      hasSelection: false,
      selRotation: 0,
      canUndo: false,
      canRedo: false,
      dirty: false,
      /* Draw or source: two views of ONE file, never two documents. */
      view: "draw",
      sourceBusy: false,
      sourceError: "",
      busy: false,
      boardFull: false,
      opaqueCount: 0,
      refusal: null,
      textModal: false,
      textInput: "",
      textAt: null,
      hasBgImage: false,
      notice: "",
      snapOn: false,

      get fillApplies() {
        if (this.tool === "rect" || this.tool === "ellipse" ||
            this.tool === "triangle" || this.tool === "polygon") return true;
        var s = engine && engine.selected();
        return !!(s && M.canFill(s.kind));
      },

      boot: function () {
        var self = this;
        var contentEl = document.getElementById("sketch-content");
        var configEl = document.getElementById("sketch-config");
        if (!contentEl || !configEl || !global.SketchModel) return;
        config = JSON.parse(configEl.textContent);
        baseHash = config.baseHash || "";
        var text = JSON.parse(contentEl.textContent);

        var measureCtx = document.createElement("canvas").getContext("2d");
        function measure(t, size, style) {
          /* `style` is a text shape (or anything carrying font/bold/italic/
             tracking). Optional so every existing caller still works, and
             honoured when given: bold, a condensed family and letter spacing
             all change advance width, and a box measured without them leaves
             the selection outline and the hit test wrong for styled text. */
          var shape = style || {};
          measureCtx.font = M.fontShorthand({
            size: size, font: shape.font, bold: shape.bold, italic: shape.italic });
          var w = measureCtx.measureText(t).width;
          // Canvas cannot measure letter-spacing, so it is added: SVG puts the
          // gap after every glyph including the last.
          if (shape.tracking) w += shape.tracking * t.length;
          return { w: Math.max(w, 1), h: size * 1.2 };
        }
        this._measure = measure;

        var parsed = D.parse(text, measure);
        if (!parsed.ok) {
          // The server refuses first; this twin catches parser differentials.
          this.refusal = parsed.reason + " (" + parsed.detail + ")";
          return;
        }
        engine = createSketch(parsed.doc);
        this.slots = loadPalette();

        svg = document.getElementById("sketch-canvas");
        svg.setAttribute("viewBox", "0 0 " + parsed.doc.width + " " + parsed.doc.height);
        renderer = createRenderer(svg, engine);
        // Console/debugging handle — engine state lives in this closure, not
        // on the Alpine component (proxying it is the perf trap), so this is
        // the only window into it.
        global.sketchDebug = { engine: engine, renderer: renderer };

        engine.onChange = function (granularity) {
          if (granularity === "all") renderer.renderAll();
          else if (granularity === "shape") {
            var s = engine.selected();
            if (s) renderer.renderShape(s);
          }
          self.syncMirrors();
          renderer.renderSelection(self.tool);
        };

        // Native listeners: coalesced events and per-move cost stay out of
        // Alpine entirely.
        svg.addEventListener("pointerdown", this.onDown.bind(this));
        svg.addEventListener("pointermove", this.onMove.bind(this));
        svg.addEventListener("pointerup", this.onUp.bind(this));
        svg.addEventListener("pointercancel", this.onUp.bind(this));
        svg.addEventListener("pointerleave", function () {
          renderer.renderEraser(self.tool, null);
        });
        svg.addEventListener("dblclick", function (ev) {
          if (self.tool === "polygon" && polyDraft) {
            ev.preventDefault();
            self.commitPoly();
          }
        });

        try {
          snapGrid = global.localStorage.getItem(GRID_LS_KEY) === "1";
        } catch (e) { snapGrid = false; }
        this.snapOn = snapGrid;
        renderer.renderGrid(snapGrid, GRID_STEP);
        if (typeof ResizeObserver !== "undefined") {
          new ResizeObserver(refreshRect).observe(svg);
        }
        global.addEventListener("resize", refreshRect);
        global.addEventListener("beforeunload", function (event) {
          if (self.dirty && config.canEdit) {
            event.preventDefault();
            event.returnValue = "";
          }
        });

        renderer.renderAll();
        // Opaque imports measure their bbox on first render — repaint once so
        // hit-testing and the notice see the measured values.
        if (engine.opaqueCount() > 0) renderer.renderAll();
        baselineText = engine.toText();
        this.syncMirrors();
      },

      syncMirrors: function () {
        if (!engine) return;
        var s = engine.selected();
        this.hasSelection = !!s;
        this.selRotation = s ? s.rot : 0;
        this.canUndo = engine.canUndo();
        this.canRedo = engine.canRedo();
        this.boardFull = engine.boardFull;
        this.opaqueCount = engine.opaqueCount();
        this.hasBgImage = !!engine.doc.backgroundImage;
        this.dirty = config.canEdit && engine.toText() !== baselineText;
        this.notice = this.boardFull ? (config.strings.boardFull || "") : "";
      },

      style: function () {
        return { color: this.currentColor, sw: this.currentWidth,
                 opacity: this.currentOpacity, fill: this.currentFill };
      },

      newBase: function () {
        return Object.assign({ id: crypto.randomUUID(), rot: 0 }, this.style());
      },

      // — pointer gestures (SketchCanvas.vue's state machine) —
      onDown: function (e) {
        if (!engine || !config.canEdit || e.button !== 0) return;
        refreshRect();
        var p = toBoard(e);
        svg.setPointerCapture(e.pointerId);
        switch (this.tool) {
          case "draw":
            engine.beginStroke(p.x, p.y, this.style());
            drag = { mode: "draw" };
            renderer.renderLiveStroke();
            return;
          case "rect": case "ellipse": case "triangle": case "arrow": case "line":
            drag = { mode: "shape", start: snapPt(p), cur: snapPt(p) };
            return;
          case "arc":
            if (arcDraft) {
              // The bend click: the bow follows the cursor until this.
              this.commitArc();
              return;
            }
            drag = { mode: "shape", start: snapPt(p), cur: snapPt(p) };
            return;
          case "polygon": {
            var sp = snapPt(p);
            if (!polyDraft) {
              polyDraft = { pts: [sp.x, sp.y] };
            } else {
              var pn = polyDraft.pts.length;
              if (pn >= 6 &&
                  Math.hypot(sp.x - polyDraft.pts[0], sp.y - polyDraft.pts[1]) <= POLY_CLOSE_RADIUS) {
                this.commitPoly();
                return;
              }
              // A double-click's second press lands on the last vertex —
              // don't duplicate it.
              if (Math.hypot(sp.x - polyDraft.pts[pn - 2], sp.y - polyDraft.pts[pn - 1]) >=
                  POLY_MIN_VERTEX_DIST) {
                polyDraft.pts.push(sp.x, sp.y);
              }
            }
            renderer.renderPolyDraft(polyDraft.pts, sp, this.style(),
              polyDraft.pts.length >= 6);
            return;
          }
          case "eraser":
            engine.beginErase();
            engine.eraseAlong(p.x, p.y, p.x, p.y, ERASER_RADIUS);
            drag = { mode: "erase", last: p };
            return;
          case "text":
            this.textAt = snapPt(p);
            this.textInput = "";
            this.textModal = true;
            var self = this;
            setTimeout(function () {
              var field = self.$refs && self.$refs.textfield;
              if (field) field.focus();
            }, 50);
            return;
          case "select": {
            var sel = engine.selected();
            if (sel) {
              var b = M.shapeBounds(sel);
              var pivot = M.shapePivot(sel);
              // Handles live in the shape's rotated frame — map the pointer
              // into it, or they only work at rot === 0.
              var local = sel.rot ? M.rotatePoint(p.x, p.y, pivot.cx, pivot.cy, -sel.rot) : p;
              if (Math.abs(local.x - (b.x + b.w / 2)) <= HANDLE_HIT &&
                  Math.abs(local.y - (b.y - ROTATE_OFFSET)) <= HANDLE_HIT) {
                engine.beginTransform();
                drag = { mode: "rotate", pivot: pivot,
                         grabOffset: angleTo(pivot, p) - sel.rot };
                return;
              }
              if (Math.abs(local.x - (b.x + b.w)) <= HANDLE_HIT &&
                  Math.abs(local.y - (b.y + b.h)) <= HANDLE_HIT) {
                engine.beginTransform();
                drag = { mode: "resize", origin: { x: b.x, y: b.y },
                         base: { w: Math.max(b.w, 1), h: Math.max(b.h, 1) },
                         pivot: pivot, rot: sel.rot };
                return;
              }
            }
            var hit = engine.selectAt(p.x, p.y, SELECT_RADIUS);
            renderer.renderSelection(this.tool);
            if (hit) {
              engine.beginTransform();
              // Absolute anchors: the move target is computed from the
              // GRAB-time bounds, so grid snapping (and float dust) can't
              // accumulate across increments.
              drag = { mode: "move", p0: p, b0: M.shapeBounds(engine.selected()) };
            } else {
              drag = { mode: "none" };
            }
            return;
          }
        }
      },

      onMove: function (e) {
        if (!engine) return;
        var cursor = toBoard(e);
        renderer.renderEraser(this.tool, cursor);
        if (drag.mode === "none") {
          // Click-built drafts rubber-band between presses.
          if (this.tool === "polygon" && polyDraft) {
            renderer.renderPolyDraft(polyDraft.pts, snapPt(cursor), this.style(),
              polyDraft.pts.length >= 6);
          } else if (this.tool === "arc" && arcDraft) {
            arcDraft.h = arcHFor(arcDraft, snapPt(cursor));
            renderer.renderArcDraft(arcDraft, this.style());
          }
          return;
        }

        // Recover the samples the browser batched — smoother strokes at no
        // cost, since the model thins them anyway.
        var events = typeof e.getCoalescedEvents === "function" && e.getCoalescedEvents().length
          ? e.getCoalescedEvents() : [e];
        for (var i = 0; i < events.length; i++) {
          var p = toBoard(events[i]);
          switch (drag.mode) {
            case "draw":
              engine.extendStroke(p.x, p.y);
              break;
            case "erase":
              engine.eraseAlong(drag.last.x, drag.last.y, p.x, p.y, ERASER_RADIUS);
              drag.last = p;
              break;
            case "shape":
              drag.cur = snapPt(p);
              break;
            case "move": {
              var rdx = p.x - drag.p0.x;
              var rdy = p.y - drag.p0.y;
              if (snapGrid) {
                // Snap the shape's top-left target, not the cursor — the
                // grab offset must not leak into the landing position.
                rdx = snapV(drag.b0.x + rdx) - drag.b0.x;
                rdy = snapV(drag.b0.y + rdy) - drag.b0.y;
              }
              var bNow = M.shapeBounds(engine.selected());
              engine.moveSelected(drag.b0.x + rdx - bNow.x, drag.b0.y + rdy - bNow.y);
              break;
            }
            case "resize": {
              // origin lives in the UNROTATED frame; map the pointer there or
              // a rotated shape scales along the wrong axes.
              var q = drag.rot
                ? M.rotatePoint(p.x, p.y, drag.pivot.cx, drag.pivot.cy, -drag.rot) : p;
              q = snapPt(q);
              var w = Math.max(1, q.x - drag.origin.x);
              var h = Math.max(1, q.y - drag.origin.y);
              engine.resizeSelected(w / drag.base.w, h / drag.base.h,
                                    drag.origin.x, drag.origin.y);
              drag.base = { w: w, h: h };
              break;
            }
            case "rotate": {
              var deg = angleTo(drag.pivot, p) - drag.grabOffset;
              if (events[i].shiftKey) {
                deg = Math.round(deg / M.ROTATE_SNAP_DEG) * M.ROTATE_SNAP_DEG;
              }
              engine.rotateSelected(deg);
              break;
            }
          }
        }
        if (drag.mode === "draw") renderer.renderLiveStroke();
        if (drag.mode === "shape") {
          renderer.renderPreview(this.tool, Object.assign({
            x: Math.min(drag.start.x, drag.cur.x),
            y: Math.min(drag.start.y, drag.cur.y),
            w: Math.abs(drag.cur.x - drag.start.x),
            h: Math.abs(drag.cur.y - drag.start.y),
            x1: drag.start.x, y1: drag.start.y, x2: drag.cur.x, y2: drag.cur.y,
          }), this.style());
        }
        if (drag.mode === "move" || drag.mode === "resize" || drag.mode === "rotate") {
          renderer.renderSelection(this.tool);
          var s = engine.selected();
          this.selRotation = s ? s.rot : 0;
        }
      },

      onUp: function (e) {
        if (!engine || drag.mode === "none") return;
        if (svg.releasePointerCapture) {
          try { svg.releasePointerCapture(e.pointerId); } catch (err) { /* gone */ }
        }
        var d = drag;
        drag = { mode: "none" };
        if (d.mode === "draw") {
          engine.commitStroke();
          renderer.renderLiveStroke();
        } else if (d.mode === "shape") {
          var w = d.cur.x - d.start.x;
          var h = d.cur.y - d.start.y;
          if (this.tool === "rect") engine.addRect(d.start.x, d.start.y, w, h, this.newBase());
          else if (this.tool === "triangle") engine.addTriangle(d.start.x, d.start.y, w, h, this.newBase());
          else if (this.tool === "arrow") engine.addArrow(d.start.x, d.start.y, d.cur.x, d.cur.y, this.newBase());
          else if (this.tool === "line") engine.addLine(d.start.x, d.start.y, d.cur.x, d.cur.y, this.newBase());
          else if (this.tool === "arc") {
            // Chord laid down — enter the bend phase; the bow tracks the
            // cursor until a click (commitArc) or Escape.
            if (Math.hypot(w, h) >= 2) {
              arcDraft = { x1: d.start.x, y1: d.start.y, x2: d.cur.x, y2: d.cur.y, h: 0 };
              renderer.renderArcDraft(arcDraft, this.style());
              return;
            }
          }
          else if (this.tool === "ellipse") {
            engine.addEllipse((d.start.x + d.cur.x) / 2, (d.start.y + d.cur.y) / 2,
                              Math.abs(w) / 2, Math.abs(h) / 2, this.newBase());
          }
          renderer.renderPreview(this.tool, null, null);
        } else if (d.mode === "move" || d.mode === "resize" || d.mode === "rotate") {
          engine.endTransform();
        }
      },

      // — click-built drafts (polygon vertices, arc bow) —
      commitPoly: function () {
        if (!engine || !polyDraft) return;
        var pts = polyDraft.pts;
        polyDraft = null;
        renderer.renderPolyDraft(null);
        if (pts.length >= 6) engine.addPolygon(pts, this.newBase());
      },
      commitArc: function () {
        if (!engine || !arcDraft) return;
        var d = arcDraft;
        arcDraft = null;
        renderer.renderArcDraft(null);
        engine.addArc(d.x1, d.y1, d.x2, d.y2, d.h, this.newBase());
      },
      cancelDrafts: function () {
        var had = polyDraft || arcDraft;
        polyDraft = null;
        arcDraft = null;
        if (had && renderer) renderer.renderPolyDraft(null);
        return !!had;
      },

      // — toolbar —
      setTool: function (t) {
        // Switching mid-gesture would commit a shape of the wrong kind.
        if (drag.mode === "draw") {
          engine.cancelStroke();
          renderer.renderLiveStroke();
        }
        drag = { mode: "none" };
        this.cancelDrafts();
        this.tool = t;
        if (t !== "select" && engine) engine.clearSelection();
        if (engine) {
          this.syncMirrors();
          renderer.renderSelection(t);
          renderer.renderEraser(t, null);
        }
      },
      toggleSnap: function () {
        snapGrid = !snapGrid;
        this.snapOn = snapGrid;
        try {
          global.localStorage.setItem(GRID_LS_KEY, snapGrid ? "1" : "0");
        } catch (e) { /* private mode: a nicety, like the palette */ }
        if (renderer) renderer.renderGrid(snapGrid, GRID_STEP);
      },
      pickColor: function (c) {
        this.currentColor = c;
        if (engine && this.hasSelection) engine.restyleSelected(this.style());
      },
      pickWidth: function (w) {
        this.currentWidth = w;
        if (engine && this.hasSelection) engine.restyleSelected(this.style());
      },
      pickOpacity: function (o) {
        this.currentOpacity = o;
        if (engine && this.hasSelection) engine.restyleSelected(this.style());
      },
      pickFill: function (c) {
        this.currentFill = c;
        if (engine && this.hasSelection) engine.restyleSelected(this.style());
      },
      /* Assign a colour to the open slot. NOT undoable, never restyles: the
       * palette is a device preference. The active stroke/fill follows the
       * slot it was pinned to only when the old colour left the row entirely
       * (duplicates are legal). */
      setSlotColor: function (c) {
        var i = this.openSlot;
        this.openSlot = null;
        if (!Number.isInteger(i) || i < 0 || i >= M.PALETTE_SLOTS) return;
        if (M.PICKER_COLORS.indexOf(c) === -1) return;
        var prev = this.slots[i];
        if (prev === c) return;
        var next = this.slots.slice();
        next[i] = c;
        this.slots = next;
        if (this.currentColor === prev && next.indexOf(prev) === -1) this.currentColor = c;
        if (this.currentFill === prev && next.indexOf(prev) === -1) this.currentFill = c;
        persistPalette(next);
      },
      setBackground: function (c) {
        if (engine) engine.setBackground(c);
      },
      chooseImage: function (files) {
        var self = this;
        var file = files && files[0];
        if (!file || !engine) return;
        X.backgroundImageFromFile(file).then(function (dataUrl) {
          engine.setBackgroundImage(dataUrl);
        }).catch(function () {
          self.notice = config.strings.badImage || "";
        });
      },
      removeImage: function () {
        if (engine) engine.setBackgroundImage(null);
      },
      rotateTo: function (deg) {
        if (engine) engine.setSelectedRotation(Number(deg));
      },
      undo: function () { if (engine) engine.undo(); },
      redo: function () { if (engine) engine.redo(); },
      deleteSelected: function () { if (engine) engine.deleteSelected(); },
      clearBoard: function () { if (engine) engine.clearBoard(); },

      // — text modal —
      confirmText: function () {
        var text = this.textInput.trim();
        this.textModal = false;
        if (!text || !this.textAt || !engine) return;
        var style = {
          font: this.textFont, bold: this.textBold, italic: this.textItalic,
          underline: this.textUnderline, tracking: this.textTracking };
        var box = this._measure(text, this.textSize, style);
        engine.addText(this.textAt.x, this.textAt.y, text, this.textSize,
                       box.w, box.h, this.newBase(), style);
        this.textAt = null;
      },
      nudgeTracking: function (direction) {
        var steps = M.TRACKING_STEPS;
        var at = steps.indexOf(this.textTracking);
        // An unrecognised value (an older file, a hand-edited one) snaps to
        // the nearest step rather than refusing to move.
        if (at === -1) {
          at = 0;
          for (var i = 1; i < steps.length; i++) {
            if (Math.abs(steps[i] - this.textTracking) <
                Math.abs(steps[at] - this.textTracking)) at = i;
          }
        }
        this.textTracking = steps[Math.min(steps.length - 1, Math.max(0, at + direction))];
      },
      get textPreviewStyle() {
        return "font-family:" + M.fontStack(this.textFont) +
               ";font-weight:" + (this.textBold ? "700" : "400") +
               ";font-style:" + (this.textItalic ? "italic" : "normal") +
               ";text-decoration:" + (this.textUnderline ? "underline" : "none") +
               ";letter-spacing:" + this.textTracking + "px";
      },
      cancelText: function () {
        this.textModal = false;
        this.textAt = null;
      },

      // — save / export —
      save: function () {
        var self = this;
        if (!engine || !config.canEdit || this.busy) return;
        var text = engine.toText();
        this.busy = true;
        fetch(config.saveUrl, {
          method: "POST",
          headers: {
            "Content-Type": "image/svg+xml",
            /* What this drawing looked like when we last agreed with the
               server. The server refuses the write if it has moved on. */
            "X-Base-Hash": baseHash || "",
          },
          body: text,
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; }); })
          .then(function (res) {
            self.busy = false;
            if (res.ok) {
              baselineText = text;
              baseHash = (res.data && res.data.content_hash) || baseHash;
              self.dirty = false;
              self.notice = config.strings.saved || "";
              setTimeout(function () { self.notice = ""; }, 2000);
              return;
            }
            /* 423: somebody holds the lock. 409: the file moved under us and
               the server kept our work as a version. Neither is "save failed"
               — telling a user their drawing was lost when it was not is the
               worst of the three messages. */
            if (res.status === 423) {
              self.notice = config.strings.locked || "";
            } else if (res.status === 409) {
              self.notice = config.strings.conflict || "";
            } else {
              self.notice = (config.strings.saveFailed || "") +
                (res.data && res.data.reason ? " (" + res.data.reason + ")" : "");
            }
          })
          .catch(function () {
            self.busy = false;
            self.notice = config.strings.saveFailed || "";
          });
      },
      // — source view (cyprian's, adapted) —

      setView: function (view) {
        this.view = view;
        if (view === "source") this.loadSource();
      },

      /* ACE is mounted lazily. The source view is a second surface and
         building an editor for it at boot costs every user who never opens
         it — the same reasoning, and the same code shape, as cyprian. */
      loadSource: function (force) {
        var self = this;
        if (typeof ace === "undefined") {
          this.sourceError = config.strings.sourceFailed || "";
          return;
        }
        if (!this._ace) {
          this._ace = ace.edit("sketch-source");
          this._ace.session.setMode("ace/mode/xml");
          /* No syntax worker: ACE would fetch worker-xml.js from a base path
             this page never configures. The server decides whether the source
             is acceptable, and says so on Apply. */
          this._ace.session.setUseWorker(false);
          this._ace.setOptions({ fontSize: "13px", showPrintMargin: false,
                                 useSoftTabs: true, tabSize: 2, wrap: true });
        }
        this.syncSourceTheme();
        if (this._sourceLoaded && !force) return;

        /* Show the drawing as it WOULD be stored, not as it was two strokes
           ago: serialize what is on the board rather than re-reading the file
           the user has since drawn on. */
        var live = engine ? engine.toText() : "";
        if (this.dirty && live) {
          this._ace.setValue(live, -1);
          this._sourceLoaded = true;
          this.sourceError = "";
          return;
        }
        fetch(config.sourceUrl, { headers: { "Accept": "text/plain" } })
          .then(function (r) { return r.text(); })
          .then(function (text) {
            self._ace.setValue(text, -1);
            self._sourceLoaded = true;
            self.sourceError = "";
          })
          .catch(function () {
            self.sourceError = config.strings.sourceFailed || "";
          });
      },

      syncSourceTheme: function () {
        if (this._ace) this._ace.setTheme(aceTheme());
      },

      /* Apply screens on the server first, then parses in the browser — the
         only SVG parser this app has. A drawing the parser cannot model is not
         an error: svgdoc carries what it does not recognise as opaque shapes,
         which is what lets an Inkscape file survive a round trip. */
      applySource: function () {
        var self = this;
        if (!this._ace || !config.canEdit) return;
        this.sourceBusy = true;
        this.sourceError = "";
        var text = this._ace.getValue();
        fetch(config.sourceUrl, {
          method: "POST",
          headers: { "Content-Type": "image/svg+xml" },
          body: text,
        })
          .then(function (r) {
            return r.json().then(function (d) { return { ok: r.ok, data: d }; });
          })
          .then(function (res) {
            self.sourceBusy = false;
            if (!res.ok) {
              self.sourceError = (res.data && (res.data.detail || res.data.error)) ||
                config.strings.applyFailed || "";
              return;
            }
            var parsed = D.parse(res.data.svg, self._measure);
            if (!parsed.ok) {
              /* The server said yes and the browser said no: a parser
                 differential, and the browser is the one that has to render
                 it, so the browser wins. */
              self.sourceError = (config.strings.applyFailed || "") +
                " (" + parsed.reason + ")";
              return;
            }
            /* Seeded into history, not written to disk: applying is an edit
               like any other, and it must be undoable. */
            engine.loadDoc(parsed.doc);
            if (svg) {
              svg.setAttribute("viewBox",
                "0 0 " + parsed.doc.width + " " + parsed.doc.height);
            }
            rectCache = null;
            self.view = "draw";
            self.notice = config.strings.applied || "";
            self.syncMirrors();
            self.dirty = true;
          })
          .catch(function () {
            self.sourceBusy = false;
            self.sourceError = config.strings.applyFailed || "";
          });
      },

      exportPng: function () {
        if (!engine) return;
        var name = (config.title || "drawing").replace(/\.svg$/i, "") + ".png";
        X.exportPng(engine.toText(), engine.doc.width, engine.doc.height, name)
          .catch(function () { /* rasterize failed — leave the board as is */ });
      },

      // — keyboard (SvgEditor.vue's onKey) —
      onKey: function (e) {
        if (!engine) return;
        var target = e.target;
        var typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA");
        if (this.textModal || typing) {
          if (e.key === "Escape" && this.textModal) this.cancelText();
          return;
        }
        var mod = e.ctrlKey || e.metaKey;
        if (mod && (e.key === "s" || e.key === "S")) {
          e.preventDefault();
          this.save();
        } else if (mod && !e.shiftKey && (e.key === "z" || e.key === "Z")) {
          e.preventDefault();
          this.undo();
        } else if ((mod && e.shiftKey && (e.key === "z" || e.key === "Z")) ||
                   (mod && (e.key === "y" || e.key === "Y"))) {
          e.preventDefault();
          this.redo();
        } else if (e.key === "Delete" || e.key === "Backspace") {
          if (this.hasSelection) {
            e.preventDefault();
            this.deleteSelected();
          }
        } else if (e.key === "Enter") {
          // Close the polygon / set the arc where it is.
          if (polyDraft) {
            e.preventDefault();
            this.commitPoly();
          } else if (arcDraft) {
            e.preventDefault();
            this.commitArc();
          }
        } else if (e.key === "Escape") {
          if (this.cancelDrafts()) return;
          engine.clearSelection();
          this.syncMirrors();
          renderer.renderSelection(this.tool);
        }
      },
    };

    return component;
  };
})(window);

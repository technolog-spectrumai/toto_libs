/* Sketch board — pure data model + geometry. No DOM writes, no fetch.
 *
 * A faithful JavaScript port of the enigma app's sketchModel.ts (types
 * dropped, one kind ADDED): the document is a plain ordered list of shapes,
 * array order IS paint order. Shapes store raw absolute coordinates per kind
 * EXCEPT rotation, which stays an angle applied at render time — a rotated
 * rect cannot be expressed in axis-aligned x/y/w/h.
 *
 * The added kind is "opaque": an imported SVG element the editor cannot
 * decompose (a whole Inkscape group, a gradient-filled path). It is carried
 * verbatim — `markup` is the exact original serialization — but it is a
 * first-class OBJECT: selectable, movable, scalable, rotatable, deletable.
 * Its placement lives OUTSIDE the markup (dx/dy translate, sx/sy scale about
 * the measured bbox centre, rot), so an untouched opaque node re-emits
 * byte-identically.
 *
 * The round-trip hint attributes keep enigma's `data-en-*` names on purpose:
 * a drawing made there opens fully editable here and vice versa.
 */
(function (global) {
  "use strict";

  // ── board space ─────────────────────────────────────────────────────────
  // Height is FIXED (it anchors the unit scale: stroke widths, font sizes and
  // every saved coordinate keep meaning what they meant); width is elastic —
  // a wider window adds room on the right, never rescales what was drawn.
  var BOARD_H = 1080;
  var BOARD_W = 1920;
  var MIN_BOARD_W = 540;
  var MAX_BOARD_W = 6 * BOARD_H;

  function boardWidthFor(elW, elH) {
    if (!isFinite(elW) || !isFinite(elH) || elW <= 0 || elH <= 0) return BOARD_W;
    var raw = Math.round((BOARD_H * elW) / elH);
    return Math.min(Math.max(raw, MIN_BOARD_W), MAX_BOARD_W);
  }

  function boardWidth(w) {
    if (typeof w !== "number" || !isFinite(w)) return BOARD_W;
    return Math.min(Math.max(Math.round(w), MIN_BOARD_W), MAX_BOARD_W);
  }

  var MIN_POINT_DIST = 1.5;

  // ── palette ─────────────────────────────────────────────────────────────
  // Seven user-assignable SLOTS (a per-device preference, never part of the
  // document) + the extras. The two lists must stay disjoint: PICKER_COLORS
  // is their concatenation and a duplicate would render twice.
  var DEFAULT_SLOT_COLORS = [
    "#ef4444", "#10b981", "#3b82f6", "#f59e0b", "#6b7280", "#111827", "#ffffff",
  ];
  var PALETTE_SLOTS = DEFAULT_SLOT_COLORS.length;
  var EXTRA_COLORS = [
    "#f97316", "#8b5cf6", "#ec4899", "#06b6d4", "#14b8a6",
    "#d946ef", "#84cc16", "#6366f1", "#92400e", "#1e40af",
  ];
  var PICKER_COLORS = DEFAULT_SLOT_COLORS.concat(EXTRA_COLORS);

  /* Coerce a stored palette to exactly PALETTE_SLOTS pickable colours —
   * per-slot fallback, so one bad entry doesn't reset the other six.
   * Duplicates are legal. */
  function normalizeSlots(x) {
    var arr = Array.isArray(x) ? x : [];
    return DEFAULT_SLOT_COLORS.map(function (fallback, i) {
      var v = arr[i];
      return typeof v === "string" && PICKER_COLORS.indexOf(v) !== -1 ? v : fallback;
    });
  }

  var STROKE_SIZES = [2, 4, 8, 16];
  var TEXT_SIZES = [16, 24, 36, 56];
  var OPACITY_STEPS = [0.25, 0.5, 0.75, 1];
  var ROTATE_SNAP_DEG = 15;
  var SVG_FONT_STACK = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";

  /* The fonts a drawing may use, and there is no way to add one.
   *
   * A drawing is an .svg FILE that this platform renders, and the antivirus
   * screens it on the way in and out — so a font is not a free-text field. A
   * typed family name would be markup somebody else's browser resolves, and
   * the obvious next request after that is @font-face, which is a URL, which
   * is a network fetch out of a document we promise is inert.
   *
   * Every stack below resolves to fonts already on the machine. Five, because
   * the point is a legible choice between kinds of letterform — not a font
   * menu — and because each one has to look like itself on Linux, macOS and
   * Windows without shipping anything.
   */
  var FONT_CHOICES = [
    { key: "sans",   label: "Sans",       stack: SVG_FONT_STACK },
    { key: "serif",  label: "Serif",      stack: "Georgia, 'Times New Roman', serif" },
    { key: "mono",   label: "Monospace",  stack: "ui-monospace, 'Cascadia Mono', Menlo, Consolas, monospace" },
    { key: "round",  label: "Rounded",    stack: "'Trebuchet MS', 'Segoe UI', Verdana, sans-serif" },
    { key: "narrow", label: "Condensed",  stack: "'Arial Narrow', 'Liberation Sans Narrow', Impact, sans-serif" },
  ];
  var DEFAULT_FONT = "sans";

  /* Letter spacing, in SVG user units, as steps rather than a free number: the
   * useful range is small and the interesting values are near zero, so a
   * slider spends most of its travel on settings nobody wants. Negative is
   * genuinely useful for a headline, so it goes both ways. */
  var TRACKING_STEPS = [-2, -1, -0.5, 0, 0.5, 1, 2, 4];

  function fontStack(key) {
    for (var i = 0; i < FONT_CHOICES.length; i++) {
      if (FONT_CHOICES[i].key === key) return FONT_CHOICES[i].stack;
    }
    return SVG_FONT_STACK;
  }

  function fontKey(value) {
    for (var i = 0; i < FONT_CHOICES.length; i++) {
      if (FONT_CHOICES[i].key === value) return value;
    }
    return DEFAULT_FONT;
  }

  /* The CSS `font` shorthand for a text shape, used by canvas measurement so
   * the box a shape reports matches what the browser will actually draw.
   * Weight and style change advance width; underline does not. */
  function fontShorthand(s) {
    return (s.italic ? "italic " : "") + (s.bold ? "700 " : "") +
           s.size + "px " + fontStack(s.font);
  }

  function canFill(kind) {
    return kind === "rect" || kind === "ellipse" || kind === "triangle" ||
           kind === "polygon";
  }

  function emptyDoc() {
    return { v: 1, shapes: [], background: null };
  }

  var BACKGROUND_COLORS = ["#ffffff", "#111827"];
  var MAX_BG_IMAGE_CHARS = 9000000;
  var BG_IMAGE_RE = /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+=*$/;

  function validateBackgroundImage(x) {
    if (typeof x !== "string" || x.length === 0 || x.length > MAX_BG_IMAGE_CHARS) return null;
    return BG_IMAGE_RE.test(x) ? x : null;
  }

  var MAX_SHAPES = 1000;
  var MAX_TEXT_LEN = 200;
  var DEFAULT_CAPS = { maxShapes: MAX_SHAPES, maxTextLen: MAX_TEXT_LEN };

  // ── geometry ────────────────────────────────────────────────────────────

  function quant(v) { return Math.round(v * 10) / 10; }

  function normDeg(deg) {
    var d = deg % 360;
    return quant(d < 0 ? d + 360 : d);
  }

  /* Freehand path data, quadratic-midpoint smoothed: raw samples become
   * control points, on-curve points are their midpoints. C1-continuous in one
   * pass, append-safe — extending a stroke never shifts what is drawn. */
  function pathD(pts) {
    var n = pts.length >> 1;
    if (n === 0) return "";
    if (n === 1) return "M" + pts[0] + " " + pts[1] + "l0.01 0";
    if (n === 2) return "M" + pts[0] + " " + pts[1] + "L" + pts[2] + " " + pts[3];
    var d = "M" + pts[0] + " " + pts[1];
    for (var i = 1; i < n - 1; i++) {
      var cx = pts[i * 2];
      var cy = pts[i * 2 + 1];
      var mx = (cx + pts[i * 2 + 2]) / 2;
      var my = (cy + pts[i * 2 + 3]) / 2;
      d += "Q" + cx + " " + cy + " " + mx + " " + my;
    }
    return d + "L" + pts[(n - 1) * 2] + " " + pts[(n - 1) * 2 + 1];
  }

  function trianglePoints(s) {
    return [
      [s.x + s.w / 2, s.y],
      [s.x + s.w, s.y + s.h],
      [s.x, s.y + s.h],
    ];
  }

  /* A polygon's flat pts ([x0,y0,x1,y1,…], like path) as [[x,y],…] pairs. */
  function polyPairs(pts) {
    var out = [];
    for (var i = 0; i + 1 < pts.length; i += 2) out.push([pts[i], pts[i + 1]]);
    return out;
  }

  /* ── circular arc (kind "arc") ─────────────────────────────────────────
   * Stored as chord endpoints + signed sagitta `h`: the arc's apex sits at
   * the chord midpoint displaced by h along the left-hand normal (screen
   * coords, y down). Radius and sweep derive from that — the ONLY stored
   * curvature parameter, so scaling/serializing can't drift. |h| below
   * ARC_FLAT_H renders as a straight segment (a degenerate radius would
   * otherwise explode). */
  var ARC_FLAT_H = 0.25;

  function arcGeometry(s) {
    var c = Math.hypot(s.x2 - s.x1, s.y2 - s.y1);
    if (c < 1e-6 || Math.abs(s.h) < ARC_FLAT_H) return null;
    var ah = Math.abs(s.h);
    var r = ah / 2 + (c * c) / (8 * ah);
    var mx = (s.x1 + s.x2) / 2;
    var my = (s.y1 + s.y2) / 2;
    var ux = (s.x2 - s.x1) / c;
    var uy = (s.y2 - s.y1) / c;
    // Left-hand normal; sign of h picks the bulge side.
    var nx = -uy * Math.sign(s.h);
    var ny = ux * Math.sign(s.h);
    var cx = mx - nx * (r - ah);
    var cy = my - ny * (r - ah);
    var a1 = Math.atan2(s.y1 - cy, s.x1 - cx);
    var a2 = Math.atan2(s.y2 - cy, s.x2 - cx);
    var aa = Math.atan2((my + ny * ah) - cy, (mx + nx * ah) - cx);
    var TAU = Math.PI * 2;
    var ccw = ((a2 - a1) % TAU + TAU) % TAU;
    var apexCcw = ((aa - a1) % TAU + TAU) % TAU;
    // Go the way that passes through the apex. "ccw" here means increasing
    // atan2 angle, which on a y-down screen is CLOCKWISE to the eye — and is
    // exactly SVG's sweep-flag=1 direction.
    var sweepPos = apexCcw <= ccw;
    var sweep = sweepPos ? ccw : ccw - TAU;   // signed, ± radians
    return { cx: cx, cy: cy, r: r, a1: a1, sweep: sweep };
  }

  function arcPoints(s, n) {
    var g = arcGeometry(s);
    if (!g) return [[s.x1, s.y1], [s.x2, s.y2]];
    n = n || 24;
    var out = [];
    for (var i = 0; i <= n; i++) {
      var a = g.a1 + (g.sweep * i) / n;
      out.push([g.cx + g.r * Math.cos(a), g.cy + g.r * Math.sin(a)]);
    }
    return out;
  }

  function arcPathD(s) {
    var g = arcGeometry(s);
    if (!g) return "M" + s.x1 + " " + s.y1 + "L" + s.x2 + " " + s.y2;
    var laf = Math.abs(g.sweep) > Math.PI ? 1 : 0;
    var sf = g.sweep > 0 ? 1 : 0;
    var r = quant(g.r);
    return "M" + s.x1 + " " + s.y1 + "A" + r + " " + r + " 0 " +
      laf + " " + sf + " " + s.x2 + " " + s.y2;
  }

  /* Arrow head: a filled triangle at (x2,y2); size scales with stroke width,
   * capped to the shaft length so a short arrow stays legible. */
  function arrowHead(s) {
    var dx = s.x2 - s.x1;
    var dy = s.y2 - s.y1;
    var len = Math.hypot(dx, dy) || 1;
    var size = Math.min(Math.max(s.sw * 3, 8), len);
    var ux = dx / len;
    var uy = dy / len;
    var px = -uy * size * 0.4;
    var py = ux * size * 0.4;
    var bx = s.x2 - ux * size;
    var by = s.y2 - uy * size;
    return [[s.x2, s.y2], [bx + px, by + py], [bx - px, by - py]];
  }

  /* Axis-aligned bounds of the UNROTATED geometry — also the rotation pivot
   * frame and the selection box. For an opaque shape the "unrotated geometry"
   * is its measured bbox scaled about its centre and translated. */
  function shapeBounds(s) {
    switch (s.kind) {
      case "path": {
        var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (var i = 0; i < s.pts.length; i += 2) {
          minX = Math.min(minX, s.pts[i]);
          maxX = Math.max(maxX, s.pts[i]);
          minY = Math.min(minY, s.pts[i + 1]);
          maxY = Math.max(maxY, s.pts[i + 1]);
        }
        if (!isFinite(minX)) return { x: 0, y: 0, w: 0, h: 0 };
        return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
      }
      case "polygon": {
        var pminX = Infinity, pminY = Infinity, pmaxX = -Infinity, pmaxY = -Infinity;
        for (var p = 0; p < s.pts.length; p += 2) {
          pminX = Math.min(pminX, s.pts[p]);
          pmaxX = Math.max(pmaxX, s.pts[p]);
          pminY = Math.min(pminY, s.pts[p + 1]);
          pmaxY = Math.max(pmaxY, s.pts[p + 1]);
        }
        if (!isFinite(pminX)) return { x: 0, y: 0, w: 0, h: 0 };
        return { x: pminX, y: pminY, w: pmaxX - pminX, h: pmaxY - pminY };
      }
      case "rect":
      case "triangle":
        return { x: s.x, y: s.y, w: s.w, h: s.h };
      case "ellipse":
        return { x: s.cx - s.rx, y: s.cy - s.ry, w: s.rx * 2, h: s.ry * 2 };
      case "arrow":
      case "line":
        return {
          x: Math.min(s.x1, s.x2),
          y: Math.min(s.y1, s.y2),
          w: Math.abs(s.x2 - s.x1),
          h: Math.abs(s.y2 - s.y1),
        };
      case "arc": {
        var apts = arcPoints(s);
        var aminX = Infinity, aminY = Infinity, amaxX = -Infinity, amaxY = -Infinity;
        for (var ai = 0; ai < apts.length; ai++) {
          aminX = Math.min(aminX, apts[ai][0]);
          amaxX = Math.max(amaxX, apts[ai][0]);
          aminY = Math.min(aminY, apts[ai][1]);
          amaxY = Math.max(amaxY, apts[ai][1]);
        }
        return { x: aminX, y: aminY, w: amaxX - aminX, h: amaxY - aminY };
      }
      case "text":
        return { x: s.x, y: s.y - s.th * 0.8, w: s.tw, h: s.th };
      case "opaque": {
        var cx = s.bx + s.bw / 2;
        var cy = s.by + s.bh / 2;
        var w = s.bw * s.sx;
        var h = s.bh * s.sy;
        return { x: cx - w / 2 + s.dx, y: cy - h / 2 + s.dy, w: w, h: h };
      }
    }
    return { x: 0, y: 0, w: 0, h: 0 };
  }

  function shapePivot(s) {
    var b = shapeBounds(s);
    return { cx: quant(b.x + b.w / 2), cy: quant(b.y + b.h / 2) };
  }

  function rotatePoint(px, py, cx, cy, deg) {
    if (!deg) return { x: px, y: py };
    var rad = (deg * Math.PI) / 180;
    var cos = Math.cos(rad);
    var sin = Math.sin(rad);
    var dx = px - cx;
    var dy = py - cy;
    return { x: cx + dx * cos - dy * sin, y: cy + dx * sin + dy * cos };
  }

  function translateShape(s, dx, dy) {
    switch (s.kind) {
      case "path":
      case "polygon":
        return Object.assign({}, s, {
          pts: s.pts.map(function (v, i) { return quant(i % 2 === 0 ? v + dx : v + dy); }),
        });
      case "rect":
      case "triangle":
        return Object.assign({}, s, { x: quant(s.x + dx), y: quant(s.y + dy) });
      case "ellipse":
        return Object.assign({}, s, { cx: quant(s.cx + dx), cy: quant(s.cy + dy) });
      case "arrow":
      case "line":
      case "arc":
        return Object.assign({}, s, {
          x1: quant(s.x1 + dx), y1: quant(s.y1 + dy),
          x2: quant(s.x2 + dx), y2: quant(s.y2 + dy),
        });
      case "text":
        return Object.assign({}, s, { x: quant(s.x + dx), y: quant(s.y + dy) });
      case "opaque":
        return Object.assign({}, s, { dx: quant(s.dx + dx), dy: quant(s.dy + dy) });
    }
    return s;
  }

  /* Scale about (ox,oy) in the shape's UNROTATED frame. Text scales its font
   * by the mean factor; an opaque shape scales its factors and re-derives its
   * translation so the transform appears about (ox,oy) like everyone else's. */
  function scaleShape(s, ox, oy, fx, fy) {
    function sx(v) { return quant(ox + (v - ox) * fx); }
    function sy(v) { return quant(oy + (v - oy) * fy); }
    switch (s.kind) {
      case "path":
      case "polygon":
        return Object.assign({}, s, {
          pts: s.pts.map(function (v, i) { return i % 2 === 0 ? sx(v) : sy(v); }),
        });
      case "rect":
      case "triangle":
        return Object.assign({}, s, { x: sx(s.x), y: sy(s.y), w: quant(s.w * fx), h: quant(s.h * fy) });
      case "ellipse":
        return Object.assign({}, s, { cx: sx(s.cx), cy: sy(s.cy), rx: quant(s.rx * fx), ry: quant(s.ry * fy) });
      case "arrow":
      case "line":
        return Object.assign({}, s, { x1: sx(s.x1), y1: sy(s.y1), x2: sx(s.x2), y2: sy(s.y2) });
      case "arc":
        // A non-uniform scale would make the circular arc elliptical — the
        // model stays circular, so the sagitta takes the MEAN factor. Exact
        // under uniform scaling, a faithful approximation otherwise.
        return Object.assign({}, s, {
          x1: sx(s.x1), y1: sy(s.y1), x2: sx(s.x2), y2: sy(s.y2),
          h: quant(s.h * ((fx + fy) / 2)),
        });
      case "text": {
        var f = (fx + fy) / 2;
        return Object.assign({}, s, {
          x: sx(s.x), y: sy(s.y),
          size: quant(Math.max(4, s.size * f)),
          // Letter spacing scales WITH the type. It is a length in user units,
          // so leaving it fixed makes an enlarged label look progressively
          // tighter and a shrunk one gappy — the one thing tracking is
          // supposed to hold steady.
          tracking: s.tracking ? quant(s.tracking * f) : s.tracking,
          tw: quant(s.tw * f), th: quant(s.th * f),
        });
      }
      case "opaque": {
        var cx0 = s.bx + s.bw / 2;
        var cy0 = s.by + s.bh / 2;
        var centerX = ox + (cx0 + s.dx - ox) * fx;
        var centerY = oy + (cy0 + s.dy - oy) * fy;
        return Object.assign({}, s, {
          sx: quant(s.sx * fx) || 0.1,
          sy: quant(s.sy * fy) || 0.1,
          dx: quant(centerX - cx0),
          dy: quant(centerY - cy0),
        });
      }
    }
    return s;
  }

  function rotateShape(s, deg) {
    return Object.assign({}, s, { rot: normDeg(deg) });
  }

  function distToSeg(px, py, x1, y1, x2, y2) {
    var dx = x2 - x1;
    var dy = y2 - y1;
    var len2 = dx * dx + dy * dy;
    var t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len2));
    return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
  }

  function pointInPoly(px, py, poly) {
    var inside = false;
    for (var i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      var xi = poly[i][0], yi = poly[i][1];
      var xj = poly[j][0], yj = poly[j][1];
      if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  function distToPoly(px, py, poly) {
    var best = Infinity;
    for (var i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      best = Math.min(best, distToSeg(px, py, poly[j][0], poly[j][1], poly[i][0], poly[i][1]));
    }
    return best;
  }

  function distToBox(px, py, x1, y1, x2, y2) {
    var ox = Math.max(x1 - px, 0, px - x2);
    var oy = Math.max(y1 - py, 0, py - y2);
    return Math.hypot(ox, oy);
  }

  /* Distance to the painted area, IGNORING rotation — hitTest maps the point
   * into the unrotated frame first, so per-kind maths stays rotation-blind. */
  function distToShape(s, px, py) {
    var filled = canFill(s.kind) && !!s.fill;
    switch (s.kind) {
      case "path": {
        var n = s.pts.length >> 1;
        if (n === 0) return Infinity;
        if (n === 1) return Math.hypot(px - s.pts[0], py - s.pts[1]);
        var best = Infinity;
        for (var i = 0; i < n - 1; i++) {
          var d = distToSeg(px, py, s.pts[i * 2], s.pts[i * 2 + 1], s.pts[i * 2 + 2], s.pts[i * 2 + 3]);
          if (d < best) {
            best = d;
            if (best === 0) break;
          }
        }
        return best;
      }
      case "rect": {
        var x2 = s.x + s.w;
        var y2 = s.y + s.h;
        if (filled && px >= s.x && px <= x2 && py >= s.y && py <= y2) return 0;
        return Math.min(
          distToSeg(px, py, s.x, s.y, x2, s.y),
          distToSeg(px, py, x2, s.y, x2, y2),
          distToSeg(px, py, x2, y2, s.x, y2),
          distToSeg(px, py, s.x, y2, s.x, s.y));
      }
      case "triangle": {
        var poly = trianglePoints(s);
        if (filled && pointInPoly(px, py, poly)) return 0;
        return distToPoly(px, py, poly);
      }
      case "ellipse": {
        var dx = (px - s.cx) / (s.rx || 1e-6);
        var dy = (py - s.cy) / (s.ry || 1e-6);
        var k = Math.hypot(dx, dy);
        if (filled && k <= 1) return 0;
        if (k === 0) return Math.min(s.rx, s.ry);
        return Math.abs(k - 1) * Math.min(s.rx, s.ry);
      }
      case "polygon": {
        var ppoly = polyPairs(s.pts);
        if (ppoly.length < 2) return Infinity;
        if (filled && pointInPoly(px, py, ppoly)) return 0;
        return distToPoly(px, py, ppoly);
      }
      case "arrow":
        return Math.min(
          distToSeg(px, py, s.x1, s.y1, s.x2, s.y2),
          distToPoly(px, py, arrowHead(s)));
      case "line":
        return distToSeg(px, py, s.x1, s.y1, s.x2, s.y2);
      case "arc": {
        // Sampled-polyline distance — distToPoly would also close the arc's
        // chord, counting the empty side as a hit.
        var arcp = arcPoints(s);
        var abest = Infinity;
        for (var az = 0; az < arcp.length - 1; az++) {
          abest = Math.min(abest, distToSeg(px, py,
            arcp[az][0], arcp[az][1], arcp[az + 1][0], arcp[az + 1][1]));
        }
        return abest;
      }
      case "text":
        return distToBox(px, py, s.x, s.y - s.th * 0.8, s.x + s.tw, s.y + s.th * 0.25);
      case "opaque": {
        // The whole imported object hits as its box — inside counts as 0 so
        // it can be grabbed anywhere, like a filled shape.
        var b = shapeBounds(s);
        return distToBox(px, py, b.x, b.y, b.x + b.w, b.y + b.h);
      }
    }
    return Infinity;
  }

  function distToShapeRotated(s, px, py) {
    if (!s.rot) return distToShape(s, px, py);
    var p0 = shapePivot(s);
    var p = rotatePoint(px, py, p0.cx, p0.cy, -s.rot);
    return distToShape(s, p.x, p.y);
  }

  /* Topmost shape within radius, or null. Backwards: later entries paint on
   * top. Not elementFromPoint — that can't express a radius, flushes layout
   * per move, and isn't testable. */
  function hitTest(shapes, x, y, radius) {
    for (var i = shapes.length - 1; i >= 0; i--) {
      var s = shapes[i];
      var tol = (s.kind === "text" || s.kind === "opaque" ? 0 : s.sw / 2) + radius;
      if (distToShapeRotated(s, x, y) <= tol) return s.id;
    }
    return null;
  }

  // ── validation ──────────────────────────────────────────────────────────
  // Rebuild every field rather than spreading: `pts` goes into a `d`
  // attribute and `text` into a text node. ("opaque" is not built here — it
  // only ever comes from svgdoc.js parsing, which enforces the guard rules.)

  var COLOR_RE = /^#[0-9a-fA-F]{6}$/;
  var COORD_MIN = -MAX_BOARD_W;
  var COORD_MAX = 2 * MAX_BOARD_W;

  function vNum(v) {
    return typeof v === "number" && isFinite(v) && v >= COORD_MIN && v <= COORD_MAX ? v : null;
  }
  function vStr(v, max) {
    return typeof v === "string" && v.length > 0 && v.length <= max ? v : null;
  }
  function vColor(v) {
    return typeof v === "string" && COLOR_RE.test(v) ? v : null;
  }

  function validateShape(x, caps) {
    if (!x || typeof x !== "object") return null;
    var o = x;
    var id = vStr(o.id, 64);
    var c = vColor(o.color);
    var sw = typeof o.sw === "number" && isFinite(o.sw) && o.sw >= 0.5 && o.sw <= 64 ? o.sw : null;
    if (!id || !c || sw === null) return null;
    var opacity = typeof o.opacity === "number" && isFinite(o.opacity) && o.opacity > 0 && o.opacity <= 1
      ? o.opacity : 1;
    var rot = typeof o.rot === "number" && isFinite(o.rot) ? normDeg(o.rot) : 0;
    var base = { id: id, color: c, sw: sw, opacity: opacity, fill: vColor(o.fill), rot: rot };

    switch (o.kind) {
      case "path": {
        if (!Array.isArray(o.pts)) return null;
        if (o.pts.length < 2 || o.pts.length % 2 !== 0) return null;
        var out = [];
        for (var i = 0; i < o.pts.length; i++) {
          var n = vNum(o.pts[i]);
          if (n === null) return null;
          out.push(n);
        }
        return Object.assign(base, { kind: "path", pts: out });
      }
      case "rect":
      case "triangle": {
        var x0 = vNum(o.x), y0 = vNum(o.y), w = vNum(o.w), h = vNum(o.h);
        if (x0 === null || y0 === null || w === null || h === null || w < 0 || h < 0) return null;
        return Object.assign(base, { kind: o.kind, x: x0, y: y0, w: w, h: h });
      }
      case "ellipse": {
        var cx = vNum(o.cx), cy = vNum(o.cy), rx = vNum(o.rx), ry = vNum(o.ry);
        if (cx === null || cy === null || rx === null || ry === null || rx < 0 || ry < 0) return null;
        return Object.assign(base, { kind: "ellipse", cx: cx, cy: cy, rx: rx, ry: ry });
      }
      case "arrow":
      case "line": {
        var ax1 = vNum(o.x1), ay1 = vNum(o.y1), ax2 = vNum(o.x2), ay2 = vNum(o.y2);
        if (ax1 === null || ay1 === null || ax2 === null || ay2 === null) return null;
        return Object.assign(base, { kind: o.kind, x1: ax1, y1: ay1, x2: ax2, y2: ay2 });
      }
      case "arc": {
        var cx1 = vNum(o.x1), cy1 = vNum(o.y1), cx2 = vNum(o.x2), cy2 = vNum(o.y2);
        if (cx1 === null || cy1 === null || cx2 === null || cy2 === null) return null;
        // The sagitta is a length, not a coordinate — bounded like one but
        // symmetric around zero.
        var hh = typeof o.h === "number" && isFinite(o.h) &&
          Math.abs(o.h) <= COORD_MAX ? quant(o.h) : null;
        if (hh === null) return null;
        return Object.assign(base, { kind: "arc", x1: cx1, y1: cy1, x2: cx2, y2: cy2, h: hh });
      }
      case "polygon": {
        if (!Array.isArray(o.pts)) return null;
        if (o.pts.length < 6 || o.pts.length % 2 !== 0) return null;
        var pout = [];
        for (var pi = 0; pi < o.pts.length; pi++) {
          var pn = vNum(o.pts[pi]);
          if (pn === null) return null;
          pout.push(pn);
        }
        return Object.assign(base, { kind: "polygon", pts: pout });
      }
      case "text": {
        var tx = vNum(o.x), ty = vNum(o.y), size = vNum(o.size), tw = vNum(o.tw), th = vNum(o.th);
        if (tx === null || ty === null || size === null || tw === null || th === null) return null;
        if (typeof o.text !== "string" || o.text.length === 0) return null;
        return Object.assign(base, {
          kind: "text", x: tx, y: ty,
          text: o.text.slice(0, caps.maxTextLen), size: size, tw: tw, th: th,
        });
      }
      default:
        return null;
    }
  }

  function validateDoc(x, caps) {
    if (!x || typeof x !== "object") return null;
    if (!Array.isArray(x.shapes)) return null;
    var doc = emptyDoc();
    doc.background = vColor(x.background);
    for (var i = 0; i < x.shapes.length; i++) {
      if (doc.shapes.length >= caps.maxShapes) break;
      var v = validateShape(x.shapes[i], caps);
      if (v) doc.shapes.push(v);
    }
    return doc;
  }

  // ── SVG serialization ───────────────────────────────────────────────────

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&apos;");
  }

  function polyPoints(pts) {
    return pts.map(function (p) { return p[0] + "," + p[1]; }).join(" ");
  }

  function rotAttr(s) {
    if (!s.rot) return "";
    var p = shapePivot(s);
    return ' transform="rotate(' + s.rot + " " + p.cx + " " + p.cy + ')"';
  }

  /* Round-trip hints — enigma's names, kept for interop (see file header).
   * `pathD` smoothing is not invertible and a triangle looks like any other
   * 3-point polygon coming back; the hints close the loop. Plain data-*
   * attributes that every other renderer ignores. */
  var HINT_KIND = "data-en-kind";
  var HINT_PTS = "data-en-pts";
  var HINT_BG = "data-en-bg";
  var HINT_BGIMG = "data-en-bgimg";
  /* Ours: an arc's defining chord + sagitta (x1 y1 x2 y2 h) — the `A` path
   * data is derived and not invertible without it. */
  var HINT_ARC = "data-en-arc";
  /* Ours alone: an opaque object's placement (dx dy sx sy rot cx cy), on the
   * wrapping <g>, so a moved import round-trips without decomposing its
   * transform matrix. */
  var HINT_WRAP = "data-en-wrap";

  /* The full transform of an opaque shape (translate + rotate about the
   * CURRENT pivot + scale about the ORIGINAL bbox centre), or "". */
  function opaqueTransform(s) {
    var cx0 = s.bx + s.bw / 2;
    var cy0 = s.by + s.bh / 2;
    var parts = [];
    if (s.rot) {
      var p = shapePivot(s);
      parts.push("rotate(" + s.rot + " " + p.cx + " " + p.cy + ")");
    }
    if (s.dx || s.dy) parts.push("translate(" + s.dx + " " + s.dy + ")");
    if (s.sx !== 1 || s.sy !== 1) {
      parts.push("translate(" + cx0 + " " + cy0 + ") scale(" + s.sx + " " + s.sy +
        ") translate(" + -cx0 + " " + -cy0 + ")");
    }
    return parts.join(" ");
  }

  function opaqueIsUntouched(s) {
    return !s.dx && !s.dy && s.sx === 1 && s.sy === 1 && !s.rot;
  }

  function shapeToSvg(s) {
    var fill = canFill(s.kind) && s.fill ? esc(s.fill) : "none";
    var common = 'stroke="' + esc(s.color) + '" stroke-width="' + s.sw + '" opacity="' + s.opacity + '"';
    var rot = rotAttr(s);
    switch (s.kind) {
      case "path":
        return '<path d="' + pathD(s.pts) + '" ' + HINT_PTS + '="' + s.pts.join(" ") +
          '" fill="none" ' + common + ' stroke-linecap="round" stroke-linejoin="round"' + rot + "/>";
      case "rect":
        return '<rect x="' + s.x + '" y="' + s.y + '" width="' + s.w + '" height="' + s.h +
          '" fill="' + fill + '" ' + common + rot + "/>";
      case "ellipse":
        return '<ellipse cx="' + s.cx + '" cy="' + s.cy + '" rx="' + s.rx + '" ry="' + s.ry +
          '" fill="' + fill + '" ' + common + rot + "/>";
      case "triangle":
        return '<polygon points="' + polyPoints(trianglePoints(s)) + '" ' + HINT_KIND +
          '="triangle" fill="' + fill + '" ' + common + ' stroke-linejoin="round"' + rot + "/>";
      case "polygon":
        // No hint: a plain <polygon> IS the model — points round-trip as-is.
        return '<polygon points="' + polyPoints(polyPairs(s.pts)) + '" fill="' + fill +
          '" ' + common + ' stroke-linejoin="round"' + rot + "/>";
      case "line":
        // No hint either: a plain <line> imports straight back as a line.
        return '<line x1="' + s.x1 + '" y1="' + s.y1 + '" x2="' + s.x2 + '" y2="' + s.y2 +
          '" ' + common + ' stroke-linecap="round"' + rot + "/>";
      case "arc":
        return '<path d="' + arcPathD(s) + '" ' + HINT_ARC + '="' +
          [s.x1, s.y1, s.x2, s.y2, s.h].join(" ") +
          '" fill="none" ' + common + ' stroke-linecap="round"' + rot + "/>";
      case "arrow":
        // One group carries transform AND opacity so shaft and head turn
        // together and composite once (per-child opacity darkens the overlap).
        return "<g " + HINT_KIND + '="arrow" opacity="' + s.opacity + '"' + rot + ">" +
          '<line x1="' + s.x1 + '" y1="' + s.y1 + '" x2="' + s.x2 + '" y2="' + s.y2 +
          '" stroke="' + esc(s.color) + '" stroke-width="' + s.sw + '" stroke-linecap="round"/>' +
          '<polygon points="' + polyPoints(arrowHead(s)) + '" fill="' + esc(s.color) + '"/>' +
          "</g>";
      case "text": {
        /* Presentation attributes, not a style="" string: the scanner reads
           attributes, and a style attribute is a place to hide a declaration
           it would have to parse CSS to see. Each is emitted only when it is
           not the default, so an ordinary label stays the short line it was. */
        var font = '<text x="' + s.x + '" y="' + s.y + '" fill="' + esc(s.color) +
          '" opacity="' + s.opacity + '" font-size="' + s.size +
          '" font-family="' + esc(fontStack(s.font)) + '"';
        if (s.bold) font += ' font-weight="bold"';
        if (s.italic) font += ' font-style="italic"';
        if (s.underline) font += ' text-decoration="underline"';
        if (s.tracking) font += ' letter-spacing="' + s.tracking + '"';
        return font + rot + ">" + esc(s.text) + "</text>";
      }
      case "opaque": {
        // Untouched → the exact original bytes. Touched → wrapped, with the
        // raw placement factors in the hint so it round-trips losslessly.
        if (opaqueIsUntouched(s)) return s.markup;
        var wrap = [s.dx, s.dy, s.sx, s.sy, s.rot, quant(s.bx + s.bw / 2), quant(s.by + s.bh / 2)];
        return "<g " + HINT_WRAP + '="' + wrap.join(" ") + '" transform="' +
          opaqueTransform(s) + '">' + s.markup + "</g>";
      }
    }
    return "";
  }

  global.SketchModel = {
    BOARD_H: BOARD_H, BOARD_W: BOARD_W, MIN_BOARD_W: MIN_BOARD_W, MAX_BOARD_W: MAX_BOARD_W,
    MIN_POINT_DIST: MIN_POINT_DIST,
    DEFAULT_SLOT_COLORS: DEFAULT_SLOT_COLORS, PALETTE_SLOTS: PALETTE_SLOTS,
    EXTRA_COLORS: EXTRA_COLORS, PICKER_COLORS: PICKER_COLORS,
    STROKE_SIZES: STROKE_SIZES, TEXT_SIZES: TEXT_SIZES, OPACITY_STEPS: OPACITY_STEPS,
    ROTATE_SNAP_DEG: ROTATE_SNAP_DEG, SVG_FONT_STACK: SVG_FONT_STACK,
    FONT_CHOICES: FONT_CHOICES, DEFAULT_FONT: DEFAULT_FONT,
    TRACKING_STEPS: TRACKING_STEPS,
    fontStack: fontStack, fontKey: fontKey, fontShorthand: fontShorthand,
    BACKGROUND_COLORS: BACKGROUND_COLORS,
    MAX_BG_IMAGE_CHARS: MAX_BG_IMAGE_CHARS,
    MAX_SHAPES: MAX_SHAPES, MAX_TEXT_LEN: MAX_TEXT_LEN, DEFAULT_CAPS: DEFAULT_CAPS,
    HINT_KIND: HINT_KIND, HINT_PTS: HINT_PTS, HINT_BG: HINT_BG,
    HINT_BGIMG: HINT_BGIMG, HINT_WRAP: HINT_WRAP, HINT_ARC: HINT_ARC,
    boardWidthFor: boardWidthFor, boardWidth: boardWidth,
    normalizeSlots: normalizeSlots, canFill: canFill, emptyDoc: emptyDoc,
    validateBackgroundImage: validateBackgroundImage,
    quant: quant, normDeg: normDeg, pathD: pathD,
    trianglePoints: trianglePoints, arrowHead: arrowHead,
    polyPairs: polyPairs, arcPoints: arcPoints, arcPathD: arcPathD,
    shapeBounds: shapeBounds, shapePivot: shapePivot, rotatePoint: rotatePoint,
    translateShape: translateShape, scaleShape: scaleShape, rotateShape: rotateShape,
    distToShape: distToShape, distToShapeRotated: distToShapeRotated, hitTest: hitTest,
    validateShape: validateShape, validateDoc: validateDoc,
    esc: esc, rotAttr: rotAttr, opaqueTransform: opaqueTransform,
    opaqueIsUntouched: opaqueIsUntouched, shapeToSvg: shapeToSvg,
  };
})(window);

/* A .svg file as the editor sees it: a MIXED document.
 *
 * Ported from enigma's svgDocument.ts with one structural upgrade: opaque
 * nodes — everything the model cannot decompose (gradients, groups, nested
 * transforms, text-on-a-path) — are not a separate list any more but
 * first-class SHAPES of kind "opaque" in the one ordered list. That is what
 * makes an uploaded Inkscape drawing genuinely editable here: every imported
 * element can be selected, moved, scaled, rotated and deleted as a whole,
 * while its internal markup is carried verbatim and an untouched element
 * re-emits byte-identically.
 *
 * SECURITY: this is an untrusted-file reader. `parse` REFUSES a file carrying
 * active content rather than stripping it — stripping would mean the user
 * sees one thing and saves another. The server runs the same rules
 * independently (toto/sketch/svg_guard.py); whichever side notices, refuses.
 */
(function (global) {
  "use strict";

  var M = global.SketchModel;

  var ACTIVE_TAGS = ["script", "foreignobject", "use", "animate", "set",
                     "iframe", "object", "embed"];
  var SAFE_HREF = /^(#|data:image\/(png|jpeg|webp);base64,)/i;
  var DRAWABLE = ["rect", "ellipse", "circle", "line", "polygon", "polyline",
                  "path", "text", "g", "image"];

  function serializeElement(el) {
    return new XMLSerializer().serializeToString(el);
  }

  function serializeChildren(el) {
    var out = [];
    for (var i = 0; i < el.childNodes.length; i++) {
      var node = el.childNodes[i];
      if (node.nodeType === 1) out.push(serializeElement(node));
      else if (node.nodeType === 3 && node.textContent.trim()) {
        out.push(node.textContent);
      }
    }
    return out.join("");
  }

  function findActive(root) {
    var all = [root].concat(Array.prototype.slice.call(root.querySelectorAll("*")));
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var tag = el.tagName.toLowerCase();
      if (ACTIVE_TAGS.indexOf(tag) !== -1) {
        return { reason: "active-content", detail: "<" + tag + ">" };
      }
      for (var a = 0; a < el.attributes.length; a++) {
        var attr = el.attributes[a];
        var name = attr.name.toLowerCase();
        if (name.indexOf("on") === 0) {
          return { reason: "active-content", detail: tag + "[" + attr.name + "]" };
        }
        if (name === "style" && /url\s*\(/i.test(attr.value)) {
          return { reason: "external-reference", detail: tag + "[style=url()]" };
        }
        if ((name === "href" || name === "xlink:href" || name === "src") &&
            attr.value.trim() && !SAFE_HREF.test(attr.value.trim())) {
          return { reason: "external-reference", detail: tag + "[" + attr.name + "]" };
        }
      }
    }
    return null;
  }

  // ── attribute helpers ───────────────────────────────────────────────────

  function n(el, name) {
    var raw = el.getAttribute(name);
    if (raw === null) return null;
    var t = raw.trim();
    if (t === "" || !/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(t)) return null;
    var v = Number(t);
    return isFinite(v) ? v : null;
  }

  function str(el, name) {
    var v = el.getAttribute(name);
    return v === null ? null : v.trim();
  }

  /* Exactly one rotate(a cx cy) — the only transform the model can express.
   * Anything else leaves the node opaque (where it is STILL movable now). */
  function rotationOf(el) {
    var t = str(el, "transform");
    if (!t) return null;
    var m = /^rotate\(\s*([-+.\deE]+)[\s,]+([-+.\deE]+)[\s,]+([-+.\deE]+)\s*\)$/.exec(t);
    if (!m) return "unsupported";
    var deg = Number(m[1]);
    return isFinite(deg) ? deg : "unsupported";
  }

  /* "#rrggbb" (expanding #rgb), null for absent, "unsupported" otherwise. */
  function importColor(raw) {
    if (raw === null || raw === "") return null;
    if (/^#[0-9a-fA-F]{6}$/.test(raw)) return raw;
    var m = /^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$/.exec(raw);
    if (m) return "#" + m[1] + m[1] + m[2] + m[2] + m[3] + m[3];
    return "unsupported";
  }

  function commonAttrs(el) {
    // A style attribute would override the presentation attributes we read —
    // not faithfully representable, so the node stays opaque.
    if (el.hasAttribute("style")) return null;
    var stroke = importColor(str(el, "stroke"));
    var rawFill = str(el, "fill");
    var fill = rawFill === "none" ? null : importColor(rawFill);
    // A colour the model can't hold verbatim (named, rgb(), url(#…)) would
    // silently change on re-serialize — such an element stays opaque.
    if (stroke === "unsupported" || fill === "unsupported") return null;
    return {
      color: stroke || "#000000",
      sw: n(el, "stroke-width") !== null ? n(el, "stroke-width") : 1,
      opacity: n(el, "opacity") !== null ? n(el, "opacity") : 1,
      // SVG's DEFAULT fill is black — an absent attribute must not import
      // as "unfilled" or the file changes appearance on the first save.
      fill: rawFill === "none" ? null : (fill || "#000000"),
    };
  }

  function numberList(raw) {
    return raw.trim().split(/[\s,]+/).filter(Boolean).map(Number);
  }

  // ── element → editable shape (null ⇒ keep opaque) ───────────────────────

  function toShape(el, caps, measure) {
    var tag = el.tagName.toLowerCase();
    var rot = rotationOf(el);
    if (rot === "unsupported") return null;
    var base = commonAttrs(el);
    if (!base) return null;
    var common = Object.assign({ id: crypto.randomUUID(), rot: rot || 0 }, base);
    var draft = null;

    switch (tag) {
      case "rect": {
        if (el.hasAttribute(M.HINT_BG)) return null;
        var w = n(el, "width"), h = n(el, "height");
        if (w === null || h === null) return null;
        if (el.hasAttribute("rx") || el.hasAttribute("ry")) return null;
        draft = Object.assign(common, {
          kind: "rect", x: n(el, "x") || 0, y: n(el, "y") || 0, w: w, h: h });
        break;
      }
      case "ellipse": {
        var rx = n(el, "rx"), ry = n(el, "ry");
        if (rx === null || ry === null) return null;
        draft = Object.assign(common, {
          kind: "ellipse", cx: n(el, "cx") || 0, cy: n(el, "cy") || 0, rx: rx, ry: ry });
        break;
      }
      case "circle": {
        var r = n(el, "r");
        if (r === null) return null;
        draft = Object.assign(common, {
          kind: "ellipse", cx: n(el, "cx") || 0, cy: n(el, "cy") || 0, rx: r, ry: r });
        break;
      }
      case "line": {
        var lx2 = n(el, "x2"), ly2 = n(el, "y2");
        if (lx2 === null || ly2 === null) return null;
        draft = Object.assign(common, {
          kind: "line", x1: n(el, "x1") || 0, y1: n(el, "y1") || 0, x2: lx2, y2: ly2 });
        break;
      }
      case "polygon": {
        var pts = numberList((str(el, "points") || "").replace(/,/g, " "));
        if (pts.length < 6 || pts.length % 2 !== 0 ||
            pts.some(function (v) { return !isFinite(v); })) return null;
        // OUR triangles (hinted) rebuild the box-inscribed triangle the
        // triangle tool would draw; any other polygon is an editable
        // polygon of its own points.
        if (el.getAttribute(M.HINT_KIND) === "triangle") {
          if (pts.length !== 6) return null;
          var xs = [pts[0], pts[2], pts[4]], ys = [pts[1], pts[3], pts[5]];
          var tx = Math.min.apply(null, xs), ty = Math.min.apply(null, ys);
          draft = Object.assign(common, {
            kind: "triangle", x: tx, y: ty,
            w: Math.max.apply(null, xs) - tx, h: Math.max.apply(null, ys) - ty });
        } else {
          draft = Object.assign(common, { kind: "polygon", pts: pts });
        }
        break;
      }
      case "path": {
        // Our `d` strings have no inverse (smoothed quadratic / derived arc)
        // — only editable when they carry the raw values we stored alongside.
        var rawArc = str(el, M.HINT_ARC);
        if (rawArc) {
          var av = numberList(rawArc);
          if (av.length !== 5 || av.some(function (v) { return !isFinite(v); })) return null;
          draft = Object.assign(common, {
            kind: "arc", x1: av[0], y1: av[1], x2: av[2], y2: av[3], h: av[4] });
          break;
        }
        var rawPts = str(el, M.HINT_PTS);
        if (!rawPts) return null;
        var ppts = numberList(rawPts);
        if (ppts.length < 2 || ppts.length % 2 !== 0 ||
            ppts.some(function (v) { return !isFinite(v); })) return null;
        draft = Object.assign(common, { kind: "path", pts: ppts });
        break;
      }
      case "text": {
        if (el.children.length > 0) return null;      // <tspan> etc.
        if (str(el, "fill") === "none") return null;  // invisible — keep verbatim
        var size = n(el, "font-size");
        var textContent = el.textContent || "";
        if (size === null || !textContent) return null;
        /* Read the styling back, or a save-and-reload silently flattens every
           label to plain sans. The font is matched against the ALLOW-LIST by
           its stack rather than trusted: an .svg may arrive from anywhere, and
           an unrecognised family falls back to the default instead of being
           carried through into what we re-emit. */
        var family = str(el, "font-family") || "";
        var fontKey = M.DEFAULT_FONT;
        for (var fi = 0; fi < M.FONT_CHOICES.length; fi++) {
          if (M.FONT_CHOICES[fi].stack === family) { fontKey = M.FONT_CHOICES[fi].key; break; }
        }
        var weight = (str(el, "font-weight") || "").toLowerCase();
        var decoration = (str(el, "text-decoration") || "").toLowerCase();
        var tracking = n(el, "letter-spacing");
        var shape = {
          kind: "text", color: common.fill || "#000000", fill: null,
          x: n(el, "x") || 0, y: n(el, "y") || 0,
          text: textContent, size: size, font: fontKey,
          bold: weight === "bold" || Number(weight) >= 600,
          italic: (str(el, "font-style") || "").toLowerCase() === "italic",
          underline: decoration.indexOf("underline") !== -1,
          tracking: tracking === null ? 0 : tracking };
        // Measured WITH its styling: bold and tracking change advance width,
        // and a box measured without them puts the selection outline in the
        // wrong place for every styled label in the file.
        var box = measure(textContent, size, shape);
        shape.tw = box.w;
        shape.th = box.h;
        draft = Object.assign(common, shape);
        break;
      }
      case "g": {
        // The only groups we emit: an arrow, or a wrapped opaque object
        // (handled by the caller before reaching here).
        if (el.getAttribute(M.HINT_KIND) !== "arrow") return null;
        var line = el.querySelector("line");
        if (!line) return null;
        var gx2 = n(line, "x2"), gy2 = n(line, "y2");
        if (gx2 === null || gy2 === null) return null;
        draft = Object.assign(common, {
          kind: "arrow",
          color: str(line, "stroke") || common.color,
          sw: n(line, "stroke-width") !== null ? n(line, "stroke-width") : common.sw,
          x1: n(line, "x1") || 0, y1: n(line, "y1") || 0, x2: gx2, y2: gy2 });
        break;
      }
      default:
        return null;
    }

    return draft ? M.validateShape(draft, caps) : null;
  }

  function opaqueShape(markup, placement) {
    return Object.assign({
      id: crypto.randomUUID(), kind: "opaque", markup: markup,
      color: "#111827", sw: 2, opacity: 1, fill: null, rot: 0,
      dx: 0, dy: 0, sx: 1, sy: 1,
      // bbox measured after first render (editor.js); hintC* pins the
      // transform pivot of a re-opened wrapped node to its saved value.
      bx: 0, by: 0, bw: 0, bh: 0, measured: false, hintCx: null, hintCy: null,
    }, placement || {});
  }

  // ── parse ───────────────────────────────────────────────────────────────

  function parse(text, measure, caps) {
    caps = caps || M.DEFAULT_CAPS;
    if (/<!DOCTYPE|<!ENTITY/i.test(text)) {
      return { ok: false, reason: "doctype", detail: "<!DOCTYPE>" };
    }

    var parsed = new DOMParser().parseFromString(text, "image/svg+xml");
    var root = parsed.documentElement;
    if (!root || root.tagName.toLowerCase() !== "svg" ||
        parsed.querySelector("parsererror")) {
      return { ok: false, reason: "not-svg", detail: "not a valid SVG document" };
    }

    var active = findActive(root);
    if (active) return { ok: false, reason: active.reason, detail: active.detail };

    var rootAttrs = {};
    for (var i = 0; i < root.attributes.length; i++) {
      rootAttrs[root.attributes[i].name] = root.attributes[i].value;
    }
    var geom = geometryOf(rootAttrs);

    var prologue = [];
    var shapes = [];
    var background = null;
    var backgroundImage = null;
    var opaqueCount = 0;

    var children = Array.prototype.slice.call(root.children);
    for (var c = 0; c < children.length; c++) {
      var el = children[c];
      var tag = el.tagName.toLowerCase();
      if (DRAWABLE.indexOf(tag) === -1) {
        prologue.push(serializeElement(el));   // <defs>, <style>, <title>…
        continue;
      }
      if (tag === "rect" && el.hasAttribute(M.HINT_BG)) {
        background = str(el, "fill");
        continue;
      }
      if (tag === "image" && el.hasAttribute(M.HINT_BGIMG)) {
        backgroundImage = M.validateBackgroundImage(str(el, "href"));
        continue;
      }
      if (shapes.length >= caps.maxShapes) {
        // Past the cap everything rides along opaque — nothing is dropped.
        shapes.push(opaqueShape(serializeElement(el)));
        opaqueCount++;
        continue;
      }
      if (tag === "g" && el.hasAttribute(M.HINT_WRAP)) {
        // One of OUR wrappers around a moved import: unwrap the placement,
        // carry the interior verbatim.
        var parts = numberList(str(el, M.HINT_WRAP) || "");
        var placement = parts.length === 7 && parts.every(isFinite)
          ? { dx: parts[0], dy: parts[1], sx: parts[2] || 1, sy: parts[3] || 1,
              rot: M.normDeg(parts[4]), hintCx: parts[5], hintCy: parts[6] }
          : null;
        shapes.push(opaqueShape(serializeChildren(el), placement));
        opaqueCount++;
        continue;
      }
      var shape = toShape(el, caps, measure);
      if (shape) {
        shapes.push(shape);
      } else {
        shapes.push(opaqueShape(serializeElement(el)));
        opaqueCount++;
      }
    }

    return {
      ok: true,
      doc: {
        rootAttrs: rootAttrs, prologue: prologue, shapes: shapes,
        width: geom.width, height: geom.height,
        background: background, backgroundImage: backgroundImage,
      },
      opaqueCount: opaqueCount,
    };
  }

  /* Board size: viewBox wins, then width/height, then the classic board —
   * clamped like any untrusted coordinate. */
  function geometryOf(attrs) {
    var vb = (attrs.viewBox || "").trim();
    if (vb) {
      var p = numberList(vb);
      if (p.length === 4 && p.every(isFinite) && p[2] > 0 && p[3] > 0) {
        return { width: clampDim(p[2]), height: clampDim(p[3]) };
      }
    }
    var w = Number(attrs.width), h = Number(attrs.height);
    if (isFinite(w) && isFinite(h) && w > 0 && h > 0) {
      return { width: clampDim(w), height: clampDim(h) };
    }
    return { width: M.BOARD_W, height: M.BOARD_H };
  }

  function clampDim(v) {
    return Math.min(Math.max(Math.round(v), 1), M.MAX_BOARD_W);
  }

  // ── serialize ───────────────────────────────────────────────────────────

  function escapeAttr(v) {
    return String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /* Rebuild the file: root attributes verbatim, prologue verbatim, our own
   * background, then every shape in paint order — editable ones regenerated,
   * opaque ones re-emitted exactly (wrapped only when moved). */
  function serialize(doc) {
    var attrs = Object.keys(doc.rootAttrs).map(function (k) {
      return k + '="' + escapeAttr(doc.rootAttrs[k]) + '"';
    }).join(" ");
    var lines = [];
    doc.prologue.forEach(function (markup) { lines.push("  " + markup); });
    if (doc.background) {
      lines.push('  <rect ' + M.HINT_BG + '="1" width="' + doc.width +
        '" height="' + doc.height + '" fill="' + escapeAttr(doc.background) + '"/>');
    }
    var img = M.validateBackgroundImage(doc.backgroundImage);
    if (img) {
      lines.push('  <image ' + M.HINT_BGIMG + '="1" href="' + escapeAttr(img) +
        '" x="0" y="0" width="' + doc.width + '" height="' + doc.height +
        '" preserveAspectRatio="xMidYMid slice"/>');
    }
    doc.shapes.forEach(function (s) { lines.push("  " + M.shapeToSvg(s)); });
    return '<?xml version="1.0" encoding="UTF-8"?>\n<svg ' + attrs + ">\n" +
      lines.join("\n") + "\n</svg>";
  }

  function empty(width, height) {
    width = width || M.BOARD_W;
    height = height || M.BOARD_H;
    return {
      rootAttrs: {
        xmlns: "http://www.w3.org/2000/svg",
        width: String(width), height: String(height),
        viewBox: "0 0 " + width + " " + height,
      },
      prologue: [], shapes: [], width: width, height: height,
      background: null, backgroundImage: null,
    };
  }

  global.SketchSvgDoc = {
    parse: parse,
    serialize: serialize,
    empty: empty,
    opaqueShape: opaqueShape,
  };
})(window);

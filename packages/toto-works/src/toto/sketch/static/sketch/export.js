/* Sketch export and image import — everything that needs a canvas or the DOM.
 *
 * Ported from enigma's sketchExport.ts, browser-only (no Tauri branch), with
 * ONE deliberate deviation: the PNG rasterizer renders the whole serialized
 * SVG as an image instead of redrawing shape by shape. Enigma avoided that
 * because of WebKit-webview quirks (dimensionless SVGs rasterize 0×0, canvas
 * taint rules vary); here the document always gets explicit width/height
 * patched in before loading, external references are refused at parse time
 * so nothing can taint the canvas, and — decisively — the board can carry
 * OPAQUE imported elements (gradients, whole groups) that only a real SVG
 * renderer can draw. One renderer, no drift, imports included.
 */
(function (global) {
  "use strict";

  var M = global.SketchModel;

  function loadImage(dataUrl) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.src = dataUrl;
      // decode() rejects on a malformed payload instead of leaving a 0×0
      // image that would rasterize as a silent no-op.
      img.decode().then(function () { resolve(img); },
                        function () { reject(new Error("not-an-image")); });
    });
  }

  var PNG_SIZE_LIMIT = 1500000;

  function hasTransparency(ctx, w, h) {
    var data = ctx.getImageData(0, 0, w, h).data;
    for (var i = 3; i < data.length; i += 4) {
      if (data[i] !== 255) return true;
    }
    return false;
  }

  /* Crop-to-fill an arbitrary image onto the default board and re-encode.
   * The crop is baked in at import so every renderer draws the result at the
   * full board rect and none can disagree about framing. */
  function prepareBackgroundImage(dataUrl) {
    return loadImage(dataUrl).then(function (img) {
      var canvas = document.createElement("canvas");
      canvas.width = M.BOARD_W;
      canvas.height = M.BOARD_H;
      var ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("canvas-unavailable");

      var iw = img.naturalWidth || img.width;
      var ih = img.naturalHeight || img.height;
      if (!iw || !ih) throw new Error("not-an-image");
      var scale = Math.max(M.BOARD_W / iw, M.BOARD_H / ih);
      ctx.drawImage(img, (M.BOARD_W - iw * scale) / 2, (M.BOARD_H - ih * scale) / 2,
                    iw * scale, ih * scale);

      // PNG keeps alpha; JPEG is smaller but has none — and serializing a
      // transparent canvas to JPEG composites onto BLACK. So the fallback is
      // gated on whether this image actually uses transparency.
      var png = canvas.toDataURL("image/png");
      if (png.length <= PNG_SIZE_LIMIT || hasTransparency(ctx, M.BOARD_W, M.BOARD_H)) {
        return png;
      }
      return canvas.toDataURL("image/jpeg", 0.85);
    });
  }

  function backgroundImageFromFile(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result || "")); };
      reader.onerror = function () { reject(new Error("unreadable")); };
      reader.readAsDataURL(file);
    }).then(function (raw) {
      if (raw.indexOf("data:image/") !== 0) throw new Error("not-an-image");
      return prepareBackgroundImage(raw);
    }).then(function (prepared) {
      if (!M.validateBackgroundImage(prepared)) throw new Error("prepare-failed");
      return prepared;
    });
  }

  function browserDownload(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }

  /* Rasterize the whole document (opaque imports included) at board size. */
  function rasterize(svgText, width, height, scale) {
    scale = scale || 1;
    // Explicit dimensions on the root or the image decodes 0×0 — set our
    // known board size over whatever the root carries. Attribute-injecting
    // with a regex would DUPLICATE width/height (sketch roots carry both)
    // and duplicate attributes make the XML undecodable.
    var parsed = new DOMParser().parseFromString(svgText, "image/svg+xml");
    var root = parsed.documentElement;
    if (!root || root.nodeName.toLowerCase() !== "svg") {
      return Promise.reject(new Error("not-svg"));
    }
    root.setAttribute("width", String(width));
    root.setAttribute("height", String(height));
    var patched = new XMLSerializer().serializeToString(root);
    var blob = new Blob([patched], { type: "image/svg+xml" });
    var url = URL.createObjectURL(blob);
    return loadImage(url).then(function (img) {
      URL.revokeObjectURL(url);
      var canvas = document.createElement("canvas");
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      var ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("canvas-unavailable");
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL("image/png");
    }, function (err) {
      URL.revokeObjectURL(url);
      throw err;
    });
  }

  function stripDataUrl(dataUrl) {
    var comma = dataUrl.indexOf(",");
    return comma === -1 ? dataUrl : dataUrl.slice(comma + 1);
  }

  function exportPng(svgText, width, height, defaultName) {
    return rasterize(svgText, width, height, 1).then(function (dataUrl) {
      var bin = atob(stripDataUrl(dataUrl));
      var bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      browserDownload(new Blob([bytes], { type: "image/png" }), defaultName);
      return true;
    });
  }

  global.SketchExport = {
    prepareBackgroundImage: prepareBackgroundImage,
    backgroundImageFromFile: backgroundImageFromFile,
    rasterize: rasterize,
    exportPng: exportPng,
  };
})(window);

/* A zone's outline, drawn by hand (2026-10-06): click the map to add a
 * corner, drag a corner to move it, click a corner to take it out. One ring;
 * it is closed when it is saved. No plugin: Leaflet markers and one polygon.
 *
 * `createOutline` is the part with no map in it (the corners and what may be
 * done to them); `attach` binds one to a Leaflet map. The server checks the
 * outline again (toto.geography.shapes): at least three different corners,
 * at most 500, and no edge may cross another.
 *
 * ONE RING ON ONE COPY OF THE WORLD. Leaflet draws the world again to the
 * left and the right of itself, and a click on a copy reads 381 where the
 * place is at 21. A corner is therefore kept on the copy the first corner is
 * on (the whole turns between them are taken off), so a ring is never half
 * here and half a world away. Which copy that is does not matter here: the
 * map script brings the ring home as a whole when it is sent.
 */
(function (root) {
  "use strict";

  var MAX_CORNERS = 500;

  function round6(value) { return Math.round(Number(value) * 1e6) / 1e6; }

  function createOutline(initial) {
    var corners = (initial || []).map(function (c) { return [round6(c[0]), round6(c[1])]; });

    /* `lng` on the copy of the world the ring is on. */
    function beside(lng, index) {
      var first = corners.length && index !== 0 ? corners[0][1] : null;
      if (first === null) { return Number(lng); }
      return Number(lng) - 360 * Math.round((Number(lng) - first) / 360);
    }

    return {
      add: function (lat, lng) {
        if (corners.length >= MAX_CORNERS) { return false; }
        var corner = [round6(lat), round6(beside(lng))];
        var twice = corners.some(function (c) { return c[0] === corner[0] && c[1] === corner[1]; });
        if (twice) { return false; }
        corners.push(corner);
        return true;
      },
      move: function (index, lat, lng) {
        if (index < 0 || index >= corners.length) { return false; }
        corners[index] = [round6(lat), round6(beside(lng, index))];
        return true;
      },
      remove: function (index) {
        if (index < 0 || index >= corners.length) { return false; }
        corners.splice(index, 1);
        return true;
      },
      undo: function () { return corners.length ? !!corners.pop() : false; },
      reset: function () { corners.length = 0; },
      count: function () { return corners.length; },
      complete: function () { return corners.length >= 3; },
      /* The corners as the server takes them: [[lat, lng], …], ring open. */
      list: function () { return corners.map(function (c) { return [c[0], c[1]]; }); }
    };
  }

  function attach(map, L, options) {
    var outline = createOutline(options.corners);
    var layer = L.layerGroup();
    var active = false;

    function changed() { if (options.onChange) { options.onChange(outline.count()); } }

    function draw() {
      layer.clearLayers();
      var list = outline.list();
      if (list.length >= 3) { L.polygon(list, {weight: 2, fillOpacity: 0.12}).addTo(layer); }
      else if (list.length === 2) { L.polyline(list, {weight: 2}).addTo(layer); }
      list.forEach(function (corner, index) {
        var handle = L.marker(corner, {
          draggable: true,
          icon: L.divIcon({className: "geography-corner", iconSize: [18, 18], iconAnchor: [9, 9],
            html: '<span style="display:block;width:18px;height:18px;border-radius:9px;' +
                  'background:#fff;border:3px solid #2a81cb;box-sizing:border-box"></span>'})
        });
        handle.on("dragend", function () {
          var at = handle.getLatLng();
          outline.move(index, at.lat, at.lng);
          draw(); changed();
        });
        handle.on("click", function (event) {
          L.DomEvent.stopPropagation(event);
          outline.remove(index);
          draw(); changed();
        });
        handle.addTo(layer);
      });
    }

    map.on("click", function (event) {
      if (!active) { return; }
      if (outline.add(event.latlng.lat, event.latlng.lng)) { draw(); changed(); }
    });

    return {
      start: function () { active = true; layer.addTo(map); draw(); changed(); },
      stop: function () { active = false; map.removeLayer(layer); },
      /* Back to the corners it was given: what was drawn and not saved goes. */
      restore: function () { outline = createOutline(options.corners); draw(); changed(); },
      active: function () { return active; },
      undo: function () { outline.undo(); draw(); changed(); },
      reset: function () { outline.reset(); draw(); changed(); },
      corners: function () { return outline.list(); }
    };
  }

  var api = {createOutline: createOutline, attach: attach, MAX_CORNERS: MAX_CORNERS};
  root.GeographyZone = api;
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
})(typeof window !== "undefined" ? window : globalThis);

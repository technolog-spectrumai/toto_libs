/* The geography map (2026-10-06): a Leaflet map with a place-name search, a
 * route panel, temporary points, and the forms that save a point or a zone.
 *
 * Two halves. `createController` is the part with rules and no page in it:
 * what is sent, when, and under which `op`. It takes its `fetch` from the
 * caller, touches no DOM and no storage, and runs under node in
 * toto.geography.tests_map_js. `mount` is the page: it draws what the
 * controller holds with Leaflet (`L`, the page's own).
 *
 * THE RULES THE CONTROLLER KEEPS
 *  - A temporary point lives in this page's memory only: no request, no
 *    localStorage, no sessionStorage, no address bar. It is gone at reload.
 *  - Search hits and a calculated route live in the page only, too.
 *  - Every charged request carries an `op`, a UUID minted once per
 *    deliberate press. A press after an answer mints a new one. A press that
 *    repeats a request the server never answered (the network failed, a 5xx,
 *    a throttle) repeats its `op`, so a retry is never charged twice.
 *  - A route request holds two coordinate pairs and nothing else about its
 *    ends: no row id, no name. The browser sends the points it already holds.
 */
(function (root) {
  "use strict";

  function uuid() {
    var c = root.crypto;
    if (c && typeof c.randomUUID === "function") { return c.randomUUID(); }
    var bytes = new Uint8Array(16);
    if (c && typeof c.getRandomValues === "function") { c.getRandomValues(bytes); }
    else { for (var i = 0; i < 16; i++) { bytes[i] = Math.floor(Math.random() * 256); } }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    var hex = Array.prototype.map.call(bytes, function (b) {
      return (b + 0x100).toString(16).slice(1);
    }).join("");
    return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) + "-" +
           hex.slice(16, 20) + "-" + hex.slice(20);
  }

  function round6(value) { return Math.round(Number(value) * 1e6) / 1e6; }

  function pair(end) { return {lat: round6(end.lat), lng: round6(end.lng)}; }

  /* createController({fetch, urls, csrf, saved, mint})
   *   urls: {search, route, savePoint, clearPoint, saveZone, clearZone}
   *   saved: the saved points the page drew, [{kind, lat, lng, label}]      */
  function createController(options) {
    var send = options.fetch;
    var urls = options.urls || {};
    var mint = options.mint || uuid;
    var counter = 0;
    var state = {
      saved: (options.saved || []).slice(),
      temporary: [],
      hits: [],
      route: null,
      pending: {}
    };

    function post(url, body) {
      return send(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": options.csrf || "",
          "X-Requested-With": "XMLHttpRequest"
        },
        body: JSON.stringify(body)
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          return {ok: response.ok, status: response.status, data: data || {}};
        });
      }, function () {
        return {ok: false, status: 0, data: {}};
      });
    }

    /* One deliberate press of a charged control. `slot` names the control,
     * `body` is the request without its op. The op of an unanswered request
     * is kept and repeated while the body stays the same. */
    function press(slot, url, body) {
      var signature = JSON.stringify(body);
      var waiting = state.pending[slot];
      var op = (waiting && waiting.signature === signature) ? waiting.op : mint();
      state.pending[slot] = {signature: signature, op: op};
      var request = {};
      Object.keys(body).forEach(function (key) { request[key] = body[key]; });
      request.op = op;
      return post(url, request).then(function (answer) {
        var unanswered = answer.status === 0 || answer.status >= 500 || answer.status === 429;
        if (!unanswered && state.pending[slot] && state.pending[slot].op === op) {
          delete state.pending[slot];
        }
        answer.op = op;
        return answer;
      });
    }

    return {
      state: state,

      /* A point the member clicked. Kept here, sent nowhere. */
      placeTemporary: function (lat, lng, label) {
        counter += 1;
        var point = {id: "t" + counter, kind: "temporary", lat: round6(lat), lng: round6(lng),
                     label: label || ""};
        state.temporary.push(point);
        return point;
      },

      removeTemporary: function (id) {
        state.temporary = state.temporary.filter(function (point) { return point.id !== id; });
      },

      clearTemporary: function () { state.temporary = []; },

      /* Everything a route may start or end at: the saved points the page
       * drew, the search hits and the temporary points. */
      ends: function () {
        return state.saved.concat(state.hits, state.temporary);
      },

      search: function (query) {
        var text = String(query || "").replace(/\s+/g, " ").trim();
        return press("search", urls.search, {q: text}).then(function (answer) {
          if (answer.ok) {
            state.hits = (answer.data.results || []).map(function (hit, index) {
              return {id: "h" + index, kind: "hit", lat: hit.lat, lng: hit.lng,
                      label: hit.label || ""};
            });
          }
          return answer;
        });
      },

      /* `from` and `to` are any two of `ends()`; only their coordinates go. */
      route: function (from, to, mode) {
        var body = {from: pair(from), to: pair(to), mode: mode};
        return press("route", urls.route, body).then(function (answer) {
          state.route = (answer.ok && answer.data.line) ? answer.data : null;
          return answer;
        });
      },

      forgetRoute: function () { state.route = null; },

      savePoint: function (point) {
        return press("point", urls.savePoint, {
          lat: round6(point.lat), lng: round6(point.lng),
          name: point.name || "", note: point.note || ""
        });
      },

      clearPoint: function () { return post(urls.clearPoint, {}); },

      saveZone: function (zone) {
        return press("zone", urls.saveZone, {
          name: zone.name || "", description: zone.description || "",
          outline: (zone.outline || []).map(function (corner) {
            return [round6(corner[0]), round6(corner[1])];
          })
        });
      },

      clearZone: function () { return post(urls.clearZone, {}); }
    };
  }

  /* ---------------------------------------------------------------------
   * The page
   * ------------------------------------------------------------------- */

  function mount(box) {
    if (box.dataset.geographyMounted) { return null; }
    box.dataset.geographyMounted = "1";
    var L = root.L;
    var config = JSON.parse(document.getElementById(box.dataset.config).textContent);
    var texts = config.texts || {};
    var q = function (name) { return box.querySelector('[data-geo="' + name + '"]'); };

    var controller = createController({
      fetch: root.fetch.bind(root), urls: config.urls, csrf: box.dataset.csrf,
      saved: config.points || []
    });

    var start = config.center || [52.2297, 21.0122];
    var map = L.map(q("map")).setView(start, config.center ? (config.zoom || 13) : 5);
    root.totoTileLayer(map);
    var layers = {saved: L.layerGroup().addTo(map), hits: L.layerGroup().addTo(map),
                  temporary: L.layerGroup().addTo(map), route: L.layerGroup().addTo(map),
                  zone: L.layerGroup().addTo(map)};
    var pick = null;        // the draggable pin of the point being edited
    var zoneEditor = null;

    function say(name, text, bad) {
      var note = q(name);
      if (!note) { return; }
      note.textContent = text || "";
      note.classList.toggle("hidden", !text);
      note.classList.toggle("font-semibold", !!bad);
    }

    function refusal(answer, fallback) {
      return (answer.data && answer.data.error) || fallback || texts.failed || "";
    }

    function marker(point, options) {
      var made = L.marker([point.lat, point.lng], Object.assign({icon: root.classicPin()},
                                                                options || {}));
      if (point.label) {
        /* As text, never as markup: Leaflet draws a string as HTML, and a
         * label is a name a member typed or a place name from outside. */
        var tip = document.createElement("span");
        tip.textContent = point.label;
        made.bindTooltip(tip);
      }
      return made;
    }

    function drawSaved() {
      layers.saved.clearLayers();
      controller.state.saved.forEach(function (point) {
        if (pick && point.kind === config.edit_kind) { return; }
        marker(point).addTo(layers.saved);
      });
    }

    function drawZone(outline) {
      layers.zone.clearLayers();
      if (outline && outline.length >= 3) {
        L.polygon(outline, {weight: 2, fillOpacity: 0.12}).addTo(layers.zone);
      }
    }

    function drawTemporary() {
      layers.temporary.clearLayers();
      controller.state.temporary.forEach(function (point) {
        var made = L.circleMarker([point.lat, point.lng], {radius: 7, weight: 2});
        made.bindTooltip(texts.temporary || "");
        made.on("click", function (event) {
          L.DomEvent.stopPropagation(event);
          controller.removeTemporary(point.id);
          drawTemporary();
          fillEnds();
        });
        made.addTo(layers.temporary);
      });
    }

    function drawHits() {
      layers.hits.clearLayers();
      var list = q("results");
      if (list) { list.textContent = ""; }
      controller.state.hits.forEach(function (hit) {
        var made = marker(hit, {opacity: 0.75}).addTo(layers.hits);
        made.on("click", function () { choose(hit); });
        if (list) {
          var item = document.createElement("li");
          var button = document.createElement("button");
          button.type = "button";
          button.textContent = hit.label;
          button.className = "block w-full px-3 py-2 text-left hover:opacity-70";
          button.addEventListener("click", function () { choose(hit); });
          item.appendChild(button);
          list.appendChild(item);
        }
      });
      if (list) { list.classList.toggle("hidden", !controller.state.hits.length); }
    }

    /* A search hit, chosen: the map goes there; where a point is being
     * edited the pin goes there too, and becomes the address only at Save. */
    function choose(hit) {
      map.setView([hit.lat, hit.lng], 14);
      if (config.can_edit_point) { placePick(hit.lat, hit.lng); }
    }

    function fillEnds() {
      ["from", "to"].forEach(function (name, which) {
        var select = q(name);
        if (!select) { return; }
        var kept = select.value;
        select.textContent = "";
        controller.ends().forEach(function (end, index) {
          var option = document.createElement("option");
          option.value = String(index);
          option.textContent = end.label ||
            ((end.kind === "temporary" ? (texts.temporary || "") + " " : "") +
             end.lat.toFixed(4) + ", " + end.lng.toFixed(4));
          select.appendChild(option);
        });
        if (kept && select.querySelector('option[value="' + kept + '"]')) { select.value = kept; }
        else if (select.options.length > which) { select.selectedIndex = which; }
      });
      var go = q("route-go");
      if (go) { go.disabled = controller.ends().length < 2; }
    }

    function placePick(lat, lng) {
      if (pick) { pick.setLatLng([lat, lng]); }
      else {
        pick = L.marker([lat, lng], {icon: root.classicPin(), draggable: true}).addTo(map);
        drawSaved();
      }
      var save = q("save-point");
      if (save) { save.disabled = false; }
    }

    /* --- search ------------------------------------------------------- */
    var box_q = q("q"), go = q("search-go");
    if (box_q && go) {
      var run = function () {
        var text = box_q.value.trim();
        if (!text || go.disabled) { return; }
        go.disabled = true;
        say("search-note", "");
        controller.search(text).then(function (answer) {
          go.disabled = false;
          if (!answer.ok) {
            controller.state.hits = [];
            drawHits(); fillEnds();
            say("search-note", refusal(answer), true);
            return;
          }
          drawHits(); fillEnds();
          if (!controller.state.hits.length) { say("search-note", texts.nothing_found); return; }
          var first = controller.state.hits[0];
          map.setView([first.lat, first.lng], 13);
        });
      };
      /* A search is charged: on Enter or the button, never while typing. */
      box_q.addEventListener("keydown", function (event) {
        if (event.key === "Enter") { event.preventDefault(); run(); }
      });
      go.addEventListener("click", run);
    }

    /* --- the map's own click: a temporary point, or the pin being edited -- */
    map.on("click", function (event) {
      if (zoneEditor && zoneEditor.active()) { return; }
      if (config.can_edit_point && q("edit-point") && !q("edit-point").classList.contains("hidden")) {
        placePick(event.latlng.lat, event.latlng.lng);
        return;
      }
      controller.placeTemporary(event.latlng.lat, event.latlng.lng);
      drawTemporary();
      fillEnds();
    });

    /* --- route -------------------------------------------------------- */
    var routeGo = q("route-go");
    if (routeGo) {
      routeGo.addEventListener("click", function () {
        var ends = controller.ends();
        var from = ends[Number(q("from").value)], to = ends[Number(q("to").value)];
        if (!from || !to) { return; }
        routeGo.disabled = true;
        say("route-note", "");
        controller.route(from, to, q("mode").value).then(function (answer) {
          routeGo.disabled = false;
          layers.route.clearLayers();
          if (!answer.ok) { say("route-note", refusal(answer), true); return; }
          if (!answer.data.line) { say("route-note", texts.no_route); return; }
          var line = L.geoJSON({type: "Feature", properties: {}, geometry: answer.data.line},
                               {style: {weight: 5, opacity: 0.8}}).addTo(layers.route);
          map.fitBounds(line.getBounds(), {padding: [24, 24]});
          say("route-note", (texts.route_summary || "%(km)s km, %(min)s min")
            .replace("%(km)s", answer.data.distance_km)
            .replace("%(min)s", answer.data.duration_min));
        });
      });
    }

    /* --- saving a point ------------------------------------------------ */
    var savePoint = q("save-point");
    if (config.can_edit_point && savePoint) {
      var saved = (config.points || []).filter(function (p) { return p.kind === config.edit_kind; })[0];
      var open = q("open-point");
      if (open) {
        open.addEventListener("click", function () {
          q("edit-point").classList.remove("hidden");
          if (saved && !pick) { placePick(saved.lat, saved.lng); }
        });
      }
      savePoint.disabled = !saved;
      savePoint.addEventListener("click", function () {
        if (!pick) { say("point-note", texts.place_first, true); return; }
        var at = pick.getLatLng();
        savePoint.disabled = true;
        controller.savePoint({lat: at.lat, lng: at.lng, name: q("point-name").value,
                              note: q("point-note-text").value}).then(function (answer) {
          savePoint.disabled = false;
          if (!answer.ok) { say("point-note", refusal(answer), true); return; }
          root.location.reload();
        });
      });
      var clearPoint = q("clear-point");
      if (clearPoint) {
        clearPoint.addEventListener("click", function () {
          controller.clearPoint().then(function (answer) {
            if (!answer.ok) { say("point-note", refusal(answer), true); return; }
            root.location.reload();
          });
        });
      }
    }

    /* --- saving a zone -------------------------------------------------- */
    var saveZone = q("save-zone");
    if (config.can_edit_zone && saveZone && root.GeographyZone) {
      zoneEditor = root.GeographyZone.attach(map, L, {
        corners: config.zone ? config.zone.outline : [],
        onChange: function (count) {
          saveZone.disabled = count < 3;
          say("zone-count", (texts.corners || "%(n)s").replace("%(n)s", count));
        }
      });
      var draw = q("draw-zone");
      if (draw) {
        draw.addEventListener("click", function () {
          q("edit-zone").classList.remove("hidden");
          layers.zone.clearLayers();
          zoneEditor.start();
        });
      }
      var undo = q("zone-undo");
      if (undo) { undo.addEventListener("click", function () { zoneEditor.undo(); }); }
      var restart = q("zone-restart");
      if (restart) { restart.addEventListener("click", function () { zoneEditor.reset(); }); }
      saveZone.addEventListener("click", function () {
        saveZone.disabled = true;
        controller.saveZone({name: q("zone-name").value,
                             description: q("zone-description").value,
                             outline: zoneEditor.corners()}).then(function (answer) {
          saveZone.disabled = false;
          if (!answer.ok) { say("zone-note", refusal(answer), true); return; }
          root.location.reload();
        });
      });
      var clearZone = q("clear-zone");
      if (clearZone) {
        clearZone.addEventListener("click", function () {
          controller.clearZone().then(function (answer) {
            if (!answer.ok) { say("zone-note", refusal(answer), true); return; }
            root.location.reload();
          });
        });
      }
    }

    drawSaved();
    drawZone(config.zone ? config.zone.outline : null);
    fillEnds();
    if (config.zone && config.zone.outline && config.zone.outline.length && !config.center) {
      map.fitBounds(L.polygon(config.zone.outline).getBounds(), {padding: [24, 24]});
    }
    /* Leaflet cannot size itself inside a box that was hidden at first. */
    setTimeout(function () { map.invalidateSize(); }, 80);
    return {map: map, controller: controller};
  }

  var api = {createController: createController, mount: mount, uuid: uuid};
  root.GeographyMap = api;
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
  if (root.document && root.document.addEventListener) {
    var all = function () {
      Array.prototype.forEach.call(
        root.document.querySelectorAll("[data-geography-map]"), mount);
    };
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", all);
    } else { all(); }
  }
})(typeof window !== "undefined" ? window : globalThis);

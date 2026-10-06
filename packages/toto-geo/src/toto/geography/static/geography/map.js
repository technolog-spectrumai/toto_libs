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
 *  - A page that was given no route door (`urls.route`; only a page that
 *    asked for route search has one) sends no route request, whatever is
 *    called: the profile's map and a community's have no route search.
 *  - Each end of the next route is remembered by the id of the point it
 *    means (an id of this page: `s0` a saved point, `h7` a search hit, `t8` a
 *    temporary point; a number is used once), never by a place in a list.
 *    When that point leaves the map (a new search replaces the hits, a
 *    temporary point is clicked away) the end is cleared and stays cleared
 *    until the member chooses again: no other point steps into its place, so
 *    a charged route never runs between two points nobody chose.
 *  - A longitude leaves the page between -180 and 180. Leaflet draws the
 *    world again to the left and to the right of itself, and a click on a
 *    copy reads 381 where the place is at 21: every door would refuse it. A
 *    point is brought home by itself; a zone's outline is moved as a whole,
 *    by the turn of its first corner, so a ring is never torn in two.
 *
 * THE RULES THE PAGE KEEPS
 *  - One click, one meaning. While the zone form is open a click is a
 *    corner; while the point form is open it moves the pin; otherwise it is
 *    a temporary point. The two forms are never open together, and each has
 *    a Cancel that puts back what is saved and gives the click back.
 *  - A search hit that is picked moves the pin only while the point form is
 *    open. With the form closed it moves the map and nothing else.
 *  - A point's and a zone's words are typed in a dialog and nowhere else.
 *    The tool beside the map holds the geometry: Continue opens the dialog,
 *    Save there sends what the form always sent, to the same door under the
 *    same op rule. A refusal is said in the dialog and leaves it open with
 *    what was typed; Cancel there closes it and leaves the pin, or the
 *    corners, on the map.
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

  /* A longitude as the doors take it. One that is already between -180 and
   * 180 is left as it is, to the last digit. */
  function wrapLng(lng) {
    var value = Number(lng);
    if (!isFinite(value) || (value >= -180 && value <= 180)) { return value; }
    return ((value + 180) % 360 + 360) % 360 - 180;
  }

  function pair(end) { return {lat: round6(end.lat), lng: round6(wrapLng(end.lng))}; }

  /* A zone's corners, moved together by as many whole turns as bring the
   * first one home. A ring that still leaves -180..180 after that lies
   * across the date line, and the server says so. */
  function homeRing(outline) {
    var corners = outline || [];
    if (!corners.length) { return []; }
    var turn = Number(corners[0][1]) - wrapLng(corners[0][1]);
    return corners.map(function (corner) {
      return [round6(corner[0]), round6(Number(corner[1]) - turn)];
    });
  }

  /* createController({fetch, urls, csrf, saved, mint})
   *   urls: {search, route, savePoint, clearPoint, saveZone, clearZone}
   *   saved: the saved points the page drew, [{kind, lat, lng, label}]      */
  function createController(options) {
    var send = options.fetch;
    var urls = options.urls || {};
    var mint = options.mint || uuid;
    var counter = 0;
    var state = {
      /* Only what is drawn is taken from the page, under an id of this
       * page's own: the saved points do not change while the page lives. */
      saved: (options.saved || []).map(function (point, index) {
        return {id: "s" + index, kind: point.kind, lat: point.lat, lng: point.lng,
                label: point.label || ""};
      }),
      temporary: [],
      hits: [],
      route: null,
      pending: {},
      /* The two ends of the next route: the id of the point each one means,
       * and whether the point it meant has left the map. */
      chosen: {from: {id: null, gone: false}, to: {id: null, gone: false}}
    };

    function everyEnd() { return state.saved.concat(state.hits, state.temporary); }

    function find(id) {
      var all = everyEnd();
      for (var i = 0; i < all.length; i++) { if (all[i].id === id) { return all[i]; } }
      return null;
    }

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
        var point = {id: "t" + counter, kind: "temporary", lat: round6(lat),
                     lng: round6(wrapLng(lng)), label: label || ""};
        state.temporary.push(point);
        return point;
      },

      removeTemporary: function (id) {
        state.temporary = state.temporary.filter(function (point) { return point.id !== id; });
      },

      clearTemporary: function () { state.temporary = []; },

      /* Everything a route may start or end at: the saved points the page
       * drew, the search hits and the temporary points. */
      ends: everyEnd,

      /* The member chose `id` (one of `ends()`) as the route's "from" or
       * "to". An id that names no point on the map chooses nothing. */
      chooseEnd: function (which, id) {
        var end = find(id);
        state.chosen[which] = {id: end ? end.id : null, gone: false};
      },

      /* The two ends as they stand: `{from, to, gone: {from, to}}`, each
       * end a point or null.
       *
       * An end whose point has left the map is cleared and marked gone; it
       * stays so until the member chooses again. An end that never held a
       * choice takes the first point the other end does not hold, and keeps
       * it: what the page showed as chosen stays chosen, whatever is added
       * to the map afterwards. */
      selection: function () {
        var all = everyEnd();
        ["from", "to"].forEach(function (which) {
          var end = state.chosen[which];
          if (end.id !== null && !find(end.id)) { end.id = null; end.gone = true; }
        });
        ["from", "to"].forEach(function (which) {
          var end = state.chosen[which];
          var other = state.chosen[which === "from" ? "to" : "from"];
          if (end.id !== null || end.gone) { return; }
          for (var i = 0; i < all.length; i++) {
            if (all[i].id !== other.id) { end.id = all[i].id; return; }
          }
        });
        return {from: find(state.chosen.from.id), to: find(state.chosen.to.id),
                gone: {from: state.chosen.from.gone, to: state.chosen.to.gone}};
      },

      search: function (query) {
        var text = String(query || "").replace(/\s+/g, " ").trim();
        return press("search", urls.search, {q: text}).then(function (answer) {
          if (answer.ok) {
            /* New hits, new ids: an end that meant a hit of the search
             * before this one finds its point gone. */
            state.hits = (answer.data.results || []).map(function (hit) {
              counter += 1;
              return {id: "h" + counter, kind: "hit", lat: hit.lat, lng: hit.lng,
                      label: hit.label || ""};
            });
          }
          return answer;
        });
      },

      /* `from` and `to` are any two of `ends()`; only their coordinates go.
       * On a page with no route door nothing is sent and nothing is found. */
      route: function (from, to, mode) {
        if (!urls.route) {
          state.route = null;
          return Promise.resolve({ok: false, status: 0, data: {}, op: null});
        }
        var body = {from: pair(from), to: pair(to), mode: mode};
        return press("route", urls.route, body).then(function (answer) {
          state.route = (answer.ok && answer.data.line) ? answer.data : null;
          return answer;
        });
      },

      /* The route between the two chosen ends. Null, and no request, while
       * one of them is not chosen. */
      routeChosen: function (mode) {
        if (!urls.route) { return null; }
        var now = this.selection();
        if (!now.from || !now.to) { return null; }
        return this.route(now.from, now.to, mode);
      },

      forgetRoute: function () { state.route = null; },

      savePoint: function (point) {
        return press("point", urls.savePoint, {
          lat: round6(point.lat), lng: round6(wrapLng(point.lng)),
          name: point.name || "", note: point.note || ""
        });
      },

      clearPoint: function () { return post(urls.clearPoint, {}); },

      saveZone: function (zone) {
        return press("zone", urls.saveZone, {
          name: zone.name || "", description: zone.description || "",
          outline: homeRing(zone.outline)
        });
      },

      clearZone: function () { return post(urls.clearZone, {}); }
    };
  }

  /* ---------------------------------------------------------------------
   * The page
   * ------------------------------------------------------------------- */

  /* dialog(el, onDismiss) -> {open(), close(), isOpen()}
   * One modal of the page. The element is the library's dialog (the
   * template: role="dialog", x-show and x-trap on its own `open`, Escape):
   * this tells it to open or close with the event "geography-dialog", and
   * hears "geography-dismiss" when the member leaves it by Escape, the
   * backdrop or the X. Leaving closes the dialog and nothing else: what is
   * on the map stays. `data-open` says the same to whoever reads the page. */
  function dialog(el, onDismiss) {
    var open = false;
    function set(on) {
      open = !!on;
      el.dataset.open = open ? "1" : "";
      el.dispatchEvent(new root.CustomEvent("geography-dialog", {detail: {open: open}}));
    }
    el.addEventListener("geography-dismiss", function () {
      if (!open) { return; }
      set(false);
      if (onDismiss) { onDismiss(); }
    });
    return {open: function () { set(true); }, close: function () { if (open) { set(false); } },
            isOpen: function () { return open; }};
  }

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
    /* The two dialogs, where the page has them: the words of the point and
     * of the zone are typed there and nowhere else. */
    var pointDialog = q("point-dialog") ? dialog(q("point-dialog")) : null;
    var zoneDialog = q("zone-dialog") ? dialog(q("zone-dialog")) : null;

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

    /* A tooltip's content as text, never as markup: Leaflet draws a string
     * as HTML, and a label is a name a member typed or a place name from
     * outside. */
    function asText(text) {
      var tip = document.createElement("span");
      tip.textContent = text;
      return tip;
    }

    function marker(point, options) {
      var made = L.marker([point.lat, point.lng], Object.assign({icon: root.classicPin()},
                                                                options || {}));
      if (point.label) { made.bindTooltip(asText(point.label)); }
      return made;
    }

    /* Is the point form open? Then a click and a picked hit move the pin. */
    function editingPoint() {
      var panel = q("edit-point");
      return !!(config.can_edit_point && panel && !panel.classList.contains("hidden"));
    }

    /* A click on a copy of the world: the view goes back to the world the
     * points are drawn on, so what the click placed is in sight. */
    function comeHome(lng) {
      if (wrapLng(lng) === Number(lng)) { return; }
      var centre = map.getCenter();
      map.setView([centre.lat, wrapLng(centre.lng)], map.getZoom(), {animate: false});
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
        made.bindTooltip(asText(texts.temporary || ""));
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

    /* A search hit, chosen: the map goes there. While the point form is
     * open the pin goes there too, and becomes the address only at Save;
     * with the form closed no pin moves, so a later Save cannot move an
     * address its owner never meant to move. */
    function choose(hit) {
      map.setView([hit.lat, hit.lng], 14);
      if (editingPoint()) { placePick(hit.lat, hit.lng); }
    }

    /* The two selects of the route panel, drawn from what the controller
     * holds: each option's value is its point's id. An end with no point is
     * an empty first line that says why, and Find route waits: while a
     * chosen point is gone the button itself says to choose again. */
    var goLabel = q("route-go-label");
    var goText = goLabel ? goLabel.textContent : "";
    var routed = !!(config.urls && config.urls.route && q("route-go"));

    function fillEnds() {
      if (!routed) { return; }       // a page with no route search has no ends
      var now = controller.selection();
      ["from", "to"].forEach(function (name) {
        var select = q(name);
        if (!select) { return; }
        select.textContent = "";
        if (!now[name]) {
          var none = document.createElement("option");
          none.value = "";
          none.disabled = true;
          none.textContent = (now.gone[name] ? texts.end_gone : texts.choose_end) || "";
          select.appendChild(none);
        }
        controller.ends().forEach(function (end) {
          var option = document.createElement("option");
          option.value = end.id;
          option.textContent = end.label ||
            ((end.kind === "temporary" ? (texts.temporary || "") + " " : "") +
             end.lat.toFixed(4) + ", " + end.lng.toFixed(4));
          select.appendChild(option);
        });
        select.value = now[name] ? now[name].id : "";
      });
      var go = q("route-go");
      if (go) { go.disabled = !(now.from && now.to); }
      if (goLabel) {
        goLabel.textContent = ((now.gone.from || now.gone.to) && texts.choose_again) || goText;
      }
    }

    ["from", "to"].forEach(function (name) {
      var select = q(name);
      if (!routed || !select) { return; }
      select.addEventListener("change", function () {
        controller.chooseEnd(name, select.value);
        fillEnds();
      });
    });

    function placePick(lat, lng) {
      lng = wrapLng(lng);
      if (pick) { pick.setLatLng([lat, lng]); }
      else {
        pick = L.marker([lat, lng], {icon: root.classicPin(), draggable: true}).addTo(map);
        drawSaved();
      }
      var next = q("point-continue");
      if (next) { next.disabled = false; }
    }

    /* The saved point of the kind this page edits, if there is one. */
    var savedPoint = (config.points || []).filter(function (p) {
      return p.kind === config.edit_kind;
    })[0];

    /* Close the point tool: its dialog too, the pin that was not saved
     * goes, the saved one is drawn again. */
    function closePoint() {
      var panel = q("edit-point");
      if (panel) { panel.classList.add("hidden"); }
      if (pointDialog) { pointDialog.close(); }
      if (pick) { map.removeLayer(pick); pick = null; }
      drawSaved();
      var next = q("point-continue");
      if (next) { next.disabled = true; }
      say("point-status", "");
      say("point-note", "");
    }

    /* Close the zone tool: its dialog too, the editor stops and goes back
     * to the saved outline, which is drawn again. */
    function closeZone() {
      var panel = q("edit-zone");
      if (panel) { panel.classList.add("hidden"); }
      if (zoneDialog) { zoneDialog.close(); }
      if (zoneEditor) { zoneEditor.stop(); zoneEditor.restore(); }
      drawZone(config.zone ? config.zone.outline : null);
      say("zone-status", "");
      say("zone-note", "");
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
      if (zoneEditor && zoneEditor.active()) { return; }      // a corner: the editor's own
      if (editingPoint()) { placePick(event.latlng.lat, event.latlng.lng); }
      else {
        controller.placeTemporary(event.latlng.lat, event.latlng.lng);
        drawTemporary();
        fillEnds();
      }
      comeHome(event.latlng.lng);
    });

    /* --- route -------------------------------------------------------- */
    var routeGo = q("route-go");
    if (routed) {
      routeGo.addEventListener("click", function () {
        var asked = controller.routeChosen(q("mode").value);
        if (!asked) { fillEnds(); return; }
        routeGo.disabled = true;
        say("route-note", "");
        asked.then(function (answer) {
          fillEnds();      // the button again, by the ends as they stand now
          layers.route.clearLayers();
          if (!answer.ok) { say("route-note", refusal(answer), true); return; }
          /* "No route" is the door's own word, a line that is null. An
           * answer that could not be read is not that, and may have been
           * charged: it never says "nothing was charged". */
          if (answer.data.line === null) { say("route-note", texts.no_route); return; }
          if (!answer.data.line) { say("route-note", texts.failed, true); return; }
          var line = L.geoJSON({type: "Feature", properties: {}, geometry: answer.data.line},
                               {style: {weight: 5, opacity: 0.8}}).addTo(layers.route);
          map.fitBounds(line.getBounds(), {padding: [24, 24]});
          say("route-note", (texts.route_summary || "%(km)s km, %(min)s min")
            .replace("%(km)s", answer.data.distance_km)
            .replace("%(min)s", answer.data.duration_min));
        });
      });
    }

    /* --- saving a point: placed on the map, named in the dialog ---------- */
    var savePoint = q("save-point"), pointContinue = q("point-continue");
    if (config.can_edit_point && savePoint && pointContinue && pointDialog) {
      var open = q("open-point");
      if (open) {
        open.addEventListener("click", function () {
          closeZone();
          q("edit-point").classList.remove("hidden");
          if (savedPoint && !pick) { placePick(savedPoint.lat, savedPoint.lng); }
        });
      }
      var closePointButton = q("close-point");
      if (closePointButton) { closePointButton.addEventListener("click", closePoint); }
      pointContinue.disabled = true;      // until a pin is on the map
      pointContinue.addEventListener("click", function () {
        if (!pick) { say("point-status", texts.place_first, true); return; }
        say("point-status", "");
        say("point-note", "");
        pointDialog.open();
      });
      /* Cancel in the dialog: back to the map, the pin where it was. */
      var cancelPoint = q("point-dialog-cancel");
      if (cancelPoint) { cancelPoint.addEventListener("click", function () { pointDialog.close(); }); }
      savePoint.addEventListener("click", function () {
        if (!pick) { say("point-note", texts.place_first, true); return; }
        var at = pick.getLatLng();
        savePoint.disabled = true;
        controller.savePoint({lat: at.lat, lng: at.lng, name: q("point-name").value,
                              note: q("point-note-text").value}).then(function (answer) {
          savePoint.disabled = false;
          /* Refused: said in the dialog, which stays open with what was typed. */
          if (!answer.ok) { say("point-note", refusal(answer), true); return; }
          root.location.reload();
        });
      });
      var clearPoint = q("clear-point");
      if (clearPoint) {
        clearPoint.addEventListener("click", function () {
          controller.clearPoint().then(function (answer) {
            if (!answer.ok) { say("point-status", refusal(answer), true); return; }
            root.location.reload();
          });
        });
      }
    }

    /* --- saving a zone: drawn on the map, named in the dialog ------------- */
    var saveZone = q("save-zone"), zoneContinue = q("zone-continue");
    if (config.can_edit_zone && saveZone && zoneContinue && zoneDialog && root.GeographyZone) {
      zoneEditor = root.GeographyZone.attach(map, L, {
        corners: config.zone ? config.zone.outline : [],
        onChange: function (count) {
          zoneContinue.disabled = count < 3;
          say("zone-count", (texts.corners || "%(n)s").replace("%(n)s", count));
        }
      });
      var draw = q("draw-zone");
      if (draw) {
        draw.addEventListener("click", function () {
          closePoint();
          q("edit-zone").classList.remove("hidden");
          layers.zone.clearLayers();
          zoneEditor.start();
        });
      }
      var closeZoneButton = q("close-zone");
      if (closeZoneButton) { closeZoneButton.addEventListener("click", closeZone); }
      var undo = q("zone-undo");
      if (undo) { undo.addEventListener("click", function () { zoneEditor.undo(); }); }
      var restart = q("zone-restart");
      if (restart) { restart.addEventListener("click", function () { zoneEditor.reset(); }); }
      zoneContinue.addEventListener("click", function () {
        if (zoneEditor.corners().length < 3) { return; }
        say("zone-status", "");
        say("zone-note", "");
        zoneDialog.open();
      });
      /* Cancel in the dialog: back to the map, the corners where they were. */
      var cancelZone = q("zone-dialog-cancel");
      if (cancelZone) { cancelZone.addEventListener("click", function () { zoneDialog.close(); }); }
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
            if (!answer.ok) { say("zone-status", refusal(answer), true); return; }
            root.location.reload();
          });
        });
      }
    }

    drawSaved();
    drawZone(config.zone ? config.zone.outline : null);
    /* More zones the page was handed, drawn and never edited (a community's
     * members' zones on its page): each with its name as text. */
    (config.zones || []).forEach(function (zone) {
      if (!zone.outline || zone.outline.length < 3) { return; }
      var shape = L.polygon(zone.outline, {weight: 2, fillOpacity: 0.08, dashArray: "6 4"});
      if (zone.label) { shape.bindTooltip(asText(zone.label)); }
      shape.addTo(map);
    });
    fillEnds();
    if (config.zone && config.zone.outline && config.zone.outline.length && !config.center) {
      map.fitBounds(L.polygon(config.zone.outline).getBounds(), {padding: [24, 24]});
    }
    /* Leaflet cannot size itself inside a box that was hidden at first. */
    setTimeout(function () { map.invalidateSize(); }, 80);
    return {map: map, controller: controller};
  }

  var api = {createController: createController, mount: mount, uuid: uuid, wrapLng: wrapLng,
             dialog: dialog};
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

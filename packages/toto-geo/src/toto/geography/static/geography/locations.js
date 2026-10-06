/* The Locations page (stage 64, 2026-10-06): everything on the map this
 * member may see, together, with an index beside it.
 *
 * Two halves, as map.js has. The functions at the top have no page in them
 * (what is near what, what a filter lets through, one press of a charged
 * control) and run under node in toto.geography.tests_locations_js. `mount`
 * is the page.
 *
 * THE RULES KEPT HERE
 *  - Every name, note, label and comment is written as TEXT (textContent; a
 *    Leaflet tooltip is given a DOM node). The one piece of HTML put into the
 *    page is a row's details, which the server's template has escaped.
 *  - "Search nearby" is worked out HERE, from the rows the page was handed:
 *    no request is made, so a centre (a click, a temporary point, a row, a
 *    search hit) is never sent anywhere, and nothing is charged.
 *  - A temporary point lives in this page's memory only (map.js's
 *    controller keeps it): it leaves the browser only as an end of a route
 *    the member asks for.
 *  - A click on the map does nothing by itself: it OFFERS a temporary point,
 *    a community pin, or a centre for nearby.
 *  - Every charged request carries an `op` minted once per deliberate press
 *    and repeated only for a request the server never answered.
 *  - Route search is map.js's controller, unchanged: two coordinate pairs
 *    and a mode, nothing else; the line is drawn and forgotten.
 */
(function (root) {
  "use strict";

  var EARTH_KM = 6371.0088;

  function rad(value) { return Number(value) * Math.PI / 180; }

  function round6(value) { return Math.round(Number(value) * 1e6) / 1e6; }

  /* A longitude on the one world the server keeps: -180..180. */
  function wrapLng(lng) {
    var value = Number(lng);
    if (value >= -180 && value <= 180) { return value; }
    return ((value + 180) % 360 + 360) % 360 - 180;
  }

  /* A ring drawn on a repeated copy of the world, moved home as a whole. */
  function homeRing(outline) {
    var corners = outline || [];
    if (!corners.length) { return []; }
    var turn = Number(corners[0][1]) - wrapLng(corners[0][1]);
    return corners.map(function (c) { return [round6(c[0]), round6(Number(c[1]) - turn)]; });
  }

  function haversineKm(a, b) {
    var dLat = rad(b.lat - a.lat), dLng = rad(b.lng - a.lng);
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return 2 * EARTH_KM * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  /* Is the point inside the ring `[[lat, lng], …]` (even-odd)? */
  function insideRing(point, ring) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var yi = ring[i][0], xi = ring[i][1], yj = ring[j][0], xj = ring[j][1];
      var crosses = ((yi > point.lat) !== (yj > point.lat)) &&
        (point.lng < (xj - xi) * (point.lat - yi) / ((yj - yi) || 1e-12) + xi);
      if (crosses) { inside = !inside; }
    }
    return inside;
  }

  /* The distance in km from a point to the edge a-b, on a flat sheet laid
   * at the point: good to well under a percent at the radii offered. */
  function edgeKm(point, a, b) {
    var k = Math.cos(rad(point.lat));
    var ax = (a[1] - point.lng) * k, ay = a[0] - point.lat;
    var bx = (b[1] - point.lng) * k, by = b[0] - point.lat;
    var dx = bx - ax, dy = by - ay;
    var length = dx * dx + dy * dy;
    var t = length ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / length)) : 0;
    var nearest = {lat: point.lat + ay + t * dy, lng: point.lng + (ax + t * dx) / (k || 1e-12)};
    return haversineKm(point, nearest);
  }

  /* How far a zone is from a centre: 0 when the centre is inside it,
   * otherwise the distance to its nearest edge. */
  function zoneKm(centre, outline) {
    if (!outline || outline.length < 3) { return Infinity; }
    if (insideRing(centre, outline)) { return 0; }
    var best = Infinity;
    for (var i = 0; i < outline.length; i++) {
      best = Math.min(best, edgeKm(centre, outline[i], outline[(i + 1) % outline.length]));
    }
    return best;
  }

  /* How far a row is from a centre, in km. */
  function rowKm(centre, row) {
    if (row.outline) { return zoneKm(centre, row.outline); }
    return haversineKm(centre, row);
  }

  /* The rows within `km` of `centre`, nearest first: [{row, km}]. A row at
   * exactly the radius is in; one beyond it never is. Pure: no request. */
  function nearby(rows, centre, km) {
    var found = [];
    (rows || []).forEach(function (row) {
      var d = rowKm(centre, row);
      if (d <= Number(km)) { found.push({row: row, km: d}); }
    });
    found.sort(function (a, b) { return a.km - b.km; });
    return found;
  }

  /* Does the index's filter let a row through?
   * filter: {text, kinds: {kind: bool}, community: slug or ""}             */
  function passes(row, filter) {
    filter = filter || {};
    if (filter.kinds && filter.kinds[row.kind] === false) { return false; }
    if (filter.community) {
      if (!row.community || row.community.slug !== filter.community) { return false; }
    }
    var text = String(filter.text || "").trim().toLowerCase();
    if (!text) { return true; }
    var hay = [row.name, row.note, row.detail, row.postal_address, row.author,
               row.community ? row.community.name : ""].join("\n").toLowerCase();
    return hay.indexOf(text) !== -1;
  }

  function centreOf(row) {
    if (!row.outline) { return {lat: row.lat, lng: row.lng}; }
    var lat = 0, lng = 0;
    row.outline.forEach(function (c) { lat += c[0]; lng += c[1]; });
    return {lat: lat / row.outline.length, lng: lng / row.outline.length};
  }

  /* createPresser({fetch, csrf, mint}) -> press(slot, url, body)
   * One deliberate press of a charged control: the op of a request the
   * server never answered (the network, a 5xx, a throttle) is kept and
   * repeated while the body stays the same; any answered press mints anew. */
  function createPresser(options) {
    var pending = {};
    var mint = options.mint;
    return function press(slot, url, body) {
      var signature = url + "\n" + JSON.stringify(body);
      var waiting = pending[slot];
      var op = (waiting && waiting.signature === signature) ? waiting.op : mint();
      pending[slot] = {signature: signature, op: op};
      var request = {};
      Object.keys(body).forEach(function (key) { request[key] = body[key]; });
      request.op = op;
      return options.fetch(url, {
        method: "POST", credentials: "same-origin",
        headers: {"Accept": "application/json", "Content-Type": "application/json",
                  "X-CSRFToken": options.csrf || "", "X-Requested-With": "XMLHttpRequest"},
        body: JSON.stringify(request)
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          return {ok: response.ok, status: response.status, data: data || {}};
        });
      }, function () { return {ok: false, status: 0, data: {}}; }).then(function (answer) {
        var unanswered = answer.status === 0 || answer.status >= 500 || answer.status === 429;
        if (!unanswered && pending[slot] && pending[slot].op === op) { delete pending[slot]; }
        answer.op = op;
        return answer;
      });
    };
  }

  var api = {haversineKm: haversineKm, insideRing: insideRing, zoneKm: zoneKm, rowKm: rowKm,
             nearby: nearby, passes: passes, centreOf: centreOf, wrapLng: wrapLng,
             homeRing: homeRing, createPresser: createPresser};
  root.GeographyLocations = api;
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }

  /* ---------------------------------------------------------------------
   * The page
   * ------------------------------------------------------------------- */

  var POINT_KINDS = {person: true, headquarters: true, pin: true};
  var COLOURS = {person: "#7c3aed", headquarters: "#b45309", area: "#b45309",
                 pin: "#2a81cb", zone: "#0f766e"};

  function mount(box) {
    if (box.dataset.geographyMounted) { return null; }
    box.dataset.geographyMounted = "1";
    var L = root.L, document = root.document;
    var config = JSON.parse(document.getElementById(box.dataset.config).textContent);
    var texts = config.texts || {};
    var kinds = texts.kinds || {};
    var rows = config.rows || [];
    var csrf = box.dataset.csrf || "";
    var q = function (name) { return box.querySelector('[data-geo="' + name + '"]'); };
    var fill = function (text, values) {
      var out = String(text || "");
      Object.keys(values || {}).forEach(function (key) {
        out = out.replace("%(" + key + ")s", values[key]);
      });
      return out;
    };

    var pointRows = rows.filter(function (row) { return POINT_KINDS[row.kind]; });
    var controller = root.GeographyMap.createController({
      fetch: root.fetch.bind(root), urls: config.urls || {}, csrf: csrf,
      saved: pointRows.map(function (row) {
        return {kind: row.kind, lat: row.lat, lng: row.lng, label: row.name || ""};
      })
    });
    var press = createPresser({fetch: root.fetch.bind(root), csrf: csrf,
                               mint: root.GeographyMap.uuid});

    var map = L.map(q("map")).setView([52.2297, 21.0122], 5);
    root.totoTileLayer(map);
    var layers = {rows: L.layerGroup().addTo(map), hits: L.layerGroup().addTo(map),
                  temporary: L.layerGroup().addTo(map), route: L.layerGroup().addTo(map),
                  nearby: L.layerGroup().addTo(map), draft: L.layerGroup().addTo(map)};

    var state = {
      filter: {text: "", kinds: {}, community: config.filter || ""},
      near: null,          // {centre: {lat, lng, name}, km}
      centre: null,        // the centre the next nearby search would use
      clicked: null,       // where the map was clicked last
      open: null,          // the opened row's id
      draft: null,         // the draggable pin of the pin form
      zoneEditor: null
    };

    function say(name, text, bad) {
      var note = typeof name === "string" ? q(name) : name;
      if (!note) { return; }
      note.textContent = text || "";
      note.classList.toggle("hidden", !text);
      note.classList.toggle("font-semibold", !!bad);
    }

    function refusal(answer) {
      return (answer.data && answer.data.error) || texts.failed || "";
    }

    function tip(layer, text) {
      if (!text) { return; }
      var node = document.createElement("span");
      node.textContent = text;       // as text, never as markup
      layer.bindTooltip(node);
    }

    function el(tag, className, text) {
      var node = document.createElement(tag);
      if (className) { node.className = className; }
      if (text !== undefined && text !== null) { node.textContent = text; }
      return node;
    }

    /* --- what is shown: the filter, then nearby ------------------------- */
    function shown() {
      var passing = rows.filter(function (row) { return passes(row, state.filter); });
      if (!state.near) { return passing.map(function (row) { return {row: row, km: null}; }); }
      return nearby(passing, state.near.centre, state.near.km);
    }

    function drawRows() {
      layers.rows.clearLayers();
      var list = q("index");
      list.textContent = "";
      var now = shown();
      now.forEach(function (item) {
        var row = item.row, layer;
        var colour = COLOURS[row.kind] || "#2a81cb";
        if (row.outline) {
          layer = L.polygon(row.outline, {color: colour, weight: 2, fillOpacity: 0.12,
                                          dashArray: row.hidden ? "4 6" : null});
        } else if (row.kind === "pin") {
          layer = L.marker([row.lat, row.lng], {icon: root.classicPin(),
                                                opacity: row.hidden ? 0.45 : 1});
        } else {
          layer = L.circleMarker([row.lat, row.lng], {radius: 8, weight: 2, color: colour,
                                                      fillOpacity: 0.5});
        }
        tip(layer, row.name || kinds[row.kind] || "");
        layer.on("click", function (event) {
          if (state.zoneEditor && state.zoneEditor.active()) { return; }
          L.DomEvent.stopPropagation(event);
          openRow(row);
        });
        layer.addTo(layers.rows);

        var entry = el("li");
        var button = el("button", "block w-full rounded-lg border border-current/10 px-3 py-2 text-left hover:opacity-75");
        button.type = "button";
        button.dataset.row = row.id;
        button.appendChild(el("span", "block font-semibold", row.name || kinds[row.kind] || ""));
        var under = [kinds[row.kind] || row.kind];
        if (row.community) { under.push(row.community.name); }
        if (row.hidden) { under.push(texts.hidden || ""); }
        if (item.km !== null) { under.push(fill(texts.km_away, {km: item.km.toFixed(1)})); }
        button.appendChild(el("span", "block text-xs opacity-65", under.join(" · ")));
        button.addEventListener("click", function () { openRow(row); closeDrawer(); });
        entry.appendChild(button);
        list.appendChild(entry);
      });
      if (!now.length) { list.appendChild(el("li", "text-sm italic opacity-60", texts.nothing || "")); }
      say("shown", state.near
        ? fill(texts.near, {n: now.length, km: state.near.km})
        : fill(texts.shown, {n: now.length}));
    }

    function fitAll() {
      var group = [];
      layers.rows.eachLayer(function (layer) { group.push(layer); });
      if (!group.length) { return; }
      map.fitBounds(L.featureGroup(group).getBounds(), {padding: [24, 24], maxZoom: 14});
    }

    /* --- the index's controls ------------------------------------------- */
    var filterBox = q("filter");
    filterBox.addEventListener("input", function () {
      state.filter.text = filterBox.value;
      drawRows();
    });
    Array.prototype.forEach.call(box.querySelectorAll("[data-geo-kind]"), function (check) {
      check.addEventListener("change", function () {
        state.filter.kinds[check.dataset.geoKind] = check.checked;
        drawRows();
      });
    });
    var communityBox = q("community");
    var named = {};
    rows.forEach(function (row) { if (row.community) { named[row.community.slug] = row.community.name; } });
    (config.communities || []).forEach(function (c) { named[c.slug] = c.name; });
    Object.keys(named).sort(function (a, b) { return named[a].localeCompare(named[b]); })
      .forEach(function (slug) {
        var option = el("option", "", named[slug]);
        option.value = slug;
        communityBox.appendChild(option);
      });
    communityBox.value = named[state.filter.community] ? state.filter.community : "";
    state.filter.community = communityBox.value;
    communityBox.addEventListener("change", function () {
      state.filter.community = communityBox.value;
      drawRows();
    });
    q("fit").addEventListener("click", fitAll);
    q("reset").addEventListener("click", function () {
      state.filter = {text: "", kinds: {}, community: ""};
      state.near = null;
      filterBox.value = "";
      communityBox.value = "";
      Array.prototype.forEach.call(box.querySelectorAll("[data-geo-kind]"),
                                   function (check) { check.checked = true; });
      layers.nearby.clearLayers();
      say("nearby-note", "");
      drawRows();
      fitAll();
    });

    /* --- the drawer, on a phone ----------------------------------------- */
    var drawer = q("drawer");
    function closeDrawer() {
      drawer.classList.add("hidden");
      drawer.classList.remove("flex");
    }
    q("drawer-open").addEventListener("click", function () {
      drawer.classList.remove("hidden");
      drawer.classList.add("flex");
    });
    q("drawer-close").addEventListener("click", closeDrawer);

    /* --- a centre for nearby --------------------------------------------- */
    function setCentre(lat, lng, name) {
      state.centre = {lat: lat, lng: lng, name: name || ""};
      say("nearby-centre", fill(texts.centre_is, {name: name || (lat.toFixed(4) + ", " + lng.toFixed(4))}));
    }

    q("nearby-go").addEventListener("click", function () {
      if (!state.centre) { say("nearby-note", texts.choose_centre, true); return; }
      var km = Number(q("nearby-radius").value);
      /* Worked out here: nothing is sent. */
      state.near = {centre: {lat: state.centre.lat, lng: state.centre.lng}, km: km};
      layers.nearby.clearLayers();
      var ring = L.circle([state.centre.lat, state.centre.lng],
                          {radius: km * 1000, weight: 1, fillOpacity: 0.04}).addTo(layers.nearby);
      drawRows();
      map.fitBounds(ring.getBounds(), {padding: [24, 24]});
      say("nearby-note", fill(texts.near, {n: shown().length, km: km}));
    });
    q("nearby-clear").addEventListener("click", function () {
      state.near = null;
      layers.nearby.clearLayers();
      say("nearby-note", "");
      drawRows();
    });

    /* --- a row, opened ---------------------------------------------------- */
    var details = q("details");

    function openRow(row) {
      state.open = row.id;
      var centre = centreOf(row);
      if (row.outline) { map.fitBounds(L.polygon(row.outline).getBounds(), {padding: [24, 24]}); }
      else { map.setView([row.lat, row.lng], Math.max(map.getZoom(), 14)); }
      setCentre(centre.lat, centre.lng, row.name || kinds[row.kind] || "");
      details.classList.remove("hidden");
      if (row.urls && row.urls.detail) { loadDetails(row); return; }
      details.textContent = "";
      details.appendChild(el("h2", "text-lg font-bold", row.name || kinds[row.kind] || ""));
      var under = [kinds[row.kind] || row.kind];
      if (row.community) { under.push(row.community.name); }
      details.appendChild(el("p", "mt-1 text-xs opacity-70", under.join(" · ")));
      if (row.detail) { details.appendChild(el("p", "mt-2 text-sm", row.detail)); }
      if (row.note) { details.appendChild(el("p", "mt-2 whitespace-pre-line break-words text-sm", row.note)); }
      if (row.own) { details.appendChild(el("p", "mt-2 text-xs opacity-70", texts.yours_note)); }
      if (row.link) {
        var link = el("a", "mt-3 inline-block text-sm font-semibold underline",
                      row.kind === "person" ? texts.open_profile : texts.open_community);
        link.href = row.link;
        details.appendChild(link);
      }
    }

    function loadDetails(row) {
      details.textContent = "…";
      root.fetch(row.urls.detail, {credentials: "same-origin",
                                   headers: {"X-Requested-With": "XMLHttpRequest"}})
        .then(function (response) {
          return response.text().then(function (body) { return {ok: response.ok, body: body}; });
        }, function () { return {ok: false, body: texts.failed || ""}; })
        .then(function (answer) {
          if (state.open !== row.id) { return; }
          if (!answer.ok) { details.textContent = answer.body || texts.failed || ""; return; }
          /* The one piece of HTML the page takes: the server's template has
           * escaped every text in it. */
          details.innerHTML = answer.body;
          wireDetails(row);
        });
    }

    function reloadTo(row) {
      var url = new URL(root.location.href);
      url.searchParams.delete("open");
      if (row) { url.searchParams.set("open", row); }
      if (state.filter.community) { url.searchParams.set("community", state.filter.community); }
      root.location.assign(url.toString());
    }

    function wireDetails(row) {
      var status = details.querySelector("[data-geo-detail-status]");
      var edit = details.querySelector("[data-geo-edit]");
      if (edit) {
        edit.addEventListener("submit", function (event) {
          event.preventDefault();
          var body = {};
          Array.prototype.forEach.call(edit.querySelectorAll("[name]"), function (field) {
            body[field.name] = field.value;
          });
          var button = edit.querySelector("button[type=submit]");
          if (button) { button.disabled = true; }
          press("edit:" + row.id, edit.getAttribute("action"), body).then(function (answer) {
            if (button) { button.disabled = false; }
            if (!answer.ok) { say(status, refusal(answer), true); return; }
            reloadTo(row.id);
          });
        });
      }
      Array.prototype.forEach.call(details.querySelectorAll("[data-geo-act]"), function (button) {
        button.addEventListener("click", function () {
          var act = button.dataset.geoAct;
          if (act === "delete" && !root.confirm(texts.confirm_delete || "")) { return; }
          button.disabled = true;
          controllerPost(row.urls[act], {}).then(function (answer) {
            button.disabled = false;
            if (!answer.ok) { say(status, refusal(answer), true); return; }
            reloadTo(act === "delete" ? "" : row.id);
          });
        });
      });
      /* The thread's forms: sent as they are (each add form carries its own
       * op), answered as JSON, and the thread is read again. */
      Array.prototype.forEach.call(details.querySelectorAll("[data-geo-thread] form"), function (form) {
        form.addEventListener("submit", function (event) {
          if (event.defaultPrevented) { return; }     // the member said no to "Withdraw?"
          event.preventDefault();
          var button = form.querySelector("button[type=submit]");
          if (button) { button.disabled = true; }
          root.fetch(form.getAttribute("action"), {
            method: "POST", credentials: "same-origin",
            headers: {"Accept": "application/json", "X-CSRFToken": csrf,
                      "X-Requested-With": "XMLHttpRequest",
                      "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            body: new root.URLSearchParams(new root.FormData(form)).toString()
          }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
              return {ok: response.ok, data: data || {}};
            });
          }, function () { return {ok: false, data: {}}; }).then(function (answer) {
            if (button) { button.disabled = false; }
            if (!answer.ok) { say(status, refusal(answer), true); return; }
            loadDetails(row);
          });
        });
      });
    }

    /* A free act: no op, an empty JSON body. */
    function controllerPost(url, body) {
      return root.fetch(url, {
        method: "POST", credentials: "same-origin",
        headers: {"Accept": "application/json", "Content-Type": "application/json",
                  "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest"},
        body: JSON.stringify(body || {})
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          return {ok: response.ok, status: response.status, data: data || {}};
        });
      }, function () { return {ok: false, status: 0, data: {}}; });
    }

    /* --- temporary points, search hits, the route's ends ------------------ */
    function drawTemporary() {
      layers.temporary.clearLayers();
      controller.state.temporary.forEach(function (point) {
        var made = L.circleMarker([point.lat, point.lng], {radius: 7, weight: 2, color: "#dc2626"});
        tip(made, point.label || texts.temporary || "");
        made.on("click", function (event) {
          L.DomEvent.stopPropagation(event);
          /* Click it again to take it away. */
          controller.removeTemporary(point.id);
          drawTemporary();
          fillEnds();
        });
        made.addTo(layers.temporary);
      });
    }

    function chooseHit(hit) {
      map.setView([hit.lat, hit.lng], 14);
      setCentre(hit.lat, hit.lng, hit.label);
    }

    function drawHits() {
      layers.hits.clearLayers();
      var list = q("results");
      if (list) { list.textContent = ""; }
      controller.state.hits.forEach(function (hit) {
        var made = L.marker([hit.lat, hit.lng], {icon: root.classicPin(), opacity: 0.7});
        tip(made, hit.label);
        made.on("click", function (event) { L.DomEvent.stopPropagation(event); chooseHit(hit); });
        made.addTo(layers.hits);
        if (list) {
          var item = el("li", "flex items-center gap-2");
          var button = el("button", "min-w-0 flex-1 px-3 py-2 text-left hover:opacity-70", hit.label);
          button.type = "button";
          button.addEventListener("click", function () { chooseHit(hit); });
          item.appendChild(button);
          /* A new search replaces these hits, and an end of a route that
           * meant one of them is then cleared. One press keeps a hit on the
           * map as a temporary point (in this page only), so a route can
           * run between the results of two searches. */
          var keep = el("button", "shrink-0 rounded-lg border border-current/30 px-2 py-1 text-xs font-semibold",
                        texts.keep_hit || "");
          keep.type = "button";
          keep.addEventListener("click", function () {
            controller.placeTemporary(hit.lat, hit.lng, hit.label);
            drawTemporary();
            fillEnds();
          });
          item.appendChild(keep);
          list.appendChild(item);
        }
      });
      if (list) { list.classList.toggle("hidden", !controller.state.hits.length); }
    }

    var goLabel = q("route-go-label");
    var goText = goLabel ? goLabel.textContent : "";

    function fillEnds() {
      if (!q("route")) { return; }
      var now = controller.selection();
      ["from", "to"].forEach(function (name) {
        var select = q(name);
        select.textContent = "";
        if (!now[name]) {
          var none = el("option", "", (now.gone[name] ? texts.end_gone : texts.choose_end) || "");
          none.value = "";
          none.disabled = true;
          select.appendChild(none);
        }
        controller.ends().forEach(function (end) {
          var option = el("option", "", end.label ||
            ((end.kind === "temporary" ? (texts.temporary || "") + " " : "") +
             end.lat.toFixed(4) + ", " + end.lng.toFixed(4)));
          option.value = end.id;
          select.appendChild(option);
        });
        select.value = now[name] ? now[name].id : "";
      });
      q("route-go").disabled = !(now.from && now.to);
      if (goLabel) {
        goLabel.textContent = ((now.gone.from || now.gone.to) && texts.choose_again) || goText;
      }
    }

    if (q("route")) {
      ["from", "to"].forEach(function (name) {
        q(name).addEventListener("change", function () {
          controller.chooseEnd(name, q(name).value);
          fillEnds();
        });
      });
      q("route-go").addEventListener("click", function () {
        var asked = controller.routeChosen(q("mode").value);
        if (!asked) { fillEnds(); return; }
        q("route-go").disabled = true;
        say("route-note", "");
        asked.then(function (answer) {
          fillEnds();
          layers.route.clearLayers();
          if (!answer.ok) { say("route-note", refusal(answer), true); return; }
          if (!answer.data.line) { say("route-note", texts.no_route); return; }
          var line = L.geoJSON({type: "Feature", properties: {}, geometry: answer.data.line},
                               {style: {weight: 5, opacity: 0.8}}).addTo(layers.route);
          map.fitBounds(line.getBounds(), {padding: [24, 24]});
          say("route-note", fill(texts.route_summary, {km: answer.data.distance_km,
                                                       min: answer.data.duration_min}));
        });
      });
    }

    var searchBox = q("q"), searchGo = q("search-go");
    if (searchBox && searchGo) {
      var run = function () {
        var text = searchBox.value.trim();
        if (!text || searchGo.disabled) { return; }
        searchGo.disabled = true;
        say("search-note", "");
        controller.search(text).then(function (answer) {
          searchGo.disabled = false;
          if (!answer.ok) {
            controller.state.hits = [];
            drawHits(); fillEnds();
            say("search-note", refusal(answer), true);
            return;
          }
          drawHits(); fillEnds();
          if (!controller.state.hits.length) { say("search-note", texts.nothing_found); return; }
          chooseHit(controller.state.hits[0]);
        });
      };
      /* A search is charged: on Enter or the button, never while typing. */
      searchBox.addEventListener("keydown", function (event) {
        if (event.key === "Enter") { event.preventDefault(); run(); }
      });
      searchGo.addEventListener("click", run);
    }

    /* --- a click on the map offers; it does nothing by itself ------------- */
    var menu = q("click-menu");
    function closeMenu() { menu.classList.add("hidden"); }

    map.on("click", function (event) {
      if (state.zoneEditor && state.zoneEditor.active()) { return; }
      var lat = round6(event.latlng.lat), lng = round6(wrapLng(event.latlng.lng));
      if (state.draft) { state.draft.setLatLng([lat, lng]); return; }
      state.clicked = {lat: lat, lng: lng};
      say("click-where", lat.toFixed(5) + ", " + lng.toFixed(5));
      menu.classList.remove("hidden");
    });
    q("click-close").addEventListener("click", closeMenu);
    q("click-temporary").addEventListener("click", function () {
      if (!state.clicked) { return; }
      /* Kept in the controller's memory; no request, no storage. */
      controller.placeTemporary(state.clicked.lat, state.clicked.lng);
      drawTemporary();
      fillEnds();
      closeMenu();
    });
    q("click-centre").addEventListener("click", function () {
      if (!state.clicked) { return; }
      setCentre(state.clicked.lat, state.clicked.lng, texts.clicked);
      closeMenu();
    });

    /* --- the communities the member may save into ------------------------- */
    var mine = config.communities || [];
    function fillCommunities(select) {
      select.textContent = "";
      if (!mine.length) {
        var none = el("option", "", texts.no_community || "");
        none.value = "";
        select.appendChild(none);
        return;
      }
      mine.forEach(function (community) {
        var option = el("option", "", community.name);
        option.value = community.slug;
        select.appendChild(option);
      });
      if (state.filter.community) { select.value = state.filter.community; }
      if (!select.value) { select.value = mine[0].slug; }
    }
    function communityOf(select) {
      for (var i = 0; i < mine.length; i++) { if (mine[i].slug === select.value) { return mine[i]; } }
      return null;
    }

    /* --- save a community pin --------------------------------------------- */
    var pinForm = q("pin-form");
    function closePin() {
      pinForm.classList.add("hidden");
      layers.draft.clearLayers();
      state.draft = null;
    }
    q("click-pin").addEventListener("click", function () {
      if (!state.clicked) { return; }
      closeMenu();
      fillCommunities(q("pin-community"));
      layers.draft.clearLayers();
      state.draft = L.marker([state.clicked.lat, state.clicked.lng],
                             {icon: root.classicPin(), draggable: true}).addTo(layers.draft);
      pinForm.classList.remove("hidden");
      say("pin-status", mine.length ? "" : texts.no_community, !mine.length);
      q("pin-save").disabled = !mine.length;
    });
    q("pin-cancel").addEventListener("click", closePin);
    q("pin-save").addEventListener("click", function () {
      var community = communityOf(q("pin-community"));
      if (!community || !state.draft) { say("pin-status", texts.choose_community, true); return; }
      var at = state.draft.getLatLng();
      var save = q("pin-save");
      save.disabled = true;
      press("pin", community.pins, {
        lat: round6(at.lat), lng: round6(wrapLng(at.lng)), name: q("pin-name").value,
        postal_address: q("pin-postal").value, note: q("pin-note").value
      }).then(function (answer) {
        save.disabled = false;
        if (!answer.ok) { say("pin-status", refusal(answer), true); return; }
        reloadTo("pin:" + answer.data.pin.uid);
      });
    });

    /* --- draw a community zone -------------------------------------------- */
    var zoneForm = q("zone-form"), zoneSave = q("zone-save");
    if (root.GeographyZone) {
      state.zoneEditor = root.GeographyZone.attach(map, L, {
        corners: [],
        onChange: function (count) {
          zoneSave.disabled = count < 3 || !mine.length;
          say("zone-count", fill(texts.corners, {n: count}));
        }
      });
      q("zone-open").addEventListener("click", function () {
        closeMenu(); closePin();
        fillCommunities(q("zone-community"));
        zoneForm.classList.remove("hidden");
        say("zone-status", mine.length ? "" : texts.no_community, !mine.length);
        state.zoneEditor.start();
      });
      q("zone-cancel").addEventListener("click", function () {
        state.zoneEditor.reset();
        state.zoneEditor.stop();
        zoneForm.classList.add("hidden");
      });
      q("zone-undo").addEventListener("click", function () { state.zoneEditor.undo(); });
      q("zone-restart").addEventListener("click", function () { state.zoneEditor.reset(); });
      zoneSave.addEventListener("click", function () {
        var community = communityOf(q("zone-community"));
        if (!community) { say("zone-status", texts.choose_community, true); return; }
        zoneSave.disabled = true;
        press("zone", community.zones, {
          name: q("zone-name").value, description: q("zone-description").value,
          outline: homeRing(state.zoneEditor.corners())
        }).then(function (answer) {
          zoneSave.disabled = false;
          if (!answer.ok) { say("zone-status", refusal(answer), true); return; }
          reloadTo("zone:" + answer.data.zone.uid);
        });
      });
    }

    /* --- first drawing ------------------------------------------------------ */
    drawRows();
    fillEnds();
    fitAll();
    if (config.open) {
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].id === config.open) { openRow(rows[i]); break; }
      }
    }
    /* Leaflet cannot size itself inside a box that was laid out late. */
    root.setTimeout(function () { map.invalidateSize(); }, 80);
    return {map: map, controller: controller, state: state};
  }

  api.mount = mount;
  if (root.document && root.document.addEventListener) {
    var all = function () {
      Array.prototype.forEach.call(
        root.document.querySelectorAll("[data-geography-locations]"), mount);
    };
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", all);
    } else { all(); }
  }
})(typeof window !== "undefined" ? window : globalThis);

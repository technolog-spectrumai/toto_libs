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
 *  - The tools beside the map are tabs (the index, Search nearby, Route):
 *    one open at a time, the map outside them and shared, so nothing on it
 *    changes when the tab does. A change of tab sends nothing: it rewrites
 *    the address (?tool=…), which the server reads at the next loading.
 *  - The community filter is a list of checkboxes: none ticked is every
 *    community. It is worked out here and sends nothing; the ticked ones go
 *    into the address (?community=a&community=b).
 *  - A pin's and a zone's words are typed in a dialog and nowhere else.
 *    Beside the map there is only the geometry: the pin to drag, the
 *    corners, Continue and Cancel. Cancel in the dialog leaves the geometry
 *    on the map; a refusal is shown in the dialog with what was typed kept.
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
   * filter: {text, kinds: {kind: bool}, communities: [slug, …]}
   * No community chosen lets every row through. One or more chosen keep the
   * rows of those communities (headquarters, areas, pins, zones) and hide
   * every other row, a person's point included: it belongs to no community.
   * (`community: slug`, the single choice the page had, reads as one.)      */
  function passes(row, filter) {
    filter = filter || {};
    if (filter.kinds && filter.kinds[row.kind] === false) { return false; }
    var wanted = filter.communities || (filter.community ? [filter.community] : []);
    if (wanted.length) {
      if (!row.community || wanted.indexOf(row.community.slug) === -1) { return false; }
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

  /* What the community filter's button says: every community, the one
   * chosen by its name, or how many. `names`: {slug: name}. */
  function communityLabel(chosen, names, texts) {
    texts = texts || {};
    if (!chosen || !chosen.length) { return texts.all_communities || ""; }
    if (chosen.length === 1) { return has(names, chosen[0]) ? names[chosen[0]] : chosen[0]; }
    return String(texts.n_communities || "%(n)s").replace("%(n)s", chosen.length);
  }

  function has(object, key) { return Object.prototype.hasOwnProperty.call(object || {}, key); }

  /* The communities the address ticked, as the page takes them: those it
   * knows, each once. `asked`: a list, or the one slug the page used to be
   * handed. A slug is a word from the address: it is looked up as a key of
   * `names` and of nothing else. */
  function knownCommunities(asked, names) {
    var list = Array.isArray(asked) ? asked : (asked ? [asked] : []);
    var out = [];
    list.forEach(function (slug) {
      if (typeof slug === "string" && has(names, slug) && out.indexOf(slug) === -1) {
        out.push(slug);
      }
    });
    return out;
  }

  /* The page's address with what a reload and a link must keep: the open
   * tab (the index is the address without one), the ticked communities, the
   * opened row. A key left out of `want` is left as the address has it. */
  function address(href, want) {
    var url = new root.URL(href);
    if (want.tool !== undefined) {
      url.searchParams.delete("tool");
      if (want.tool && want.tool !== "index") { url.searchParams.set("tool", want.tool); }
    }
    if (want.communities !== undefined) {
      url.searchParams.delete("community");
      want.communities.forEach(function (slug) { url.searchParams.append("community", slug); });
    }
    if (want.open !== undefined) {
      url.searchParams.delete("open");
      if (want.open) { url.searchParams.set("open", want.open); }
    }
    return url.toString();
  }

  /* The tab an arrow key moves to from `current`, round the ends; Home and
   * End go to the first and the last. Null for any other key. */
  function stepTab(names, current, key) {
    if (!names.length) { return null; }
    var at = Math.max(0, names.indexOf(current));
    if (key === "ArrowRight") { return names[(at + 1) % names.length]; }
    if (key === "ArrowLeft") { return names[(at - 1 + names.length) % names.length]; }
    if (key === "Home") { return names[0]; }
    if (key === "End") { return names[names.length - 1]; }
    return null;
  }

  /* createTabs({tabs, panel, onChange}) -> {select(name, focus), current()}
   * A tab strip: `tabs` are the role="tab" buttons (data-geo-tab), `panel`
   * answers a tab's role="tabpanel". One tab is selected (aria-selected,
   * the one in the Tab order) and its panel is the one not hidden. A click
   * selects; the arrow keys, Home and End select and move the focus. It
   * starts at the tab the server drew as selected. Nothing is fetched. */
  function createTabs(options) {
    var tabs = Array.prototype.slice.call(options.tabs || []);
    var names = tabs.map(function (tab) { return tab.dataset.geoTab; });
    var current = null;

    function draw(focus) {
      tabs.forEach(function (tab) {
        var on = tab.dataset.geoTab === current;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.setAttribute("tabindex", on ? "0" : "-1");
        var panel = options.panel(tab.dataset.geoTab);
        if (panel) { panel.classList.toggle("hidden", !on); }
        if (on && focus) { tab.focus(); }
      });
    }

    function select(name, focus) {
      if (names.indexOf(name) === -1) { return false; }
      var changed = name !== current;
      current = name;
      draw(focus);
      if (changed && options.onChange) { options.onChange(name); }
      return true;
    }

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () { select(tab.dataset.geoTab); });
      tab.addEventListener("keydown", function (event) {
        var to = stepTab(names, current, event.key);
        if (to === null) { return; }
        event.preventDefault();
        select(to, true);
      });
    });
    var first = tabs.filter(function (tab) {
      return tab.getAttribute("aria-selected") === "true";
    })[0] || tabs[0];
    if (first) { current = first.dataset.geoTab; draw(false); }
    return {select: select, current: function () { return current; }, names: names};
  }

  var api = {haversineKm: haversineKm, insideRing: insideRing, zoneKm: zoneKm, rowKm: rowKm,
             nearby: nearby, passes: passes, centreOf: centreOf, wrapLng: wrapLng,
             homeRing: homeRing, createPresser: createPresser,
             communityLabel: communityLabel, knownCommunities: knownCommunities,
             address: address, stepTab: stepTab, createTabs: createTabs};
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
      filter: {text: "", kinds: {}, communities: []},
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

    /* One line of a list of rows: the name, and under it the kind, the
     * community and, after a nearby search, how far. All as text. */
    function entry(item) {
      var row = item.row;
      var line = el("li");
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
      line.appendChild(button);
      return line;
    }

    function drawRows() {
      layers.rows.clearLayers();
      var list = q("index"), nearList = q("nearby-results");
      list.textContent = "";
      if (nearList) { nearList.textContent = ""; }
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

        list.appendChild(entry(item));
        /* The same rows, nearest first, in the Search nearby tab: what a
         * search found is read where it was asked. */
        if (state.near && nearList) { nearList.appendChild(entry(item)); }
      });
      if (!now.length) {
        list.appendChild(el("li", "text-sm italic opacity-60", texts.nothing || ""));
        if (state.near && nearList) {
          nearList.appendChild(el("li", "text-sm italic opacity-60", texts.nothing || ""));
        }
      }
      say("shown", state.near
        ? fill(texts.near, {n: now.length, km: state.near.km})
        : fill(texts.shown, {n: now.length}));
      /* The nearby tab's own line, kept true when a filter narrows it. */
      if (state.near) { say("nearby-note", fill(texts.near, {n: now.length, km: state.near.km})); }
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
    /* The community filter: a button that opens a list of checkboxes, one
     * for every community the page knows (those of its rows, and the
     * member's own). Each name is written as text. Ticking sends nothing. */
    var named = Object.create(null);      // slug -> name; a slug is never a method's name
    rows.forEach(function (row) { if (row.community) { named[row.community.slug] = row.community.name; } });
    (config.communities || []).forEach(function (c) { named[c.slug] = c.name; });
    var slugs = Object.keys(named).sort(function (a, b) { return named[a].localeCompare(named[b]); });
    var communityFilter = q("community-filter"), communityToggle = q("community-toggle");
    var communityList = q("community-list"), communityBoxes = q("community-boxes");
    var ticks = Object.create(null);
    state.filter.communities = knownCommunities(config.filter, named);

    function chosenCommunities() {
      return slugs.filter(function (slug) { return ticks[slug].checked; });
    }

    function applyCommunities(remembered) {
      state.filter.communities = chosenCommunities();
      q("community-chosen").textContent = communityLabel(state.filter.communities, named, texts);
      drawRows();
      if (remembered) { remember(); }
    }

    function tickCommunities(on) {
      slugs.forEach(function (slug) { ticks[slug].checked = on; });
      applyCommunities(true);
    }

    slugs.forEach(function (slug) {
      var label = el("label", "flex items-center gap-2 rounded px-1 py-1");
      var tick = el("input");
      tick.type = "checkbox";
      tick.value = slug;
      tick.checked = state.filter.communities.indexOf(slug) !== -1;
      tick.addEventListener("change", function () { applyCommunities(true); });
      label.appendChild(tick);
      label.appendChild(el("span", "min-w-0 break-words", named[slug]));
      communityBoxes.appendChild(label);
      ticks[slug] = tick;
    });
    if (!slugs.length) {
      communityBoxes.appendChild(el("p", "text-xs italic opacity-60", texts.no_communities || ""));
    }
    q("community-chosen").textContent = communityLabel(state.filter.communities, named, texts);

    function communityListOpen() { return !communityList.classList.contains("hidden"); }
    function showCommunityList(on) {
      communityList.classList.toggle("hidden", !on);
      communityToggle.setAttribute("aria-expanded", on ? "true" : "false");
    }
    communityToggle.addEventListener("click", function () { showCommunityList(!communityListOpen()); });
    communityToggle.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowDown") { return; }
      event.preventDefault();
      showCommunityList(true);
      var first = slugs.length ? ticks[slugs[0]] : q("community-all");
      if (first && first.focus) { first.focus(); }
    });
    /* Escape closes it and gives the focus back to its button; so does a
     * click anywhere else, and the focus leaving it. */
    communityFilter.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !communityListOpen()) { return; }
      event.stopPropagation();
      showCommunityList(false);
      communityToggle.focus();
    });
    communityFilter.addEventListener("focusout", function (event) {
      if (event.relatedTarget && !communityFilter.contains(event.relatedTarget)) {
        showCommunityList(false);
      }
    });
    document.addEventListener("click", function (event) {
      if (communityListOpen() && !communityFilter.contains(event.target)) { showCommunityList(false); }
    });
    q("community-all").addEventListener("click", function () { tickCommunities(true); });
    q("community-none").addEventListener("click", function () { tickCommunities(false); });

    q("fit").addEventListener("click", fitAll);
    q("reset").addEventListener("click", function () {
      state.filter = {text: "", kinds: {}, communities: []};
      state.near = null;
      filterBox.value = "";
      Array.prototype.forEach.call(box.querySelectorAll("[data-geo-kind]"),
                                   function (check) { check.checked = true; });
      layers.nearby.clearLayers();
      say("nearby-note", "");
      tickCommunities(false);      // draws the rows, and the address forgets them
      fitAll();
    });

    /* --- the drawer, on a phone: the three tabs are in it ----------------- */
    var drawer = q("drawer");
    function closeDrawer() {
      drawer.classList.add("hidden");
      drawer.classList.remove("flex");
    }
    function openDrawer() {
      drawer.classList.remove("hidden");
      drawer.classList.add("flex");
    }
    q("drawer-close").addEventListener("click", closeDrawer);

    /* --- the tabs: the index, Search nearby, Route ------------------------ */
    var tabs = createTabs({
      tabs: box.querySelectorAll("[data-geo-tab]"),
      panel: function (name) { return box.querySelector('[data-geo-panel="' + name + '"]'); },
      /* A change of tab asks the server nothing: the address is rewritten
       * where it stands, and what is on the map is not touched. */
      onChange: function () { remember(); }
    });
    /* On a phone three buttons above the map open the drawer at a tab. */
    Array.prototype.forEach.call(box.querySelectorAll("[data-geo-open]"), function (button) {
      button.addEventListener("click", function () {
        tabs.select(button.dataset.geoOpen);
        openDrawer();
      });
    });

    /* The address as a reload and a link must find it: the open tab and the
     * ticked communities. Rewritten in place: no request, no new entry in
     * the browser's history. */
    function remember() {
      try {
        root.history.replaceState(root.history.state, "", address(root.location.href, {
          tool: tabs.current(), communities: state.filter.communities}));
      } catch (error) { /* an address that cannot be rewritten stays as it is */ }
    }

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
      root.location.assign(address(root.location.href, {
        open: row || "", tool: tabs.current(), communities: state.filter.communities}));
    }

    function wireDetails(row) {
      var status = details.querySelector("[data-geo-detail-status]");
      /* The author's change: asked in the dialog. The button holds the
       * words as the server has them now, and the door. */
      var change = details.querySelector("[data-geo-change]");
      if (change) {
        change.addEventListener("click", function () {
          var words = change.dataset;
          if (words.geoChange === "pin") {
            openPinDialog({row: row, url: words.url, name: words.name || "",
                           postal: words.postal || "", note: words.note || ""});
          } else {
            openZoneDialog({row: row, url: words.url, name: words.name || "",
                            description: words.description || ""});
          }
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
      /* The centre is read, and the search pressed, in its own tab. */
      tabs.select("nearby");
      openDrawer();
    });

    /* --- the communities the member may save into ------------------------- */
    var mine = config.communities || [];
    /* One community for a new pin or zone: a contribution belongs to one.
     * What was chosen before stays chosen; else the one community the
     * filter shows, where it is one of the member's; else the first. */
    function fillCommunities(select) {
      var before = select.value;
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
      var wanted = [before].concat(state.filter.communities);
      for (var i = 0; i < wanted.length; i++) {
        if (wanted[i] && communityOf({value: wanted[i]})) { select.value = wanted[i]; return; }
      }
      select.value = mine[0].slug;
    }
    function communityOf(select) {
      for (var i = 0; i < mine.length; i++) { if (mine[i].slug === select.value) { return mine[i]; } }
      return null;
    }

    /* --- the two dialogs: the only place a pin's or a zone's words are typed -
     * Each is the page's modal (the template: x-show, x-trap, Escape), opened
     * and closed by GeographyMap.dialog. "new" asks the community too and
     * names the price of a pin or a zone; "change" is the author's change of
     * a saved row, with the price of a change. */
    function setMode(dialogBox, mode) {
      Array.prototype.forEach.call(dialogBox.querySelectorAll("[data-geo-mode]"), function (node) {
        node.classList.toggle("hidden", node.dataset.geoMode !== mode);
      });
    }

    /* --- a community pin: placed on the map, named in the dialog ----------- */
    var pinTool = q("pin-tool"), pinSave = q("pin-save");
    var pinDialog = root.GeographyMap.dialog(q("pin-dialog"));
    var pinChange = null;       // {row, url}: the saved pin whose words are open
    var pinWords = "";          // whose words the dialog's fields hold

    function pinFields(name, postal, note) {
      q("pin-name").value = name;
      q("pin-postal").value = postal;
      q("pin-note").value = note;
    }

    /* Drop the pin that was not saved, and what was typed for it. */
    function closePin() {
      if (!pinChange) { pinDialog.close(); }
      pinTool.classList.add("hidden");
      layers.draft.clearLayers();
      state.draft = null;
      if (pinWords === "new") { pinFields("", "", ""); pinWords = ""; }
    }

    /* `change`: {row, url, name, postal, note} for a saved pin; nothing for
     * the pin being placed. What was typed for a new pin is still there
     * after a Cancel; a change always starts from the saved words. */
    function openPinDialog(change) {
      var whose = change ? change.row.id : "new";
      if (change) { pinFields(change.name, change.postal, change.note); }
      else if (pinWords !== "new") { pinFields("", "", ""); }
      pinWords = whose;
      pinChange = change ? {row: change.row, url: change.url} : null;
      setMode(q("pin-dialog"), change ? "change" : "new");
      if (change) {
        q("pin-community-fixed").textContent = change.row.community ? change.row.community.name : "";
      } else { fillCommunities(q("pin-community")); }
      say("pin-status", "");
      pinSave.disabled = false;
      pinDialog.open();
    }

    q("click-pin").addEventListener("click", function () {
      if (!state.clicked) { return; }
      closeMenu();
      layers.draft.clearLayers();
      state.draft = L.marker([state.clicked.lat, state.clicked.lng],
                             {icon: root.classicPin(), draggable: true}).addTo(layers.draft);
      pinTool.classList.remove("hidden");
      say("pin-tool-status", mine.length ? "" : texts.no_community, !mine.length);
      q("pin-continue").disabled = !mine.length;
    });
    q("pin-continue").addEventListener("click", function () {
      if (state.draft) { openPinDialog(null); }
    });
    q("pin-cancel").addEventListener("click", closePin);
    /* Cancel in the dialog: back to the map, the pin where it was. */
    q("pin-dialog-cancel").addEventListener("click", function () { pinDialog.close(); });
    pinSave.addEventListener("click", function () {
      var words = {name: q("pin-name").value, postal_address: q("pin-postal").value,
                   note: q("pin-note").value};
      if (pinChange) {
        var changed = pinChange;
        pinSave.disabled = true;
        press("edit:" + changed.row.id, changed.url, words).then(function (answer) {
          pinSave.disabled = false;
          /* Refused: said here, the dialog open, what was typed kept. */
          if (!answer.ok) { say("pin-status", refusal(answer), true); return; }
          reloadTo(changed.row.id);
        });
        return;
      }
      var community = communityOf(q("pin-community"));
      if (!community || !state.draft) { say("pin-status", texts.choose_community, true); return; }
      var at = state.draft.getLatLng();
      pinSave.disabled = true;
      press("pin", community.pins, {
        lat: round6(at.lat), lng: round6(wrapLng(at.lng)), name: words.name,
        postal_address: words.postal_address, note: words.note
      }).then(function (answer) {
        pinSave.disabled = false;
        if (!answer.ok) { say("pin-status", refusal(answer), true); return; }
        reloadTo("pin:" + answer.data.pin.uid);
      });
    });

    /* --- a community zone: drawn on the map, named in the dialog ----------- */
    var zoneTool = q("zone-tool"), zoneSave = q("zone-save"), zoneContinue = q("zone-continue");
    var zoneDialog = root.GeographyMap.dialog(q("zone-dialog"));
    var zoneChange = null, zoneWords = "";

    function zoneFields(name, description) {
      q("zone-name").value = name;
      q("zone-description").value = description;
    }

    function openZoneDialog(change) {
      var whose = change ? change.row.id : "new";
      if (change) { zoneFields(change.name, change.description); }
      else if (zoneWords !== "new") { zoneFields("", ""); }
      zoneWords = whose;
      zoneChange = change ? {row: change.row, url: change.url} : null;
      setMode(q("zone-dialog"), change ? "change" : "new");
      if (change) {
        q("zone-community-fixed").textContent = change.row.community ? change.row.community.name : "";
      } else { fillCommunities(q("zone-community")); }
      say("zone-status", "");
      zoneSave.disabled = false;
      zoneDialog.open();
    }

    if (root.GeographyZone) {
      state.zoneEditor = root.GeographyZone.attach(map, L, {
        corners: [],
        onChange: function (count) {
          zoneContinue.disabled = count < 3 || !mine.length;
          say("zone-count", fill(texts.corners, {n: count}));
        }
      });
      q("zone-open").addEventListener("click", function () {
        closeMenu(); closePin();
        zoneTool.classList.remove("hidden");
        say("zone-tool-status", mine.length ? "" : texts.no_community, !mine.length);
        state.zoneEditor.start();
      });
      q("zone-cancel").addEventListener("click", function () {
        if (!zoneChange) { zoneDialog.close(); }
        state.zoneEditor.reset();
        state.zoneEditor.stop();
        zoneTool.classList.add("hidden");
        if (zoneWords === "new") { zoneFields("", ""); zoneWords = ""; }
      });
      q("zone-undo").addEventListener("click", function () { state.zoneEditor.undo(); });
      q("zone-restart").addEventListener("click", function () { state.zoneEditor.reset(); });
      zoneContinue.addEventListener("click", function () {
        if (state.zoneEditor.corners().length >= 3) { openZoneDialog(null); }
      });
    }
    /* Cancel in the dialog: back to the map, the corners where they were. */
    q("zone-dialog-cancel").addEventListener("click", function () { zoneDialog.close(); });
    zoneSave.addEventListener("click", function () {
      var words = {name: q("zone-name").value, description: q("zone-description").value};
      if (zoneChange) {
        var changed = zoneChange;
        zoneSave.disabled = true;
        press("edit:" + changed.row.id, changed.url, words).then(function (answer) {
          zoneSave.disabled = false;
          if (!answer.ok) { say("zone-status", refusal(answer), true); return; }
          reloadTo(changed.row.id);
        });
        return;
      }
      var community = communityOf(q("zone-community"));
      if (!community || !state.zoneEditor) { say("zone-status", texts.choose_community, true); return; }
      zoneSave.disabled = true;
      press("zone", community.zones, {
        name: words.name, description: words.description,
        outline: homeRing(state.zoneEditor.corners())
      }).then(function (answer) {
        zoneSave.disabled = false;
        if (!answer.ok) { say("zone-status", refusal(answer), true); return; }
        reloadTo("zone:" + answer.data.zone.uid);
      });
    });

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
    return {map: map, controller: controller, state: state, tabs: tabs};
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

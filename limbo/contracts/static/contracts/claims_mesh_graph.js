/**
 * claims_mesh_graph.js
 * Cytoscape renderer for the contracts Claims Mesh graph.
 * Reads Oya theme colors from the #oya-theme-colors JSON script tag.
 * No hardcoded hex colors.
 */

(function (global) {
  "use strict";

  // ---------------------------------------------------------------------------
  // Oya theme helpers
  // ---------------------------------------------------------------------------

  function getOyaColors() {
    var el = document.getElementById("oya-theme-colors");
    if (!el) return {};
    try { return JSON.parse(el.textContent) || {}; } catch (_) { return {}; }
  }

  function currentModeSuffix() {
    try {
      if (typeof Alpine !== "undefined") {
        var dm = Alpine.evaluate(document.documentElement, "darkMode");
        return dm ? "dark" : "light";
      }
    } catch (_) {}
    return document.documentElement.classList.contains("dark") ? "dark" : "light";
  }

  function colorToken(token, colors, suffix) {
    suffix = suffix || currentModeSuffix();
    return colors[token + "-" + suffix]
      || colors[token]
      || (suffix === "dark" ? "#888" : "#666");
  }

  function buildPalette(colors, suffix) {
    suffix = suffix || currentModeSuffix();
    var c = function (t) { return colorToken(t, colors, suffix); };
    return {
      bg:      c("primary-bg"),
      bubble:  c("bubble-bg"),
      sunken:  c("sunken"),
      text:    c("text-main"),
      accent:  c("accent"),
      warn:    c("warn"),
      caution: c("caution"),
      success: c("success"),
      link:    c("link"),
    };
  }

  // Type → palette token mapping (no hex, no high-level instruments)
  function typeColor(type, palette) {
    var map = {
      entitlement:      palette.link,
      obligation:       palette.warn,
      schedule:         palette.accent,
      condition:        palette.caution,
      allocation:       palette.success,
      event:            palette.accent,
      ledger_account:   palette.sunken || palette.bubble,
      ledger_transaction: palette.success,
      asset:            palette.success,
      resource_type:    palette.link,
      contract:         palette.accent,
      agreement:        palette.accent,
      manual:           palette.caution,
      note:             palette.caution,
    };
    return map[type] || palette.text;
  }

  // CSS custom props for legend swatches
  function setLegendVars(palette) {
    var root = document.documentElement;
    root.style.setProperty("--cy-legend-entitlement",   palette.link);
    root.style.setProperty("--cy-legend-obligation",    palette.warn);
    root.style.setProperty("--cy-legend-schedule",      palette.accent);
    root.style.setProperty("--cy-legend-condition",     palette.caution);
    root.style.setProperty("--cy-legend-allocation",    palette.success);
    root.style.setProperty("--cy-legend-event",         palette.accent);
    root.style.setProperty("--cy-legend-account",       palette.sunken || palette.bubble);
    root.style.setProperty("--cy-legend-asset",         palette.success);
    root.style.setProperty("--cy-legend-transaction",   palette.success);
    root.style.setProperty("--cy-legend-contract",      palette.accent);
    root.style.setProperty("--cy-legend-note",          palette.caution);
  }

  // ---------------------------------------------------------------------------
  // Cytoscape stylesheet builder
  // ---------------------------------------------------------------------------

  function buildStyles(palette) {
    return [
      {
        selector: "node",
        style: {
          "background-color": palette.bubble,
          "border-color": "data(nodeColor)",
          "border-width": 2,
          "label": "data(label)",
          "color": palette.text,
          "font-size": 10,
          "font-weight": "bold",
          "text-valign": "center",
          "text-halign": "center",
          "text-wrap": "wrap",
          "text-max-width": 100,
          "width": 110,
          "height": 44,
          "shape": "data(shape)",
        },
      },
      {
        selector: "node[?is_manual]",
        style: {
          "border-style": "dashed",
          "opacity": 0.85,
        },
      },
      {
        selector: "node:selected",
        style: {
          "border-width": 3,
          "border-color": palette.accent,
          "background-color": palette.accent,
          "color": palette.bg,
        },
      },
      {
        selector: "edge",
        style: {
          "width": 1.5,
          "line-color": palette.accent,
          "target-arrow-color": palette.accent,
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          "label": "data(label)",
          "font-size": 9,
          "color": palette.text,
          "text-background-color": palette.bubble,
          "text-background-opacity": 0.85,
          "text-background-padding": "2px",
          "text-rotation": "autorotate",
          "text-margin-y": -6,
          "opacity": 0.8,
        },
      },
      {
        selector: "edge:selected",
        style: {
          "width": 2.5,
          "line-color": palette.accent,
          "target-arrow-color": palette.accent,
          "opacity": 1,
        },
      },
    ];
  }

  // ---------------------------------------------------------------------------
  // Main render function
  // ---------------------------------------------------------------------------

  var _instances = {};

  function renderClaimsMesh(containerId, url, options) {
    options = options || {};
    var container = document.getElementById(containerId);
    if (!container) { console.warn("renderClaimsMesh: container not found:", containerId); return; }

    var colors = getOyaColors();
    var suffix = currentModeSuffix();
    var palette = buildPalette(colors, suffix);
    setLegendVars(palette);
    container.style.backgroundColor = palette.bg;

    container.style.opacity = "0.4";

    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var nodes = data.nodes || [];
        var edges = data.edges || [];

        if (!nodes.length && !edges.length) {
          container.style.opacity = "1";
          container.innerHTML = '<p style="padding:1.5rem;font-size:.875rem;opacity:.5;text-align:center">No graph data available.</p>';
          return;
        }

        var elements = [];
        nodes.forEach(function (n) {
          var d = n.data || n;
          elements.push({
            data: {
              id:        d.id,
              label:     d.label || d.id,
              type:      d.type || "manual",
              shape:     d.shape || "ellipse",
              is_manual: d.is_manual || false,
              status:    d.status || "",
              url:       d.url || "",
              description: d.description || "",
              nodeColor: typeColor(d.type || "manual", palette),
            },
          });
        });
        edges.forEach(function (e) {
          var d = e.data || e;
          elements.push({ data: { source: d.source, target: d.target, label: d.label || d.type, type: d.type, description: d.description || "" } });
        });

        if (_instances[containerId]) {
          _instances[containerId].destroy();
          delete _instances[containerId];
        }

        var cy = cytoscape({
          container: container,
          elements: elements,
          style: buildStyles(palette),
          layout: {
            name: "breadthfirst",
            directed: true,
            spacingFactor: 1.5,
            padding: 24,
            animate: true,
            animationDuration: 350,
          },
          wheelSensitivity: 0.2,
          minZoom: 0.1,
          maxZoom: 4,
        });
        _instances[containerId] = cy;
        container.style.opacity = "1";

        // Node click → info panel
        var infoPanel = document.getElementById("cy-node-info");
        var labelEl   = document.getElementById("cy-node-label");
        var typeEl    = document.getElementById("cy-node-type");
        var statusEl  = document.getElementById("cy-node-status");
        var manualEl  = document.getElementById("cy-node-manual");
        var descEl    = document.getElementById("cy-node-desc");
        var linkEl    = document.getElementById("cy-node-link");

        cy.on("tap", "node", function (evt) {
          var d = evt.target.data();
          if (labelEl)  labelEl.textContent  = d.label  || "";
          if (typeEl)   typeEl.textContent   = d.type   || "";
          if (statusEl) statusEl.textContent = d.status ? "· " + d.status : "";
          if (descEl)   descEl.textContent   = d.description || "";
          if (manualEl) { d.is_manual ? manualEl.classList.remove("hidden") : manualEl.classList.add("hidden"); }
          if (linkEl) {
            if (d.url) { linkEl.href = d.url; linkEl.classList.remove("hidden"); }
            else        { linkEl.classList.add("hidden"); }
          }
          if (infoPanel) infoPanel.classList.remove("hidden");
        });
        cy.on("tap", function (evt) {
          if (evt.target === cy && infoPanel) infoPanel.classList.add("hidden");
        });

        new ResizeObserver(function () { if (cy) { cy.resize(); cy.fit(); } }).observe(container);

        // Fit / reload buttons (by convention, prefix = containerId)
        var fitBtn    = document.getElementById("cy-fit-btn");
        var reloadBtn = document.getElementById("cy-reload-btn");
        if (fitBtn)    fitBtn.addEventListener("click",    function () { cy && cy.fit(); });
        if (reloadBtn) reloadBtn.addEventListener("click", function () { renderClaimsMesh(containerId, url, options); });

        // Re-render on theme toggle
        var toggle = document.querySelector(".site-theme-toggle-button");
        if (toggle) {
          toggle.addEventListener("click", function () {
            setTimeout(function () { renderClaimsMesh(containerId, url, options); }, 60);
          });
        }
      })
      .catch(function (err) {
        container.style.opacity = "1";
        container.innerHTML = '<p style="padding:1.5rem;font-size:.875rem;opacity:.5;text-align:center">Could not load Claims Mesh.</p>';
        console.error("renderClaimsMesh fetch error:", err);
      });
  }

  // ---------------------------------------------------------------------------
  // Cytoscape loader
  // ---------------------------------------------------------------------------

  function loadCytoscape(cb) {
    if (window.cytoscape) { cb(); return; }
    var s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.29.2/cytoscape.min.js";
    s.onload = cb;
    document.head.appendChild(s);
  }

  // Public wrapper that ensures Cytoscape is loaded first
  function renderClaimsMeshSafe(containerId, url, options) {
    loadCytoscape(function () { renderClaimsMesh(containerId, url, options); });
  }

  // Expose
  global.getOyaColors      = getOyaColors;
  global.colorToken        = colorToken;
  global.currentModeSuffix = currentModeSuffix;
  global.buildPalette      = buildPalette;
  global.typeColor         = typeColor;
  global.renderClaimsMesh  = renderClaimsMeshSafe;

})(window);

/**
 * community_chain_graph.js
 * Cytoscape renderer for the Administrata community chain view.
 * Communities = rounded rectangles, persons = circles, parent→child arrows.
 * Reads Oya theme colors from the #oya-theme-colors JSON script tag.
 */

(function (global) {
  "use strict";

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
    return colors[token + "-" + suffix] || colors[token] || (suffix === "dark" ? "#888" : "#666");
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

  function buildStyles(palette) {
    return [
      {
        selector: "node[type = 'community']",
        style: {
          "background-color": palette.bubble,
          "border-color": palette.accent,
          "border-width": 2,
          "label": "data(label)",
          "color": palette.text,
          "font-size": 11,
          "font-weight": "bold",
          "text-valign": "center",
          "text-halign": "center",
          "text-wrap": "wrap",
          "text-max-width": 120,
          "width": 140,
          "height": 50,
          "shape": "roundrectangle",
        },
      },
      {
        selector: "node[type = 'person']",
        style: {
          "background-color": palette.sunken,
          "border-color": palette.link,
          "border-width": 2,
          "label": "data(label)",
          "color": palette.text,
          "font-size": 10,
          "font-weight": "normal",
          "text-valign": "bottom",
          "text-halign": "center",
          "text-margin-y": 5,
          "text-wrap": "wrap",
          "text-max-width": 90,
          "width": 46,
          "height": 46,
          "shape": "ellipse",
        },
      },
      {
        selector: "node:selected",
        style: {
          "border-width": 3,
          "border-color": palette.warn,
          "background-color": palette.accent,
          "color": palette.bg,
        },
      },
      {
        selector: "edge[type = 'parent_child']",
        style: {
          "width": 2,
          "line-color": palette.accent,
          "target-arrow-color": palette.accent,
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          "opacity": 0.85,
        },
      },
      {
        selector: "edge[type = 'head']",
        style: {
          "width": 1.5,
          "line-color": palette.link,
          "curve-style": "bezier",
          "label": "head",
          "font-size": 9,
          "color": palette.text,
          "text-background-color": palette.bubble,
          "text-background-opacity": 0.85,
          "text-background-padding": "2px",
          "text-rotation": "autorotate",
          "text-margin-y": -6,
          "opacity": 0.7,
        },
      },
      {
        selector: "edge:selected",
        style: {
          "width": 3,
          "opacity": 1,
        },
      },
    ];
  }

  var _instances = {};

  function renderCommunityChain(containerId, url, options) {
    options = options || {};
    var container = document.getElementById(containerId);
    if (!container) { console.warn("renderCommunityChain: container not found:", containerId); return; }

    var colors = getOyaColors();
    var suffix = currentModeSuffix();
    var palette = buildPalette(colors, suffix);
    container.style.backgroundColor = palette.bg;
    container.style.opacity = "0.4";

    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var nodes = data.nodes || [];
        var edges = data.edges || [];

        if (!nodes.length) {
          container.style.opacity = "1";
          container.innerHTML = '<p style="padding:1.5rem;font-size:.875rem;opacity:.5;text-align:center">No community data available.</p>';
          return;
        }

        var elements = [];
        nodes.forEach(function (n) {
          elements.push({
            data: {
              id:    n.id,
              label: n.label || n.id,
              type:  n.type || "community",
              shape: n.shape || "roundrectangle",
              url:   n.url || "",
            },
          });
        });
        edges.forEach(function (e) {
          elements.push({
            data: {
              source: e.source,
              target: e.target,
              label:  e.label || "",
              type:   e.type  || "parent_child",
            },
          });
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
            spacingFactor: 1.6,
            padding: 30,
            animate: true,
            animationDuration: 400,
          },
          wheelSensitivity: 0.2,
          minZoom: 0.1,
          maxZoom: 4,
        });
        _instances[containerId] = cy;
        container.style.opacity = "1";

        // Node click → info panel
        var infoPanel = document.getElementById("cy-chain-info");
        var labelEl   = document.getElementById("cy-chain-label");
        var typeEl    = document.getElementById("cy-chain-type");
        var linkEl    = document.getElementById("cy-chain-link");

        cy.on("tap", "node", function (evt) {
          var d = evt.target.data();
          if (labelEl)  labelEl.textContent  = d.label || "";
          if (typeEl)   typeEl.textContent   = d.type === "person" ? "Person" : "Community";
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

        var fitBtn    = document.getElementById("cy-chain-fit-btn");
        var reloadBtn = document.getElementById("cy-chain-reload-btn");
        if (fitBtn)    fitBtn.addEventListener("click",    function () { cy && cy.fit(); });
        if (reloadBtn) reloadBtn.addEventListener("click", function () { renderCommunityChain(containerId, url, options); });

        var toggle = document.querySelector(".site-theme-toggle-button");
        if (toggle) {
          toggle.addEventListener("click", function () {
            setTimeout(function () { renderCommunityChain(containerId, url, options); }, 60);
          });
        }
      })
      .catch(function (err) {
        container.style.opacity = "1";
        container.innerHTML = '<p style="padding:1.5rem;font-size:.875rem;opacity:.5;text-align:center">Could not load community chain.</p>';
        console.error("renderCommunityChain fetch error:", err);
      });
  }

  function loadCytoscape(cb) {
    if (window.cytoscape) { cb(); return; }
    var s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.29.2/cytoscape.min.js";
    s.onload = cb;
    document.head.appendChild(s);
  }

  function renderCommunityChainSafe(containerId, url, options) {
    loadCytoscape(function () { renderCommunityChain(containerId, url, options); });
  }

  global.renderCommunityChain = renderCommunityChainSafe;

})(window);

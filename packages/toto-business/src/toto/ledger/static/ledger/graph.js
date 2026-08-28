/*
 * The Business Center's Cytoscape renderer, adapted from Social Hub's community-chain view.
 * One lifecycle serves organization trees and ledger chains.
 */
(function (global) {
  "use strict";

  var instances = {};

  function colors() {
    var node = document.getElementById("bc-theme-colors");
    if (!node) return {};
    try { return JSON.parse(node.textContent) || {}; } catch (_) { return {}; }
  }

  function darkMode() {
    try {
      return typeof Alpine !== "undefined" &&
        Alpine.evaluate(document.documentElement, "darkMode");
    } catch (_) {
      return document.documentElement.classList.contains("dark");
    }
  }

  function palette() {
    var source = colors();
    var suffix = darkMode() ? "dark" : "light";
    function token(name, fallback) {
      return source[name + "-" + suffix] || source[name] || fallback;
    }
    return {
      background: token("primary-bg", suffix === "dark" ? "#111827" : "#f8fafc"),
      surface: token("bubble-bg", suffix === "dark" ? "#1f2937" : "#ffffff"),
      sunken: token("sunken", suffix === "dark" ? "#0f172a" : "#e2e8f0"),
      text: token("text-main", suffix === "dark" ? "#f8fafc" : "#111827"),
      accent: token("accent", "#2563eb"),
      link: token("link", "#0ea5e9"),
      success: token("success", "#16a34a"),
      warn: token("warn", "#dc2626")
    };
  }

  function graphStyles(p) {
    return [
      {
        selector: "node",
        style: {
          "background-color": p.surface,
          "border-color": p.accent,
          "border-width": 2,
          "label": "data(label)",
          "color": p.text,
          "font-size": 11,
          "font-weight": "bold",
          "text-valign": "center",
          "text-halign": "center",
          "text-wrap": "wrap",
          "text-max-width": 130,
          "width": 150,
          "height": 54,
          "shape": "roundrectangle"
        }
      },
      {
        selector: "node[type = 'company']",
        style: {
          "background-color": p.accent,
          "color": p.background,
          "width": 180,
          "height": 62
        }
      },
      {
        selector: "node[type = 'person']",
        style: {
          "background-color": p.sunken,
          "border-color": p.link,
          "font-weight": "normal",
          "font-size": 10,
          "width": 70,
          "height": 70,
          "shape": "ellipse"
        }
      },
      {
        selector: "node[type = 'ledger_entry']",
        style: {
          "width": 180,
          "height": 68,
          "font-family": "monospace",
          "font-size": 10
        }
      },
      {
        selector: "node:selected",
        style: {
          "border-width": 4,
          "border-color": p.warn
        }
      },
      {
        selector: "edge",
        style: {
          "width": 2,
          "line-color": p.accent,
          "target-arrow-color": p.accent,
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          "opacity": 0.85
        }
      },
      {
        selector: "edge[type = 'head']",
        style: {
          "line-color": p.link,
          "target-arrow-color": p.link,
          "line-style": "dashed",
          "label": "data(label)",
          "font-size": 9,
          "color": p.text,
          "text-background-color": p.surface,
          "text-background-opacity": 0.9,
          "text-background-padding": 2
        }
      },
      {
        selector: "edge[type = 'member']",
        style: {
          "line-color": p.link,
          "target-arrow-color": p.link,
          "width": 1,
          "opacity": 0.55
        }
      },
      {
        selector: "edge[type = 'supersedes']",
        style: {
          "line-style": "dashed",
          "line-color": p.warn,
          "target-arrow-color": p.warn
        }
      }
    ];
  }

  function renderTotoGraph(containerId, url, options) {
    options = options || {};
    var container = document.getElementById(containerId);
    if (!container || !url) return;
    var p = palette();
    container.style.backgroundColor = p.background;
    container.style.opacity = "0.45";

    fetch(url, {headers: {"Accept": "application/json"}})
      .then(function (response) {
        if (!response.ok) throw new Error("Graph request returned " + response.status);
        return response.json();
      })
      .then(function (data) {
        if (instances[containerId]) {
          instances[containerId].destroy();
          delete instances[containerId];
        }
        var nodes = (data.nodes || []).map(function (node) {
          return {data: node};
        });
        var edges = (data.edges || []).map(function (edge, index) {
          return {data: Object.assign({id: "edge-" + index}, edge)};
        });
        if (!nodes.length) {
          container.textContent = options.emptyText || "No graph data.";
          container.style.opacity = "1";
          return;
        }
        var cy = cytoscape({
          container: container,
          elements: nodes.concat(edges),
          style: graphStyles(p),
          layout: {
            name: options.layout || "breadthfirst",
            directed: true,
            spacingFactor: 1.45,
            padding: 28,
            animate: false
          },
          wheelSensitivity: 0.2,
          minZoom: 0.1,
          maxZoom: 4
        });
        instances[containerId] = cy;
        container.style.opacity = "1";
        cy.resize();
        cy.fit();

        var info = document.getElementById(containerId + "-info");
        var label = document.getElementById(containerId + "-label");
        var type = document.getElementById(containerId + "-type");
        var link = document.getElementById(containerId + "-link");
        cy.on("tap", "node", function (event) {
          var data = event.target.data();
          if (label) label.textContent = data.label || "";
          if (type) type.textContent = (data.type || "").replace("_", " ");
          if (link) {
            link.href = data.url || "#";
            link.classList.toggle("hidden", !data.url);
          }
          if (info) info.classList.remove("hidden");
        });
        cy.on("dbltap", "node", function (event) {
          var target = event.target.data("url");
          if (target) global.location.assign(target);
        });
        cy.on("tap", function (event) {
          if (event.target === cy && info) info.classList.add("hidden");
        });

        if (!container.dataset.resizeObserved) {
          new ResizeObserver(function () {
            var current = instances[containerId];
            if (current) {
              current.resize();
              current.fit();
            }
          }).observe(container);
          container.dataset.resizeObserved = "1";
        }
      })
      .catch(function (error) {
        container.style.opacity = "1";
        container.textContent = options.errorText || "Could not load graph.";
        console.error("Irena graph error:", error);
      });
  }

  function fitTotoGraph(containerId) {
    if (instances[containerId]) instances[containerId].fit();
  }

  global.renderTotoGraph = renderTotoGraph;
  global.fitTotoGraph = fitTotoGraph;
})(window);

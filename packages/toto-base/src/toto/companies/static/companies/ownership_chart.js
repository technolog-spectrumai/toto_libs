/* The ownership ring of a company's Shareholdings tab (2026-10-06).
 *
 * The owner: "add pie chart to show the structure of ownsership - use
 * similat trick that file vault how mauch each file types takes space". So
 * it is the vault's "files by type" ring: Chart.js, a doughnut with a 60%
 * hole, the legend under it.
 *
 * What it draws is DATA, handed by the template as a json_script
 * (toto.companies.register.chart_of): labels, values, shares, colours. A
 * holder's name is a label Chart.js paints on its canvas; nothing here
 * writes markup, and nothing is sent anywhere.
 *
 * A slice's tip states the holder, the shares as the register has them, and
 * the percentage of the slices now shown: Chart.js lets a reader switch a
 * holder off in the legend, and the rest are then shares of what is left.
 *
 * Without Chart.js (the script did not load) nothing is drawn; the table
 * beside the ring says everything the ring would.
 */
(function (root) {
  "use strict";

  function percentOf(value, total) {
    return total > 0 ? (value * 100 / total).toFixed(2) : "0.00";
  }

  /* The total of the slices a reader has not switched off. */
  function shownTotal(chart, values) {
    var total = 0;
    for (var at = 0; at < values.length; at++) {
      if (chart.getDataVisibility(at)) { total += Number(values[at]) || 0; }
    }
    return total;
  }

  function tip(data) {
    return function (item) {
      var values = item.chart.data.datasets[item.datasetIndex].data;
      var value = Number(values[item.dataIndex]) || 0;
      var shares = (data.shares || [])[item.dataIndex];
      return item.label + ": " + (shares === undefined ? String(value) : shares) +
        " (" + percentOf(value, shownTotal(item.chart, values)) + "%)";
    };
  }

  function draw(canvas) {
    var Chart = root.Chart;
    var source = root.document.getElementById(canvas.dataset.companyChart || "");
    if (!Chart || !source) { return null; }
    var data;
    try { data = JSON.parse(source.textContent); } catch (error) { return null; }
    if (!data || !data.values || !data.values.length) { return null; }
    var dark = false;
    try { dark = root.localStorage.getItem("darkMode") === "true"; } catch (error) { dark = false; }
    return new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: data.labels,
        datasets: [{data: data.values, backgroundColor: data.colours, borderWidth: 0}]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "60%",
        plugins: {
          legend: {position: "bottom",
                   labels: {boxWidth: 12, padding: 12, color: dark ? "#e2e8f0" : "#374151"}},
          tooltip: {callbacks: {label: tip(data)}}
        }
      }
    });
  }

  var api = {percentOf: percentOf, shownTotal: shownTotal, tip: tip, draw: draw};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
  root.CompanyOwnershipChart = api;

  if (root.document && root.document.querySelectorAll) {
    var all = function () {
      Array.prototype.forEach.call(
        root.document.querySelectorAll("canvas[data-company-chart]"), draw);
    };
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", all);
    } else { all(); }
  }
})(typeof window !== "undefined" ? window : globalThis);

/* The classical map pin, drawn inline. Suite copy of the shape the
 * placidia host proved out (its static/placidia/pin.js).
 *
 * Leaflet's stock icon resolves marker-icon.png RELATIVE TO THE STYLESHEET.
 * Under production's manifest storage every static file carries a hashed
 * name, so that request 404s and the marker renders as a broken image —
 * while DEBUG serves unhashed names and never shows the problem. An inline
 * SVG has nothing to resolve, in either mode. */
window.classicPin = function () {
  return L.divIcon({
    className: "classic-pin",
    html: '<svg xmlns="http://www.w3.org/2000/svg" width="25" height="41"'
        + ' viewBox="0 0 25 41">'
        + '<path d="M12.5 0.5C5.9 0.5 0.5 5.9 0.5 12.5c0 9 12 27.5 12 27.5'
        + 's12-18.5 12-27.5C24.5 5.9 19.1 0.5 12.5 0.5z"'
        + ' fill="#2a81cb" stroke="#1e5d92"/>'
        + '<circle cx="12.5" cy="12.5" r="4.5" fill="#fff"/></svg>',
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    tooltipAnchor: [1, -24],
  });
};

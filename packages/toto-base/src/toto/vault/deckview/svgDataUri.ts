// SVG text as a picture's address.
//
// The one way this app SHOWS an SVG it did not write: as the `src` of an
// `<img>`. A browser draws a picture in a mode of its own — no script in it
// runs, nothing it points at is fetched, nothing in it can be clicked — so the
// file can hold anything and the page is none the wiser. Web Play shows a
// drawing this way (../web/WebHost.vue), a slide's `svg` block does
// (../web/playDeck.ts), and so does the drawing editor's View
// (./SketchEditor.vue), which is why it lives apart from all three.
//
// Both policies already let it through: `img-src data:` is in the desktop's
// (src-tauri/tauri.conf.json) and in the web page's (../web/csp.ts).

/** Text as a `data:` URI for an `<img>`. Chunked: spreading a whole file into
 *  one argument list blows the call limit (see petalite's `toDataUri`, which
 *  has no entry for SVG on purpose). */
export function svgDataUri(svg: string): string {
  const bytes = new TextEncoder().encode(svg);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return `data:image/svg+xml;base64,${btoa(binary)}`;
}

// ── reading it permissively ─────────────────────────────────────────────────
// A picture is read by the browser's XML parser, which is strict: the root
// must be an <svg> in the SVG namespace, every prefix must be declared, every
// tag closed. A great deal of real SVG is none of that, because it was written
// to sit INLINE in a web page, where the HTML parser reads it and asks for
// nothing — no `xmlns`, no `xmlns:xlink`, unquoted attributes, `&nbsp;`. Such
// text draws on every site that shows SVG and would not draw here.
//
// So the View asks twice. As it stands first, because that is the file itself
// and the only reading that knows a DOCTYPE's entities. Then the way a page
// would have read it: through the HTML parser, into a document that is not
// this page's (nothing in it loads or runs), and written back out by the XML
// serialiser as a document of its own — which is where the namespaces it never
// declared get declared.

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * Whether `text` is a picture exactly as it stands: well-formed, with an
 * `<svg>` in the SVG namespace at its root. Answers `true` for anything with
 * a DOCTYPE without reading it — entity expansion is the parser-level trap
 * the drawing engine refuses to walk into, and this is not the place to walk
 * into it either. The picture finds out, behind the browser's own limits.
 */
export function standsAlone(text: string): boolean {
  if (/<!DOCTYPE|<!ENTITY/i.test(text)) return true;
  if (typeof DOMParser === "undefined") return true;
  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  const root = doc.documentElement;
  return Boolean(root) && !doc.querySelector("parsererror")
    && root.localName === "svg" && root.namespaceURI === SVG_NS;
}

/**
 * `text` as a web page would read an inline `<svg>`, written out as a
 * document of its own; "" when there is no `<svg>` in it at all.
 */
export function inlineSvgAsPicture(text: string): string {
  if (typeof DOMParser === "undefined" || typeof XMLSerializer === "undefined") return "";
  try {
    const svg = new DOMParser().parseFromString(text, "text/html").querySelector("svg");
    return svg ? new XMLSerializer().serializeToString(svg) : "";
  } catch {
    return "";
  }
}

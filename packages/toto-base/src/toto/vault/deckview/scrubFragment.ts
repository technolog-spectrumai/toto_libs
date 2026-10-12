// Markup for INSIDE the app's own page, through an allow-list.
//
// Ported from zenobia's `toto.htmlview.scrub.scrub_fragment`, rule for rule.
// `security/scrub.ts` is the wrong tool here: it STRIPS chosen things from a
// block rendered in a shadow root, and keeps everything else. A rendered note
// is put into the document, where what that one keeps on purpose becomes the
// danger — a `<style>` restyles the app, a `style` attribute lays an overlay
// over it, a stray `</main>` closes its markup. So nothing survives here
// unless it is named: every tag, attribute and text node is written out again
// by this module, unknown tags are unwrapped, and the dangerous ones go with
// their contents.
//
// What survives is what GitHub-flavoured Markdown produces and a README's raw
// HTML commonly says: headings with ids, tables (a cell's `text-align` becomes
// a class), lists, checkboxes forced disabled, `<details>`, `<kbd>`,
// `<sub>`/`<sup>`, `<p align="center">`, images. No `style`, no author's
// `class` beyond the known tokens, no `on*`. A link is http(s), mailto, a
// `#fragment` or what the resolver makes of a relative path; anything else
// reads as its text. An image is what the resolver gives, or a `data:image/`
// raster; an external one becomes a link and is never loaded.

/** Dropped with their content. */
const FRAGMENT_DROP = new Set([
  "script", "iframe", "object", "embed", "applet", "frame", "frameset",
  "style", "template", "noscript", "noembed", "noframes", "textarea", "select",
  "option", "svg", "math", "head", "title", "xmp", "canvas", "audio", "video",
  "picture", "source", "track", "form", "button", "meta", "link", "base",
]);

/** Every tag a fragment may keep. */
export const FRAGMENT_TAGS: ReadonlySet<string> = new Set([
  "h1", "h2", "h3", "h4", "h5", "h6", "p", "a", "blockquote", "ul", "ol", "li",
  "dl", "dt", "dd", "code", "pre", "table", "thead", "tbody", "tfoot", "tr", "th",
  "td", "hr", "br", "wbr", "img", "strong", "b", "em", "i", "del", "s", "strike",
  "ins", "u", "mark", "kbd", "samp", "tt", "var", "small", "sup", "sub", "abbr",
  "q", "details", "summary", "div", "span", "input",
]);

/** Class tokens the converter writes (or an author may), kept as they are;
 *  `markdown.css` styles them. Every other class is dropped. */
export const KNOWN_CLASSES: ReadonlySet<string> = new Set([
  "headerlink", "footnote", "footnote-ref", "footnote-backref", "toc",
  "task-list-item", "contains-task-list",
  "md-mermaid", "md-mermaid-source", "md-mermaid-svg",
  "md-math", "md-math-display",
]);

const VOID = new Set(["br", "hr", "img", "input", "wbr"]);
const ATTRS: Record<string, readonly string[]> = {
  a: ["title"],
  td: ["colspan", "rowspan"],
  th: ["colspan", "rowspan"],
  ol: ["start"],
  abbr: ["title"],
};
const ID_TAGS = new Set(["h1", "h2", "h3", "h4", "h5", "h6", "li", "sup", "a", "div", "span", "dt", "p"]);
const ALIGN_TAGS = new Set(["p", "div", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"]);
const ALIGN: Record<string, string> = {
  left: "md-align-left", center: "md-align-center", right: "md-align-right", justify: "md-align-justify",
};
const ID = /^[\p{L}\p{N}_\-:.]{1,128}$/u;
const COUNT = /^\d{1,4}$/;
const SIZE = /^\d{1,5}%?$/;
const LANGUAGE = /^language-([\w+#.\-]{1,40})$/;
const CELL_ALIGN = /^\s*text-align\s*:\s*(left|center|right|justify)\s*;?\s*$/i;
const DATA_RASTER = /^data:image\/(png|jpe?g|gif|webp);base64,[a-z0-9+/=]+$/i;
const LINK_SCHEMES = new Set(["http", "https", "mailto"]);
const EXECUTABLE_SCHEMES = new Set(["javascript", "vbscript", "livescript", "mocha"]);
const SAFE_DATA_PREFIXES = ["image/", "font/", "audio/", "video/"];
/** Everything a browser ignores inside a URL. Stripped before the scheme is
 *  read: `java&#9;script:` hides its control character in the MIDDLE. */
const URL_NOISE = /[\x00-\x20\x7f]/g;
const SCHEME = /^([a-z][a-z0-9+.\-]*):/i;
const XHTML = "http://www.w3.org/1999/xhtml";
const EXTERNAL_REL = "nofollow noopener noreferrer";

/** Every id a fragment writes carries this, and every `#fragment` link with
 *  it, so a heading called "app" cannot collide with the page around it. */
export const ID_PREFIX = "md-";

/** What a relative path becomes. Each answers the value to carry (in
 *  `data-md-link` / `data-md-src`), or null for "show the text only". */
export interface FragmentResolver {
  link(path: string): string | null;
  image(path: string): string | null;
}

export type UrlKind = "fragment" | "external" | "data" | "relative" | "bad";

function isExecutableUrl(value: string): boolean {
  const collapsed = value.replace(URL_NOISE, "");
  const match = SCHEME.exec(collapsed);
  if (!match) return false; // relative: no scheme, nothing to run
  const scheme = match[1].toLowerCase();
  if (EXECUTABLE_SCHEMES.has(scheme)) return true;
  if (scheme === "data") {
    const body = collapsed.slice(match[0].length).toLowerCase();
    return !SAFE_DATA_PREFIXES.some((prefix) => body.startsWith(prefix));
  }
  return false;
}

export function urlKind(value: string): [UrlKind, string] {
  const raw = (value ?? "").trim();
  const collapsed = raw.replace(URL_NOISE, "");
  if (!collapsed) return ["bad", ""];
  if (collapsed.startsWith("#")) return ["fragment", raw.slice(raw.indexOf("#") + 1)];
  if (isExecutableUrl(raw)) return ["bad", ""];
  const match = SCHEME.exec(collapsed);
  if (match) {
    const scheme = match[1].toLowerCase();
    if (LINK_SCHEMES.has(scheme)) return ["external", raw];
    if (scheme === "data") return ["data", collapsed]; // case kept: base64 is case-sensitive
    return ["bad", ""];
  }
  // Another host, spelled to slip past a scheme check: not a path.
  if (collapsed.startsWith("//") || collapsed.startsWith("\\") || collapsed.startsWith("/\\")) {
    return ["bad", ""];
  }
  return ["relative", raw];
}

function escapeText(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escapeAttr(value: string): string {
  return escapeText(value).replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function attr(name: string, value: string): string {
  return ` ${name}="${escapeAttr(value)}"`;
}

interface Context {
  resolver: FragmentResolver | null;
  out: string[];
  /** Depth inside a kept `<a>`: no link inside a link. */
  links: number;
}

function classes(el: Element, tag: string): string {
  const parts = (el.getAttribute("class") ?? "").split(/\s+/).filter((token) => KNOWN_CLASSES.has(token));
  const align = (el.getAttribute("align") ?? "").trim().toLowerCase();
  if (ALIGN_TAGS.has(tag) && ALIGN[align]) parts.push(ALIGN[align]);
  if (tag === "td" || tag === "th") {
    const cell = CELL_ALIGN.exec(el.getAttribute("style") ?? "");
    if (cell) parts.push(ALIGN[cell[1].toLowerCase()]);
  }
  return parts.join(" ");
}

/** The attributes of a kept link, or null when the link is refused and reads
 *  as its text. */
function link(value: string, ctx: Context): string | null {
  const [kind, url] = urlKind(value);
  if (kind === "fragment") return attr("href", url ? `#${ID_PREFIX}${url}` : "#");
  if (kind === "external") return attr("href", url) + attr("rel", EXTERNAL_REL);
  if (kind === "relative" && ctx.resolver) {
    const target = ctx.resolver.link(url);
    // The address is carried, not navigated to: the page has one document,
    // and the preview opens the file itself when the link is clicked.
    if (target !== null) return attr("href", "#") + attr("data-md-link", target);
  }
  return null;
}

function image(el: Element, ctx: Context): void {
  const alt = el.getAttribute("alt") ?? "";
  const [kind, url] = urlKind(el.getAttribute("src") ?? "");
  let source: string | null = null;
  if (kind === "data" && DATA_RASTER.test(url)) {
    source = attr("src", url);
  } else if (kind === "relative" && ctx.resolver) {
    // No `src` yet: the preview reads the file and fills it in.
    const target = ctx.resolver.image(url);
    if (target !== null) source = attr("data-md-src", target);
  }
  if (source !== null) {
    const parts = [source, attr("alt", alt)];
    const title = el.getAttribute("title");
    if (title) parts.push(attr("title", title));
    for (const name of ["width", "height"]) {
      const value = el.getAttribute(name) ?? "";
      if (SIZE.test(value)) parts.push(attr(name, value));
    }
    ctx.out.push(`<img${parts.join("")}>`);
    return;
  }
  const scheme = kind === "external" ? SCHEME.exec(url.replace(URL_NOISE, "")) : null;
  if (scheme && ["http", "https"].includes(scheme[1].toLowerCase()) && ctx.links === 0) {
    // Shown as a link, never fetched: the fetch would tell the image's host
    // who is reading, and when.
    ctx.out.push(
      `<a${attr("href", url)}${attr("rel", EXTERNAL_REL)} class="md-external-image">${escapeText(alt || url)}</a>`,
    );
    return;
  }
  if (alt) ctx.out.push(escapeText(alt));
}

function children(node: Node, ctx: Context): void {
  for (const child of Array.from(node.childNodes)) walk(child, ctx);
}

function walk(node: Node, ctx: Context): void {
  if (node.nodeType === 3) {
    ctx.out.push(escapeText(node.nodeValue ?? ""));
    return;
  }
  if (node.nodeType !== 1) return; // comments, processing instructions, doctypes
  const el = node as Element;
  const tag = el.localName.toLowerCase();
  if (el.namespaceURI !== XHTML || FRAGMENT_DROP.has(tag)) return;
  if (!FRAGMENT_TAGS.has(tag)) {
    children(el, ctx);
    return;
  }
  if (tag === "img") {
    image(el, ctx);
    return;
  }
  if (tag === "input") {
    if ((el.getAttribute("type") ?? "").toLowerCase() === "checkbox") {
      ctx.out.push(`<input type="checkbox" disabled${el.hasAttribute("checked") ? " checked" : ""}>`);
    }
    return;
  }

  const parts: string[] = [];
  if (tag === "a") {
    if (ctx.links > 0) {
      children(el, ctx); // no link inside a link
      return;
    }
    if (el.hasAttribute("href")) {
      const href = link(el.getAttribute("href") ?? "", ctx);
      if (href === null) {
        children(el, ctx); // a refused link reads as its text
        return;
      }
      parts.push(href);
    }
  }
  for (const name of ATTRS[tag] ?? []) {
    const value = el.getAttribute(name);
    if (value === null) continue;
    if (name === "colspan" || name === "rowspan" || name === "start") {
      if (COUNT.test(value)) parts.push(attr(name, value));
    } else {
      parts.push(attr(name, value));
    }
  }
  if (tag === "details" && el.hasAttribute("open")) parts.push(" open");
  if (tag === "code") {
    for (const token of (el.getAttribute("class") ?? "").split(/\s+/)) {
      const language = LANGUAGE.exec(token);
      if (language) {
        parts.push(attr("data-lang", language[1]));
        break;
      }
    }
  }
  const ident = el.getAttribute("id") ?? "";
  if (ident && ID_TAGS.has(tag) && ID.test(ident)) parts.unshift(attr("id", ID_PREFIX + ident));
  const css = classes(el, tag);
  if (css) parts.push(attr("class", css));

  ctx.out.push(`<${tag}${parts.join("")}>`);
  if (VOID.has(tag)) return;
  if (tag === "a") ctx.links += 1;
  children(el, ctx);
  if (tag === "a") ctx.links -= 1;
  ctx.out.push(`</${tag}>`);
}

/**
 * `html` through the allow-list, as a string that is safe to put in the page.
 *
 * Parsed, never regexed, and parsed into a document that is not the page's:
 * nothing in it loads or runs. Never throws: a parser that gives up yields the
 * input escaped.
 */
export function scrubFragment(html: string, options: { resolver?: FragmentResolver | null } = {}): string {
  const source = html ?? "";
  if (typeof DOMParser === "undefined") return escapeText(source);
  try {
    // An explicit <body>: without it a leading `<style>` or `<meta>` is moved
    // to the head. Either way it is dropped; this keeps the walk in one place.
    const doc = new DOMParser().parseFromString(`<!doctype html><body>${source}`, "text/html");
    const ctx: Context = { resolver: options.resolver ?? null, out: [], links: 0 };
    children(doc.body, ctx);
    return ctx.out.join("");
  } catch {
    return escapeText(source);
  }
}

// A deck, made fit to PRESENT from a file somebody else may have written.
//
// `DeckPlayer` draws slides through `SlideBlock`, which puts a block's rich
// text into the page as it stands (`v-html`) — right on the desktop, where the
// deck is the user's own. In the web build Play opens other members' files in
// a page of the platform's origin, so the host hands the player a COPY in which
// everything `SlideBlock` would insert as markup has first been through the
// app's allow-list, `markdown/scrubFragment.ts`. Nothing in the editors
// changes, and the file is never touched: Play does not save.
//
// What is done depends on how `SlideBlock` draws the type — the scrubber reads
// its input as HTML, and passing it something that is not HTML would only
// damage it:
//
//   heading, text, quote, html, and any type this build does not know
//                 → the payload through `scrubFragment`
//   list          → every item through `scrubFragment`
//   image         → the payload is an address, put in `src`: kept only if the
//                   same allow-list keeps it as a picture (a `data:` raster)
//   svg           → drawn as an inert PICTURE, the way Play shows a `.svg`
//                   file: the allow-list drops `<svg>` whole, which would
//                   leave every drawing on a slide blank. As a `data:` image
//                   nothing in it runs, loads or links, and none of its
//                   markup reaches the page
//   code          → untouched: drawn as text, escaped by Vue. Scrubbing would
//                   eat every `<` a code sample is there to show
//   formula       → the LaTeX is untouched (it is text, like code), and the
//                   file's cached `render` — markup, inserted as it stands —
//                   is dropped, for every type. This build's own KaTeX draws
//                   the formula again from the source, with `trust` off
import { type Block, type Presentation } from "./presentation";
import { scrubFragment } from "./scrubFragment";
import { scrub } from "./scrub";
import { svgDataUri } from "./svgDataUri";

// Where it was before the drawing editor's View needed it too; the host still
// asks for it by this address.
export { svgDataUri };

/** The width of a slide, which a drawing with no size of its own is given. */
const SLIDE_WIDTH = 1280;

/** A drawing with a `viewBox` and no size fills its box when it is inline. As
 *  a picture it would have no size at all, so it is given the slide's width
 *  and the viewBox's shape; the slide's own rules fit it down from there. */
function sizeFromViewBox(svg: Element): void {
  if (svg.hasAttribute("width") || svg.hasAttribute("height")) return;
  const box = (svg.getAttribute("viewBox") ?? "").trim().split(/[\s,]+/).map(Number);
  if (box.length !== 4 || !box.every(Number.isFinite) || box[2] <= 0 || box[3] <= 0) return;
  svg.setAttribute("width", String(SLIDE_WIDTH));
  svg.setAttribute("height", String(Math.round((SLIDE_WIDTH * box[3]) / box[2])));
}

/**
 * An `svg` block's markup as a picture's address, or "" when there is no
 * drawing in it.
 *
 * Read by the HTML parser, as the page would have read it inline — a block's
 * `<svg>` often has no `xmlns`, which a standalone picture cannot do without —
 * and in a document that is not the page's, where nothing loads or runs. The
 * serialiser then writes the first drawing out as a document of its own.
 */
export function svgPicture(markup: string): string {
  if (typeof DOMParser === "undefined" || typeof XMLSerializer === "undefined") return "";
  try {
    // Scripts, handlers and `javascript:` targets are taken out first, by the
    // deck's own scrubber. A picture runs none of them; this is for the day
    // somebody opens that picture on its own. Its `<style>` stays: inside a
    // picture a stylesheet reaches the picture and nothing else.
    const inert = scrub(markup ?? "", { styles: false, scripts: true });
    const doc = new DOMParser().parseFromString(`<!doctype html><body>${inert}`, "text/html");
    const svg = doc.body.querySelector("svg");
    if (!svg) return "";
    sizeFromViewBox(svg);
    return svgDataUri(new XMLSerializer().serializeToString(svg));
  } catch {
    return "";
  }
}

function escapeAttribute(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** An image block's address, if the allow-list keeps a picture with it. Asked
 *  of `scrubFragment` rather than decided here, so a picture on a slide and a
 *  picture inside a slide's text obey one rule. */
function pictureAddress(address: string): string {
  if (typeof DOMParser === "undefined") return "";
  const kept = scrubFragment(`<img src="${escapeAttribute((address ?? "").trim())}">`);
  const img = new DOMParser().parseFromString(kept, "text/html").querySelector("img");
  return img?.getAttribute("src") ?? "";
}

function scrubBlock(block: Block): Block {
  const safe: Block = {
    ...block,
    items: (block.items ?? []).map((item) => scrubFragment(item)),
    // The file's cached markup is never shown, whatever the type says.
    render: "",
  };
  switch (block.type) {
    case "code":
    case "formula":
      return safe;
    case "image":
      safe.payload = pictureAddress(block.payload);
      return safe;
    case "svg": {
      const picture = svgPicture(block.payload);
      // Drawn by SlideBlock's image branch from here on. With no drawing in
      // it the block stays what it was, and empty.
      return picture ? { ...safe, type: "image", payload: picture } : { ...safe, payload: "" };
    }
    default:
      safe.payload = scrubFragment(block.payload);
      return safe;
  }
}

/** A copy of `deck` in which nothing a file wrote reaches the page as markup. */
export function scrubDeck(deck: Presentation): Presentation {
  const copy = structuredClone(deck) as Presentation;
  for (const slide of copy.slides) slide.blocks = slide.blocks.map(scrubBlock);
  return copy;
}

// ADAPTED (2026-10-12, toto.vault's deck player): Morion's `drawFormulas` and
// `playableDeck` end the file there. `drawFormulas` is in main.ts, with the
// KaTeX handed in by the page (vendor/katex, loaded only when the deck has a
// formula) instead of imported; main.ts calls `scrubDeck(loads(text))`.

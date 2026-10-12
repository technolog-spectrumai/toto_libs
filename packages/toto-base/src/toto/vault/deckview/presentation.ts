// Memo's presentation format (XML v2), in TypeScript.
//
// A PORT, not a reimplementation: the reference is
// portal/vendor/toto_libs/packages/toto-works/src/toto/memo/presentation_format.py,
// and the golden tests compare this module's output against files that module
// produced. When the two disagree, Python is right — it is the server that
// stores and re-sanitises these bytes.
//
// The rules that carry the format:
//
//   * Every payload is CDATA-wrapped, with no per-type exceptions, including a
//     literal "]]>" (split across two sections). The only rule that cannot lose
//     a byte.
//   * Nothing unknown is discarded — unknown attributes, unknown block types
//     and unknown child elements all ride through `dumps` unchanged.
//   * `<title>` stays a slide ELEMENT rather than a block: the filmstrip, the
//     gallery card and the outline all read it, and making it a block would
//     force each of them to hunt through the block list for "the heading that
//     is really the title".
//
// Like the document format, this does NOT sanitise: memo sanitises server-side
// on both save and open, because a deck can arrive from anywhere.

import { attrString, escText, serializeUnknown, wrapCdata } from "./xml";

export const FORMAT_VERSION = "2";

/** A layout is a fixed set of BOXES, and that is the whole geometry model.
 *  Nothing resizes or repositions a box — more layouts is the answer to "I need
 *  a different shape", not draggable corners. `""` is a layout's single unnamed
 *  box, which keeps every one-box layout identical to what v1 wrote. */
export const LAYOUT_SLOTS: Record<string, readonly string[]> = {
  "title-content": [""],
  section: [""],
  quote: [""],
  "full-bleed": [""],
  "two-column": ["left", "right"],
  "three-column": ["left", "middle", "right"],
  "image-left": ["media", "body"],
  "image-right": ["body", "media"],
  "two-row": ["top", "bottom"],
  grid: ["a", "b", "c", "d"],
  lead: ["lead", "body"],
};
export const LAYOUTS = Object.keys(LAYOUT_SLOTS);

export const BLOCK_TYPES = [
  "heading", "text", "list", "image", "svg", "code", "quote", "formula", "html",
] as const;
export type BlockType = (typeof BLOCK_TYPES)[number];

/** Every slot any layout names. A block keeps a slot the current layout does
 *  not have — changing layout and back must not shuffle the deck. */
export const SLOTS = Array.from(
  new Set(Object.values(LAYOUT_SLOTS).flat()),
).sort();

export const THEMES = ["black", "white"] as const;
export const FONTS = ["sans", "serif", "mono", "rounded", "condensed"] as const;

export const DEFAULT_LAYOUT = "title-content";
export const DEFAULT_THEME = "black";
export const DEFAULT_FONT = "sans";

const MAX_SLIDES = 500;
const MAX_BLOCKS_PER_SLIDE = 40;
const MAX_ITEMS = 60;
export const MAX_PAYLOAD_BYTES = 8 * 1024 * 1024;

const BLOCK_OWN_ATTRS = new Set(["id", "type", "slot"]);
const SLIDE_OWN_ATTRS = new Set(["id", "layout"]);
const ROOT_OWN_ATTRS = new Set(["version", "title", "theme", "font"]);
/** Order matters only for deterministic output. */
const BLOCK_ATTR_ORDER = ["level", "ordered", "alt", "fit", "language", "cite", "scale"];

const ID_RE = /^[A-Za-z0-9_-]{1,32}$/;

export class PresentationParseError extends Error {}

export interface Block {
  id: string;
  type: string;
  payload: string;
  items: string[];
  slot: string;
  /** type="formula" only: the KaTeX markup, cached at save time. WeasyPrint
   *  runs no JavaScript, so without this an exported deck loses every formula.
   *  The LaTeX source stays the truth; this is derived. */
  render: string;
  attrs: Record<string, string>;
}

export interface Slide {
  id: string;
  title: string;
  layout: string;
  blocks: Block[];
  attrs: Record<string, string>;
  extra: string[];
}

export interface Presentation {
  title: string;
  slides: Slide[];
  version: string;
  theme: string;
  font: string;
  attrs: Record<string, string>;
  extra: string[];
}

// ── ids ─────────────────────────────────────────────────────────────────────

function newId(prefix: string): string {
  const hex = Array.from(crypto.getRandomValues(new Uint8Array(4)))
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${prefix}-${hex}`;
}

function cleanId(value: unknown, prefix: string): string {
  const v = String(value ?? "");
  return ID_RE.test(v) ? v : newId(prefix);
}

function unique(value: string, prefix: string, seen: Set<string>): string {
  let v = value;
  while (!v || seen.has(v)) v = newId(prefix);
  seen.add(v);
  return v;
}

// ── geometry ────────────────────────────────────────────────────────────────

/** The boxes this layout has, in reading order. */
export function slotsFor(layout: string): readonly string[] {
  return LAYOUT_SLOTS[layout] ?? LAYOUT_SLOTS[DEFAULT_LAYOUT];
}

/** Which box a block actually lands in. A block carrying `right` on a one-box
 *  layout is not an error and is not dropped: it shows in the only box there
 *  is, and still says `right` in the file. */
export function resolveSlot(layout: string, slot: string): string {
  const slots = slotsFor(layout);
  return slots.includes(slot) ? slot : slots[0];
}

/** The flow regions this layout renders, as [slot, blocks] pairs — one
 *  definition of "which blocks are in the right-hand column" rather than three
 *  that drift. */
export function columnsOf(slide: Slide): Array<[string, Block[]]> {
  const slots = slotsFor(slide.layout);
  const buckets = new Map<string, Block[]>(slots.map((s) => [s, []]));
  for (const block of slide.blocks) {
    buckets.get(resolveSlot(slide.layout, block.slot))!.push(block);
  }
  return slots.map((s) => [s, buckets.get(s)!] as [string, Block[]]);
}

// ── constructors ────────────────────────────────────────────────────────────

export function newBlock(over: Partial<Block> = {}): Block {
  return {
    id: over.id || newId("b"),
    type: over.type ?? "text",
    payload: over.payload ?? "",
    items: over.items ? [...over.items] : [],
    slot: over.slot ?? "",
    render: over.render ?? "",
    attrs: { ...(over.attrs ?? {}) },
  };
}

export function newSlide(over: Partial<Slide> = {}): Slide {
  return {
    id: over.id || newId("s"),
    title: over.title ?? "",
    layout: over.layout ?? DEFAULT_LAYOUT,
    blocks: over.blocks ? [...over.blocks] : [],
    attrs: { ...(over.attrs ?? {}) },
    extra: over.extra ? [...over.extra] : [],
  };
}

export function newPresentation(title = ""): Presentation {
  return {
    title,
    slides: [newSlide({ title, blocks: [newBlock({ type: "text", payload: "<p></p>" })] })],
    version: FORMAT_VERSION,
    theme: DEFAULT_THEME,
    font: DEFAULT_FONT,
    attrs: {},
    extra: [],
  };
}

// ── escaping (matches Python exactly — see the document format's note) ──────

// ── serialise ───────────────────────────────────────────────────────────────

export function dumps(pres: Presentation): string {
  const rootExtra: Record<string, string> = {};
  for (const [k, v] of Object.entries(pres.attrs || {})) {
    if (!ROOT_OWN_ATTRS.has(k)) rootExtra[k] = v;
  }

  const lines = ['<?xml version="1.0" encoding="utf-8"?>'];
  lines.push("<presentation" + attrString([
    ["version", FORMAT_VERSION],
    ["title", pres.title],
    ["theme", pres.theme || DEFAULT_THEME],
    ["font", pres.font || DEFAULT_FONT],
  ], rootExtra) + ">");

  for (const slide of pres.slides) {
    const slideExtra: Record<string, string> = {};
    for (const [k, v] of Object.entries(slide.attrs || {})) {
      if (!SLIDE_OWN_ATTRS.has(k)) slideExtra[k] = v;
    }
    lines.push("  <slide" + attrString([
      ["id", slide.id],
      ["layout", slide.layout || DEFAULT_LAYOUT],
    ], slideExtra) + ">");
    lines.push(`    <title>${escText(slide.title)}</title>`);

    for (const block of slide.blocks) {
      const blockExtra: Record<string, string> = {};
      for (const [k, v] of Object.entries(block.attrs || {})) {
        if (!BLOCK_OWN_ATTRS.has(k) && !BLOCK_ATTR_ORDER.includes(k)) blockExtra[k] = v;
      }
      const known: Array<[string, string]> = [
        ["id", block.id], ["type", block.type], ["slot", block.slot],
      ];
      for (const k of BLOCK_ATTR_ORDER) {
        if (k in (block.attrs || {})) known.push([k, block.attrs[k]]);
      }
      const head = "    <block" + attrString(known, blockExtra);

      if (block.type === "list") {
        lines.push(head + ">");
        for (const item of block.items) lines.push(`      <item>${wrapCdata(item)}</item>`);
        lines.push("    </block>");
      } else if (block.type === "formula") {
        // Both parts as CHILDREN, never as element text beside a child: mixed
        // content means the payload silently absorbs the indentation whitespace
        // around its sibling.
        lines.push(head + ">");
        lines.push(`      <source>${wrapCdata(block.payload)}</source>`);
        if (block.render) lines.push(`      <render>${wrapCdata(block.render)}</render>`);
        lines.push("    </block>");
      } else {
        lines.push(head + `>${wrapCdata(block.payload)}</block>`);
      }
    }

    for (const raw of slide.extra || []) lines.push("    " + raw.trim());
    lines.push("  </slide>");
  }

  for (const raw of pres.extra || []) lines.push("  " + raw.trim());
  lines.push("</presentation>");
  return lines.join("\n") + "\n";
}

// ── parse ───────────────────────────────────────────────────────────────────

export function loads(text: string): Presentation {
  const trimmed = (text || "").trim();
  if (!trimmed) return newPresentation();

  const parsed = new DOMParser().parseFromString(trimmed, "application/xml");
  const failure = parsed.querySelector("parsererror");
  if (failure) {
    throw new PresentationParseError(`Invalid presentation document: ${failure.textContent ?? ""}`);
  }

  const root = parsed.documentElement;
  if (!root || root.tagName !== "presentation") {
    throw new PresentationParseError(
      `Expected root <presentation>, found <${root?.tagName ?? "?"}>`);
  }

  const version = root.getAttribute("version") || "1";
  // A hand-edit that added blocks but forgot to bump `version` should not be
  // silently flattened back to one HTML blob.
  const v2 = version === "2" || root.querySelector("block") !== null;

  const attrs: Record<string, string> = {};
  for (const a of Array.from(root.attributes)) {
    if (!ROOT_OWN_ATTRS.has(a.name)) attrs[a.name] = a.value;
  }

  const pres: Presentation = {
    title: root.getAttribute("title") || "",
    slides: [],
    version: FORMAT_VERSION,
    theme: root.getAttribute("theme") || DEFAULT_THEME,
    font: root.getAttribute("font") || DEFAULT_FONT,
    attrs,
    extra: [],
  };
  if (!(THEMES as readonly string[]).includes(pres.theme)) pres.theme = DEFAULT_THEME;
  if (!(FONTS as readonly string[]).includes(pres.font)) pres.font = DEFAULT_FONT;

  const seen = new Set<string>();
  for (const child of Array.from(root.children)) {
    if (child.tagName !== "slide") {
      pres.extra.push(serializeUnknown(child));
      continue;
    }
    if (pres.slides.length >= MAX_SLIDES) break;
    pres.slides.push(v2 ? parseSlide(child, seen) : upgradeSlide(child, seen));
  }
  return pres;
}

function parseSlide(el: Element, seen: Set<string>): Slide {
  const attrs: Record<string, string> = {};
  for (const a of Array.from(el.attributes)) {
    if (!SLIDE_OWN_ATTRS.has(a.name)) attrs[a.name] = a.value;
  }
  const slide: Slide = {
    id: unique(cleanId(el.getAttribute("id"), "s"), "s", seen),
    title: "",
    layout: el.getAttribute("layout") || DEFAULT_LAYOUT,
    blocks: [],
    attrs,
    extra: [],
  };
  if (!LAYOUTS.includes(slide.layout)) slide.layout = DEFAULT_LAYOUT;

  for (const child of Array.from(el.children)) {
    if (child.tagName === "title") slide.title = child.textContent || "";
    else if (child.tagName === "block") {
      if (slide.blocks.length < MAX_BLOCKS_PER_SLIDE) {
        slide.blocks.push(parseBlock(child, seen));
      }
    } else {
      slide.extra.push(serializeUnknown(child));
    }
  }
  return slide;
}

function parseBlock(el: Element, seen: Set<string>): Block {
  const attrs: Record<string, string> = {};
  for (const a of Array.from(el.attributes)) {
    if (!BLOCK_OWN_ATTRS.has(a.name)) attrs[a.name] = a.value;
  }
  const block: Block = {
    id: unique(cleanId(el.getAttribute("id"), "b"), "b", seen),
    type: el.getAttribute("type") || "text",
    payload: "",
    items: [],
    slot: el.getAttribute("slot") || "",
    render: "",
    attrs,
  };
  if (!SLOTS.includes(block.slot)) block.slot = "";

  if (block.type === "list") {
    for (const item of Array.from(el.children)) {
      if (item.tagName === "item" && block.items.length < MAX_ITEMS) {
        block.items.push(item.textContent || "");
      }
    }
  } else {
    // `<source>` when present, element text otherwise — so a hand-written
    // `<block type="formula">x^2</block>` still opens.
    const source = Array.from(el.children).find((c) => c.tagName === "source");
    block.payload = (source ? source.textContent : el.textContent) || "";
    const rendered = Array.from(el.children).find((c) => c.tagName === "render");
    if (rendered) block.render = rendered.textContent || "";
  }
  return block;
}

/** A v1 `<slide><title/><body/></slide>` as a v2 slide, IN MEMORY only. The
 *  body becomes exactly one `html` block, which renders through the same
 *  unescaped path v1 used — so an upgraded deck presents byte-identically.
 *  Nothing is written back until the user saves. */
function upgradeSlide(el: Element, seen: Set<string>): Slide {
  const titleEl = Array.from(el.children).find((c) => c.tagName === "title");
  const bodyEl = Array.from(el.children).find((c) => c.tagName === "body");
  const body = bodyEl ? bodyEl.textContent || "" : "";

  const attrs: Record<string, string> = {};
  for (const a of Array.from(el.attributes)) {
    if (!SLIDE_OWN_ATTRS.has(a.name)) attrs[a.name] = a.value;
  }
  const slide: Slide = {
    id: unique(cleanId(el.getAttribute("id"), "s"), "s", seen),
    title: titleEl ? titleEl.textContent || "" : "",
    layout: el.getAttribute("layout") || DEFAULT_LAYOUT,
    blocks: body ? [newBlock({ type: "html", payload: body })] : [],
    attrs,
    extra: [],
  };
  if (!LAYOUTS.includes(slide.layout)) slide.layout = DEFAULT_LAYOUT;

  for (const child of Array.from(el.children)) {
    if (!["title", "body"].includes(child.tagName)) {
      slide.extra.push(serializeUnknown(child));
    }
  }
  return slide;
}

// ── identity ────────────────────────────────────────────────────────────────

const SNIFF_SKIP = /<\?xml[^>]*\?>|<!--[\s\S]*?-->|<!DOCTYPE[^>]*>/g;
const SNIFF_RE = /^<\s*presentation\s*[/>]|^<\s*presentation\s/i;

export function sniffIsPresentation(head: string): boolean {
  return SNIFF_RE.test((head || "").slice(0, 2048).replace(SNIFF_SKIP, "").trimStart());
}

// ── the theme a deck is SHOWN in ────────────────────────────────────────────
//
// Morion's own, and not in the Python reference, which draws `theme` as it is
// written. Here a deck follows the app's light or dark mode unless somebody
// chose a theme for it — and `theme` alone cannot say whether anybody did:
// every deck is written with one from its first save, and that one is `black`.
// So a deck's file is read like this:
//
//   theme="white"                        chosen: nothing else writes `white`
//   theme="black" theme-pinned="true"    chosen
//   theme="black"                        nobody chose; the app's mode decides
//
// `theme-pinned` is a root attribute the format carries as one it does not
// know (`attrs`), so the reference keeps it through a save and goes on drawing
// the deck by `theme`, as it always has.

export const THEME_PINNED = "theme-pinned";
/** What Deck settings offers. `auto` is "follow the app's mode". */
export const THEME_CHOICES = ["auto", "black", "white"] as const;
export type ThemeChoice = (typeof THEME_CHOICES)[number];

/** What was chosen for this deck, as its file says it. */
export function themeChoice(pres: Pick<Presentation, "theme" | "attrs">): ThemeChoice {
  if (pres.theme === "white") return "white";
  return pres.attrs?.[THEME_PINNED] === "true" ? "black" : "auto";
}

/** Choose, in place. `auto` is written as a deck nobody touched is: the
 *  default theme and no pin. */
export function chooseTheme(pres: Presentation, choice: ThemeChoice): void {
  const attrs = { ...(pres.attrs ?? {}) };
  if (choice === "auto") delete attrs[THEME_PINNED];
  else attrs[THEME_PINNED] = "true";
  pres.attrs = attrs;
  pres.theme = choice === "auto" ? DEFAULT_THEME : choice;
}

/** The theme to draw the deck in: its own where one was chosen, else the one
 *  that goes with the app's mode — `black` in dark, `white` in light. */
export function shownTheme(
  pres: Pick<Presentation, "theme" | "attrs">, dark: boolean,
): (typeof THEMES)[number] {
  const choice = themeChoice(pres);
  if (choice === "auto") return dark ? "black" : "white";
  return choice;
}

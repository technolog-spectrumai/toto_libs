// The deck player's entry for a plain page (2026-10-12): Morion's deck format
// (presentation.ts), the copy it makes fit to present from somebody else's
// file (playDeck.ts: every block through the allow-list), its slide
// stylesheet (slide.css) and reveal.js — without Vue. What DeckPlayer.vue and
// SlideBlock.vue build as templates is built here as DOM, block for block,
// with the same classes and attributes, so slide.css lays a slide out exactly
// as it does in Morion.
//
// Everything that is put on the page as markup has been through `scrubDeck`
// first; everything else (a slide's title, a code sample, a formula's source,
// a quote's cite) is text.
import Reveal from "reveal.js";
import "reveal.js/dist/reveal.css";
import "./slide.css";
import {
  columnsOf, loads, PresentationParseError, shownTheme, slotsFor,
  type Block, type Presentation,
} from "./presentation";
import { scrubDeck } from "./playDeck";

export interface Opened {
  deck: Presentation | null;
  /** "" | "empty" | "not-a-deck" */
  refusal: string;
}

/** A `.pxml` file's text as a deck made fit to present. */
export function open(text: string): Opened {
  if (!text.trim()) return { deck: null, refusal: "empty" };
  try {
    const deck = scrubDeck(loads(text));
    if (!deck.slides.length) return { deck: null, refusal: "empty" };
    return { deck, refusal: "" };
  } catch (err) {
    if (err instanceof PresentationParseError) return { deck: null, refusal: "not-a-deck" };
    throw err;
  }
}

/** SlideBlock's `scaleCss`: the resolved auto-fit multiplier, or "". */
function scaleOf(block: Block): string {
  const raw = (block.attrs?.scale || "auto").trim();
  const number = raw.startsWith("auto:") ? raw.slice(5) : raw.startsWith("auto") ? "" : raw;
  if (!number) return "";
  const value = Number(number);
  if (!Number.isFinite(value)) return "";
  return Math.abs(value - 1) < 0.005 ? "" : value.toFixed(2);
}

function el(tag: string, type: string, block: Block, scaled = true): HTMLElement {
  const node = document.createElement(tag);
  node.className = "memo-block";
  node.dataset.type = type;
  node.dataset.blockId = block.id;
  const scale = scaled ? scaleOf(block) : "";
  if (scale) node.style.setProperty("--memo-block-scale", scale);
  return node;
}

/** One block, as SlideBlock.vue draws it. `payload` and `items` are the
 *  allow-list's output (scrubDeck), so they go in as markup. */
function blockNode(block: Block): HTMLElement {
  switch (block.type) {
    case "heading": {
      const level = block.attrs?.level;
      const node = el(level === "1" ? "h1" : level === "3" ? "h3" : level === "4" ? "h4" : "h2", "heading", block);
      node.innerHTML = block.payload;
      return node;
    }
    case "text": {
      const node = el("div", "text", block);
      node.innerHTML = block.payload;
      return node;
    }
    case "list": {
      const node = el("div", "list", block);
      const list = document.createElement(block.attrs?.ordered === "true" ? "ol" : "ul");
      for (const item of block.items ?? []) {
        const li = document.createElement("li");
        li.innerHTML = item;
        list.appendChild(li);
      }
      node.appendChild(list);
      return node;
    }
    case "image": {
      const node = el("div", "image", block, false);
      node.dataset.fit = block.attrs?.fit || "contain";
      if (block.payload) {
        const img = document.createElement("img");
        img.src = block.payload;
        img.alt = block.attrs?.alt || "";
        node.appendChild(img);
      }
      return node;
    }
    case "code": {
      const node = el("pre", "code", block);
      node.dataset.language = block.attrs?.language || "";
      node.textContent = block.payload;
      return node;
    }
    case "formula": {
      // As SlideBlock: the render drawn by `drawFormulas` (this build's own
      // KaTeX; the file's cached markup was dropped by scrubDeck), else the
      // source as text.
      const node = el("div", "formula", block);
      if (block.render) {
        const span = document.createElement("span");
        span.innerHTML = block.render;
        node.appendChild(span);
      } else {
        const code = document.createElement("code");
        code.className = "memo-formula-source";
        code.textContent = block.payload;
        node.appendChild(code);
      }
      return node;
    }
    case "quote": {
      const node = el("blockquote", "quote", block);
      const span = document.createElement("span");
      span.innerHTML = block.payload;
      node.appendChild(span);
      if (block.attrs?.cite) {
        const cite = document.createElement("cite");
        cite.className = "memo-cite";
        cite.textContent = block.attrs.cite;
        node.appendChild(cite);
      }
      return node;
    }
    case "html": {
      const node = el("div", "html", block, false);
      const span = document.createElement("span");
      span.innerHTML = block.payload;
      node.appendChild(span);
      return node;
    }
    case "svg": {
      // scrubDeck turned a drawing into an image block; one left as svg had
      // no drawing in it.
      const node = el("div", "svg", block, false);
      node.setAttribute("role", "img");
      node.setAttribute("aria-label", block.attrs?.alt || "");
      return node;
    }
    default: {
      const node = el("div", "unknown", block, false);
      const span = document.createElement("span");
      span.className = "memo-legacy";
      span.textContent = block.type;
      node.appendChild(span);
      return node;
    }
  }
}

/** Whether the deck has a formula to draw: the page loads KaTeX only then. */
export function hasFormulas(deck: Presentation): boolean {
  return deck.slides.some((slide) => slide.blocks.some((b) => b.type === "formula" && b.payload.trim()));
}

interface KatexLike {
  renderToString(source: string, options: Record<string, unknown>): string;
}

/**
 * Draws every formula of an already scrubbed deck with `katex` (the page's
 * vendor/katex): Morion's `playDeck.drawFormulas`, with the KaTeX handed in
 * instead of imported. The same options: `\href`, `\url`, `\includegraphics`
 * and the `\html…` commands stay inert (`trust` off), a size or an expansion
 * a formula asks for is capped. A formula KaTeX cannot read comes back as
 * its source, marked, by KaTeX itself (throwOnError off).
 */
export function drawFormulas(deck: Presentation, katex: KatexLike): void {
  for (const slide of deck.slides) {
    for (const block of slide.blocks) {
      if (block.type !== "formula" || !block.payload.trim()) continue;
      try {
        block.render = katex.renderToString(block.payload.trim(), {
          throwOnError: false, trust: false, strict: "ignore", maxSize: 20, maxExpand: 1000,
        });
      } catch {
        block.render = "";
      }
    }
  }
}

export interface Player { destroy(): void; slide(index: number): void }

/**
 * Builds the slides in `stage` (an element with class `reveal`, inside one
 * with class `memo-player`) and starts reveal.js on it, with the options
 * Morion's DeckPlayer gives it.
 */
export async function play(stage: HTMLElement, deck: Presentation, dark: boolean): Promise<Player> {
  const theme = `memo-theme-${shownTheme(deck, dark)}`;
  stage.closest(".memo-player")?.classList.add(theme);
  const slides = document.createElement("div");
  slides.className = "slides";
  for (const slide of deck.slides) {
    const section = document.createElement("section");
    const box = document.createElement("div");
    box.className = `memo-slide ${theme} memo-font-${deck.font}`;
    box.dataset.layout = slide.layout;
    if (slide.title) {
      const title = document.createElement("h2");
      title.className = "memo-title";
      title.textContent = slide.title;
      box.appendChild(title);
    }
    for (const [slot, blocks] of columnsOf(slide)) {
      const flow = document.createElement("div");
      flow.className = "memo-flow";
      flow.dataset.slot = slot;
      flow.style.setProperty("--memo-columns", String(slotsFor(slide.layout).length));
      for (const block of blocks) flow.appendChild(blockNode(block));
      box.appendChild(flow);
    }
    section.appendChild(box);
    slides.appendChild(section);
  }
  stage.replaceChildren(slides);

  const reveal = new Reveal(stage, {
    // 1280×720 and centring off: Morion's DeckPlayer says why.
    width: 1280,
    height: 720,
    center: false,
    margin: 0.04,
    transition: "slide",
    hash: false,
    slideNumber: "c/t",
    controls: true,
    progress: true,
    embedded: true,
    keyboardCondition: "focused",
  });
  await reveal.initialize();
  return {
    destroy() { try { reveal.destroy(); } catch { /* a player that will not tear down */ } },
    slide(index: number) { reveal.slide(index); },
  };
}

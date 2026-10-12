// The Markdown viewer's entry for a plain page (2026-10-12): Morion's
// renderer (render.ts, scrubFragment.ts, anchors.ts — copied from
// morion/apps/morion/src/markdown, see README.md) without Morion's Vue
// preview. What MarkdownPreview.vue did around the renderer is here, for a
// Django page with no framework:
//
//   - `render(source)` gives the allow-listed HTML or a refusal's name;
//   - `drawDiagrams(root, dark, mermaid)` draws each ```mermaid block with
//     the Mermaid the page loaded (only when the note has one);
//   - `drawFormulas(root, katex)` draws each formula with the KaTeX the page
//     loaded (only when the note has one), as MarkdownPreview.vue does.
//
// No file of the vault is resolved from a note: a relative link or image
// reads as its text, as the renderer does with no resolver.
import { githubMarkdown, MarkdownTooComplex, MarkdownTooLarge, MAX_MARKDOWN_BYTES } from "./render";

export interface Rendered {
  html: string;
  /** "" | "empty" | "too_large" | "too_complex" */
  refusal: string;
  mermaid: number;
  math: number;
}

export function render(source: string): Rendered {
  if (!source.trim()) return { html: "", refusal: "empty", mermaid: 0, math: 0 };
  try {
    const made = githubMarkdown(source, { resolver: null });
    return { html: made.html, refusal: "", mermaid: made.mermaid, math: made.math };
  } catch (err) {
    if (err instanceof MarkdownTooComplex) return { html: "", refusal: "too_complex", mermaid: 0, math: 0 };
    if (err instanceof MarkdownTooLarge) return { html: "", refusal: "too_large", mermaid: 0, math: 0 };
    throw err;
  }
}

interface KatexLike {
  render(source: string, into: HTMLElement, options: Record<string, unknown>): void;
}

/** Draws every formula under `root`: Morion's `MarkdownPreview.drawFormulas`
 *  with the KaTeX handed in. A formula KaTeX cannot read keeps its source,
 *  with the parser's word beside it. Returns how many were drawn. */
export function drawFormulas(root: HTMLElement, katex: KatexLike): number {
  const boxes = Array.from(root.querySelectorAll<HTMLElement>(".md-math"));
  let drawn = 0;
  for (const box of boxes) {
    if (box.classList.contains("md-math-drawn")) continue;
    // Drawn into a detached element: a formula KaTeX cannot read leaves its
    // source where it was.
    const made = document.createElement("span");
    try {
      katex.render(box.textContent ?? "", made, {
        displayMode: box.classList.contains("md-math-display"),
        throwOnError: true,
        // \href, \url, \includegraphics and the \html… commands stay inert.
        trust: false,
        strict: "ignore",
        output: "htmlAndMathml",
        maxSize: 20,
        maxExpand: 1000,
      });
      box.replaceChildren(...Array.from(made.childNodes));
      box.classList.add("md-math-drawn");
      drawn += 1;
    } catch (err) {
      const chip = document.createElement("span");
      chip.className = "md-math-error";
      chip.setAttribute("role", "note");
      chip.textContent = String((err as Error)?.message ?? err).replace(/^KaTeX parse error:\s*/, "");
      box.appendChild(chip);
    }
  }
  return drawn;
}

interface MermaidLike {
  initialize(options: Record<string, unknown>): void;
  render(id: string, source: string): Promise<{ svg: string }>;
}

let round = 0;

/** Draws every diagram under `root`. A diagram that does not parse keeps its
 *  source on the page. Returns how many were drawn. */
export async function drawDiagrams(root: HTMLElement, dark: boolean, mermaid: MermaidLike): Promise<number> {
  const mine = ++round;
  const boxes = Array.from(root.querySelectorAll<HTMLElement>(".md-mermaid"));
  if (!boxes.length) return 0;
  mermaid.initialize({
    startOnLoad: false,
    // Labels are text, `click` lines and links in a diagram do nothing.
    securityLevel: "strict",
    suppressErrorRendering: true,
    theme: dark ? "dark" : "default",
    flowchart: { htmlLabels: false },
  });
  let drawn = 0;
  for (const [index, box] of boxes.entries()) {
    const source = box.querySelector<HTMLElement>(".md-mermaid-source");
    const target = box.querySelector<HTMLElement>(".md-mermaid-svg");
    if (!source || !target) continue;
    try {
      const made = await mermaid.render(`md-mermaid-${mine}-${index}`, source.textContent ?? "");
      if (mine !== round) return drawn;
      target.innerHTML = made.svg;
      source.hidden = true;
      drawn += 1;
    } catch {
      if (mine !== round) return drawn;
      target.textContent = "";
      source.hidden = false;
    }
  }
  return drawn;
}

export { MAX_MARKDOWN_BYTES };

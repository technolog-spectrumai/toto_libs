// A Markdown note as GitHub would show it — the same capabilities as
// zenobia's Play page (`toto.htmlview.markdown_source.github_markdown`), so a
// note written for one reads the same in the other:
//
//   tables with alignment, fenced code with its language, footnotes, heading
//   addresses with a permalink and `[TOC]`, ~~strikethrough~~, task lists,
//   bare-URL links, ```mermaid diagrams, and formulas in GitHub's spellings
//   ($x$, $$x$$, $`x`$, a `$$` block, a ```math fence).
//
// The converter is markdown-it (CommonMark) where zenobia has Python-Markdown,
// so the edges differ in CommonMark's favour: a fence works inside a list or a
// quote, two spaces nest a list, `1)` is a list, `#hashtag` is no heading.
// Everything below — the formula rules, the URL rule, the anchors, the limits —
// is zenobia's, ported.
//
// Raw HTML is let through the converter and then through `scrubFragment`,
// which is the one place that decides what reaches the page. The converter's
// own link check is switched off for the same reason: one authority.
//
// A diagram or a formula travels as its escaped SOURCE in a container
// (`md-mermaid`, `md-math`); nothing is drawn here. `MarkdownPreview.vue`
// draws them, and loads Mermaid or KaTeX only when there is one.
import MarkdownIt from "markdown-it";
import type { Options } from "markdown-it";
import type StateBlock from "markdown-it/lib/rules_block/state_block.mjs";
import type StateCore from "markdown-it/lib/rules_core/state_core.mjs";
import type StateInline from "markdown-it/lib/rules_inline/state_inline.mjs";
import type Token from "markdown-it/lib/token.mjs";
import footnote from "markdown-it-footnote";
import { HeadingAnchors } from "./anchors";
import { ID_PREFIX, scrubFragment, type FragmentResolver } from "./scrubFragment";

/** Larger than any real README. A bigger note is not rendered. */
export const MAX_MARKDOWN_BYTES = 512 * 1024;
/** Lists or quotes nested deeper than this are not rendered. */
export const MAX_MARKDOWN_NESTING = 64;

export class MarkdownTooLarge extends Error {
  constructor(message = "too_large") {
    super(message);
    this.name = "MarkdownTooLarge";
  }
}

export class MarkdownTooComplex extends MarkdownTooLarge {
  constructor(message = "too_complex") {
    super(message);
    this.name = "MarkdownTooComplex";
  }
}

export interface TocEntry {
  level: number;
  /** The id as the page carries it, `md-` included. */
  anchor: string;
  text: string;
}

export interface GithubMarkdown {
  html: string;
  toc: TocEntry[];
  /** ```mermaid blocks the converter found. */
  mermaid: number;
  /** Formulas the converter found, in every spelling. */
  math: number;
}

interface Env {
  mermaid: number;
  math: number;
  toc: TocEntry[];
  footnotes?: { list?: unknown[] };
}

const TASK = /^\[([ xX])\][ \t]+/;
const URL = /(?:https?:\/\/|www\.)[^\s<>"'\x00-\x1f\x7f]+/gi;
/** What may not stand right before a bare URL: it is then part of a word, a
 *  path or an address. */
const URL_BEFORE = /[\p{L}\p{N}_/@.:\-]/u;
const URL_TRAILING = ".,:;!?'\"*_~";
/** A fence's info that makes it a diagram: ```mermaid, ```.mermaid, ```{.mermaid}. */
const MERMAID_INFO = /^\.?mermaid$/i;
/** A fence's info that makes it a display formula: ```math, as on GitHub. */
const MATH_INFO = /^\.?math$/i;
const MATH_FENCE_LINE = /^\$\$[ \t]*$/;
/** $`x`$ — GitHub's other inline spelling. */
const MATH_CODE = /\$`([^`\n]+)`\$/y;
const SPACE = /\s/;
const DIGIT = /\d/;

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** The container a formula travels in: its source, escaped, as text. */
function formulaHtml(tex: string, display: boolean, block = false): string {
  const source = escapeHtml(tex.trim());
  if (block) return `<div class="md-math md-math-display">${source}</div>`;
  return `<span class="${display ? "md-math md-math-display" : "md-math"}">${source}</span>`;
}

function fenceInfo(info: string): string {
  const trimmed = info.trim();
  return trimmed.startsWith("{") && trimmed.endsWith("}") ? trimmed.slice(1, -1).trim() : trimmed;
}

// ── formulas ────────────────────────────────────────────────────────────────

/** `$$` on a line of its own, the formula, `$$` on a line of its own. */
function mathBlock(state: StateBlock, startLine: number, endLine: number, silent: boolean): boolean {
  const line = (n: number) => state.src.slice(state.bMarks[n] + state.tShift[n], state.eMarks[n]);
  if (state.sCount[startLine] - state.blkIndent >= 4) return false;
  if (!MATH_FENCE_LINE.test(line(startLine))) return false;

  let close = startLine + 1;
  for (; close < endLine; close++) {
    if (state.sCount[close] < state.blkIndent && !state.isEmpty(close)) return false;
    if (state.sCount[close] - state.blkIndent < 4 && MATH_FENCE_LINE.test(line(close))) break;
  }
  if (close >= endLine) return false;

  const tex = state.getLines(startLine + 1, close, state.blkIndent, false);
  // An empty block is left alone: the two lines read as text.
  if (!tex.trim()) return false;
  if (silent) return true;

  const token = state.push("math_block", "div", 0);
  token.content = tex;
  token.map = [startLine, close + 1];
  state.line = close + 1;
  return true;
}

/** For each length of backtick run in this text, where the LAST such run
 *  starts — which answers "does a code span open here?" in one lookup. */
const backtickRuns = new WeakMap<StateInline, Map<number, number>>();

function opensCodeSpan(state: StateInline, at: number): boolean {
  let runs = backtickRuns.get(state);
  if (!runs) {
    runs = new Map();
    const src = state.src;
    for (let i = 0; i < src.length; i++) {
      if (src.charCodeAt(i) !== 0x60) continue;
      const start = i;
      while (i + 1 < src.length && src.charCodeAt(i + 1) === 0x60) i++;
      runs.set(i - start + 1, start);
    }
    backtickRuns.set(state, runs);
  }
  let length = 1;
  while (state.src.charCodeAt(at + length) === 0x60) length++;
  return (runs.get(length) ?? -1) > at;
}

/**
 * `$x$`, `$$x$$` and $`x`$ in running text.
 *
 * - `$x$`: a non-space just inside both dollars and no digit after the closing
 *   one, so "It costs $5 and $10" stays text. It does not cross a line.
 * - `$$x$$`: display, inside a paragraph; it may wrap.
 * - a backslash pair is consumed whole, so `\$` inside a formula never closes
 *   it; and a dollar after a backslash or another dollar never opens one.
 * - a formula never reaches across a code span.
 */
function mathInline(state: StateInline, silent: boolean): boolean {
  const { src, pos: start, posMax: max } = state;
  if (src.charCodeAt(start) !== 0x24) return false;

  const emit = (tex: string, display: boolean, end: number): boolean => {
    if (!silent) {
      const token = state.push("math_inline", "span", 0);
      token.content = tex;
      token.meta = { display };
    }
    state.pos = end;
    return true;
  };

  if (src.charCodeAt(start + 1) === 0x60) {
    MATH_CODE.lastIndex = start;
    const match = MATH_CODE.exec(src);
    if (match && start + match[0].length <= max) return emit(match[1], false, start + match[0].length);
  }

  const before = start > 0 ? src[start - 1] : "";
  if (before === "\\" || before === "$") return false;

  if (src.charCodeAt(start + 1) === 0x24) {
    if (src.charCodeAt(start + 2) === 0x24) return false;
    for (let i = start + 2; i < max; i++) {
      const ch = src.charCodeAt(i);
      if (ch === 0x5c) {
        i++;
      } else if (ch === 0x24) {
        if (src.charCodeAt(i + 1) !== 0x24 || i + 1 >= max || i === start + 2) return false;
        return emit(src.slice(start + 2, i), true, i + 2);
      } else if (ch === 0x60 && opensCodeSpan(state, i)) {
        return false;
      }
    }
    return false;
  }

  if (start + 1 >= max || SPACE.test(src[start + 1])) return false;
  for (let i = start + 1; i < max; i++) {
    const ch = src.charCodeAt(i);
    if (ch === 0x0a) return false;
    if (ch === 0x5c) {
      if (i + 1 >= max || src.charCodeAt(i + 1) === 0x0a) return false;
      i++;
    } else if (ch === 0x24) {
      const after = i + 1 < max ? src[i + 1] : "";
      if (SPACE.test(src[i - 1]) || after === "$" || DIGIT.test(after)) return false;
      return emit(src.slice(start + 1, i), false, i + 1);
    } else if (ch === 0x60 && opensCodeSpan(state, i)) {
      return false;
    }
  }
  return false;
}

// ── task lists ──────────────────────────────────────────────────────────────

/** `- [ ]` and `- [x]`: a disabled checkbox, in a tight or a loose list. */
function taskLists(state: StateCore): void {
  const tokens = state.tokens;
  const lists: Token[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token.type === "bullet_list_open" || token.type === "ordered_list_open") lists.push(token);
    else if (token.type === "bullet_list_close" || token.type === "ordered_list_close") lists.pop();
    if (token.type !== "list_item_open") continue;
    const inline = tokens[i + 1]?.type === "paragraph_open" ? tokens[i + 2] : undefined;
    const first = inline?.type === "inline" ? inline.children?.[0] : undefined;
    if (!inline || !first || first.type !== "text") continue;
    const match = TASK.exec(first.content);
    if (!match) continue;
    first.content = first.content.slice(match[0].length);
    const box = new state.Token("html_inline", "", 0);
    box.content = `<input type="checkbox" disabled${match[1] === " " ? "" : " checked"}>`;
    inline.children!.unshift(box);
    token.attrSet("class", "task-list-item");
    lists[lists.length - 1]?.attrSet("class", "contains-task-list");
  }
}

// ── bare-URL links ──────────────────────────────────────────────────────────

function trimUrl(url: string): string {
  let out = url;
  while (out) {
    const last = out[out.length - 1];
    const unbalanced = last === ")" && out.split("(").length < out.split(")").length;
    if (!URL_TRAILING.includes(last) && !unbalanced) break;
    out = out.slice(0, -1);
  }
  return out;
}

/** One text token as text and links. `https://`, `http://` and `www.` only:
 *  an e-mail address or a bare domain stays text. */
function linkified(state: StateCore, token: Token): Token[] | null {
  const text = token.content;
  const out: Token[] = [];
  let last = 0;
  const plain = (content: string) => {
    if (!content) return;
    const piece = new state.Token("text", "", 0);
    piece.content = content;
    piece.level = token.level;
    out.push(piece);
  };

  URL.lastIndex = 0;
  for (let match = URL.exec(text); match; match = URL.exec(text)) {
    if (match.index > 0 && URL_BEFORE.test(text[match.index - 1])) {
      URL.lastIndex = match.index + 1;
      continue;
    }
    const url = trimUrl(match[0]);
    if (["http://", "https://", "www.", ""].includes(url.toLowerCase())) continue;
    plain(text.slice(last, match.index));
    const open = new state.Token("link_open", "a", 1);
    open.attrSet("href", url.includes("://") ? url : `https://${url}`);
    open.level = token.level;
    const label = new state.Token("text", "", 0);
    label.content = url;
    label.level = token.level + 1;
    const close = new state.Token("link_close", "a", -1);
    close.level = token.level;
    out.push(open, label, close);
    last = match.index + url.length;
    URL.lastIndex = last;
  }
  if (!out.length) return null;
  plain(text.slice(last));
  return out;
}

function bareUrls(state: StateCore): void {
  for (const block of state.tokens) {
    if (block.type !== "inline" || !block.children) continue;
    const next: Token[] = [];
    let links = 0;
    for (const child of block.children) {
      if (child.type === "link_open") links++;
      else if (child.type === "link_close") links--;
      else if (child.type === "html_inline") {
        if (/^<a[\s>]/i.test(child.content)) links++;
        else if (/^<\/a\s*>/i.test(child.content)) links = Math.max(0, links - 1);
      }
      const pieces = child.type === "text" && links === 0 ? linkified(state, child) : null;
      if (pieces) next.push(...pieces);
      else next.push(child);
    }
    block.children = next;
  }
}

// ── heading addresses, permalinks, [TOC] ────────────────────────────────────

/** A heading's words, without its markup: what its address is made from and
 *  what the contents list shows. */
function plainText(tokens: Token[] | null): string {
  let out = "";
  for (const token of tokens ?? []) {
    if (token.type === "text" || token.type === "code_inline" || token.type === "math_inline") out += token.content;
    else if (token.type === "image") out += plainText(token.children);
    else if (token.type === "softbreak" || token.type === "hardbreak") out += " ";
  }
  return out.trim();
}

function tocHtml(toc: Array<{ level: number; id: string; text: string }>): string {
  const out: string[] = ['<div class="toc">'];
  const open: number[] = [];
  for (const entry of toc) {
    while (open.length && open[open.length - 1] > entry.level) {
      out.push("</li></ul>");
      open.pop();
    }
    if (open.length && open[open.length - 1] === entry.level) {
      out.push("</li>");
    } else {
      out.push("<ul>");
      open.push(entry.level);
    }
    out.push(`<li><a href="#${entry.id}">${escapeHtml(entry.text)}</a>`);
  }
  while (open.length) {
    out.push("</li></ul>");
    open.pop();
  }
  out.push("</div>\n");
  return out.join("");
}

function headings(state: StateCore): void {
  const env = state.env as Env;
  const anchors = new HeadingAnchors();
  // A footnote's ids are not a heading's to take.
  const notes = env.footnotes?.list?.length ?? 0;
  for (let n = 1; n <= notes; n++) {
    anchors.reserve(`fn-${n}`);
    anchors.reserve(`fnref-${n}`);
  }

  const toc: Array<{ level: number; id: string; text: string }> = [];
  const tokens = state.tokens;
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i].type !== "heading_open") continue;
    const inline = tokens[i + 1];
    if (inline?.type !== "inline") continue;
    const text = plainText(inline.children);
    const id = anchors.next(text);
    tokens[i].attrSet("id", id);
    toc.push({ level: Number(tokens[i].tag.slice(1)), id, text });
    const permalink = new state.Token("html_inline", "", 0);
    permalink.content = `<a class="headerlink" href="#${id}">#</a>`;
    (inline.children ??= []).push(permalink);
  }
  env.toc = toc.map(({ level, id, text }) => ({ level, anchor: ID_PREFIX + id, text }));

  // `[TOC]` alone in a paragraph: the list of every heading, in its place.
  for (let i = 0; i + 2 < tokens.length; i++) {
    if (tokens[i].type !== "paragraph_open" || tokens[i].level !== 0) continue;
    if (tokens[i + 1].type !== "inline" || tokens[i + 1].content.trim() !== "[TOC]") continue;
    const list = new state.Token("html_block", "", 0);
    list.content = tocHtml(toc);
    tokens.splice(i, 3, list);
  }
}

// ── the converter ───────────────────────────────────────────────────────────

let converter: MarkdownIt | null = null;

function build(): MarkdownIt {
  const options: Options = { html: true, linkify: false, typographer: false, breaks: false };
  const md = new MarkdownIt(options);

  // The allow-list decides which links survive, not the converter: a refused
  // link then reads as its text, where the converter would show its brackets.
  md.validateLink = () => true;

  md.use(footnote);
  // GitHub has no inline `^[note]` spelling, and neither does zenobia.
  md.inline.ruler.disable("footnote_inline");
  // Ids as `fn-1` / `fnref-1`, and the note set off the way Python-Markdown
  // writes it, so the allow-list's `footnote` tokens are what is styled.
  md.renderer.rules.footnote_anchor_name = (tokens, idx) => `-${Number(tokens[idx].meta.id) + 1}`;
  md.renderer.rules.footnote_caption = (tokens, idx) => String(Number(tokens[idx].meta.id) + 1);
  md.renderer.rules.footnote_ref = (tokens, idx, opts, env, self) => {
    const id = self.rules.footnote_anchor_name!(tokens, idx, opts, env, self);
    const caption = self.rules.footnote_caption!(tokens, idx, opts, env, self);
    const ref = tokens[idx].meta.subId > 0 ? `${id}:${tokens[idx].meta.subId}` : id;
    return `<sup id="fnref${ref}"><a class="footnote-ref" href="#fn${id}">${caption}</a></sup>`;
  };
  md.renderer.rules.footnote_block_open = () => '<div class="footnote">\n<hr>\n<ol>\n';
  md.renderer.rules.footnote_block_close = () => "</ol>\n</div>\n";
  md.renderer.rules.footnote_open = (tokens, idx, opts, env, self) =>
    `<li id="fn${self.rules.footnote_anchor_name!(tokens, idx, opts, env, self)}">`;
  md.renderer.rules.footnote_anchor = (tokens, idx, opts, env, self) => {
    const id = self.rules.footnote_anchor_name!(tokens, idx, opts, env, self);
    const ref = tokens[idx].meta.subId > 0 ? `${id}:${tokens[idx].meta.subId}` : id;
    return ` <a class="footnote-backref" href="#fnref${ref}">↩</a>`;
  };

  md.renderer.rules.s_open = () => "<del>";
  md.renderer.rules.s_close = () => "</del>";

  md.block.ruler.before("fence", "math_block", mathBlock, {
    alt: ["paragraph", "reference", "blockquote", "list"],
  });
  md.inline.ruler.before("escape", "math_inline", mathInline);
  md.renderer.rules.math_block = (tokens, idx, _opts, env: Env) => {
    env.math += 1;
    return `${formulaHtml(tokens[idx].content, true, true)}\n`;
  };
  md.renderer.rules.math_inline = (tokens, idx, _opts, env: Env) => {
    env.math += 1;
    return formulaHtml(tokens[idx].content, tokens[idx].meta.display === true);
  };

  const fence = md.renderer.rules.fence!;
  md.renderer.rules.fence = (tokens, idx, opts, env: Env, self) => {
    const token = tokens[idx];
    const info = fenceInfo(token.info);
    if (MERMAID_INFO.test(info)) {
      // The source stays ESCAPED text; the preview reads it back with
      // textContent for Mermaid's strict mode.
      env.mermaid += 1;
      return (
        `<div class="md-mermaid"><pre class="md-mermaid-source">${escapeHtml(token.content)}</pre>` +
        '<div class="md-mermaid-svg"></div></div>\n'
      );
    }
    if (MATH_INFO.test(info) && token.content.trim()) {
      env.math += 1;
      return `${formulaHtml(token.content, true, true)}\n`;
    }
    return fence(tokens, idx, opts, env, self);
  };

  // After `text_join`, so a URL or a `[ ]` is one text token, not several.
  md.core.ruler.push("md_task_lists", taskLists);
  md.core.ruler.push("md_bare_urls", bareUrls);
  md.core.ruler.push("md_headings", headings);
  return md;
}

const NESTING_PREFIX = /^(?:[ \t]|>|(?:[-*+]|\d{1,9}[.)])(?=[ \t]|$))+/gm;
const NESTING_MARKER = />|[-*+]|\d{1,9}[.)]/g;

/** The deepest block nesting any line asks for: each `>` and each list marker
 *  in its leading run is a level, and so is every four columns of indentation
 *  (a tab is four). An estimate: a `* * *` rule counts three. */
export function nestingDepth(source: string): number {
  let deepest = 0;
  NESTING_PREFIX.lastIndex = 0;
  for (let match = NESTING_PREFIX.exec(source); match; match = NESTING_PREFIX.exec(source)) {
    const prefix = match[0];
    const markers = prefix.match(NESTING_MARKER)?.length ?? 0;
    const spaces = prefix.split(" ").length - 1;
    const tabs = prefix.split("\t").length - 1;
    // A marker's own separating space is no indentation.
    const columns = Math.max(0, spaces - markers) + 4 * tabs;
    deepest = Math.max(deepest, markers + Math.floor(columns / 4));
  }
  return deepest;
}

/**
 * `text` as a rendered note: sanitized markup, its headings, and how many
 * diagrams and formulas it carries.
 *
 * Throws `MarkdownTooLarge` over the size cap and `MarkdownTooComplex` for
 * nesting past the limit; both are refusals to show, never a partial render.
 */
export function githubMarkdown(text: string, options: { resolver?: FragmentResolver | null } = {}): GithubMarkdown {
  if (new TextEncoder().encode(text).length > MAX_MARKDOWN_BYTES) throw new MarkdownTooLarge();
  // A byte-order mark glued to `# Title` would make it a paragraph.
  const source = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  if (nestingDepth(source) > MAX_MARKDOWN_NESTING) throw new MarkdownTooComplex();

  converter ??= build();
  const env: Env = { mermaid: 0, math: 0, toc: [] };
  let html: string;
  try {
    html = converter.render(source, env);
  } catch (err) {
    // The stack running out is the document's shape, not a bug to report.
    if (err instanceof RangeError) throw new MarkdownTooComplex();
    throw err;
  }
  return {
    html: scrubFragment(html, { resolver: options.resolver ?? null }),
    toc: env.toc,
    mermaid: env.mermaid,
    math: env.math,
  };
}

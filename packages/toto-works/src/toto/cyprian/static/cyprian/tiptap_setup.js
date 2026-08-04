/* TipTap, assembled for a document section.
 *
 * This is the ONLY ES module in cyprian, and the only one in this suite. It is
 * loaded as `<script type="module">` and resolves its bare imports through the
 * import map cyprian/tiptap.py renders into the page — see that module and
 * vendor/tiptap/VERSION.txt for why an import map rather than the UMD bundle
 * every other vendored library here uses.
 *
 * It exports one factory onto `window.CyprianTipTap`, so the Alpine component
 * (a classic script, like everything else) can drive it without becoming a
 * module itself.
 *
 * The custom marks and nodes below are ours because TipTap's equivalents write
 * INLINE STYLES — `@tiptap/extension-color` emits `style="color: …"` and the
 * server strips every `style` attribute in this suite, so the colour would
 * vanish on the first save. A class survives, means something in the reader and
 * in print, and follows the platform theme. That is the whole reason five small
 * extensions live here instead of five more vendored packages.
 */
import { Editor, Extension, Mark, Node, mergeAttributes } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import StarterKit from "@tiptap/starter-kit";
import Image from "@tiptap/extension-image";
import Link from "@tiptap/extension-link";
import Underline from "@tiptap/extension-underline";
import Placeholder from "@tiptap/extension-placeholder";
import Table from "@tiptap/extension-table";
import TableRow from "@tiptap/extension-table-row";
import TableCell from "@tiptap/extension-table-cell";
import TableHeader from "@tiptap/extension-table-header";

/* ---- colour and size, as classes ------------------------------------- */

const INKS = ["accent", "success", "warn"];
const SIZES = ["sm", "lg"];

/** One mark, one class, one attribute — used for both ink and size. */
function classMark(name, attribute, values, prefix) {
  return Mark.create({
    name,
    // A span carrying only a class; nothing here can nest into itself twice.
    addAttributes() {
      return {
        [attribute]: {
          default: null,
          parseHTML: (element) => {
            const found = values.find((v) =>
              element.classList.contains(prefix + v));
            return found || null;
          },
          renderHTML: (attrs) => {
            const value = attrs[attribute];
            return value ? { class: prefix + value } : {};
          },
        },
      };
    },
    parseHTML() {
      return [{
        tag: "span",
        getAttrs: (element) =>
          values.some((v) => element.classList.contains(prefix + v)) && null,
      }];
    },
    renderHTML({ HTMLAttributes }) {
      return ["span", mergeAttributes(HTMLAttributes), 0];
    },
    addCommands() {
      const setter = "set" + name[0].toUpperCase() + name.slice(1);
      const unsetter = "unset" + name[0].toUpperCase() + name.slice(1);
      return {
        [setter]: (value) => ({ commands }) =>
          values.indexOf(value) === -1
            ? commands.unsetMark(name)
            : commands.setMark(name, { [attribute]: value }),
        [unsetter]: () => ({ commands }) => commands.unsetMark(name),
      };
    },
  });
}

const CyInk = classMark("cyInk", "ink", INKS, "cy-ink-");
const CySize = classMark("cySize", "size", SIZES, "cy-size-");

/* ---- the three block nodes the toolbar offers -------------------------- */

/* A formula. The LaTeX lives in `data-latex`; the KaTeX rendering lives in the
 * node's HTML so the PDF has something to show — WeasyPrint runs no JavaScript.
 * `atom: true` because the rendered markup is not editable text: you edit the
 * source in the dialog, exactly as memo does. */
const Formula = Node.create({
  name: "formula",
  group: "block",
  atom: true,
  draggable: false,
  addAttributes() {
    return {
      latex: {
        default: "",
        parseHTML: (element) => element.getAttribute("data-latex") || "",
        renderHTML: (attrs) => ({ "data-latex": attrs.latex || "" }),
      },
      rendered: {
        default: "",
        parseHTML: (element) => element.innerHTML || "",
        renderHTML: () => ({}),
      },
    };
  },
  parseHTML() {
    return [{ tag: "div.cy-formula" }];
  },
  renderHTML({ HTMLAttributes, node }) {
    // The rendering is raw HTML, so it cannot go through renderHTML's array
    // form. A node view would be the alternative and would cost a DOM
    // implementation for something that never changes while displayed.
    const dom = document.createElement("div");
    dom.className = "cy-formula";
    dom.setAttribute("data-latex", node.attrs.latex || "");
    dom.innerHTML = node.attrs.rendered
      || `<code class="cy-formula-source">${escapeText(node.attrs.latex || "")}</code>`;
    return { dom };
  },
  addCommands() {
    return {
      setFormula: (attrs) => ({ commands }) =>
        commands.insertContent({ type: "formula", attrs }),
    };
  },
});

/* A callout: a note, a warning, an aside. Ordinary block content inside, so it
 * is editable in place — unlike a formula, a callout is just prose in a box. */
const Callout = Node.create({
  name: "callout",
  group: "block",
  content: "block+",
  defining: true,
  addAttributes() {
    return {
      tone: {
        default: "note",
        parseHTML: (element) => element.getAttribute("data-tone") || "note",
        renderHTML: (attrs) => ({ "data-tone": attrs.tone || "note" }),
      },
    };
  },
  parseHTML() {
    return [{ tag: "div.cy-callout" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes(HTMLAttributes, { class: "cy-callout" }), 0];
  },
  addCommands() {
    return {
      setCallout: (tone) => ({ commands }) =>
        commands.wrapIn("callout", { tone: tone || "note" }),
      unsetCallout: () => ({ commands }) => commands.lift("callout"),
    };
  },
});

/* A page break. Nothing to edit; the paginator gives it a page of its own and
 * the PDF gets `break-after: page`. */
const PageBreak = Node.create({
  name: "pageBreak",
  group: "block",
  atom: true,
  parseHTML() {
    return [{ tag: "div.cy-pagebreak" }];
  },
  renderHTML() {
    return ["div", { class: "cy-pagebreak" }];
  },
  addCommands() {
    return {
      setPageBreak: () => ({ commands }) =>
        commands.insertContent({ type: "pageBreak" }),
    };
  },
});

function escapeText(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;");
}

/* ---- pages -------------------------------------------------------------
 *
 * Where the A4 sheet ends, drawn as a real gap in the flow so text that passes
 * the boundary visibly lands on the next page while you type.
 *
 * As a ProseMirror DECORATION, not as attributes we set ourselves. The view
 * owns its DOM and re-renders nodes from its state, so anything written onto
 * those elements from outside is wiped by the next keystroke — which is exactly
 * what the first attempt did. A decoration is the supported way to say "this
 * node looks different" without touching the tree.
 */
const paginationKey = new PluginKey("cyPagination");

const Pagination = Extension.create({
  name: "cyPagination",
  addOptions() {
    return {
      // Usable height of one page's content box, in CSS pixels. A function so
      // a margin change is picked up without rebuilding the editor.
      pageHeight: () => 0,
      onPages: null,
    };
  },
  addProseMirrorPlugins() {
    const options = this.options;
    return [
      new Plugin({
        key: paginationKey,
        state: {
          init: () => DecorationSet.empty,
          apply(tr, old) {
            const next = tr.getMeta(paginationKey);
            return next === undefined ? old.map(tr.mapping, tr.doc) : next;
          },
        },
        props: {
          decorations(state) { return paginationKey.getState(state); },
        },
        view(view) {
          let frame = null;
          let last = "";

          const measure = () => {
            frame = null;
            const height = options.pageHeight();
            if (!height) return;

            const decorations = [];
            const tall = [];
            let used = 0;
            let page = 1;
            let offset = 0;

            view.state.doc.forEach((node, pos) => {
              const dom = view.nodeDOM(pos);
              const box = dom && dom.getBoundingClientRect
                ? dom.getBoundingClientRect().height : 0;
              const isBreak = node.type.name === "pageBreak";

              if (isBreak || (used + box > height && used > 0)) {
                page += 1;
                used = 0;
                decorations.push(Decoration.node(pos, pos + node.nodeSize, {
                  class: "cy-page-start",
                  "data-page-start": String(page),
                }));
              }
              if (box > height) {
                // Taller than a page on its own: it WILL split, and saying so
                // beats a silent surprise in the PDF.
                tall.push(pos);
                decorations.push(Decoration.node(pos, pos + node.nodeSize, {
                  class: "cy-tall", "data-tall": "1",
                }));
                used = height;
              } else {
                used += box;
              }
              offset += 1;
            });

            // Only dispatch when something actually moved: a transaction per
            // frame would re-enter this and never settle.
            const signature = decorations.map((d) => d.from + ":" + d.to).join(",")
                              + "|" + page;
            if (signature === last) return;
            last = signature;

            view.dispatch(view.state.tr.setMeta(
              paginationKey, DecorationSet.create(view.state.doc, decorations)));
            if (options.onPages) options.onPages(page, tall.length);
          };

          const schedule = () => {
            if (frame) return;
            frame = requestAnimationFrame(measure);
          };

          schedule();
          return {
            update: schedule,
            destroy() { if (frame) cancelAnimationFrame(frame); },
          };
        },
      }),
    ];
  },
});

/* ---- the editor ------------------------------------------------------- */

const EXTENSIONS = (options) => [
  StarterKit.configure({
    // Our own, so the class survives the sanitiser and the dialog owns the
    // source.
    horizontalRule: {},
    heading: { levels: [1, 2, 3] },
  }),
  Underline,
  Link.configure({ openOnClick: false, autolink: true,
                   protocols: ["http", "https", "mailto"] }),
  Image.configure({ inline: false, allowBase64: true }),
  Table.configure({ resizable: false }),
  TableRow, TableHeader, TableCell,
  CyInk, CySize, Formula, Callout, PageBreak,
  Placeholder.configure({ placeholder: "Write something" }),
  Pagination.configure({
    pageHeight: options.pageHeight || (() => 0),
    onPages: options.onPages || null,
  }),
];

/**
 * Mount an editor on `element`, seeded with `html`.
 *
 * `onUpdate` receives the HTML on every change — the model is read FROM the
 * editor and never written back into a focused one, which is memo's rule and
 * the reason the caret stays where the writer put it.
 */
function imageFile(transfer) {
  const files = transfer && transfer.files;
  if (!files || !files.length) return null;
  const file = files[0];
  return /^image\//.test(file.type) ? file : null;
}

function create(element, html, handlers) {
  handlers = handlers || {};
  return new Editor({
    element: element,
    extensions: EXTENSIONS(handlers),
    content: html || "<p></p>",
    editorProps: {
      attributes: { class: "cy-content cy-editable" },
      /* A screenshot pasted or dropped straight onto the page. The file goes to
       * whoever mounted us (the Alpine component uploads it and inserts the
       * data URI the server answers with) — the same path as the image dialog,
       * so the resize policy and the SVG sanitiser stay server-side and singular.
       * Returning true stops ProseMirror pasting the raw file as text. */
      handlePaste: (view, event) => {
        const file = imageFile(event.clipboardData);
        if (file && handlers.onImageFile) { handlers.onImageFile(file); return true; }
        return false;
      },
      handleDrop: (view, event) => {
        const file = imageFile(event.dataTransfer);
        if (file && handlers.onImageFile) { handlers.onImageFile(file); return true; }
        return false;
      },
    },
    onUpdate: ({ editor }) => {
      if (handlers.onUpdate) handlers.onUpdate(editor.getHTML());
    },
    onSelectionUpdate: ({ editor }) => {
      if (handlers.onSelection) handlers.onSelection(editor);
    },
    onFocus: ({ editor }) => {
      if (handlers.onFocus) handlers.onFocus(editor);
    },
  });
}

window.CyprianTipTap = { create: create, INKS: INKS, SIZES: SIZES };
window.dispatchEvent(new CustomEvent("cyprian-tiptap-ready"));

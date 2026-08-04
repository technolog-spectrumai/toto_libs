/* TipTap, assembled for a slide box.
 *
 * The vendored files and the import map that resolves the bare specifiers below
 * live in this app — see memo/tiptap.py for why, and for the four things that
 * had to be true for an import map to work in a repo with no bundler.
 *
 * This is deliberately a SMALLER editor than cyprian's. A slide box holds a
 * phrase, a sentence or a short list; the things a document needs — pagination,
 * page breaks, callouts, tables, colour and size marks — are either meaningless
 * at 1280x720 or belong to the slide's layout rather than to its prose.
 *
 * There are no class-setting marks here at all, and that is not an oversight:
 * `memo/sanitize.py` strips every attribute except a link's href/title, so a
 * mark that carried its meaning in a class would vanish on the first save. What
 * survives is what the allowlist already names — strong, em, u, s, code, a, and
 * the block tags a text box may hold — so this toolbar offers exactly that and
 * nothing that would silently disappear.
 *
 * It exports one factory onto `window.MemoTipTap`, so the Alpine component (a
 * classic script, like everything else here) can drive it without becoming a
 * module itself.
 */
import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import Underline from "@tiptap/extension-underline";
import Placeholder from "@tiptap/extension-placeholder";

/* Two shapes of box, one editor.
 *
 * `heading` and `quote` are sanitised with `sanitize.inline()` on the server:
 * block tags are unwrapped, so offering a bullet list in a heading would be
 * offering something that disappears on save. `text` goes through
 * `sanitize.rich()` and may hold real blocks. The difference is expressed once,
 * here, rather than in the toolbar template. */
const INLINE_ONLY = {
  heading: true,
  quote: true,
};

function extensionsFor(kind, placeholder) {
  const inline = INLINE_ONLY[kind] === true;
  return [
    StarterKit.configure({
      heading: false,          // the box's own "Size" select decides h1/h2/h3
      codeBlock: false,        // that is the `code` box, with ACE and a language
      horizontalRule: false,
      blockquote: inline ? false : {},
      bulletList: inline ? false : {},
      orderedList: inline ? false : {},
      listItem: inline ? false : {},
    }),
    Underline,
    Link.configure({ openOnClick: false, autolink: true,
                     protocols: ["http", "https", "mailto"] }),
    Placeholder.configure({ placeholder: placeholder || "" }),
  ];
}

/**
 * Mount an editor on `element`, seeded with `html`.
 *
 * `onUpdate` receives the HTML on every change — the model is read FROM the
 * editor and never written back into a focused one, which is this app's rule
 * everywhere and the reason the caret stays where the writer put it.
 */
function create(element, html, options) {
  options = options || {};
  return new Editor({
    element: element,
    extensions: extensionsFor(options.kind, options.placeholder),
    content: html || "",
    editorProps: {
      attributes: { class: "memo-prose" },
    },
    onUpdate: ({ editor }) => {
      if (options.onUpdate) options.onUpdate(editor.getHTML());
    },
    onSelectionUpdate: ({ editor }) => {
      if (options.onSelection) options.onSelection(editor);
    },
  });
}

window.MemoTipTap = { create: create, INLINE_ONLY: INLINE_ONLY };
window.dispatchEvent(new CustomEvent("memo-tiptap-ready"));

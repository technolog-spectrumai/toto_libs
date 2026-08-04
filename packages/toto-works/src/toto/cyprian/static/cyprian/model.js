/* The document, as data. No DOM, no Alpine, no fetch.
 *
 * There is very little left here, and that is the point: a document is a title,
 * a few settings and ONE body of HTML. TipTap owns what is inside the body, so
 * this file no longer has a vocabulary of blocks, sections or slots to keep in
 * step with anything.
 */
(function (global) {
  "use strict";

  /* Margins, as three named choices. Paper size is not a choice at all — A4
   * only, because a document that can be one of two sizes needs a control, a
   * rule in every stylesheet and a branch in the paginator, for something
   * almost nobody changes. */
  var MARGINS = [
    { id: "narrow", label: "Narrow" },
    { id: "normal", label: "Normal" },
    { id: "wide",   label: "Wide" }
  ];

  var FONTS = [
    { id: "serif",     label: "Serif" },
    { id: "sans",      label: "Sans" },
    { id: "mono",      label: "Mono" },
    { id: "rounded",   label: "Rounded" },
    { id: "condensed", label: "Condensed" }
  ];

  var FONT_CLASS = {
    serif: "cy-font-serif", sans: "cy-font-sans", mono: "cy-font-mono",
    rounded: "cy-font-rounded", condensed: "cy-font-condensed"
  };

  /* What the toolbar can put in a section, beyond what TipTap's own schema
   * gives us. There is no "block type" any more: a section is one document, and
   * these are nodes inside it.
   *
   * The dialog is down to the two that genuinely need a form — a picture has to
   * be chosen from somewhere, and a formula is LaTeX you want to see rendered
   * before you commit to it. Everything else is one click.
   */
  var DIALOG_KINDS = [
    { id: "image",   label: "Image",   icon: "fa-image" },
    { id: "formula", label: "Formula", icon: "fa-square-root-variable" }
  ];

  /* Text colour, from the Oya palette. Classes, never inline styles — the
   * server strips `style` everywhere in this suite, so an inline-style mark
   * would vanish on the first save. See sanitize_html.py. */
  var INKS = [
    { id: "accent",  label: "Accent" },
    { id: "success", label: "Success" },
    { id: "warn",    label: "Warning" }
  ];

  /* Three relative sizes. Headings already give three levels; this is for a
   * caption or an aside inside a paragraph. */
  var SIZES = [
    { id: "sm", label: "Small" },
    { id: "",   label: "Normal" },
    { id: "lg", label: "Large" }
  ];

  var TONES = [
    { id: "note",  label: "Note" },
    { id: "warn",  label: "Warning" },
    { id: "tip",   label: "Tip" },
    { id: "quote", label: "Aside" }
  ];

  function newId(prefix) {
    var rand;
    try {
      rand = global.crypto.randomUUID().replace(/-/g, "").slice(0, 8);
    } catch (e) {
      rand = Math.random().toString(16).slice(2, 10);
    }
    return prefix + "-" + rand;
  }



  /* Server dict -> editor state. Coerces every field, so a hand-edited file
   * cannot put the editor into a shape its templates cannot render. Unknown
   * block types and attributes survive untouched, matching what
   * document_format.py guarantees on the Python side. */
  /* Server dict -> editor state.
   *
   * One body, no sections: the document IS the content, and headings inside it
   * are the structure. Every field is coerced, so a hand-edited file cannot put
   * the editor into a shape its template cannot render.
   */
  function fromServer(data) {
    data = data || {};
    return {
      title: data.title || "",
      meta: Object.assign({}, data.meta || {}),
      font: FONT_CLASS[data.font] ? data.font : "serif",
      margins: ["narrow", "normal", "wide"].indexOf(data.margins) !== -1
               ? data.margins : "normal",
      toc: data.toc !== false,
      cover: data.cover === true,
      content: data.content || "<p></p>",
      attrs: Object.assign({}, data.attrs || {}),
      extra: (data.extra || []).slice()
    };
  }




  // ---- mutations -----------------------------------------------------------





  // ---- tables --------------------------------------------------------------





  // ---- counting ------------------------------------------------------------

  function stripTags(html) { return (html || "").replace(/<[^>]*>/g, " "); }

  function wordCount(state) {
    return stripTags(state.content).split(/\s+/).filter(Boolean).length;
  }

  function toPayload(state) {
    return {
      title: state.title,
      meta: state.meta,
      font: state.font,
      margins: state.margins,
      toc: state.toc,
      cover: state.cover,
      content: state.content,
      attrs: state.attrs,
      extra: state.extra
    };
  }

  global.CyprianModel = {
    MARGINS: MARGINS, FONTS: FONTS, FONT_CLASS: FONT_CLASS,
    DIALOG_KINDS: DIALOG_KINDS, TONES: TONES, INKS: INKS, SIZES: SIZES,
    newId: newId, fromServer: fromServer,
    wordCount: wordCount, toPayload: toPayload
  };
})(window);

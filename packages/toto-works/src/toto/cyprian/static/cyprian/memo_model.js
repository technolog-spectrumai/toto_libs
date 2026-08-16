/* The deck, as data. No DOM, no Alpine, no fetch.
 *
 * Everything that changes a deck is a function here, so there is exactly one
 * definition of what "move a block" means — shared by the drag handlers, the
 * keyboard equivalents and the context menu. That is not tidiness for its own
 * sake: the house rule is that every drag has a click equivalent calling the
 * SAME code path, and the only way to keep that true is to have one path.
 */
(function (global) {
  "use strict";

  /* A layout is a fixed set of BOXES, and that is the whole geometry model.
   *
   * Nothing here resizes or moves a box: you pick a layout, you get its boxes,
   * and any box takes any kind of block — text, a picture, an SVG, code, a
   * formula. Free geometry turns every slide into a small layout project and a
   * deck of thirty into thirty slightly different margins; more layouts is the
   * answer to "I need another shape".
   *
   * `slots` must match LAYOUT_SLOTS in presentation_format.py — the Python side
   * groups blocks for the player, the thumbnail and the PDF, and a disagreement
   * would show as a block that moves when you press Present.
   */
  var LAYOUTS = [
    { id: "title-content", label: "One box",              icon: "fa-square",
      slots: [""] },
    { id: "two-column",    label: "Two equal columns",    icon: "fa-table-columns",
      slots: ["left", "right"] },
    { id: "image-left",    label: "Two columns, wide left",  icon: "fa-table-columns",
      slots: ["media", "body"] },
    { id: "image-right",   label: "Two columns, wide right", icon: "fa-table-columns",
      slots: ["body", "media"] },
    { id: "three-column",  label: "Three columns",        icon: "fa-grip-lines-vertical",
      slots: ["left", "middle", "right"] },
    { id: "two-row",       label: "Two rows",             icon: "fa-grip-lines",
      slots: ["top", "bottom"] },
    { id: "grid",          label: "Four boxes",           icon: "fa-table-cells-large",
      slots: ["a", "b", "c", "d"] },
    { id: "lead",          label: "Tall box over a short one", icon: "fa-window-maximize",
      slots: ["lead", "body"] },
    { id: "full-bleed",    label: "One box, edge to edge", icon: "fa-panorama",
      slots: [""] },
    { id: "section",       label: "One box, centred",     icon: "fa-align-center",
      slots: [""] },
    { id: "quote",         label: "One box, large text",  icon: "fa-quote-left",
      slots: [""] }
  ];

  /* Box names are POSITIONS, never content. A layout called "picture and text"
   * decides for you what goes where; "two columns, wide left" leaves that to
   * the person writing the deck, which is the whole point of a box taking any
   * kind of content. The ids stay as they were so older files still load. */
  var SLOT_LABEL = {
    "": "Box", left: "Left", right: "Right", middle: "Middle",
    media: "Wide side", body: "Narrow side", top: "Top", bottom: "Bottom",
    a: "Top left", b: "Top right", c: "Bottom left", d: "Bottom right",
    lead: "Tall box"
  };

  function slotsFor(layout) {
    for (var i = 0; i < LAYOUTS.length; i++) {
      if (LAYOUTS[i].id === layout) return LAYOUTS[i].slots;
    }
    return [""];
  }

  function slotLabel(slot) { return SLOT_LABEL[slot] || slot; }

  /* Labels only — the applied class comes from a literal map below, never a
   * template string, because a JS-assembled class does not exist under the
   * Tailwind JIT build. */
  var FONTS = [
    { id: "sans",      label: "Sans" },
    { id: "serif",     label: "Serif" },
    { id: "mono",      label: "Mono" },
    { id: "rounded",   label: "Rounded" },
    { id: "condensed", label: "Condensed" }
  ];

  var FONT_CLASS = {
    sans: "memo-font-sans",
    serif: "memo-font-serif",
    mono: "memo-font-mono",
    rounded: "memo-font-rounded",
    condensed: "memo-font-condensed"
  };

  /* What the box toolbar offers.
   *
   * `html` is deliberately absent: it exists only as the shape a v1 slide
   * upgrades into, and offering it would invite people back into hand-writing
   * markup — the thing this editor replaces.
   *
   * `svg` is absent too, and that is a change rather than an oversight. An SVG
   * is a PICTURE, and asking someone to know which kind of picture file they
   * have before they can choose it is a question about file formats, not about
   * the slide. Image accepts both: the dialog switches the box to an svg node
   * by itself when the payload turns out to be markup, because an <img src>
   * pointing at SVG source would simply show nothing. Existing svg boxes still
   * render and still edit. */
  var BLOCK_TYPES = [
    { id: "heading", label: "Heading", icon: "fa-heading" },
    { id: "text",    label: "Text",    icon: "fa-paragraph" },
    { id: "list",    label: "List",    icon: "fa-list-ul" },
    { id: "image",   label: "Image",   icon: "fa-image" },
    { id: "code",    label: "Code",    icon: "fa-code" },
    { id: "quote",   label: "Quote",   icon: "fa-quote-left" },
    { id: "formula", label: "Formula", icon: "fa-square-root-variable" }
  ];

  /* The cheatsheet. Source and a short label; the preview beside each row is
   * rendered by KaTeX from the source itself, so it can never drift from what
   * you would actually get. */
  var FORMULA_HELP = [
    { id: "basics", label: "Basics", entries: [
      ["x^{2}", "power"],
      ["x_{i}", "index"],
      ["x_{i}^{2}", "both"],
      ["\\frac{a}{b}", "fraction"],
      ["\\sqrt{x}", "root"],
      ["\\sqrt[3]{x}", "nth root"],
      ["\\left( \\frac{a}{b} \\right)", "sized brackets"],
      ["|x|", "absolute"]
    ]},
    { id: "greek", label: "Greek", entries: [
      ["\\alpha \\beta \\gamma", "lower"],
      ["\\Gamma \\Delta \\Omega", "upper"],
      ["\\theta \\lambda \\mu", "more"],
      ["\\pi \\sigma \\phi", "more"],
      ["\\epsilon \\varepsilon", "two epsilons"]
    ]},
    { id: "bigops", label: "Sums", entries: [
      ["\\sum_{i=1}^{n} i", "sum"],
      ["\\prod_{i=1}^{n} i", "product"],
      ["\\int_{a}^{b} f(x)\\,dx", "integral"],
      ["\\iint_{D} f\\,dA", "double"],
      ["\\lim_{x \\to 0} \\frac{\\sin x}{x}", "limit"],
      ["\\frac{d}{dx} f(x)", "derivative"],
      ["\\partial_{x} f", "partial"]
    ]},
    { id: "relations", label: "Relations", entries: [
      ["a \\le b \\ge c", "inequalities"],
      ["a \\neq b", "not equal"],
      ["a \\approx b", "approximately"],
      ["a \\equiv b", "equivalent"],
      ["x \\in A \\subset B", "sets"],
      ["a \\to b", "arrow"],
      ["a \\Rightarrow b", "implies"],
      ["\\pm \\times \\cdot \\div", "operators"]
    ]},
    { id: "structures", label: "Structures", entries: [
      ["\\begin{matrix} a & b \\\\ c & d \\end{matrix}", "matrix"],
      ["\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}", "bracketed"],
      ["\\begin{cases} a & x < 0 \\\\ b & x \\ge 0 \\end{cases}", "cases"],
      ["\\vec{v} \\hat{n} \\bar{x}", "accents"],
      ["\\overline{AB}", "overline"],
      ["\\text{if } x > 0", "words in maths"]
    ]},
    { id: "examples", label: "Examples", entries: [
      ["E = mc^{2}", "mass-energy"],
      ["\\frac{-b \\pm \\sqrt{b^{2}-4ac}}{2a}", "quadratic"],
      ["e^{i\\pi} + 1 = 0", "Euler"],
      ["\\nabla \\cdot \\mathbf{E} = \\frac{\\rho}{\\varepsilon_0}", "Gauss"],
      ["P(A \\mid B) = \\frac{P(B \\mid A)P(A)}{P(B)}", "Bayes"]
    ]}
  ];

  var PLACEHOLDER = {
    heading: "Heading",
    text: "Say something",
    list: "List item",
    code: "code",
    quote: "Quote",
    formula: "E = mc^2",
    image: "",
    svg: "",
    html: ""
  };

  function newId(prefix) {
    var rand;
    try {
      rand = global.crypto.randomUUID().replace(/-/g, "").slice(0, 8);
    } catch (e) {
      rand = Math.random().toString(16).slice(2, 10);
    }
    return prefix + "-" + rand;
  }

  function newBlock(type) {
    var block = {
      id: newId("b"), type: type || "text", payload: "", items: [],
      slot: "", render: "", attrs: {}
    };
    if (type === "list") block.items = [""];
    if (type === "heading") block.attrs.level = "2";
    if (type === "image") block.attrs.fit = "contain";
    block.attrs.scale = "auto";
    return block;
  }

  function newSlide(layout) {
    var slide = {
      id: newId("s"), title: "", layout: layout || "title-content",
      blocks: [], attrs: {}, extra: []
    };
    if (slide.layout === "quote") slide.blocks.push(newBlock("quote"));
    else if (slide.layout === "section") slide.blocks.push(newBlock("heading"));
    else if (slide.layout === "full-bleed") slide.blocks.push(newBlock("image"));
    else slide.blocks.push(newBlock("text"));
    return slide;
  }

  /* Server dict -> editor state. Coerces every field, so a hand-edited file or
   * an older build cannot put the editor into a shape its templates cannot
   * render. Unknown block types and unknown attributes survive untouched —
   * matching what presentation_format.py guarantees on the Python side. */
  function fromServer(data) {
    data = data || {};
    var slides = (data.slides || []).map(function (s) {
      return {
        id: s.id || newId("s"),
        title: s.title || "",
        layout: s.layout || "title-content",
        blocks: (s.blocks || []).map(function (b) {
          return {
            id: b.id || newId("b"),
            type: b.type || "text",
            payload: b.payload || "",
            items: (b.items || []).slice(),
            slot: b.slot || "",
            render: b.render || "",
            attrs: Object.assign({}, b.attrs || {})
          };
        }),
        attrs: Object.assign({}, s.attrs || {}),
        extra: (s.extra || []).slice()
      };
    });
    if (!slides.length) slides = [newSlide("title-content")];
    return {
      title: data.title || "",
      theme: data.theme === "white" ? "white" : "black",
      font: FONT_CLASS[data.font] ? data.font : "sans",
      slides: slides,
      attrs: Object.assign({}, data.attrs || {}),
      extra: (data.extra || []).slice()
    };
  }

  /* Which flow regions a layout renders, as [slot, blocks] pairs. Mirrors
   * Slide.columns in presentation_format.py — the two must agree or the editor
   * shows a different arrangement from the player. */
  /* The slide's boxes, as [slot, blocks] pairs, in reading order.
   *
   * A block whose slot this layout does not have falls into the FIRST box and
   * keeps its own slot in the file — so switching layout and back puts every
   * block where it was. Mirrors Slide.columns in presentation_format.py. */
  function columns(slide) {
    if (!slide) return [];
    var slots = slotsFor(slide.layout);
    var buckets = {};
    slots.forEach(function (slot) { buckets[slot] = []; });
    slide.blocks.forEach(function (block) {
      var slot = slots.indexOf(block.slot) !== -1 ? block.slot : slots[0];
      buckets[slot].push(block);
    });
    return slots.map(function (slot) { return [slot, buckets[slot]]; });
  }

  function indexOfId(list, id) {
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return i;
    return -1;
  }

  function findSlide(state, id) {
    var at = indexOfId(state.slides, id);
    return at === -1 ? null : state.slides[at];
  }

  function findBlock(state, id) {
    for (var i = 0; i < state.slides.length; i++) {
      var at = indexOfId(state.slides[i].blocks, id);
      if (at !== -1) return { slide: state.slides[i], block: state.slides[i].blocks[at], at: at };
    }
    return null;
  }

  // ---- mutations -----------------------------------------------------------

  function moveSlide(state, id, to) {
    var from = indexOfId(state.slides, id);
    if (from === -1) return false;
    if (to > from) to -= 1;                       // removing shifts the target
    to = Math.max(0, Math.min(to, state.slides.length - 1));
    if (to === from) return false;
    state.slides.splice(to, 0, state.slides.splice(from, 1)[0]);
    return true;
  }

  /* Reorder a block, and move it between the columns of a two-column layout.
   *
   * Those are the SAME operation on purpose. `slot` is just another property,
   * so dropping into the right-hand column is a normal move rather than a
   * special case that has to be written, tested and kept in step separately.
   *
   * `before` is the id of the block to land in front of, or null for the end.
   */
  function moveBlock(state, id, slot, before) {
    var found = findBlock(state, id);
    if (!found) return false;
    var blocks = found.slide.blocks;
    var block = blocks.splice(found.at, 1)[0];
    block.slot = slot || "";
    var at = before ? indexOfId(blocks, before) : -1;
    if (at === -1) blocks.push(block); else blocks.splice(at, 0, block);
    return true;
  }

  function insertBlock(state, slideId, block, slot, before) {
    var at = indexOfId(state.slides, slideId);
    if (at === -1) return null;
    var blocks = state.slides[at].blocks;
    block.slot = slot || "";
    var to = before ? indexOfId(blocks, before) : -1;
    if (to === -1) blocks.push(block); else blocks.splice(to, 0, block);
    return block;
  }

  function deleteBlock(state, id) {
    var found = findBlock(state, id);
    if (!found) return false;
    found.slide.blocks.splice(found.at, 1);
    return true;
  }

  function nudge(list, id, delta) {
    var from = indexOfId(list, id);
    if (from === -1) return false;
    var to = from + delta;
    if (to < 0 || to >= list.length) return false;
    var moved = list.splice(from, 1)[0];
    list.splice(to, 0, moved);
    return true;
  }

  /* Changing layout never destroys a block. Slots that the new layout has no
   * region for are kept in the data and simply not laid out — so switching to
   * two-column and back leaves the arrangement intact. */
  function setLayout(state, slideId, layout) {
    var at = indexOfId(state.slides, slideId);
    if (at === -1) return false;
    state.slides[at].layout = layout;
    return true;
  }

  function toPayload(state) {
    return {
      title: state.title,
      theme: state.theme,
      font: state.font,
      slides: state.slides.map(function (s) {
        return {
          id: s.id, title: s.title, layout: s.layout,
          attrs: s.attrs, extra: s.extra,
          blocks: s.blocks.map(function (b) {
            return {
              id: b.id, type: b.type, payload: b.payload,
              items: b.items, slot: b.slot, render: b.render, attrs: b.attrs
            };
          })
        };
      }),
      attrs: state.attrs,
      extra: state.extra
    };
  }

  /* "auto" until the editor measures it, then a number. `blockScale` is what
   * the templates bind to, so an unmeasured block still renders at full size
   * rather than at zero. */
  function blockScale(block) {
    var raw = (block && block.attrs && block.attrs.scale) || "auto";
    var value = parseFloat(raw.indexOf("auto:") === 0 ? raw.slice(5) : raw);
    return isNaN(value) ? 1 : value;
  }

  /* Auto blocks keep re-fitting as their content changes; a pinned one does
   * not. The resolved number rides along with the intent — "auto:0.80" — so
   * storing a measurement never silently pins the block. */
  function isAutoScaled(block) {
    var raw = (block && block.attrs && block.attrs.scale) || "auto";
    return raw.indexOf("auto") === 0;
  }

  global.MemoModel = {
    LAYOUTS: LAYOUTS,
    slotsFor: slotsFor,
    slotLabel: slotLabel,
    blockScale: blockScale,
    isAutoScaled: isAutoScaled,
    FONTS: FONTS,
    FONT_CLASS: FONT_CLASS,
    FORMULA_HELP: FORMULA_HELP,
    BLOCK_TYPES: BLOCK_TYPES,
    PLACEHOLDER: PLACEHOLDER,
    newId: newId,
    newBlock: newBlock,
    newSlide: newSlide,
    fromServer: fromServer,
    columns: columns,
    indexOfId: indexOfId,
    findSlide: findSlide,
    findBlock: findBlock,
    moveSlide: moveSlide,
    moveBlock: moveBlock,
    insertBlock: insertBlock,
    deleteBlock: deleteBlock,
    nudge: nudge,
    setLayout: setLayout,
    toPayload: toPayload
  };
})(window);

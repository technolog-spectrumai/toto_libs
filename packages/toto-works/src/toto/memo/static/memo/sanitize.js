/* Client-side tidying for pasted content.
 *
 * THIS IS NOT A SECURITY BOUNDARY. The server re-sanitises everything through
 * toto/memo/sanitize.py on every save, and that is the pass that counts —
 * anyone can skip this file entirely by posting to the endpoint directly. What
 * this is for is the paste: dropping a paragraph in from a word processor or a
 * web page should leave the words and lose the four levels of
 * <span style="mso-..."> immediately and visibly, rather than looking fine
 * until the next reload quietly rewrites it.
 *
 * The allowlist is kept identical to the Python one on purpose. If the two
 * differ, text changes under the user on save, which reads as data loss.
 */
(function (global) {
  "use strict";

  var INLINE = ["B", "STRONG", "I", "EM", "U", "S", "DEL", "INS", "CODE", "A",
                "BR", "SPAN", "SUB", "SUP", "MARK", "SMALL"];
  // Kept identical to sanitize.py's BLOCK_TAGS — see the note there about the
  // writing lines as <div> and offering headings and quotes in its toolbar.
  var BLOCK = ["P", "DIV", "H1", "H2", "H3", "BLOCKQUOTE", "PRE",
               "UL", "OL", "LI"];
  var SAFE_SCHEME = /^(https?:|mailto:|[#/])/i;

  function clean(html, allowed) {
    // A <template> is inert: its contents are parsed but never fetched, never
    // executed and never rendered. innerHTML on a live node would run
    // <img onerror> before anything could be stripped.
    var host = document.createElement("template");
    host.innerHTML = html || "";
    walk(host.content, allowed);
    return host.innerHTML;
  }

  function walk(node, allowed) {
    var child = node.firstChild;
    while (child) {
      var next = child.nextSibling;
      if (child.nodeType === 1) {
        var tag = child.tagName;
        if (tag === "SCRIPT" || tag === "STYLE" || tag === "IFRAME" ||
            tag === "OBJECT" || tag === "EMBED") {
          child.remove();                       // dropped with its contents
        } else if (allowed.indexOf(tag) === -1) {
          walk(child, allowed);
          unwrap(child);                        // keep the words, lose the tag
        } else {
          strip(child);
          walk(child, allowed);
        }
      } else if (child.nodeType === 8) {
        child.remove();                         // comment
      }
      child = next;
    }
  }

  function strip(el) {
    var keep = el.tagName === "A" ? ["href", "title"] : [];
    var names = Array.prototype.map.call(el.attributes, function (a) { return a.name; });
    names.forEach(function (name) {
      if (keep.indexOf(name.toLowerCase()) === -1) { el.removeAttribute(name); return; }
      if (name.toLowerCase() === "href" && !SAFE_SCHEME.test(el.getAttribute(name) || "")) {
        el.removeAttribute(name);
      }
    });
  }

  function unwrap(el) {
    var parent = el.parentNode;
    while (el.firstChild) parent.insertBefore(el.firstChild, el);
    parent.removeChild(el);
  }

  global.MemoSanitize = {
    inline: function (html) { return clean(html, INLINE); },
    rich: function (html) { return clean(html, INLINE.concat(BLOCK)); },
    text: function (html) {
      var host = document.createElement("template");
      host.innerHTML = html || "";
      return host.content.textContent || "";
    }
  };
})(window);

// XML serialisation shared by the document and presentation formats.
//
// Both are ports of Python modules that write XML by hand, and both have golden
// tests asserting byte-identical output. These helpers are therefore matched to
// ElementTree's behaviour, not to what a browser would naturally emit — the
// differences are small and every one of them would show up as a spurious diff
// on the server after a save that changed nothing.

/** Python's `_esc_attr`: & < > and the quote. */
export function escAttr(text: string): string {
  return (text || "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** Python's `_esc_text`: deliberately only & and < . */
export function escText(text: string): string {
  return (text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
}

/** A CDATA section cannot contain "]]>"; split it across two sections so the
 *  byte sequence survives. The one rule that cannot lose a byte. */
export function wrapCdata(body: string): string {
  const safe = (body || "").replace(/]]>/g, "]]]]><![CDATA[>");
  return `<![CDATA[${safe}]]>`;
}

/** Known attributes in declared order, then unknown ones sorted. Deterministic
 *  output is what lets the golden tests compare strings. */
export function attrString(
  own: Array<[string, string]>, extra: Record<string, string>,
): string {
  const parts = own
    .filter(([, v]) => v !== "" && v !== null && v !== undefined)
    .map(([k, v]) => ` ${k}="${escAttr(v)}"`);
  for (const k of Object.keys(extra).sort()) {
    parts.push(` ${k}="${escAttr(extra[k])}"`);
  }
  return parts.join("");
}

/** An unknown element, preserved verbatim — the format's "nothing unknown is
 *  discarded" promise.
 *
 *  Normalised to ElementTree's spacing: Python writes `<tag />` for an empty
 *  element, the browser's XMLSerializer writes `<tag/>`. Without this, a deck
 *  carrying any self-closing unknown element re-serialises one byte different
 *  from what the server holds, and every save shows a diff for a document
 *  nobody edited.
 */
export function serializeUnknown(el: Element): string {
  return new XMLSerializer().serializeToString(el).trim().replace(/\s*\/>/g, " />");
}

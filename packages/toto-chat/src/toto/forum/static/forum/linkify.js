// Links in a room's messages (2026-10-02, stage 47.4).
//
// The owner: "forum automatically detects links to itself and make them
// hyperlinks BUT anywhere outside it will be just text". So a URL becomes a
// link only when its host is one of this platform's own names: the page's
// own host, plus the names the room view hands over (toto.forum.links). A
// URL anywhere else stays text, and so does everything that is not http(s).
//
// URLs are found the way Markdown Play finds them (zenobia's
// toto.htmlview.markdown_source: _URL_RE, _URL_TRAILING, _trim_url): http://,
// https:// or www., not glued to a word, an address or another URL; trailing
// punctuation and an unbalanced ")" are left out of the link.
//
// Built from text nodes and createElement only, never markup: a message is
// whatever its sender typed, and the row goes through Alpine.initTree. The
// link's text is the typed substring exactly, so reading the body's
// textContent back (Edit, Reply, reply quotes) gives the message unchanged.
//
// A link to another of our names points at this page's origin with the same
// path, query and fragment, so the session cookie goes with it. Same tab;
// a #msg-<uuid> in this room only changes the hash, which the room page's
// hashchange handler jumps to.

const URL_RE = /(?<![\p{L}\p{N}_/@.:\-])(?:https?:\/\/|www\.)[^\s\x85<>"'\x00-\x1f\x7f]+/giu;
const URL_TRAILING = ".,:;!?'\"*_~";
const EMPTY = new Set(["http://", "https://", "www.", ""]);

export const LINK_CLASS = "underline underline-offset-2 hover:opacity-80";

function count(text, char) {
  return text.split(char).length - 1;
}

function trimUrl(url) {
  while (url && (URL_TRAILING.includes(url[url.length - 1])
                 || (url[url.length - 1] === ")" && count(url, "(") < count(url, ")")))) {
    url = url.slice(0, -1);
  }
  return url;
}

function normalHosts(hosts) {
  const names = new Set();
  for (const host of hosts || []) {
    if (typeof host === "string" && host.trim()) names.add(host.trim().toLowerCase());
  }
  return names;
}

// Where a candidate URL may point, or null when it stays text.
export function selfHref(candidate, hosts, origin) {
  const absolute = /^https?:\/\//i.test(candidate) ? candidate : "https://" + candidate;
  // No user@ before the host: "https://ours@elsewhere" and "https://x@ours"
  // both read as something they are not.
  const authority = absolute.replace(/^https?:\/\//i, "").split(/[/?#\\]/)[0];
  if (authority.includes("@")) return null;
  let url;
  try {
    url = new URL(absolute);
  } catch (error) {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (url.username || url.password) return null;
  if (!normalHosts(hosts).has(url.hostname.toLowerCase())) return null;
  return origin + url.pathname + url.search + url.hash;
}

// The message cut into pieces: {text} or {text, href}. Joined, the texts are
// the message.
export function splitLinks(text, hosts, origin) {
  const message = String(text ?? "");
  const parts = [];
  let last = 0;
  for (const match of message.matchAll(URL_RE)) {
    const url = trimUrl(match[0]);
    if (EMPTY.has(url.toLowerCase())) continue;
    const href = selfHref(url, hosts, origin);
    if (!href) continue;
    if (match.index > last) parts.push({ text: message.slice(last, match.index) });
    parts.push({ text: url, href });
    last = match.index + url.length;
  }
  if (last < message.length) parts.push({ text: message.slice(last) });
  return parts;
}

// A DocumentFragment of text nodes and <a> elements for one message body.
export function linkify(text, hosts, doc = document, origin = location.origin) {
  const fragment = doc.createDocumentFragment();
  for (const part of splitLinks(text, hosts, origin)) {
    if (!part.href) {
      fragment.appendChild(doc.createTextNode(part.text));
      continue;
    }
    const link = doc.createElement("a");
    link.href = part.href;
    link.rel = "noopener";
    link.className = LINK_CLASS;
    link.appendChild(doc.createTextNode(part.text));
    fragment.appendChild(link);
  }
  return fragment;
}

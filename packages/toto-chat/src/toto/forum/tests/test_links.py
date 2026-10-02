"""Links in a room's messages: only to this platform, and only in the room
(2026-10-02, stage 47.4).

The owner: "forum automatically detects links to itself and make them
hyperlinks BUT anywhere outside it will be just text". static/forum/linkify.js
does it in the browser and is run here in node; toto.forum.links gives it the
platform's names; and the search page and the ZIP export, which show the same
bodies, are checked to show such a URL as escaped text.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from toto.forum import links, store
from toto.forum.models import ForumChannel, ForumMember

User = get_user_model()

SCRIPT = Path(__file__).resolve().parent.parent / "static" / "forum" / "linkify.js"

_NODE = shutil.which("node")

ORIGIN = "https://zenobia.example.org"
HOSTS = ["zenobia.example.org", "spectrumdata.pl", "www.spectrumdata.pl", "146.59.92.66"]
MSG = "#msg-0b0c3a9e-8d5c-4c1e-9a43-2f6d8e1b7a10"

#: Imports the script as the browser would (an ES module), with a document
#: that has only what the script may use: createDocumentFragment,
#: createTextNode, createElement and plain properties. Markup setters throw.
#: Prints, per message, the pieces and the fragment's textContent.
_HARNESS = r"""
const fs = require("fs");
const path = process.argv[1];
const cases = JSON.parse(fs.readFileSync(0, "utf8"));

class Text {
  constructor(text) { this.nodeType = 3; this.text = String(text); }
  get textContent() { return this.text; }
}
class Parent {
  constructor() { this.children = []; }
  appendChild(child) { this.children.push(child); return child; }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  set innerHTML(v) { throw new Error("innerHTML used"); }
  set outerHTML(v) { throw new Error("outerHTML used"); }
  insertAdjacentHTML() { throw new Error("insertAdjacentHTML used"); }
}
class Fragment extends Parent { constructor() { super(); this.nodeType = 11; } }
class Element extends Parent {
  constructor(tag) { super(); this.nodeType = 1; this.tagName = tag.toUpperCase();
                     this.href = ""; this.rel = ""; this.className = ""; this.target = ""; }
  setAttribute() { throw new Error("setAttribute used"); }
}
const doc = {
  createDocumentFragment: () => new Fragment(),
  createTextNode: (t) => new Text(t),
  createElement: (tag) => new Element(tag),
  write() { throw new Error("document.write used"); },
};

(async () => {
  const source = fs.readFileSync(path, "utf8");
  const mod = await import("data:text/javascript;charset=utf-8," + encodeURIComponent(source));
  const out = cases.map(({text, hosts, origin}) => {
    try {
      const fragment = mod.linkify(text, hosts, doc, origin);
      const parts = fragment.children.map((node) => node.nodeType === 3
        ? {text: node.textContent}
        : {tag: node.tagName, text: node.textContent, href: node.href, rel: node.rel,
           target: node.target, cls: node.className,
           kids: node.children.map((k) => k.nodeType)});
      return {parts, textContent: fragment.textContent};
    } catch (error) {
      return {error: String(error)};
    }
  });
  process.stdout.write(JSON.stringify(out));
})().catch((error) => { console.error(error); process.exit(1); });
"""


def _run(texts, hosts=HOSTS, origin=ORIGIN):
    cases = [{"text": t, "hosts": hosts, "origin": origin} for t in texts]
    done = subprocess.run([_NODE, "-e", _HARNESS, str(SCRIPT)], input=json.dumps(cases),
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    if done.returncode:
        raise AssertionError(done.stderr)
    results = json.loads(done.stdout)
    for text, result in zip(texts, results):
        if "error" in result:
            raise AssertionError(f"{text!r}: {result['error']}")
    return results


def _links(result):
    return [p for p in result["parts"] if "tag" in p]


@unittest.skipUnless(_NODE, "node is not installed")
class LinkifyTests(SimpleTestCase):
    def one(self, text, **kwargs):
        return _run([text], **kwargs)[0]

    def assertText(self, *texts, **kwargs):
        for text, result in zip(texts, _run(list(texts), **kwargs)):
            with self.subTest(text=text):
                self.assertEqual(_links(result), [], text)
                self.assertEqual(result["textContent"], text)

    def assertLink(self, text, link_text, href, **kwargs):
        result = self.one(text, **kwargs)
        found = _links(result)
        self.assertEqual(len(found), 1, result)
        self.assertEqual(found[0]["tag"], "A")
        self.assertEqual(found[0]["text"], link_text)
        self.assertEqual(found[0]["href"], href)
        self.assertEqual(result["textContent"], text)
        return found[0]

    # -- what becomes a link -------------------------------------------------

    def test_a_link_to_this_page_s_host_is_a_link_in_the_same_tab(self):
        url = f"https://zenobia.example.org/forum/room/{MSG}"
        link = self.assertLink(f"see {url} now", url, url)
        self.assertEqual(link["rel"], "noopener")
        self.assertEqual(link["target"], "")          # same tab
        self.assertIn("underline", link["cls"])
        self.assertEqual(link["kids"], [3])           # one text node inside

    def test_a_link_to_another_of_our_names_goes_to_this_origin(self):
        self.assertLink("https://spectrumdata.pl/forum/x/?q=1&b=2#y",
                        "https://spectrumdata.pl/forum/x/?q=1&b=2#y",
                        ORIGIN + "/forum/x/?q=1&b=2#y")
        self.assertLink("http://146.59.92.66/files/", "http://146.59.92.66/files/",
                        ORIGIN + "/files/")

    def test_a_port_does_not_matter(self):
        self.assertLink("http://zenobia.example.org:8443/a", "http://zenobia.example.org:8443/a",
                        ORIGIN + "/a")

    def test_www_is_https_and_needs_its_own_name_on_the_list(self):
        self.assertLink("go to www.spectrumdata.pl/forum/ please", "www.spectrumdata.pl/forum/",
                        ORIGIN + "/forum/")
        self.assertText("www.zenobia.example.org/forum/")   # www.<ours> is not on the list

    def test_capitals_in_scheme_and_host_are_the_same_host(self):
        self.assertLink("HTTPS://ZENOBIA.Example.ORG/Forum/", "HTTPS://ZENOBIA.Example.ORG/Forum/",
                        ORIGIN + "/Forum/")

    def test_the_host_list_is_compared_without_case_or_spaces(self):
        self.assertLink("https://spectrumdata.pl/", "https://spectrumdata.pl/", ORIGIN + "/",
                        hosts=["  SpectrumData.PL "])

    def test_two_links_and_the_text_between(self):
        result = self.one("a https://spectrumdata.pl/x b https://evil.com/ c "
                          "https://zenobia.example.org/y d")
        self.assertEqual([p.get("href") for p in result["parts"]],
                         [None, ORIGIN + "/x", None, ORIGIN + "/y", None])

    # -- what stays text ----------------------------------------------------

    def test_any_other_host_stays_text(self):
        self.assertText("https://evil.com/forum/", "see www.evil.com/x", "http://example.org",
                        "https://zenobia.example.com/")

    def test_other_schemes_stay_text(self):
        self.assertText("javascript:alert(1)", "data:text/html,<b>x</b>",
                        "JAVASCRIPT://zenobia.example.org/%0aalert(1)",
                        "javascript://www.spectrumdata.pl/%0aalert(1)",
                        "ftp://zenobia.example.org/x", "mailto:a@zenobia.example.org",
                        "//zenobia.example.org/forum/")

    def test_look_alike_hosts_stay_text(self):
        self.assertText(
            "https://zenobia.example.org.evil.com/",
            "https://evil.com/?x=zenobia.example.org",
            "https://evil.com/#https://zenobia.example.org/",
            "https://zenobia.example.org@evil.com/",
            "https://evil.com@zenobia.example.org/forum/",
            "https://user:pw@zenobia.example.org/",
            "https://:@zenobia.example.org/",
            "https://evil.com\\@zenobia.example.org/",
            "https://zenobia.example.org./",
            "https://zenobiа.example.org/",          # Cyrillic а
            "https://xn--zenobi-7ve.example.org/",
            "https://zenobia-example.org/",
            "https://zenobia.example.org%2eevil.com/",
        )

    def test_a_url_glued_to_a_word_or_address_stays_text(self):
        self.assertText("foohttps://zenobia.example.org/", "x@www.spectrumdata.pl",
                        "ążhttps://zenobia.example.org/", "/www.spectrumdata.pl",
                        "https://", "www.", "http://")

    def test_nothing_without_a_host_list_but_the_page_gives_its_own(self):
        self.assertText("https://zenobia.example.org/", hosts=[])
        self.assertText("https://zenobia.example.org/", hosts=None)

    # -- the edges of a link ------------------------------------------------

    def test_trailing_punctuation_is_left_out(self):
        for tail in (".", ",", ":", ";", "!", "?", "'", '"', "*", "_", "~", "!?.", "...", ")."):
            with self.subTest(tail=tail):
                url = "https://zenobia.example.org/forum/a/"
                self.assertLink(f"({url}{tail}" if tail.startswith(")") else url + tail,
                                url, url)

    def test_balanced_parentheses_stay_in(self):
        url = "https://zenobia.example.org/wiki/Foo_(bar)"
        self.assertLink(f"see {url}.", url, url)
        self.assertLink(f"(see {url})", url, url)

    def test_markup_ends_a_link_and_stays_text(self):
        result = self.one('https://zenobia.example.org/p"><script>alert(1)</script>')
        self.assertEqual(_links(result)[0]["text"], "https://zenobia.example.org/p")
        self.assertEqual(result["parts"][1], {"text": '"><script>alert(1)</script>'})

    def test_the_text_is_the_message_exactly(self):
        texts = [
            "", "plain", "line one\nhttps://zenobia.example.org/x\n\tline three",
            "<b>bold</b> &amp; https://zenobia.example.org/?a=<1>&b=2",
            "zażółć gęślą jaźń https://spectrumdata.pl/ą/ę?ż=ź#ł 🎉",
            "https://zenobia.example.org/a)b)c)",
            "((https://zenobia.example.org/(x)))",
            "https://zenobia.example.org/‮evil", "a\u0000b https://zenobia.example.org/\u0007",
            "www.spectrumdata.pl" * 3, "https://zenobia.example.org/" + "a" * 5000,
            "  https://zenobia.example.org  ", " https://zenobia.example.org/ ",
        ]
        for text, result in zip(texts, _run(texts)):
            with self.subTest(text=text[:60]):
                self.assertEqual(result["textContent"], text)
                self.assertEqual("".join(p["text"] for p in result["parts"]), text)

    def test_no_markup_is_ever_written(self):
        source = SCRIPT.read_text()
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith("//"))
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                          "eval(", "Function(", "setAttribute", "target"):
            self.assertNotIn(forbidden, code, forbidden)


class PlatformHostsTests(SimpleTestCase):
    @override_settings(
        PLATFORM_DOMAIN="Zenobia.Example.org", MONIT_CERT_DOMAIN="zenobia.example.org",
        TAILNET_PUBLIC_URL="https://box.tail1234.ts.net",
        ALLOWED_HOSTS=["localhost", "127.0.0.1", "web-zenobia", "nginx", "testserver",
                       "zenobia.local", "146.59.92.66", "spectrumdata.pl.",
                       "www.spectrumdata.pl", ".example.com", "*", "[::1]", "0.0.0.0",
                       "app.localhost", "[2001:db8::1]", "zenobia.example.org:8443", " "])
    def test_public_names_in_order_internal_ones_left_out(self):
        self.assertEqual(links.platform_hosts(), [
            "zenobia.example.org", "box.tail1234.ts.net", "zenobia.local", "146.59.92.66",
            "spectrumdata.pl", "www.spectrumdata.pl", "example.com", "[2001:db8::1]"])

    @override_settings(PLATFORM_DOMAIN="", MONIT_CERT_DOMAIN="", TAILNET_PUBLIC_URL="",
                       ALLOWED_HOSTS=[])
    def test_empty_settings_give_no_names(self):
        self.assertEqual(links.platform_hosts(), [])

    @override_settings(PLATFORM_DOMAIN="localhost", MONIT_CERT_DOMAIN=None,
                       TAILNET_PUBLIC_URL=None, ALLOWED_HOSTS=None)
    def test_the_bare_default_gives_no_names(self):
        self.assertEqual(links.platform_hosts(), [])

    def test_missing_settings_are_no_names(self):
        with override_settings():
            for name in ("PLATFORM_DOMAIN", "MONIT_CERT_DOMAIN", "TAILNET_PUBLIC_URL"):
                if hasattr(settings, name):
                    delattr(settings, name)
            with override_settings(ALLOWED_HOSTS=["zenobia.example.org"]):
                self.assertEqual(links.platform_hosts(), ["zenobia.example.org"])

    @override_settings(PLATFORM_DOMAIN="zażółć.pl", ALLOWED_HOSTS=[])
    def test_an_international_name_is_in_the_browser_s_spelling(self):
        self.assertIn("xn--za-6ja4f8n1l.pl", links.platform_hosts())


class _Page(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform
        from toto.people.models import Person

        Platform.objects.get_or_create(
            site_name="Toto", defaults={"author": "Test", "publication_year": 2026})
        cls.user = User.objects.create_user(username="linker", password="x")
        cls.person = Person.objects.create(user=cls.user, display_name="Linker")
        cls.room = ForumChannel.objects.create(name="Room", slug="room")
        ForumMember.objects.create(channel=cls.room, person=cls.person, is_active=True)

    def setUp(self):
        self.client.force_login(self.user)


@override_settings(PLATFORM_DOMAIN="zenobia.example.org", MONIT_CERT_DOMAIN="",
                   TAILNET_PUBLIC_URL="",
                   ALLOWED_HOSTS=["testserver", "web-zenobia", "nginx", "spectrumdata.pl"])
class RoomPageTests(_Page):
    def test_the_room_page_carries_the_names_and_loads_the_script(self):
        page = self.client.get("/forum/room/").content.decode()
        found = re.search(r'<script id="forum-link-hosts" type="application/json">(.*?)</script>',
                          page, re.S)
        self.assertIsNotNone(found, "no host list on the room page")
        self.assertEqual(json.loads(found.group(1)), ["zenobia.example.org", "spectrumdata.pl"])
        self.assertRegex(page, r"""\(\{ linkify \} = await import\("/static/forum/linkify[^"]*\.js"\)\)""")
        self.assertIn("body.appendChild(linkify(data.message", page)
        self.assertNotIn("web-zenobia", found.group(1))

    @unittest.skipUnless(_NODE, "node is not installed")
    def test_the_room_page_script_still_parses(self):
        page = self.client.get("/forum/room/").content.decode()
        script = re.search(r'<script type="module">(.*?)</script>', page, re.S).group(1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "room.mjs"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([_NODE, "--check", str(path)], capture_output=True, text=True,
                                  timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)


#: A URL to this platform, with what HTML must escape in it.
SELF_URL = "https://testserver/forum/room/?a=1&b=<2>"


@override_settings(PLATFORM_DOMAIN="testserver", ALLOWED_HOSTS=["testserver"])
class OutsideTheRoomTests(_Page):
    def setUp(self):
        super().setUp()
        store.store_message(self.room, msg_type="chat_message",
                            body=f"lookup {SELF_URL} please", sender=self.user,
                            sender_name="Linker")

    def assertPlainText(self, page):
        self.assertIn("https://testserver/forum/room/?a=1&amp;b=&lt;2&gt;", page)
        self.assertNotRegex(page, r"""href=["']https://testserver/forum/room/\?""")
        self.assertNotIn("<2>", page)

    def test_the_search_page_shows_it_as_text(self):
        page = self.client.get("/forum/search/", {"q": "lookup"}).content.decode()
        self.assertIn("lookup", page)
        self.assertPlainText(page)

    def test_the_export_shows_it_as_text(self):
        from toto.forum import export

        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        with override_settings(FORUM_ATTACHMENT_ROOT=root, MEDIA_ROOT=root):
            data = b"".join(export.stream_archive(export.survey(actor="linker")))
        page = zipfile.ZipFile(io.BytesIO(data)).read("rooms/room.html").decode()
        self.assertIn("lookup", page)
        self.assertPlainText(page)
        self.assertNotIn("<a href=\"http", page)

    def test_the_files_tab_caption_shows_it_as_text(self):
        from django.core.files.base import ContentFile

        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        with override_settings(FORUM_ATTACHMENT_ROOT=root, MEDIA_ROOT=root):
            store.store_message(
                self.room, msg_type="image_message", body=f"caption {SELF_URL}",
                sender=self.user, sender_name="Linker",
                attachment=ContentFile(b"bytes", name="shot.png"),
                attachment_name="shot.png", attachment_mime="image/png", attachment_size=5)
            page = self.client.get("/forum/room/files/").content.decode()
        self.assertIn("caption", page)
        self.assertPlainText(page)

    def test_the_history_api_gives_the_body_unchanged(self):
        data = self.client.get("/forum/api/channels/room/messages/").json()
        self.assertIn(f"lookup {SELF_URL} please", [m.get("message") for m in data["messages"]])

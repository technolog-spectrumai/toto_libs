"""Saving a document twice must not change it.

THE MISSING TEST. This app had 87 tests across five modules and not one
asserted `loads(dumps(d)) == d`, which is the single property a
read-path/write-path pair has to have. Its absence is why a real defect sat
here undetected and why the catalogued description of that defect was wrong.

What it found: `htmldoc.page` escapes the title through `_text` (`&` ->
`&amp;`) and `from_html.title_of` read it back RAW, so every save escaped an
already-escaped title. "Tom & Jerry" became "Tom &amp; Jerry", then
"Tom &amp;amp; Jerry" — one `amp;` per save, without limit.

The app's known-defects list said the accumulation was bare `<span>`s piling
up in the BODY. The body is provably fine: the sanitiser is idempotent and the
client drops unmatched spans. It was the title, and unlike the span story it
was unbounded.

ASYMMETRIC PARSING is the underlying shape and the reason this file exists:
the write path builds a page with a real serialiser, the read path takes it
apart with regexes. Those two can drift apart in either direction, and only a
round trip notices.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from ..htmldoc import Document, dumps, loads

#: Each is (label, title, body). Bodies are written the way the sanitiser
#: leaves them, so a difference after a round trip is the round trip's doing
#: and not the sanitiser normalising input on first contact.
CASES = [
    ("plain", "Notes", "<p>Hello</p>"),
    ("empty body", "Notes", ""),
    ("empty title", "", "<p>Hello</p>"),
    ("ampersand in title", "Tom & Jerry", "<p>x</p>"),
    ("angle bracket in title", "a < b", "<p>x</p>"),
    ("quote in title", 'The "Big" One', "<p>x</p>"),
    ("entity in body", "T", "<p>caf&eacute;</p>"),
    ("ampersand in body", "T", "<p>Tom &amp; Jerry</p>"),
    ("cdata close in body", "T", "<p>a ]]&gt; b</p>"),
    ("table", "T", "<table><tr><td>1</td><td>2</td></tr></table>"),
    ("nested marks", "T", "<ul><li><strong>a</strong> and <em>b</em></li></ul>"),
    ("heading and rule", "T", "<h2>Section</h2><hr>"),
    ("link", "T", '<p><a href="https://example.com/a?b=1&amp;c=2">x</a></p>'),
    ("blockquote", "T", "<blockquote><p>quoted</p></blockquote>"),
    ("code", "T", "<pre><code>if a &lt; b:\n    pass</code></pre>"),
]


class RoundTripTests(SimpleTestCase):
    def test_one_save_changes_nothing(self):
        for label, title, body in CASES:
            with self.subTest(case=label):
                first = Document.from_dict({"title": title, "content": body})
                again = loads(dumps(first))
                self.assertEqual(again.to_dict(), first.to_dict())

    def test_five_saves_change_nothing(self):
        """The compounding case, which one round trip can miss.

        A defect that adds a fixed amount each time looks like a small
        difference after one save and like corruption after ten. The title
        bug added exactly one `amp;` per cycle.
        """
        for label, title, body in CASES:
            with self.subTest(case=label):
                document = Document.from_dict({"title": title, "content": body})
                expected = document.to_dict()
                for _ in range(5):
                    document = loads(dumps(document))
                self.assertEqual(document.to_dict(), expected)

    def test_a_title_with_an_ampersand_does_not_grow(self):
        """Named on its own, because a subTest failure inside a loop is easy
        to read as "the loop is flaky" rather than "this input is broken"."""
        document = Document.from_dict({"title": "Tom & Jerry", "content": ""})
        for _ in range(10):
            document = loads(dumps(document))
        self.assertEqual(document.title, "Tom & Jerry")
        self.assertNotIn("amp;", document.title)

    def test_the_stored_page_escapes_the_title(self):
        """The other half of the pair. The fix must not be "stop escaping" —
        an unescaped `<` in a title would close the tag."""
        stored = dumps(Document.from_dict({"title": "a < b & c", "content": ""}))
        self.assertIn("<title>a &lt; b &amp; c</title>", stored)

    def test_a_bare_fragment_is_read_as_a_body(self):
        """`read` is documented as tolerant: a file hand-edited in ACE, with
        no <body>, is itself the body. Pinned so the regex path keeps it."""
        document = loads("<p>just a fragment</p>")
        self.assertEqual(document.content, "<p>just a fragment</p>")

    def test_the_case_list_is_not_empty(self):
        """A floor: every test above loops over CASES."""
        self.assertGreater(len(CASES), 10)

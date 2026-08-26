"""Turning a standalone HTML page into a document.

The knowledge lives in cyprian, not in the viewer that offers it: `toto.
htmlview` guarantees it has no endpoint that could persist a change, and
creating a document is a write. So the viewer links here, and keeps its
guarantee — see `from_html`'s module docstring.

What is asserted is the honest half of the deal the choice page states in
words: the body carries over, the page's own styling does not, and the
ORIGINAL FILE IS NOT TOUCHED.
"""

from django.test import SimpleTestCase

from toto.cyprian import from_html

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Working handbook</title>
<style>body { font-family: sans-serif; }</style>
</head>
<body><h1>Working handbook</h1><p class="meta">Internal.</p>
<table><tr><td>Q1</td></tr></table></body></html>"""


class BodyExtractionTests(SimpleTestCase):

    def test_the_body_is_taken_and_the_head_left(self):
        body = from_html.body_of(PAGE)
        self.assertIn("Working handbook", body)
        self.assertIn("<table", body)
        self.assertNotIn("<title>", body)
        self.assertNotIn("<meta", body)

    def test_the_stylesheet_does_not_come_with_it(self):
        """The one loss the choice page promises out loud."""
        self.assertNotIn("font-family", from_html.body_of(PAGE))

    def test_a_bare_fragment_is_used_whole(self):
        """A hand-written snippet has no <body>, and is not therefore empty."""
        self.assertEqual(from_html.body_of("<p>hi</p>"), "<p>hi</p>")

    def test_an_inline_script_is_dropped(self):
        body = from_html.body_of(
            "<body><p>keep</p><script>alert(1)</script></body>")
        self.assertIn("keep", body)
        self.assertNotIn("alert", body)

    def test_nothing_is_lost_after_a_void_element(self):
        """Guards the truncation bug fixed in `antivirus.sanitize.document`:
        a bare void tag used to drift the depth counter and silently discard
        the rest of the document."""
        body = from_html.body_of(
            '<body><p>first</p><noscript><img src="p.gif"></noscript>'
            '<p>last</p></body>')
        self.assertIn("first", body)
        self.assertIn("last", body)


class TitleTests(SimpleTestCase):

    def test_the_page_title_wins(self):
        self.assertEqual(from_html.title_of(PAGE, fallback="x.html"),
                         "Working handbook")

    def test_the_filename_is_the_fallback(self):
        """Without this a document arrives called report.html, extension and
        all, which is nobody's idea of a title."""
        self.assertEqual(from_html.title_of("<p>x</p>", fallback="notes.html"),
                         "notes")

    def test_an_empty_title_element_falls_back_too(self):
        self.assertEqual(
            from_html.title_of("<html><head><title></title></head><body>x</body>",
                               fallback="notes.html"),
            "notes")

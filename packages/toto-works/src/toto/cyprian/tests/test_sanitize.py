"""What a section's content is allowed to be.

This is the only sanitisation that counts: the writer posts whatever TipTap
serialised, and a document renders for other people in the reader, the library
and the PDF.
"""

from django.test import SimpleTestCase

from toto.cyprian.sanitize_html import ALLOWED_CLASSES, sanitize_content as clean


class TagTests(SimpleTestCase):
    def test_the_prose_vocabulary_survives(self):
        html = ("<p>hello <strong>bold</strong> <em>italic</em> "
                "<u>under</u> <s>struck</s> <code>code</code></p>")
        self.assertEqual(clean(html), html)

    def test_structure_survives(self):
        for html in ("<h1>One</h1>", "<h2>Two</h2>", "<h3>Three</h3>",
                     "<ul><li>a</li></ul>", "<ol><li>b</li></ol>",
                     "<blockquote><p>q</p></blockquote>",
                     "<pre><code>x</code></pre>", "<hr>"):
            with self.subTest(html=html):
                self.assertEqual(clean(html), html)

    def test_a_table_survives_with_its_header_row(self):
        html = ("<table><thead><tr><th>Region</th></tr></thead>"
                "<tbody><tr><td>North</td></tr></tbody></table>")
        self.assertEqual(clean(html), html)

    def test_a_colspan_survives_and_a_rubbish_one_does_not(self):
        self.assertIn('colspan="2"', clean('<td colspan="2">x</td>'))
        self.assertNotIn("colspan", clean('<td colspan="lots">x</td>'))

    def test_an_unknown_tag_is_unwrapped_not_dropped(self):
        # Pasting from a word processor must cost you the styling, not the
        # sentence.
        self.assertEqual(clean("<font size=7><big>words</big></font>"), "words")

    def test_a_script_is_dropped_with_its_contents(self):
        self.assertEqual(clean("<script>alert(1)</script>after"), "after")

    def test_a_nested_end_tag_does_not_end_the_suppression_early(self):
        self.assertEqual(clean("<script><b></b>alert(1)</script>ok"), "ok")

    def test_a_comment_never_survives(self):
        self.assertEqual(clean("<p>a<!-- secret -->b</p>"), "<p>ab</p>")


class AttributeTests(SimpleTestCase):
    def test_style_and_event_handlers_are_stripped(self):
        # The reason colour and size are classes: nothing keeps a style
        # attribute, so an inline-style mark would vanish on the first save.
        self.assertEqual(
            clean('<p style="color:red" onclick="x()" id="z">t</p>'),
            "<p>t</p>")

    def test_our_classes_survive_and_others_do_not(self):
        self.assertEqual(
            clean('<span class="cy-ink-success stolen-from-the-page">up</span>'),
            '<span class="cy-ink-success">up</span>')

    def test_every_allowed_class_really_is_allowed(self):
        for name in sorted(ALLOWED_CLASSES):
            with self.subTest(name=name):
                self.assertIn(name, clean(f'<span class="{name}">x</span>'))

    def test_a_link_keeps_a_safe_href(self):
        self.assertEqual(clean('<a href="https://ok.example">x</a>'),
                         '<a href="https://ok.example">x</a>')
        self.assertIn('href="/relative"', clean('<a href="/relative">x</a>'))

    def test_a_javascript_href_loses_the_link_and_keeps_the_words(self):
        for href in ("javascript:alert(1)", "java\tscript:alert(1)",
                     "data:text/html;base64,x"):
            with self.subTest(href=href):
                out = clean(f'<a href="{href}">click</a> tail')
                self.assertEqual(out, "click tail")
                self.assertNotIn("</a>", out, "a stray end tag was left behind")

    def test_an_image_must_be_a_self_contained_data_uri(self):
        self.assertIn("<img", clean('<img src="data:image/png;base64,iVBORw0K">'))
        self.assertEqual(clean('<img src="https://tracker.example/1.png">'), "")

    def test_a_picture_keeps_its_width_and_alignment(self):
        out = clean('<img src="data:image/png;base64,iVBORw0K" '
                    'data-width="50" data-align="left">')
        self.assertIn('data-width="50"', out)
        self.assertIn('data-align="left"', out)
        self.assertNotIn("data-width", clean(
            '<img src="data:image/png;base64,iVBORw0K" data-width="37">'))


class NodeTests(SimpleTestCase):
    def test_a_callout_keeps_a_real_tone_and_loses_an_invented_one(self):
        self.assertIn('data-tone="warn"',
                      clean('<div class="cy-callout" data-tone="warn"><p>x</p></div>'))
        self.assertNotIn("data-tone",
                         clean('<div class="cy-callout" data-tone="pink">x</div>'))

    def test_a_formula_keeps_its_source_and_its_rendering(self):
        out = clean('<div class="cy-formula" data-latex="E = mc^2">'
                    '<span class="katex">E</span></div>')
        self.assertIn('data-latex="E = mc^2"', out)
        self.assertIn('class="katex"', out)

    def test_a_page_break_survives(self):
        self.assertEqual(clean('<div class="cy-pagebreak"></div>'),
                         '<div class="cy-pagebreak"></div>')


class TextTests(SimpleTestCase):
    def test_only_ampersand_and_less_than_are_escaped(self):
        # A bare `>` is legal text, and escaping it would corrupt the literal
        # `]]>` the document format goes out of its way to preserve.
        self.assertEqual(clean("<p>a &amp; b &lt; c > d</p>"),
                         "<p>a &amp; b &lt; c > d</p>")

    def test_a_literal_cdata_terminator_passes_through(self):
        self.assertEqual(clean("<p>]]></p>"), "<p>]]></p>")

    def test_something_enormous_is_truncated_rather_than_refused(self):
        # A corrupt or hostile file must still open — the rule every cap in the
        # format follows.
        out = clean("<p>" + "x" * 5000 + "</p>", limit=1000)
        self.assertLess(len(out), 1200)

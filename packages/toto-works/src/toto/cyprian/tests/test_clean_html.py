"""Readable HTML out of a stored body.

The contract is narrow and worth stating: **content is preserved, whitespace is
not.** Every test here is either "the markup still says the same thing" or "a
person could now find their place in it".
"""

import base64

from django.test import SimpleTestCase

from .. import clean_html

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n not really a png").decode()
DATA_URI = f"data:image/png;base64,{PNG}"


class PrettyTests(SimpleTestCase):
    def test_blocks_get_their_own_lines(self):
        out = clean_html.pretty("<h1>Title</h1><p>One.</p><p>Two.</p>")
        self.assertEqual(out.splitlines(), [
            "<h1>", "  Title", "</h1>",
            "<p>", "  One.", "</p>",
            "<p>", "  Two.", "</p>",
        ])

    def test_nesting_indents(self):
        out = clean_html.pretty("<ul><li>One</li><li>Two</li></ul>")
        self.assertEqual(out.splitlines(), [
            "<ul>",
            "  <li>", "    One", "  </li>",
            "  <li>", "    Two", "  </li>",
            "</ul>",
        ])

    def test_inline_markup_stays_in_the_sentence(self):
        """A newline inside a sentence is a space on the page.

        Breaking before `<em>` would move the words, so inline runs are left
        exactly as the author wrote them.
        """
        out = clean_html.pretty("<p>A <em>stressed</em> word and <strong>bold</strong>.</p>")
        self.assertEqual(out.splitlines(), [
            "<p>", "  A <em>stressed</em> word and <strong>bold</strong>.", "</p>",
        ])

    def test_pre_is_never_touched(self):
        """Whitespace inside <pre> is content, not formatting."""
        source = "<pre>def f():\n    return 1\n</pre>"
        self.assertIn("def f():\n    return 1", clean_html.pretty(source))

    def test_void_elements_do_not_open_a_level(self):
        out = clean_html.pretty("<p>One<br>Two</p><hr>")
        self.assertEqual(out.splitlines(), [
            "<p>", "  One<br>Two", "</p>", "<hr>",
        ])

    def test_a_table_indents_by_row_and_cell(self):
        out = clean_html.pretty("<table><tr><td>a</td><td>b</td></tr></table>")
        self.assertEqual(out.splitlines(), [
            "<table>",
            "  <tr>",
            "    <td>", "      a", "    </td>",
            "    <td>", "      b", "    </td>",
            "  </tr>",
            "</table>",
        ])

    def test_attributes_survive_with_their_quoting(self):
        out = clean_html.pretty('<div class="cy-callout" data-tone="warn">Careful</div>')
        self.assertIn('<div class="cy-callout" data-tone="warn">', out)

    def test_the_ampersand_rule_is_the_sanitizers(self):
        """`&` and `<` escaped, `>` left bare — the same rule as _esc_text."""
        out = clean_html.pretty("<p>a &amp; b &lt; c > d</p>")
        self.assertIn("a &amp; b &lt; c > d", out)

    def test_empty_input_is_empty_output(self):
        self.assertEqual(clean_html.pretty(""), "")

    def test_it_is_idempotent(self):
        """Running it twice must not keep adding indentation."""
        once = clean_html.pretty("<div><p>Text</p></div>")
        self.assertEqual(clean_html.pretty(once), once)


class ExtractImagesTests(SimpleTestCase):
    def test_an_embedded_picture_comes_out_and_the_markup_shrinks(self):
        body = f'<p>Before</p><img src="{DATA_URI}" alt="Company logo"><p>After</p>'

        out, images = clean_html.extract_images(body)

        self.assertEqual(len(images), 1)
        self.assertNotIn("base64", out)
        self.assertLess(len(out), len(body))
        self.assertEqual(images[0].data, b"\x89PNG\r\n\x1a\n not really a png")

    def test_the_name_comes_from_the_alt_text_because_a_person_wrote_it(self):
        body = f'<img src="{DATA_URI}" alt="Company Logo">'
        _, images = clean_html.extract_images(body)
        self.assertEqual(images[0].name, "company-logo")
        self.assertEqual(images[0].filename, "company-logo.png")

    def test_an_unnamed_picture_gets_a_number(self):
        body = f'<img src="{DATA_URI}"><img src="{DATA_URI}">'
        _, images = clean_html.extract_images(body)
        self.assertEqual([i.name for i in images], ["image-1", "image-2"])

    def test_two_pictures_with_the_same_alt_do_not_collide(self):
        body = f'<img src="{DATA_URI}" alt="Logo"><img src="{DATA_URI}" alt="Logo">'
        _, images = clean_html.extract_images(body)
        self.assertEqual([i.name for i in images], ["logo", "logo-2"])

    def test_the_caller_decides_what_the_placeholder_says(self):
        """Jinja is Aralia's spelling, and it does not belong in this module."""
        body = f'<img src="{DATA_URI}" alt="Logo">'
        out, _ = clean_html.extract_images(
            body, namer=lambda name: "{{ asset('%s') }}" % name)
        self.assertIn("""src="{{ asset('logo') }}\"""", out)

    def test_jpeg_gets_the_extension_people_expect(self):
        body = f'<img src="data:image/jpeg;base64,{PNG}" alt="Photo">'
        _, images = clean_html.extract_images(body)
        self.assertEqual(images[0].filename, "photo.jpg")

    def test_a_src_we_cannot_decode_is_left_alone(self):
        """This module rewrites pictures; policing them is the sanitizer's job."""
        body = '<img src="https://example.com/logo.png" alt="Remote">'
        out, images = clean_html.extract_images(body)
        self.assertEqual(images, [])
        self.assertIn('src="https://example.com/logo.png"', out)

    def test_a_payload_broken_across_lines_still_decodes(self):
        """The sanitizer's regex tolerates whitespace in base64, so this happens."""
        body = f'<img src="data:image/png;base64,{PNG[:8]}\n{PNG[8:]}" alt="Wrapped">'
        _, images = clean_html.extract_images(body)
        self.assertEqual(len(images), 1)

    def test_rubbish_base64_is_not_an_image(self):
        body = '<img src="data:image/png;base64,!!!!not base64!!!!" alt="Bad">'
        _, images = clean_html.extract_images(body)
        self.assertEqual(images, [])

    def test_the_alt_text_survives_onto_the_image(self):
        body = f'<img src="{DATA_URI}" alt="Company logo">'
        _, images = clean_html.extract_images(body)
        self.assertEqual(images[0].alt, "Company logo")


class ForAuthoringTests(SimpleTestCase):
    def test_a_real_document_becomes_something_you_could_type_into(self):
        body = (f'<h1>Invoice</h1><p><img src="{DATA_URI}" alt="Logo"></p>'
                "<table><tr><td>Widget</td><td>2</td></tr></table>")

        out, images = clean_html.for_authoring(
            body, namer=lambda name: "{{ asset('%s') }}" % name)

        self.assertEqual([i.name for i in images], ["logo"])
        self.assertNotIn("base64", out)
        self.assertIn("\n", out)
        # The place a person would put a Jinja tag is now findable.
        self.assertIn("      Widget", out)

    def test_the_pictures_come_out_before_the_indenting(self):
        """Otherwise the indenter is handed a 40 kB attribute and 'readable' is
        a word with no meaning."""
        body = f'<p><img src="{DATA_URI}" alt="Logo"></p>'
        out, _ = clean_html.for_authoring(body)
        self.assertTrue(all(len(line) < 200 for line in out.splitlines()))

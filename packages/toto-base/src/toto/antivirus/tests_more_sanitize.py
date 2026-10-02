"""The rewriting sanitisers (``toto.antivirus.sanitize``) that stand in front
of every rich-text save: slide text, inline SVG, cached KaTeX and a
document section. Each test is one thing a hostile or merely messy input
tries, and what must come out.
"""

from unittest import skip

from django.test import SimpleTestCase

from toto.antivirus.sanitize import (plain_text, sanitize_content, sanitize_inline,
                                     sanitize_rich, sanitize_svg)
from toto.antivirus.sanitize.markup import sanitize_katex


class InlineAndRichTests(SimpleTestCase):
    def test_inline_marks_survive_and_their_attributes_do_not(self):
        self.assertEqual(
            sanitize_inline('<b class="x" style="color:red" onclick="evil()">bold</b> <em>it</em>'),
            "<b>bold</b> <em>it</em>")

    def test_an_unknown_tag_is_unwrapped_and_its_words_kept(self):
        self.assertEqual(sanitize_inline("<font face=x>Hello</font> <p>para</p>"), "Hello para")

    def test_script_style_and_iframe_go_with_everything_inside(self):
        self.assertEqual(sanitize_inline("a<script>alert(1)</script>b<style>*{}</style>c"
                                         "<iframe><b>x</b></iframe>d"), "abcd")

    def test_a_nested_mark_inside_a_dropped_frame_does_not_end_the_drop(self):
        self.assertEqual(sanitize_rich("<object><p>hidden</p></object><p>shown</p>"),
                         "<p>shown</p>")

    def test_script_urls_are_refused_however_they_are_spelled(self):
        for href in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\tscript:alert(1)",
                     " javascript:alert(1)", "&#106;avascript:alert(1)",
                     "java\nscript:alert(1)", "vbscript:x", "data:text/html,<b>"):
            with self.subTest(href=href):
                self.assertEqual(sanitize_inline(f'<a href="{href}">x</a>'), "<a>x</a>")

    def test_ordinary_links_survive_with_their_title_escaped(self):
        self.assertEqual(sanitize_inline('<a href="https://e.org/?a=1&amp;b=2" '
                                         'title="say &quot;hi&quot;">x</a>'),
                         '<a href="https://e.org/?a=1&amp;b=2" title="say &quot;hi&quot;">x</a>')
        for href in ("/relative", "#slide-3", "mailto:a@b.c", "page.html"):
            with self.subTest(href=href):
                self.assertIn(f'href="{href}"', sanitize_inline(f'<a href="{href}">x</a>'))

    def test_text_is_escaped_once_and_a_bare_gt_is_left_alone(self):
        self.assertEqual(sanitize_inline("a > b &amp; c &lt;script&gt;"),
                         "a > b &amp; c &lt;script>")

    def test_comments_never_survive_and_nothing_is_empty(self):
        self.assertEqual(sanitize_inline("a<!-- secret -->b"), "ab")
        self.assertEqual(sanitize_inline(None), "")
        self.assertEqual(sanitize_rich(""), "")

    def test_rich_keeps_blocks_inline_does_not(self):
        html = "<h1>T</h1><blockquote><p>q</p></blockquote><ul><li>i</li></ul><h4>x</h4>"
        self.assertEqual(sanitize_rich(html),
                         "<h1>T</h1><blockquote><p>q</p></blockquote><ul><li>i</li></ul>x")
        self.assertEqual(sanitize_inline(html), "Tqix")

    def test_a_line_break_is_emitted_without_a_closing_tag(self):
        self.assertEqual(sanitize_inline("a<br/>b<br>c</br>"), "a<br>b<br>c")

    def test_plain_text_drops_every_tag_and_decodes_entities_once(self):
        self.assertEqual(plain_text("<b>Fish &amp; chips</b><script>x()</script> &amp;lt;"),
                         "Fish & chips &lt;")


class SvgTests(SimpleTestCase):
    def test_a_plain_drawing_keeps_its_case_sensitive_names(self):
        svg = ('<svg viewBox="0 0 10 10" preserveAspectRatio="none"><linearGradient id="g">'
               '</linearGradient><rect width="1"/></svg>')
        self.assertEqual(sanitize_svg(svg), svg)

    def test_every_event_handler_goes(self):
        self.assertEqual(sanitize_svg('<svg onload="x()"><g onClick="y()"/></svg>'),
                         "<svg><g/></svg>")

    def test_a_dropped_element_takes_its_whole_subtree(self):
        self.assertEqual(sanitize_svg("<svg><foreignObject><a></a>payload</foreignObject>"
                                      "<g/></svg>"), "<svg><g/></svg>")

    def test_the_same_element_nested_in_itself_stays_dropped_to_its_real_end(self):
        self.assertEqual(sanitize_svg("<svg><foreignObject><foreignObject></foreignObject>"
                                      "inner</foreignObject>tail</svg>"), "<svg>tail</svg>")

    def test_only_same_document_references_survive(self):
        self.assertEqual(sanitize_svg('<svg><use href="#a"/><use href="https://e/x.svg#a"/>'
                                      '<image xlink:href="data:image/png;base64,AA"/></svg>'),
                         '<svg><use href="#a"/><use/></svg>')

    def test_an_animation_that_rewrites_a_link_is_dropped_whole(self):
        for tag in ('<animate attributeName="href" to="javascript:x">'
                    '<desc>kept?</desc></animate>',
                    '<set attributeName=" XLINK:HREF " to="javascript:x"/>'):
            with self.subTest(tag=tag):
                self.assertEqual(sanitize_svg(f"<svg><g>{tag}</g></svg>"),
                                 '<svg><g></g></svg>')

    def test_an_ordinary_animation_is_kept(self):
        self.assertEqual(sanitize_svg('<svg><animate attributeName="opacity" dur="1s"/></svg>'),
                         '<svg><animate attributeName="opacity" dur="1s"/></svg>')

    def test_a_style_url_keeps_only_a_same_document_reference(self):
        self.assertEqual(sanitize_svg('<svg style="fill:url(javascript:x)"/>'), "<svg/>")
        self.assertEqual(sanitize_svg('<svg style="fill:url(https://e/p.png)"/>'), "<svg/>")
        self.assertEqual(sanitize_svg('<svg style="fill:url(#g)"/>'),
                         '<svg style="fill:url(#g)"/>')

    def test_prolog_doctype_and_comments_are_removed_and_entities_kept(self):
        svg = ('<?xml version="1.0"?><!DOCTYPE svg><!-- c --><svg><text>&amp; &#169;'
               '</text></svg>')
        self.assertEqual(sanitize_svg(svg), "<svg><text>&amp; &#169;</text></svg>")

    def test_a_stray_close_of_a_dropped_tag_is_ignored(self):
        self.assertEqual(sanitize_svg("<svg></script><g/></svg>"), "<svg><g/></svg>")

    def test_html_breakout_tags_and_their_payload_never_survive(self):
        # Stage 51: a browser leaves foreign content at <p>, <meta>, <div> and
        # the like, so a <form> after one is a live HTML form on the page.
        out = sanitize_svg('<svg><p></p><form action=x><button>b</button></form>'
                           '<meta http-equiv=refresh content=0><style>a{}</style>'
                           '</div></svg>')
        for needle in ("form", "button", "meta", "style", "<p", "</div>", "refresh"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, out)
        self.assertEqual(out, "<svg></svg>")

    def test_only_drawing_elements_and_their_attributes_are_kept(self):
        # A link is unwrapped: the drawing inside it stays, the link does not.
        self.assertEqual(
            sanitize_svg('<svg class="fixed inset-0" width="9"><a href="#t"><rect x="1"/></a>'
                         '<image href="#i"/><font color=red>f</font><circle r="2" '
                         'data-x="1" formaction="javascript:x" fill="red"/></svg>'),
            '<svg width="9"><rect x="1"/><circle r="2" fill="red"/></svg>')

    def test_nothing_is_kept_outside_an_svg_root_and_the_tree_is_balanced(self):
        self.assertEqual(sanitize_svg('<rect/>text<svg><g><rect></svg></svg><b>x</b>'),
                         "<svg><g><rect></rect></g></svg>")

    def test_an_unclosed_breakout_tag_does_not_swallow_the_close(self):
        self.assertEqual(sanitize_svg('<svg><p>x<form><input name=a></svg>'),
                         "<svg></svg>")

    def test_a_style_keeps_presentation_and_drops_layout(self):
        self.assertEqual(
            sanitize_svg('<svg style="position:fixed;inset:0;fill:red;'
                         'stroke:url(#g);opacity:.5;background:url(https://e/p.png)"/>'),
            '<svg style="fill:red;stroke:url(#g);opacity:.5"/>')


class KatexTests(SimpleTestCase):
    def test_nothing_in_is_nothing_out(self):
        self.assertEqual(sanitize_katex(""), "")
        self.assertEqual(sanitize_katex("   "), "")
        self.assertEqual(sanitize_katex(None), "")

    def test_only_katexs_own_classes_survive(self):
        self.assertEqual(sanitize_katex('<span class="katex mord evil sizing size3">x</span>'),
                         '<span class="katex mord sizing size3">x</span>')

    def test_style_keeps_metrics_and_drops_anything_that_could_move_or_fetch(self):
        out = sanitize_katex('<span style="height:1em;position:fixed;top:0;'
                             'background:url(x);width:url(https://e);position:relative">x</span>')
        self.assertEqual(out, '<span style="height:1em;top:0;position:relative">x</span>')

    def test_an_element_katex_never_writes_goes_with_its_subtree(self):
        self.assertEqual(sanitize_katex('<span>a<a href="https://e">link<b>b</b></a>c</span>'),
                         "<span>ac</span>")

    def test_handlers_ids_and_links_are_dropped_and_mathml_kept(self):
        out = sanitize_katex('<math onclick="x()" id="m"><semantics><mi mathvariant="bold">'
                             'x</mi><annotation encoding="application/x-tex">x</annotation>'
                             '</semantics></math>')
        self.assertEqual(out, '<math><semantics><mi mathvariant="bold">x</mi>'
                              '<annotation encoding="application/x-tex">x</annotation>'
                              '</semantics></math>')

    def test_the_stretchy_svg_keeps_its_viewbox_case(self):
        self.assertEqual(sanitize_katex('<svg viewbox="0 0 1 1"><path d="M0 0"/></svg>'),
                         '<svg viewBox="0 0 1 1"><path d="M0 0"/></svg>')


class DocumentTests(SimpleTestCase):
    def test_a_tracking_pixel_in_noscript_does_not_eat_the_document(self):
        self.assertEqual(sanitize_content('<noscript><img src="pixel.gif"></noscript>'
                                          '<p>after</p>'), "<p>after</p>")

    def test_an_embed_never_starts_a_drop_nothing_can_end(self):
        self.assertEqual(sanitize_content('<embed src="x.swf"><p>after</p>'), "<p>after</p>")

    def test_a_picture_must_live_in_the_file(self):
        self.assertEqual(sanitize_content('<img src="https://e/p.png" alt="x">'), "")
        data = "data:image/png;base64,iVBORw0KGgo="
        self.assertEqual(sanitize_content(f'<img src="{data}" alt="a &quot;b&quot;" '
                                          f'onerror="x()">'),
                         f'<img src="{data}" alt="a &quot;b&quot;">')

    def test_a_link_without_a_target_loses_the_tag_and_keeps_the_words(self):
        self.assertEqual(sanitize_content('<p><a href="javascript:x()">click</a> me</p>'),
                         "<p>click me</p>")

    def test_only_our_classes_and_our_data_values_survive(self):
        out = sanitize_content('<div class="cy-callout evil" data-tone="warn" data-x="1">'
                               '<span class="cy-ink-accent" data-tone="shout">t</span></div>')
        self.assertEqual(out, '<div class="cy-callout" data-tone="warn">'
                              '<span class="cy-ink-accent">t</span></div>')

    def test_data_attributes_are_checked_against_their_vocabulary(self):
        out = sanitize_content('<pre data-language="python3" data-align="left" data-width="40">'
                               '<code data-language="a b">x</code></pre>')
        self.assertEqual(out, '<pre data-language="python3" data-align="left">'
                              '<code>x</code></pre>')

    def test_table_spans_must_be_small_numbers(self):
        self.assertEqual(sanitize_content('<table><tr><td colspan="2" rowspan="x">a</td>'
                                          '<td colspan="1234">b</td></tr></table>'),
                         '<table><tr><td colspan="2">a</td><td>b</td></tr></table>')

    def test_svg_and_math_are_dropped_with_their_contents_in_a_document(self):
        self.assertEqual(sanitize_content("<p>a<svg><text>t</text></svg>b<math><mi>x</mi>"
                                          "</math>c</p>"), "<p>abc</p>")

    def test_an_enormous_body_is_truncated_not_refused(self):
        self.assertEqual(sanitize_content("<p>" + "x" * 50, limit=10), "<p>xxxxxxx")

    @skip("BUG antivirus/sanitize/document.py:230 handle_endtag - an unclosed non-void child "
          "inside a dropped element (e.g. <noscript><p>..</noscript>, legal HTML with an "
          "optional </p>) shifts the depth, the (tag, depth) pair never matches, and "
          "suppression stays on for the rest of the document: everything after is silently "
          "discarded - the same failure the VOID_ELEMENTS fix (document.py:91-101) closed "
          "for <img>")
    def test_an_unclosed_paragraph_inside_noscript_does_not_eat_the_document(self):
        self.assertEqual(sanitize_content("<noscript><p>Enable JavaScript</noscript>"
                                          "<p>The article.</p>"), "<p>The article.</p>")

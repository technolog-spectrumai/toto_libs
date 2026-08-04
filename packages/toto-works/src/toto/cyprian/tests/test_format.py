"""The document format: round-trips, the version 1 conversion, and what it
refuses to lose."""

from django.test import SimpleTestCase

from toto.cyprian import document_format as df


CONTENT = (
    "<p>Revenue is <strong>up</strong>.</p>"
    "<ul><li>North</li><li>South</li></ul>"
    "<table><tr><th>Region</th><td>+12%</td></tr></table>"
    '<div class="cy-callout" data-tone="warn"><p>Unaudited.</p></div>'
    '<div class="cy-formula" data-latex="E = mc^2">'
    '<code class="cy-formula-source">E = mc^2</code></div>'
    "<pre><code>print(1)</code></pre>"
    '<div class="cy-pagebreak"></div><hr>'
)

V1 = """<?xml version="1.0" encoding="utf-8"?>
<document version="1" title="Quarterly Report" font="serif" page="a4">
  <meta><field name="author"><![CDATA[A. Lovelace]]></field></meta>
  <section id="s-1" level="1" color="accent">
    <heading><![CDATA[Introduction]]></heading>
    <block id="b-1" type="text"><![CDATA[<p>prose here</p>]]></block>
    <block id="b-2" type="list" ordered="false">
      <item><![CDATA[alpha]]></item><item><![CDATA[beta]]></item>
    </block>
    <block id="b-3" type="table" header="true">
      <row><cell><![CDATA[Head]]></cell><cell><![CDATA[Other]]></cell></row>
      <row><cell><![CDATA[one]]></cell><cell><![CDATA[two]]></cell></row>
    </block>
    <block id="b-4" type="quote" cite="Ada"><![CDATA[<p>quoted</p>]]></block>
    <block id="b-5" type="code" language="python"><![CDATA[print(1)]]></block>
    <block id="b-6" type="formula"><source><![CDATA[E = mc^2]]></source></block>
    <block id="b-7" type="callout" tone="tip"><![CDATA[<p>noted</p>]]></block>
    <block id="b-8" type="divider"/>
    <block id="b-9" type="pagebreak"/>
    <block id="b-10" type="image" width="50" align="left"><![CDATA[data:image/png;base64,iVBORw0K]]></block>
  </section>
</document>
"""


V2 = """<?xml version="1.0" encoding="utf-8"?>
<document version="2" title="Two" page="letter" numbering="decimal">
  <section id="s-1" level="1" color="accent">
    <heading><![CDATA[Introduction]]></heading>
    <content><![CDATA[<p>Body one.</p>]]></content>
  </section>
  <section id="s-2" level="2">
    <heading><![CDATA[Detail]]></heading>
    <content><![CDATA[<p>Body two.</p>]]></content>
  </section>
</document>
"""


def _doc() -> df.Document:
    return df.Document(
        title="Quarterly Report", font="serif", margins="wide",
        meta={"author": "A. Lovelace", "subtitle": "FY26"},
        content="<h1>Introduction</h1>" + CONTENT + "<h2>Detail</h2><p>More.</p>",
    )


class DocumentFormatTests(SimpleTestCase):
    # -- round trip ----------------------------------------------------------
    def test_a_document_round_trips(self):
        back = df.loads(df.dumps(_doc()))
        self.assertEqual(back.title, "Quarterly Report")
        self.assertEqual(back.meta["author"], "A. Lovelace")
        self.assertIn("<strong>up</strong>", back.content)
        self.assertEqual([h["text"] for h in back.outline],
                         ["Introduction", "Detail"])

    def test_dumps_is_stable_from_the_second_serialisation(self):
        once = df.dumps(_doc())
        self.assertEqual(df.dumps(df.loads(once)), once)

    def test_the_version_is_three(self):
        self.assertEqual(df.FORMAT_VERSION, "3")
        self.assertIn('version="3"', df.dumps(_doc()))

    def test_paper_size_and_numbering_are_not_written(self):
        # A4 only, and no section numbering: both controls went away, so
        # neither attribute may come back through a round trip.
        again = df.dumps(df.loads(V2))
        self.assertNotIn("page=", again)
        self.assertNotIn("numbering=", again)

    def test_a_cdata_terminator_survives(self):
        # The one sequence a CDATA-wrapped payload cannot contain, split across
        # two sections by _wrap_cdata precisely so it round-trips.
        back = df.loads(df.dumps(df.Document(content="<p>a ]]> b</p>")))
        self.assertIn("]]>", back.content)

    def test_the_body_is_one_content_element(self):
        dumped = df.dumps(_doc())
        self.assertEqual(dumped.count("<content>"), 1)
        self.assertNotIn("<section", dumped)

    # -- version 2 -----------------------------------------------------------
    def test_sections_become_headings_in_the_body(self):
        back = df.loads(V2)
        self.assertEqual(
            back.content,
            "<h1>Introduction</h1><p>Body one.</p>"
            "<h2>Detail</h2><p>Body two.</p>")

    def test_the_outline_comes_from_those_headings(self):
        back = df.loads(V2)
        self.assertEqual([(h["level"], h["text"]) for h in back.outline],
                         [(1, "Introduction"), (2, "Detail")])

    def test_the_contents_page_gets_real_anchors(self):
        # `target-counter()` resolves the page number after layout, which needs
        # something real to point at.
        back = df.loads(V2)
        anchored = back.anchored_content
        for entry in back.outline:
            with self.subTest(entry=entry["text"]):
                self.assertIn(f'id="{entry["id"]}"', anchored)

    # -- version 1 -----------------------------------------------------------
    def test_a_version_one_document_still_opens(self):
        back = df.loads(V1)
        self.assertEqual(back.title, "Quarterly Report")
        self.assertIn("<h1>Introduction</h1>", back.content)

    def test_every_block_type_converts_to_content(self):
        content = df.loads(V1).content
        for fragment in (
            "<p>prose here</p>",                          # text
            "<li>alpha</li>",                             # list
            "<th>Head</th>",                              # table header row
            "<td>one</td>",                               # table body
            "<blockquote>",                               # quote
            'class="cy-cite"',                            # its citation
            '<pre data-language="python"><code>print(1)</code></pre>',
            'class="cy-formula" data-latex="E = mc^2"',   # formula
            'class="cy-callout" data-tone="tip"',         # callout
            "<hr>",                                       # divider
            'class="cy-pagebreak"',                       # page break
            'src="data:image/png;base64,iVBORw0K"',       # image
            'data-width="50"', 'data-align="left"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, content)

    def test_converting_writes_version_three(self):
        again = df.dumps(df.loads(V1))
        self.assertIn('version="3"', again)
        self.assertNotIn("<block", again.split("<content>")[0])
        # And it is stable from there: the conversion happens once.
        self.assertEqual(df.dumps(df.loads(again)), again)

    def test_a_block_type_this_build_cannot_render_is_kept_verbatim(self):
        # It has no HTML to become, and the format promises nothing unknown is
        # discarded. Invisible before, invisible after, bytes intact.
        xml = V1.replace("</section>",
                         '<block id="b-99" type="sparkline" spark="1">'
                         "<![CDATA[1,2,3]]></block></section>")
        back = df.loads(xml)
        self.assertIn('type="sparkline"', "".join(back.extra))
        again = df.dumps(back)
        self.assertIn('type="sparkline"', again)
        self.assertIn("1,2,3", again)

    def test_code_survives_the_conversion_literally(self):
        # v1 kept code unsanitised and escaped it at render. The conversion
        # escapes it once, so the snippet somebody pasted in to SHOW is still
        # there rather than being eaten as markup.
        xml = V1.replace("print(1)", "<script>alert(1)</script>")
        content = df.loads(xml).content
        # `<` escaped, `>` left alone — _esc_text's rule, and the reason a
        # literal `]]>` in a document survives at all.
        self.assertIn("&lt;script>alert(1)&lt;/script>", content)

    # -- clamping ------------------------------------------------------------
    def test_out_of_range_values_fall_back(self):
        document = df.Document(font="comic", margins="huge", content="<p>x</p>")
        back = df.loads(df.dumps(document))
        self.assertEqual(back.font, df.DEFAULT_FONT)
        self.assertEqual(back.margins, df.DEFAULT_MARGINS)

    def test_wrong_root_raises(self):
        with self.assertRaises(df.DocumentParseError):
            df.loads("<presentation></presentation>")

    def test_invalid_xml_raises(self):
        with self.assertRaises(df.DocumentParseError):
            df.loads("<document><oops")

    def test_empty_input_yields_a_usable_document(self):
        document = df.new_document("Fresh")
        self.assertIn("<h1>Fresh</h1>", document.content)
        self.assertIn("<p>", document.content)
        self.assertEqual(df.loads("").content, "<p></p>")

    def test_the_cheap_sniff_agrees_with_the_full_parse(self):
        xml = df.dumps(_doc())
        self.assertTrue(df.sniff_is_document(xml.encode()))
        self.assertTrue(df.is_document(xml))
        self.assertFalse(df.sniff_is_document(b"<presentation/>"))

    # -- sanitisation --------------------------------------------------------
    def test_content_is_sanitised_on_open_not_only_on_save(self):
        # A document is not only ever written by this editor: anyone can upload
        # an XML file to the vault and open it, and a public one renders for
        # other people.
        xml = df.dumps(df.Document(
            content='<p onclick="x()">hi</p><script>alert(1)</script>'))
        content = df.loads(xml).content
        self.assertIn("<p>hi</p>", content)
        self.assertNotIn("onclick", content)
        self.assertNotIn("alert(1)", content)

    def test_a_v2_payload_from_an_older_tab_still_saves(self):
        # An older client posting sections is folded rather than refused.
        document = df.Document.from_dict(
            {"title": "T", "sections": [{"heading": "H", "level": 2,
                                         "content": "<p>body</p>"}]})
        self.assertEqual(document.content, "<h2>H</h2><p>body</p>")

    # -- derived -------------------------------------------------------------
    def test_word_count_and_reading_time(self):
        document = df.Document(content="<h1>Two words</h1><p>three more words</p>")
        self.assertEqual(document.word_count, 5)
        self.assertEqual(document.reading_time_minutes, 1)

    def test_an_empty_heading_is_not_an_outline_entry(self):
        document = df.Document(content="<h1>Kept</h1><h2></h2><p>body</p>")
        self.assertEqual([h["text"] for h in document.outline], ["Kept"])


class UnknownMarkupTests(SimpleTestCase):
    """Nothing unknown is discarded — the format's oldest promise."""

    def test_unknown_attributes_and_elements_round_trip(self):
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<document version="3" title="T" sparkle="yes">\n'
            "  <content><![CDATA[<p>body</p>]]></content>\n"
            "  <appendix>later</appendix>\n"
            "</document>\n"
        )
        again = df.dumps(df.loads(xml))
        for fragment in ('sparkle="yes"', "<appendix>", "later"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, again)


class HandoutDefaultTests(SimpleTestCase):
    """A new document is a one-page handout until it says otherwise."""

    def test_a_new_document_has_no_cover_and_no_contents_page(self):
        doc = df.new_document("Pochodne")
        self.assertFalse(doc.cover)
        self.assertFalse(doc.toc)

    def test_the_cover_flag_round_trips(self):
        doc = df.new_document("R")
        doc.cover = True
        back = df.loads(df.dumps(doc))
        self.assertTrue(back.cover)
        self.assertFalse(df.loads(df.dumps(df.new_document("R"))).cover)

    def test_an_old_file_without_the_attribute_keeps_its_contents_page(self):
        # `toc` defaulted on in the era before the handout tuning; a file that
        # never wrote the attribute must not lose its contents page now.
        xml = ('<?xml version="1.0" encoding="utf-8"?>'
               '<document version="3" title="Old"><content>'
               '<![CDATA[<h1>Old</h1>]]></content></document>')
        old = df.loads(xml)
        self.assertTrue(old.toc)
        self.assertFalse(old.cover)   # but no cover appears out of nowhere

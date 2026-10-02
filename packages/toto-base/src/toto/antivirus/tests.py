"""What the scanners refuse, and what the seam does when the app is off.

The second half matters as much as the first. `toto.antivirus` is optional, so
every call site in toto-base runs on hosts without it — and the failure mode of
getting that wrong is not an error, it is content written unscreened while the
interface says nothing. These tests are how that stays true.
"""

import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from toto.vault.models import Bucket, VaultFile
from toto.vault.scanning import (SCANNABLE_TYPES, Verdict, scan,
                                 scanning_enabled)

from .scanners import json_scan, markup, scanned_types
from .models import RunStatus, ScanResult, ScanRun, ScanVerdict

User = get_user_model()


def _scan_inline(client, pk):
    """POST antivirus:scan_file and return the finished payload.

    No mock: tests have no celery worker, so the POST takes the REAL inline
    fallback — the same path a worker-less deployment takes — and comes back
    finished. A refused POST (403/404/402/429) is returned as-is.
    """
    from django.urls import reverse as _reverse

    posted = client.post(_reverse("antivirus:scan_file", args=[pk]))
    if posted.status_code != 200 or posted.json().get("finished"):
        return posted
    return client.get(
        _reverse("antivirus:scan_status", args=[posted.json()["run_id"]]))


class MarkupScannerTests(SimpleTestCase):
    """The rules, one test per way in."""

    def test_a_plain_drawing_passes(self):
        self.assertTrue(markup.scan_svg('<svg><rect x="1" y="1"/></svg>').ok)

    def test_script_is_refused(self):
        verdict = markup.scan_svg('<svg><script>alert(1)</script></svg>')
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "active-content")
        self.assertIn("script", verdict.detail)

    def test_a_self_closing_script_cannot_slip_past(self):
        """handle_startendtag, not just handle_starttag."""
        self.assertFalse(markup.scan_svg('<svg><script/></svg>').ok)

    def test_every_event_handler_is_refused(self):
        for attr in ("onload", "onclick", "onerror", "onmouseover"):
            with self.subTest(attr=attr):
                verdict = markup.scan_svg(f'<svg {attr}="x()"><rect/></svg>')
                self.assertFalse(verdict.ok)
                self.assertEqual(verdict.reason, "active-content")

    def test_a_script_url_is_refused_in_any_format(self):
        for scanner in (markup.scan_svg, markup.scan_html):
            with self.subTest(scanner=scanner.__name__):
                self.assertFalse(scanner('<a href="javascript:alert(1)">x</a>').ok)

    def test_a_script_url_with_noise_in_its_scheme_is_refused(self):
        # Stage 51: a browser strips tab and newline from a URL before it reads
        # the scheme; the parser hands the check the decoded characters.
        for href in ("java&#x09;script:alert(1)", "java&#x0A;script:alert(1)",
                     "java&#13;script:x", "&#x01;javascript:x", "vb&#9;script:x",
                     "java\tscript:x"):
            for scanner in (markup.scan_svg, markup.scan_html):
                with self.subTest(href=href, scanner=scanner.__name__):
                    verdict = scanner(f'<svg><a href="{href}">x</a></svg>'
                                      if scanner is markup.scan_svg
                                      else f'<a href="{href}">x</a>')
                    self.assertFalse(verdict.ok)
                    self.assertEqual(verdict.reason, "active-content")

    def test_doctype_is_refused_before_parsing(self):
        """Billion laughs: a parser that already expanded entities has lost."""
        verdict = markup.scan_xml(
            '<?xml version="1.0"?>\n<!DOCTYPE lolz [<!ENTITY lol "lol">]>\n<x/>')
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "declaration")
        self.assertEqual(verdict.line, 2)

    def test_cdata_is_refused(self):
        """html.parser does not look inside it; a browser's DOMParser does."""
        self.assertFalse(markup.scan_svg('<svg><![CDATA[<script>x</script>]]></svg>').ok)

    def test_svg_refuses_an_external_reference(self):
        for markup_text in ('<svg><image href="https://evil/x.png"/></svg>',
                            '<svg><use xlink:href="//host/x#y"/></svg>',
                            '<svg><rect style="fill:url(https://evil/x)"/></svg>'):
            with self.subTest(markup_text=markup_text):
                self.assertFalse(markup.scan_svg(markup_text).ok)

    def test_svg_allows_its_own_embedded_picture(self):
        self.assertTrue(markup.scan_svg(
            '<svg><image href="data:image/png;base64,AAAA"/></svg>').ok)

    def test_svg_allows_a_fragment_reference(self):
        self.assertTrue(markup.scan_svg('<svg><use href="#shape"/></svg>').ok)

    def test_html_keeps_ordinary_hyperlinks(self):
        """The rule that must NOT be shared with SVG.

        Refusing external hrefs in HTML would refuse every normal page — the
        formats are rendered differently and so are judged differently.
        """
        verdict = markup.scan_html(
            '<html><body><a href="https://example.com">read this</a>'
            '<img src="https://example.com/logo.png"></body></html>')
        self.assertTrue(verdict.ok, verdict.detail)

    def test_html_still_refuses_the_dangerous_things(self):
        for markup_text in ('<html><script>x</script></html>',
                            '<html><iframe src="https://evil"></iframe></html>',
                            '<html><body onload="x()"></body></html>',
                            '<html><object data="x.swf"></object></html>'):
            with self.subTest(markup_text=markup_text):
                self.assertFalse(markup.scan_html(markup_text).ok)

    def test_svg_must_be_an_svg(self):
        verdict = markup.scan_svg('<html><body>not a drawing</body></html>')
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "wrong-shape")

    def test_empty_is_refused_rather_than_waved_through(self):
        for text in ("", "   ", None):
            with self.subTest(text=text):
                self.assertFalse(markup.scan_svg(text).ok)

    def test_the_refusal_names_a_line(self):
        verdict = markup.scan_html("<html>\n<body>\n<script>x</script>\n</body>")
        self.assertEqual(verdict.line, 3)

    def test_nothing_is_ever_rewritten(self):
        """A scanner returns a verdict, never content. This is the contract."""
        verdict = markup.scan_svg('<svg><rect/></svg>')
        self.assertIsInstance(verdict, Verdict)
        self.assertFalse(hasattr(verdict, "content"))


class JsonScannerTests(SimpleTestCase):
    def test_ordinary_json_passes(self):
        self.assertTrue(json_scan.scan_json('{"a": [1, 2, {"b": "c"}]}').ok)

    def test_malformed_json_is_refused(self):
        verdict = json_scan.scan_json('{"a": ')
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "malformed")

    def test_deep_nesting_is_refused_before_it_is_parsed(self):
        """Python's json recurses; a few kB of brackets is a stack overflow."""
        verdict = json_scan.scan_json("[" * 500 + "]" * 500)
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "too-deep")

    def test_brackets_inside_a_string_do_not_count_as_nesting(self):
        self.assertTrue(json_scan.scan_json('{"a": "[[[[[[[[[["}').ok)

    def test_empty_is_refused(self):
        self.assertFalse(json_scan.scan_json("").ok)


#: A minimal but REAL deck: presentation_format.dumps CDATA-wraps every block,
#: which is the whole reason decks cannot ride on the xml scanner.
DECK = """<?xml version="1.0"?>
<presentation version="2" title="Q" theme="black" font="sans">
  <slide id="s-1" layout="title-content">
    <title>t</title>
    <block id="b-1" type="text"><![CDATA[<p>hello</p>]]></block>
  </slide>
</presentation>
"""


class RegistryTests(SimpleTestCase):
    def test_the_built_in_scanners_registered(self):
        self.assertEqual(set(scanned_types()),
                         {"svg", "html", "xml", "json", "pdf", "pxml"})

    def test_a_deck_is_screened_by_its_own_scanner(self):
        # Not by scan_xml. A deck CDATA-wraps every block payload and the xml
        # scanner refuses CDATA outright, so routing decks there refused every
        # deck ever written — unread, for its envelope rather than its contents.
        from .scanners import scanner_for
        from .scanners.markup import scan_pxml, scan_xml

        self.assertIs(scanner_for("pxml"), scan_pxml)
        self.assertIsNot(scanner_for("pxml"), scan_xml)
        self.assertFalse(scan_xml(DECK).ok)
        self.assertTrue(scan_pxml(DECK).ok)

    def test_a_hostile_slide_payload_is_still_refused(self):
        from .scanners.markup import scan_pxml

        for bad in ("<script>alert(1)</script>",
                    "<b onclick=\"evil()\">x</b>",
                    "<iframe src=/></iframe>",
                    "<a href=\"javascript:evil()\">x</a>"):
            with self.subTest(payload=bad):
                self.assertFalse(scan_pxml(DECK.replace("<p>hello</p>", bad)).ok)

    def test_a_payload_is_screened_however_it_was_spelled(self):
        """The bypass this scanner shipped with, pinned.

        The first version matched `<![CDATA[...]]>` and screened what was inside
        it. But CDATA is only ONE spelling of character data: the same script
        written `&lt;script&gt;` carries no CDATA at all, so nothing screened
        it, while the skeleton scan saw inert entity references and passed the
        file. XML then decodes those references straight back into a live
        <script> for the template, which renders block payloads with |safe.

        Every spelling below decodes to the identical bytes, so every one of
        them must be refused identically.
        """
        from .scanners.markup import scan_pxml

        spellings = {
            "cdata": "<![CDATA[<script>alert(1)</script>]]>",
            "entity": "&lt;script&gt;alert(1)&lt;/script&gt;",
            "numeric charref": "&#60;script&#62;alert(1)&#60;/script&#62;",
            "entity onerror": "&lt;img src=x onerror=evil()&gt;",
            "entity iframe": "&lt;iframe src=/&gt;&lt;/iframe&gt;",
            "entity script url": '&lt;a href="javascript:evil()"&gt;x&lt;/a&gt;',
        }
        for name, payload in spellings.items():
            with self.subTest(spelling=name):
                self.assertFalse(
                    scan_pxml(DECK.replace("<![CDATA[<p>hello</p>]]>", payload)).ok,
                    f"{name} got past the scanner")

    def test_ordinary_prose_is_not_mistaken_for_markup(self):
        # The screening must not refuse a deck for containing "<" in a sentence.
        from .scanners.markup import scan_pxml

        self.assertTrue(scan_pxml(DECK.replace("<p>hello</p>", "5 &lt; 6")).ok)
        self.assertTrue(scan_pxml(DECK.replace("<title>t</title>",
                                               "<title>Q1 &amp; Q2</title>")).ok)

    def test_a_deck_that_is_not_well_formed_xml_is_refused(self):
        # The parse is what makes the two spellings one thing, so a file the
        # parser cannot read cannot be screened and must not be stored.
        from .scanners.markup import scan_pxml

        self.assertFalse(scan_pxml(DECK.replace("</presentation>", "")).ok)
        self.assertFalse(scan_pxml(DECK.replace("<![CDATA[", "")).ok)

    def test_the_deck_around_the_payloads_is_screened_too(self):
        # A second CDATA smuggled into the skeleton still meets the blanket
        # refusal, and a file that is not a deck is not accepted as one.
        from .scanners.markup import scan_pxml

        self.assertFalse(scan_pxml(
            DECK.replace("<title>t</title>", "<title>t</title><script>x()</script>")).ok)
        self.assertFalse(scan_pxml(DECK.replace("presentation", "notadeck")).ok)
        self.assertFalse(scan_pxml(
            DECK.replace('<?xml version="1.0"?>', '<!DOCTYPE p [<!ENTITY x "y">]>')).ok)

    def test_two_scanners_cannot_claim_one_type(self):
        from .scanners import DuplicateScanner, register

        with self.assertRaises(DuplicateScanner):
            register("svg", lambda text: Verdict.clean())


class SeamTests(TestCase):
    """The façade, with the app installed."""

    def test_it_scans_a_type_it_knows(self):
        verdict = scan("<svg><script>x</script></svg>", file_type="svg")
        self.assertFalse(verdict.ok)
        self.assertTrue(verdict.scanned)

    def test_a_type_nothing_screens_is_clean_but_unscanned(self):
        """The distinction the whole marking rule rests on."""
        verdict = scan(b"\x89PNG", file_type="image")
        self.assertTrue(verdict.ok)
        self.assertFalse(verdict.scanned)

    def test_bytes_that_are_not_text_are_refused_not_waved_through(self):
        verdict = scan(b"\xff\xfe\x00binary", file_type="svg")
        self.assertFalse(verdict.ok)

    def test_a_crashing_scanner_does_not_break_the_save(self):
        """It must also not claim the content was screened."""
        from unittest import mock

        with mock.patch("toto.antivirus.engine.scan",
                        side_effect=RuntimeError("boom")):
            verdict = scan("<svg/>", file_type="svg")

        self.assertTrue(verdict.ok)
        self.assertFalse(verdict.scanned)

    def test_scanning_is_enabled_here(self):
        self.assertTrue(scanning_enabled())


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="antivirus-test-"))
class ScanViewTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        self.user = User.objects.create_user("owner", password="pw")
        self.stranger = User.objects.create_user("stranger", password="pw")
        self.bucket = Bucket.objects.create(name="Mine", slug="mine",
                                            owner=self.user)
        self.client.force_login(self.user)

    def _file(self, body, *, file_type="svg", title="drawing.svg", owner=None):
        vault_file = VaultFile(owner=owner or self.user, title=title,
                               file_type=file_type, bucket=self.bucket)
        vault_file.file.save(title, ContentFile(body.encode()), save=False)
        vault_file.save()
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save(update_fields=["content_hash"])
        return vault_file

    def test_the_page_renders_with_both_counters(self):
        response = self.client.get(reverse("antivirus:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Threats found")
        self.assertContains(response, "Files scanned")

    def test_scanning_a_clean_file_records_a_clean_verdict(self):
        vault_file = self._file("<svg><rect/></svg>")

        response = _scan_inline(self.client, vault_file.pk)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["clean"])
        self.assertEqual(vault_file.scan_results.get().verdict, "clean")

    def test_scanning_a_hostile_file_flags_it_and_changes_nothing(self):
        """Flag only. The bytes are untouched and the file still opens."""
        body = '<svg><script>alert(1)</script></svg>'
        vault_file = self._file(body)

        response = _scan_inline(self.client, vault_file.pk)

        self.assertFalse(response.json()["clean"])
        self.assertEqual(vault_file.scan_results.get().verdict, "refused")
        with vault_file.file.open("rb") as handle:
            self.assertEqual(handle.read().decode(), body)

    def test_a_stranger_cannot_scan_your_file(self):
        vault_file = self._file("<svg/>")
        self.client.force_login(self.stranger)
        self.assertEqual(
            _scan_inline(self.client, vault_file.pk).status_code,
            404)

    def test_the_tick_shows_only_for_current_clean_bytes(self):
        from toto.vault.scanning import clean_file_ids

        vault_file = self._file("<svg><rect/></svg>")
        self.assertEqual(clean_file_ids([vault_file]), set())

        _scan_inline(self.client, vault_file.pk)
        vault_file.refresh_from_db()
        self.assertEqual(clean_file_ids([vault_file]), {vault_file.pk})

        # Edit it: the verdict was about the old bytes, so the tick goes.
        vault_file.content_hash = "0" * 64
        vault_file.save(update_fields=["content_hash"])
        self.assertEqual(clean_file_ids([vault_file]), set())

    def test_a_refused_file_never_gets_a_tick(self):
        from toto.vault.scanning import clean_file_ids

        vault_file = self._file('<svg onload="x()"/>')
        _scan_inline(self.client, vault_file.pk)
        vault_file.refresh_from_db()
        self.assertEqual(clean_file_ids([vault_file]), set())

    def test_preferences_are_per_user_and_narrow_only_their_own(self):
        from .models import ScanPreference

        self.client.post(reverse("antivirus:set_preference"),
                         {"types": ["svg", "html"]})

        self.assertEqual(set(ScanPreference.types_for(self.user)), {"svg", "html"})
        # Untouched for everyone else.
        self.assertEqual(set(ScanPreference.types_for(self.stranger)),
                         {"svg", "html", "xml", "json", "pdf", "pxml"})

    def test_an_unknown_type_cannot_be_smuggled_into_preferences(self):
        from .models import ScanPreference

        self.client.post(reverse("antivirus:set_preference"),
                         {"types": ["svg", "image", "../etc"]})
        self.assertEqual(list(ScanPreference.types_for(self.user)), ["svg"])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="antivirus-doors-"))
class DoorTests(TestCase):
    """One test per entrance content can arrive through.

    The assertion that matters at every one of them is the same: **the hostile
    bytes are not on disk afterwards.** A door that reports a refusal and writes
    anyway has done nothing, and the only way to know which kind it is, is to
    read the file back.
    """

    HOSTILE = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    CLEAN = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="4" height="4"/></svg>'

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        self.user = User.objects.create_user("owner", password="pw")
        self.bucket = Bucket.objects.create(name="Mine", slug="mine",
                                            owner=self.user)
        self.client.force_login(self.user)

    def _file(self, body=None, *, file_type="svg", title="drawing.svg"):
        vault_file = VaultFile(owner=self.user, title=title, file_type=file_type,
                               bucket=self.bucket)
        vault_file.file.save(title, ContentFile((body or self.CLEAN).encode()),
                             save=False)
        vault_file.save()
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save(update_fields=["content_hash"])
        return vault_file

    def _body_of(self, vault_file):
        vault_file.refresh_from_db()
        with vault_file.file.open("rb") as handle:
            return handle.read().decode()

    # -- editor.save_file ---------------------------------------------------

    def test_the_editor_save_refuses_and_writes_nothing(self):
        vault_file = self._file()

        response = self.client.post(
            reverse("editor:xml_save", args=[vault_file.pk]),
            {"content": self.HOSTILE})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["reason"], "active-content")
        self.assertEqual(self._body_of(vault_file), self.CLEAN)

    def test_the_editor_save_still_saves_clean_content(self):
        vault_file = self._file()
        new = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="2"/></svg>'

        response = self.client.post(
            reverse("editor:xml_save", args=[vault_file.pk]), {"content": new})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._body_of(vault_file), new)

    def test_the_editor_save_updates_the_hash_it_used_to_leave_stale(self):
        """Without this the tick would describe bytes the file no longer holds."""
        import hashlib

        vault_file = self._file()
        new = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="9"/></svg>'

        self.client.post(reverse("editor:xml_save", args=[vault_file.pk]),
                         {"content": new})

        vault_file.refresh_from_db()
        self.assertEqual(vault_file.content_hash,
                         hashlib.sha256(new.encode()).hexdigest())
        self.assertEqual(vault_file.file_size_bytes, len(new.encode()))

    def test_a_saved_clean_file_earns_its_tick(self):
        from toto.vault.scanning import clean_file_ids

        vault_file = self._file()
        self.client.post(reverse("editor:xml_save", args=[vault_file.pk]),
                         {"content": self.CLEAN})

        vault_file.refresh_from_db()
        self.assertEqual(clean_file_ids([vault_file]), {vault_file.pk})

    def test_an_unscannable_type_saves_exactly_as_before(self):
        """The silence half of the rule: no scan, no tick, no obstruction."""
        from toto.vault.scanning import clean_file_ids

        vault_file = self._file("hello", file_type="text", title="notes.txt")

        response = self.client.post(
            reverse("editor:text_save", args=[vault_file.pk]),
            {"content": "<script>alert(1)</script>"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._body_of(vault_file), "<script>alert(1)</script>")
        vault_file.refresh_from_db()
        self.assertEqual(clean_file_ids([vault_file]), set())

    # -- the API twin -------------------------------------------------------

    def test_the_api_save_refuses_and_writes_nothing(self):
        vault_file = self._file()

        response = self.client.put(
            reverse("vault:api_file_content", args=[vault_file.key]),
            data=json.dumps({"content": self.HOSTILE}),
            content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._body_of(vault_file), self.CLEAN)

    # -- uploads ------------------------------------------------------------

    def test_an_upload_of_a_hostile_file_creates_no_row(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        before = VaultFile.objects.count()
        response = self.client.post(
            reverse("vault:api_file_upload"),
            {"file": SimpleUploadedFile("bad.svg", self.HOSTILE.encode(),
                                        content_type="image/svg+xml"),
             "bucket_slug": self.bucket.slug})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(VaultFile.objects.count(), before)

    def test_a_clean_upload_still_lands_and_is_ticked(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from toto.vault.scanning import clean_file_ids

        response = self.client.post(
            reverse("vault:api_file_upload"),
            {"file": SimpleUploadedFile("good.svg", self.CLEAN.encode(),
                                        content_type="image/svg+xml"),
             "bucket_slug": self.bucket.slug})

        self.assertEqual(response.status_code, 201, response.content)
        stored = VaultFile.objects.get(bucket=self.bucket, title="good")
        self.assertEqual(clean_file_ids([stored]), {stored.pk})

    def test_one_bad_file_in_a_gateway_batch_does_not_lose_the_others(self):
        """The batch rule the size and quota checks already follow."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from toto.vault.models import FileGateway, VaultDirectory

        directory = VaultDirectory.objects.create(name="drop", bucket=self.bucket,
                                                 owner=self.user)
        FileGateway.objects.create(directory=directory, bucket=self.bucket,
                                   max_file_size=10240)

        response = self.client.post(
            reverse("vault:gateway_upload", args=[directory.pk]),
            {"file": [
                SimpleUploadedFile("bad.svg", self.HOSTILE.encode(),
                                   content_type="image/svg+xml"),
                SimpleUploadedFile("good.svg", self.CLEAN.encode(),
                                   content_type="image/svg+xml"),
            ]})

        payload = response.json()
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["title"], "good.svg")
        self.assertEqual(len(payload["errors"]), 1)
        self.assertIn("bad.svg", payload["errors"][0])
        self.assertFalse(VaultFile.objects.filter(title="bad.svg").exists())

    # -- version restore ----------------------------------------------------

    def test_restoring_an_old_body_re_screens_it(self):
        """"We stored it once" is not a verdict — rules change under a file."""
        from toto.vault import versions

        vault_file = self._file()
        old = versions.save_version(vault_file, body=self.HOSTILE.encode(),
                                    author=self.user)

        response = self.client.post(
            reverse("vault:version_restore", args=[vault_file.pk, old.pk]))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["reason"], "active-content")
        self.assertEqual(self._body_of(vault_file), self.CLEAN)


class SocketDoorTests(TestCase):
    """The websocket, where the patch path hides the final bytes.

    Driven through `write_file` rather than a live socket: that method IS the
    write door — `receive` only routes to it — and a channels test harness would
    add a layer without testing more of the thing in question.
    """

    HOSTILE = '<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
    CLEAN = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="antivirus-socket-"))
    def test_the_socket_write_refuses_without_touching_the_file(self):
        from asgiref.sync import async_to_sync

        from toto.core.models import Platform
        from toto.editor.consumer import EditorFileSyncConsumer

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        user = User.objects.create_user("socket-owner", password="pw")
        bucket = Bucket.objects.create(name="S", slug="s", owner=user)
        vault_file = VaultFile(owner=user, title="d.svg", file_type="svg",
                               bucket=bucket)
        vault_file.file.save("d.svg", ContentFile(self.CLEAN.encode()), save=False)
        vault_file.save()

        consumer = EditorFileSyncConsumer()
        consumer.file_pk = vault_file.pk
        consumer.user = user

        # `write_file` answers (verdict, content_hash) since 8/2026: the socket
        # reports the digest it stamped so the client's save can carry it as a
        # precondition instead of hashing the buffer itself. A refusal stamps
        # nothing, so the hash is blank.
        verdict, content_hash = async_to_sync(consumer.write_file)(self.HOSTILE)

        self.assertFalse(verdict.ok)
        self.assertEqual(content_hash, "")
        vault_file.refresh_from_db()
        with vault_file.file.open("rb") as handle:
            self.assertEqual(handle.read().decode(), self.CLEAN)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="antivirus-listing-"))
class ListingTickTests(TestCase):
    """The mark, and the silence next to it.

    "Do not spread panic" is the requirement, so it is asserted directly: an
    unscanned row must carry no marker of any kind, not a quieter one.
    """

    CLEAN = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        self.user = User.objects.create_user("lister", password="pw")
        self.bucket = Bucket.objects.create(name="Mine", slug="mine",
                                            owner=self.user)
        self.client.force_login(self.user)

    def _file(self, title="drawing.svg"):
        vault_file = VaultFile(owner=self.user, title=title, file_type="svg",
                               bucket=self.bucket)
        vault_file.file.save(title, ContentFile(self.CLEAN.encode()), save=False)
        vault_file.save()
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save(update_fields=["content_hash"])
        return vault_file

    def _items(self, response):
        return {item["title"]: item
                for item in response.context["flat_items"] if item["t"] == "file"}

    def test_an_unscanned_file_carries_no_mark_at_all(self):
        self._file()
        items = self._items(self.client.get(reverse("vault:public_list")))
        self.assertFalse(items["drawing.svg"]["scan_ok"])

    def test_a_scanned_clean_file_is_marked(self):
        vault_file = self._file()
        _scan_inline(self.client, vault_file.pk)

        items = self._items(self.client.get(reverse("vault:public_list")))
        self.assertTrue(items["drawing.svg"]["scan_ok"])

    def test_editing_through_a_screened_door_keeps_the_mark(self):
        """Because the door re-scanned the new bytes on the way in.

        The tick tracks the hash, and the editor now updates the hash *and*
        records a verdict against it — so an edit does not flicker the mark off
        and leave the user wondering what they broke. Contrast the next test:
        bytes that change without passing a door lose it immediately.
        """
        vault_file = self._file()
        _scan_inline(self.client, vault_file.pk)

        self.client.post(reverse("editor:xml_save", args=[vault_file.pk]),
                         {"content": '<svg xmlns="http://www.w3.org/2000/svg"><circle/></svg>'})

        items = self._items(self.client.get(reverse("vault:public_list")))
        self.assertTrue(items["drawing.svg"]["scan_ok"])

    def test_bytes_that_changed_outside_a_door_lose_the_mark(self):
        """No verdict exists for the hash the file now carries."""
        vault_file = self._file()
        _scan_inline(self.client, vault_file.pk)

        VaultFile.objects.filter(pk=vault_file.pk).update(content_hash="0" * 64)

        items = self._items(self.client.get(reverse("vault:public_list")))
        self.assertFalse(items["drawing.svg"]["scan_ok"])

    def test_the_whole_listing_is_one_extra_query(self):
        from toto.antivirus.models import ScanResult

        for n in range(6):
            vault_file = self._file(f"d{n}.svg")
            _scan_inline(self.client, vault_file.pk)
        self.assertEqual(ScanResult.objects.count(), 6)

        from toto.vault.scanning import clean_file_ids

        files = list(VaultFile.objects.filter(owner=self.user))
        with self.assertNumQueries(1):
            self.assertEqual(len(clean_file_ids(files)), 6)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="antivirus-panel-"))
class ScanPanelTests(TestCase):
    """The panel lists exactly what the button can scan.

    The bug this pins: the tree was built from ``accessible_files`` while the
    endpoint accepted ``_my_files``, so encrypted files were listed with a Scan
    button that 404s — and the client rendered that HTTP failure through the
    ``!clean`` branch, so a file nothing had read reported itself as "Refused".
    """

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        self.user = User.objects.create_user("panel", password="pw")
        self.other = User.objects.create_user("other", password="pw")
        self.bucket = Bucket.objects.create(name="Mine", slug="panel-mine",
                                            owner=self.user)
        self.their_bucket = Bucket.objects.create(name="Theirs",
                                                  slug="panel-theirs",
                                                  owner=self.other)
        self.client.force_login(self.user)

    def _file(self, *, owner=None, bucket=None, title="a.svg",
              public=False, encrypted=False):
        vault_file = VaultFile(owner=owner or self.user, title=title,
                               file_type="svg", bucket=bucket or self.bucket,
                               is_public=public, is_encrypted=encrypted)
        vault_file.file.save(title, ContentFile(b"<svg><rect/></svg>"),
                             save=False)
        vault_file.save()
        return vault_file

    def _listed(self):
        import re

        body = self.client.get(reverse("antivirus:index")).content.decode()
        # askScan, not scan: the button opens the confirmation modal now, and
        # the actual POST happens from the modal's checked set.
        return {int(pk) for pk in re.findall(r'@click="askScan\((\d+)\)"', body)}

    def test_every_listed_file_can_actually_be_scanned(self):
        """The invariant. A row with a button the endpoint refuses is a dead
        control, and nothing on the page could explain it."""
        self._file(title="ok.svg")
        self._file(title="enc.svg", encrypted=True)
        self._file(owner=self.other, bucket=self.their_bucket,
                   title="theirs.svg", public=True)

        for pk in self._listed():
            with self.subTest(pk=pk):
                response = _scan_inline(self.client, pk)
                self.assertEqual(response.status_code, 200)

    def test_an_encrypted_file_is_listed_but_disabled(self):
        """The tree shows everything readable now; what changed is that a row
        the endpoint refuses carries NO action, not that it is hidden."""
        encrypted = self._file(title="enc.svg", encrypted=True)

        self.assertNotIn(encrypted.pk, self._listed())
        body = self.client.get(reverse("antivirus:index")).content.decode()
        self.assertIn("enc.svg", body)

    def test_a_strangers_public_file_is_visible_but_not_scannable(self):
        """Readable is not mine. Letting anyone queue work against anyone's
        files is how a scan button becomes an amplifier — the row is shown
        (it IS readable) but carries no scan control."""
        theirs = self._file(owner=self.other, bucket=self.their_bucket,
                            title="theirs.svg", public=True)

        self.assertNotIn(theirs.pk, self._listed())
        self.assertEqual(
            _scan_inline(self.client, theirs.pk).status_code,
            404)

    def test_a_file_in_my_bucket_owned_by_someone_else_is_still_mine(self):
        """Dropping the public arm must not drop the bucket claim with it."""
        theirs_here = self._file(owner=self.other, title="guest.svg")

        self.assertIn(theirs_here.pk, self._listed())

    def test_my_own_file_is_listed(self):
        mine = self._file(title="mine.svg")

        self.assertIn(mine.pk, self._listed())

    def test_the_client_builds_the_url_from_the_route(self):
        """A hardcoded "/antivirus/…" works until the app is mounted elsewhere,
        and then fails silently."""
        body = self.client.get(reverse("antivirus:index")).content.decode()

        self.assertIn(reverse("antivirus:scan_file", args=[0]), body)

    def test_a_failed_request_is_not_rendered_as_a_verdict(self):
        """`ok === false` has to be its own branch. Without it a 404 falls
        through to `!clean` and the row says the file was refused."""
        import pathlib

        row = (pathlib.Path(__file__).parent / "templates" / "antivirus"
               / "_scan_row.html").read_text()

        self.assertIn("ok === false", row)
        self.assertIn("ok !== false", row)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="antivirus-tabs-"))
class _TabsFixture(TestCase):
    """Shared fixture for the three-tab surfaces."""

    HOSTILE = '<svg><script>alert(1)</script></svg>'
    CLEAN = "<svg><rect/></svg>"

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        self.user = User.objects.create_user("tabs", password="pw")
        self.staff = User.objects.create_user("tabs-staff", password="pw",
                                              is_staff=True)
        self.other = User.objects.create_user("tabs-other", password="pw")
        self.bucket = Bucket.objects.create(name="Tabs", slug="tabs",
                                            owner=self.user)
        self.their_bucket = Bucket.objects.create(name="TabsTheirs",
                                                  slug="tabs-theirs",
                                                  owner=self.other)
        self.client.force_login(self.user)

    def _file(self, body=CLEAN, *, title="a.svg", file_type="svg", owner=None,
              bucket=None, public=False, encrypted=False):
        vault_file = VaultFile(owner=owner or self.user, title=title,
                               file_type=file_type,
                               bucket=bucket or self.bucket,
                               is_public=public, is_encrypted=encrypted)
        vault_file.file.save(title, ContentFile(body.encode()), save=False)
        vault_file.save()
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save(update_fields=["content_hash"])
        return vault_file

    def _scan(self, vault_file):
        return _scan_inline(self.client, vault_file.pk)


class TabsTests(_TabsFixture):
    def test_all_three_tabs_render_with_the_nav(self):
        for name in ("antivirus:index", "antivirus:statistics",
                     "antivirus:pathology"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Pathology")
                self.assertContains(response, "Statistics")


class FilesTreeStatusTests(_TabsFixture):
    """The tree is the vault's, read-only, in security terms."""

    def _body(self):
        return self.client.get(reverse("antivirus:index")).content.decode()

    def test_an_unscanned_file_warns(self):
        self._file(title="fresh.svg")

        self.assertIn("Not scanned yet", self._body())

    def test_a_clean_scan_earns_the_green_check(self):
        vault_file = self._file(title="good.svg")
        self._scan(vault_file)

        self.assertIn("Scanned — nothing found", self._body())

    def test_a_threat_warns_by_name(self):
        vault_file = self._file(self.HOSTILE, title="bad.svg")
        self._scan(vault_file)

        self.assertIn("Threat found", self._body())

    def test_editing_a_clean_file_takes_the_check_away(self):
        """The icon is about the bytes the file holds NOW."""
        vault_file = self._file(title="edited.svg")
        self._scan(vault_file)
        VaultFile.objects.filter(pk=vault_file.pk).update(
            content_hash="different-bytes-now")

        body = self._body()
        self.assertNotIn("Scanned — nothing found", body)
        self.assertIn("Not scanned yet", body)

    def test_disabled_rows_say_why(self):
        self._file(title="locked.svg", encrypted=True)
        self._file(title="photo.png", file_type="png")
        self._file(title="pub.svg", owner=self.other,
                   bucket=self.their_bucket, public=True)

        body = self._body()
        self.assertIn("Encrypted — the scanner cannot read it.", body)
        self.assertIn("This type cannot be scanned.", body)
        self.assertIn("Not yours to scan.", body)

    def test_no_management_controls_anywhere(self):
        """Read-only in the strong sense: the page offers scanning and nothing
        the vault already owns."""
        self._file(title="a.svg")

        body = self._body()
        for forbidden in ("Upload", "New File", "New file"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_the_modal_ships_a_picking_tree(self):
        self._file(title="pick.svg")

        body = self._body()
        self.assertIn("checked[", body)
        self.assertIn("confirmScan", body)


class ErrorRecordingTests(_TabsFixture):
    def test_an_unreadable_file_is_recorded_not_forgotten(self):
        """Before ERROR existed this was a 400 and nothing else — a file that
        cannot be checked looked exactly like one nobody had tried."""
        import os

        vault_file = self._file(title="ghost.svg")
        os.remove(vault_file.file.path)

        response = self._scan(vault_file)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ok"])
        row = ScanResult.objects.get(file=vault_file)
        self.assertEqual(row.verdict, ScanVerdict.ERROR)

    def test_the_failure_shows_in_pathology_as_failed(self):
        import os

        vault_file = self._file(title="ghost2.svg")
        os.remove(vault_file.file.path)
        self._scan(vault_file)

        response = self.client.get(reverse("antivirus:pathology"))

        self.assertContains(response, "ghost2.svg")
        self.assertContains(response, "Failed to scan")


class StatisticsTests(_TabsFixture):
    def test_the_numbers_add_up(self):
        clean = self._file(title="s1.svg")
        hostile = self._file(self.HOSTILE, title="s2.svg")
        self._scan(clean)
        self._scan(hostile)

        context = self.client.get(reverse("antivirus:statistics")).context

        self.assertEqual(context["files_scanned"], 2)
        self.assertEqual(context["scans_run"], 2)
        self.assertEqual(context["clean_count"], 1)
        self.assertEqual(context["threat_count"], 1)
        self.assertGreater(context["data_scanned"], 0)

    def test_two_scans_one_day_is_one_bucket(self):
        """The Meta-ordering GROUP BY trap, as a regression test: without the
        trailing order_by the chart gets one row per SCAN."""
        import json as jsonlib

        self._scan(self._file(title="d1.svg"))
        self._scan(self._file(title="d2.svg"))

        payload = jsonlib.loads(
            self.client.get(reverse("antivirus:statistics"))
            .context["activity_chart_json"])

        self.assertEqual(len(payload["labels"]), 30)
        clean_series = next(d for d in payload["datasets"]
                            if d["label"] == "Clean")
        self.assertEqual(sum(clean_series["data"]), 2)
        self.assertEqual(clean_series["data"][-1], 2)

    def test_a_user_sees_their_files_and_staff_see_everything(self):
        theirs = self._file(title="theirs.svg", owner=self.other,
                            bucket=self.their_bucket)
        from . import engine
        from toto.vault.scanning import scan as facade_scan

        engine.record(theirs, facade_scan(self.CLEAN, file_type="svg"),
                      user=self.other, door="manual", content=self.CLEAN)

        mine = self.client.get(reverse("antivirus:statistics")).context
        self.assertEqual(mine["scans_run"], 0)
        self.assertFalse(mine["staff_view"])

        self.client.force_login(self.staff)
        alls = self.client.get(reverse("antivirus:statistics")).context
        self.assertEqual(alls["scans_run"], 1)
        self.assertTrue(alls["staff_view"])


class PathologyTests(_TabsFixture):
    def test_a_finding_carries_its_severity_and_currency(self):
        vault_file = self._file(self.HOSTILE, title="p1.svg")
        self._scan(vault_file)

        response = self.client.get(reverse("antivirus:pathology"))

        self.assertContains(response, "p1.svg")
        self.assertContains(response, "Threat")
        self.assertContains(response, "Still present")

    def test_a_fixed_file_reads_as_historic_not_infected(self):
        vault_file = self._file(self.HOSTILE, title="p2.svg")
        self._scan(vault_file)
        VaultFile.objects.filter(pk=vault_file.pk).update(
            content_hash="rewritten-clean")

        response = self.client.get(reverse("antivirus:pathology"))

        self.assertContains(response, "Bytes changed since")
        self.assertNotContains(response, "Still present")

    def test_a_strangers_finding_is_invisible_until_you_are_staff(self):
        theirs = self._file(self.HOSTILE, title="secret-bad.svg",
                            owner=self.other, bucket=self.their_bucket)
        from . import engine
        from toto.vault.scanning import scan as facade_scan

        engine.record(theirs, facade_scan(self.HOSTILE, file_type="svg"),
                      user=self.other, door="manual", content=self.HOSTILE)

        self.assertNotContains(
            self.client.get(reverse("antivirus:pathology")), "secret-bad.svg")

        self.client.force_login(self.staff)
        self.assertContains(
            self.client.get(reverse("antivirus:pathology")), "secret-bad.svg")


class HealthReportTests(_TabsFixture):
    def test_counts_and_status_over_a_bucket(self):
        from toto.vault import scanning

        clean = self._file(title="h1.svg")
        self._scan(clean)
        self._file(title="h2.svg")                      # unscanned
        self._file(title="photo.png", file_type="png")  # unscannable

        report = scanning.health_report(
            VaultFile.objects.filter(bucket=self.bucket))

        self.assertEqual(report["scannable"], 2)
        self.assertEqual(report["unscannable"], 1)
        self.assertEqual(report["clean"], 1)
        self.assertEqual(report["unscanned"], 1)
        self.assertEqual(report["status"], "partial")
        self.assertGreater(report["scanned_bytes"], 0)

    def test_a_threat_outranks_everything(self):
        from toto.vault import scanning

        hostile = self._file(self.HOSTILE, title="h3.svg")
        self._scan(hostile)

        report = scanning.health_report(
            VaultFile.objects.filter(bucket=self.bucket))

        self.assertEqual(report["status"], "threats")

    def test_no_antivirus_means_none_not_zeroes(self):
        """The façade's contract: the metrics card must not render a card full
        of zeroes about a scanner that does not exist."""
        from django.test import modify_settings

        from toto.vault import scanning

        with modify_settings(INSTALLED_APPS={"remove": "toto.antivirus"}):
            self.assertIsNone(scanning.health_report(VaultFile.objects.all()))


class VaultSurfaceTests(_TabsFixture):
    def test_the_bucket_metrics_page_carries_the_card(self):
        vault_file = self._file(title="m1.svg")
        self._scan(vault_file)

        response = self.client.get(
            reverse("vault:bucket_metrics", args=[self.bucket.slug]))

        self.assertContains(response, "Antivirus")
        self.assertContains(response, "Data scanned")

    def test_the_vault_tree_carries_the_shield_and_nothing_else(self):
        """The shield is the vault's ONLY antivirus surface: a link, no scan
        buttons, no modal."""
        response = self.client.get(reverse("vault:public_list"))
        body = response.content.decode()

        self.assertIn("fa-shield-virus", body)
        self.assertIn(reverse("antivirus:index"), body)
        self.assertNotIn("askScan", body)
        self.assertNotIn("confirmScan", body)


class HtmlDoctypeTests(TestCase):
    """The bare HTML5 doctype is inert, and every real HTML file starts with it.

    Refusing it meant no ordinary .html could pass the upload door at all — the
    scanner had turned from a guard into a ban. The allowance is EXACTLY the
    bare form: an identifier, a subset or an entity falls outside the pattern
    and refuses precisely as before. SVG and XML keep refusing every doctype,
    because their parsers actually process DTDs and that is where XXE lives.
    """

    def test_a_real_html_file_passes(self):
        for body in ("<!doctype html><html><body><h1>Hi</h1></body></html>",
                     "<!DOCTYPE html><html><body>x</body></html>",
                     "<!DOCTYPE  html >\n<html><body>x</body></html>"):
            with self.subTest(body=body[:30]):
                self.assertTrue(scan(body, file_type="html").ok)

    def test_the_dangerous_doctypes_still_refuse(self):
        for body in ('<!DOCTYPE html [ <!ENTITY x "y"> ]><html>&x;</html>',
                     '<!DOCTYPE html SYSTEM "http://evil/dtd"><html></html>',
                     '<!ENTITY x "y"><html></html>'):
            with self.subTest(body=body[:40]):
                verdict = scan(body, file_type="html")
                self.assertFalse(verdict.ok)
                self.assertEqual(verdict.reason, "declaration")

    def test_the_doctype_does_not_smuggle_anything_past_the_rest(self):
        verdict = scan("<!doctype html><html><script>alert(1)</script></html>",
                       file_type="html")

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "active-content")

    def test_svg_and_xml_keep_refusing_every_doctype(self):
        for file_type, body in (("svg", "<!doctype html><svg><rect/></svg>"),
                                ("xml", "<!DOCTYPE html><data><row/></data>")):
            with self.subTest(file_type=file_type):
                self.assertFalse(scan(body, file_type=file_type).ok)


class RescanTests(_TabsFixture):
    """A verdict is about bytes; pressing Scan again re-checks the bytes now."""

    def test_scanning_twice_updates_rather_than_piling_up(self):
        vault_file = self._file(title="again.svg")

        self._scan(vault_file)
        self._scan(vault_file)

        self.assertEqual(ScanResult.objects.filter(file=vault_file).count(), 1)

    def test_a_rescan_after_an_edit_judges_the_new_bytes(self):
        """The old verdict stays as history; the new one is about now."""
        vault_file = self._file(self.HOSTILE, title="fixed.svg")
        self._scan(vault_file)

        vault_file.file.save("fixed.svg", ContentFile(self.CLEAN.encode()),
                             save=False)
        vault_file.content_hash = vault_file.create_hash()
        # "file" too: FileField.save(save=False) renames the stored file, and
        # persisting only the hash leaves the row pointing at the old bytes.
        vault_file.save(update_fields=["file", "content_hash"])
        response = self._scan(vault_file)

        self.assertTrue(response.json()["clean"])
        self.assertEqual(ScanResult.objects.filter(file=vault_file).count(), 2)

    def test_a_scanned_row_offers_a_rescan(self):
        vault_file = self._file(title="offer.svg")
        self._scan(vault_file)

        body = self.client.get(reverse("antivirus:index")).content.decode()

        self.assertIn("Re-scan", body)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="antivirus-htmldoor-"))
class HtmlUploadDoorTests(TestCase):
    """End to end: an ordinary HTML file lands through the vault door."""

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        self.user = User.objects.create_user("uploader", password="pw")
        self.bucket = Bucket.objects.create(name="Up", slug="up",
                                            owner=self.user)
        self.client.force_login(self.user)

    def test_a_plain_html5_file_uploads(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        body = "<!doctype html><html><body><h1>Notes</h1></body></html>"
        response = self.client.post(
            reverse("vault:api_file_upload"),
            {"file": SimpleUploadedFile("notes.html", body.encode(),
                                        content_type="text/html"),
             "bucket_slug": self.bucket.slug})

        self.assertEqual(response.status_code, 201, response.content)

    def test_a_hostile_html_file_still_does_not(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        body = "<!doctype html><html><script>alert(1)</script></html>"
        before = VaultFile.objects.count()
        response = self.client.post(
            reverse("vault:api_file_upload"),
            {"file": SimpleUploadedFile("bad.html", body.encode(),
                                        content_type="text/html"),
             "bucket_slug": self.bucket.slug})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(VaultFile.objects.count(), before)


class PdfScannerTests(SimpleTestCase):
    """The binary scanner: refuse what a PDF can DO, parse nothing.

    Deliberately not refused: /AcroForm and signature machinery — notarius
    writes signed PDFs, and a scanner that refuses the platform's own output
    is a ban, not a guard. The same lesson as the HTML5 doctype.
    """

    CLEAN = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
             b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
             b"trailer<</Root 1 0 R/Size 3>>\n%%EOF")

    def test_a_plain_pdf_passes(self):
        self.assertTrue(scan(self.CLEAN, file_type="pdf").ok)

    def test_active_content_markers_refuse_by_name(self):
        for marker in (b"/JavaScript", b"/JS", b"/OpenAction", b"/AA",
                       b"/Launch", b"/EmbeddedFile", b"/RichMedia", b"/XFA"):
            with self.subTest(marker=marker.decode()):
                body = self.CLEAN.replace(b"trailer", marker + b" trailer")
                verdict = scan(body, file_type="pdf")
                self.assertFalse(verdict.ok)
                self.assertEqual(verdict.reason, "active-content")

    def test_a_name_that_merely_starts_the_same_way_is_not_a_marker(self):
        """PDF names are case-sensitive tokens; /JSXform is not /JS."""
        body = self.CLEAN.replace(b"trailer", b"/JSXform trailer")

        self.assertTrue(scan(body, file_type="pdf").ok)

    def test_signature_machinery_is_not_refused(self):
        """A notarius-signed document must pass its own platform's scanner."""
        body = self.CLEAN.replace(
            b"trailer", b"/AcroForm<</Fields[]>> /Sig /ByteRange[0 1 2 3] trailer")

        self.assertTrue(scan(body, file_type="pdf").ok)

    def test_an_encrypted_pdf_is_refused_not_waved_through(self):
        body = self.CLEAN.replace(b"trailer", b"/Encrypt 5 0 R trailer")
        verdict = scan(body, file_type="pdf")

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "wrong-shape")

    def test_bytes_that_are_not_a_pdf_are_refused(self):
        verdict = scan(b"GIF89a not a pdf at all", file_type="pdf")

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "wrong-shape")

    def test_the_binary_path_does_not_choke_on_binary(self):
        """The old text path refused every real PDF as not-UTF-8 without ever
        looking at it — the reason binary scanners exist."""
        body = self.CLEAN + b"\nstream\n\x00\xff\xfe\x89binary\xda\nendstream\n"

        self.assertTrue(scan(body, file_type="pdf").ok)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="antivirus-queue-"))
class QueuedScanTests(_TabsFixture):
    """The Scan button is queued, billable work."""

    def test_no_worker_falls_back_inline(self):
        """celery is not running in tests — the user's exact environment. The
        POST must complete the scan inline rather than refuse: a missing
        worker means slower, never "scanning is broken"."""
        vault_file = self._file(title="q1.svg")

        response = self.client.post(
            reverse("antivirus:scan_file", args=[vault_file.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["finished"])
        self.assertTrue(payload["clean"])
        run = ScanRun.objects.get()
        self.assertEqual(run.status, RunStatus.SUCCESS)

    def test_the_queue_is_still_tried_first(self):
        """The fallback must not become the path of least resistance: with a
        worker listening, the request queues and returns unfinished."""
        from unittest import mock

        vault_file = self._file(title="q1b.svg")

        with mock.patch("toto.antivirus.dispatch.dispatch_run") as queued:
            response = self.client.post(
                reverse("antivirus:scan_file", args=[vault_file.pk]))

        queued.assert_called_once()
        payload = response.json()
        self.assertNotIn("clean", payload)
        self.assertEqual(ScanRun.objects.get().status, RunStatus.PENDING)

    def test_over_limit_is_refused_before_any_run_exists(self):
        from unittest import mock

        vault_file = self._file(title="q2.svg")

        class Broke(Exception):
            status_code = 402

        with mock.patch("toto.antivirus.services.check_affordable",
                        side_effect=Broke("no funds")):
            response = self.client.post(
                reverse("antivirus:scan_file", args=[vault_file.pk]))

        self.assertEqual(response.status_code, 402)
        self.assertEqual(ScanRun.objects.count(), 0)

    def test_the_poll_is_owner_only(self):
        vault_file = self._file(title="q3.svg")
        run_response = self._scan(vault_file)
        self.assertEqual(run_response.status_code, 200)
        run = ScanRun.objects.get()

        self.client.force_login(self.other)

        self.assertEqual(
            self.client.get(reverse("antivirus:scan_status",
                                    args=[run.pk])).status_code, 404)

    def test_a_delivered_verdict_charges_once(self):
        from .models import AntivirusUsageEvent

        vault_file = self._file(title="q4.svg")
        self._scan(vault_file)

        events = AntivirusUsageEvent.objects.filter(
            metric_code="antivirus.scan")
        self.assertEqual(events.count(), 1)

        # Settling again must not double-bill: the idempotency key is the run.
        from . import services as antivirus_services

        antivirus_services.settle(ScanRun.objects.get())
        self.assertEqual(events.count(), 1)

    def test_a_refused_verdict_still_charges(self):
        """The scan ran and the answer is "this file is hostile" — that is the
        service, delivered."""
        from .models import AntivirusUsageEvent

        vault_file = self._file(self.HOSTILE, title="q5.svg")
        self._scan(vault_file)

        self.assertEqual(AntivirusUsageEvent.objects.count(), 1)

    def test_a_failed_run_charges_nothing(self):
        import os

        from .models import AntivirusUsageEvent

        vault_file = self._file(title="q6.svg")
        os.remove(vault_file.file.path)
        self._scan(vault_file)

        self.assertEqual(AntivirusUsageEvent.objects.count(), 0)
        self.assertEqual(ScanRun.objects.get().status, RunStatus.FAILED)

    def test_the_stuck_run_policy_is_registered(self):
        from toto.quota.sweeps import all_policies

        labels = {policy.model_label for policy in all_policies()}
        self.assertIn("antivirus.ScanRun", labels)

    def test_the_metric_is_registered_and_priced(self):
        from toto.quota.metrics import registry as metric_registry

        codes = {metric.code for metric in metric_registry}
        self.assertIn("antivirus.scan", codes)


class PreferenceDoorsTests(_TabsFixture):
    """The preference finally does what its card always claimed.

    The doors never consulted ScanPreference before — the card said "which of
    your files are screened as they are saved" and no door read it. Now every
    automatic door asks ``scanning.should_scan(owner, type, door)``, and the
    owner's choice can narrow their own screening by type AND by door. Skipped
    means saved-but-UNSCANNED — never marked clean.
    """

    def _prefer(self, user=None, types=None, doors=None):
        from .models import ScanPreference

        ScanPreference.objects.update_or_create(
            user=user or self.user,
            defaults={"types": types if types is not None else
                      list(SCANNABLE_TYPES),
                      "doors": doors})

    def test_no_row_means_everything_everywhere(self):
        from toto.vault.scanning import should_scan

        self.assertTrue(should_scan(self.user, "svg", door="editor"))
        self.assertTrue(should_scan(self.user, "pdf", door="api-upload"))

    def test_a_door_switched_off_lets_the_owners_file_pass_unscanned(self):
        """By design: the preference narrows YOUR OWN screening. The file
        lands, no verdict exists, and the app shows it red-unscanned."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        self._prefer(doors=["editor", "restore"])   # uploads OFF

        response = self.client.post(
            reverse("vault:api_file_upload"),
            {"file": SimpleUploadedFile(
                "wild.svg", b"<svg><script>alert(1)</script></svg>",
                content_type="image/svg+xml"),
             "bucket_slug": self.bucket.slug})

        self.assertEqual(response.status_code, 201)
        stored = VaultFile.objects.get(title="wild")
        self.assertEqual(stored.scan_results.count(), 0)

    def test_the_same_upload_refuses_when_the_door_is_on(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self._prefer(doors=["editor", "upload", "restore"])

        response = self.client.post(
            reverse("vault:api_file_upload"),
            {"file": SimpleUploadedFile(
                "wild2.svg", b"<svg><script>alert(1)</script></svg>",
                content_type="image/svg+xml"),
             "bucket_slug": self.bucket.slug})

        self.assertEqual(response.status_code, 400)

    def test_an_unknown_door_is_never_silently_exempt(self):
        from toto.vault.scanning import should_scan

        self._prefer(doors=[])   # every known door off

        self.assertTrue(should_scan(self.user, "svg", door="some-new-door"))

    def test_the_manual_scan_ignores_the_preference_entirely(self):
        """Pressing Scan is the deliberate act preferences exist to replace."""
        self._prefer(types=[], doors=[])
        vault_file = self._file(title="deliberate.svg")

        response = self._scan(vault_file)

        self.assertTrue(response.json()["clean"])

    def test_null_doors_means_all_doors_for_an_old_row(self):
        """Rows saved before the field existed must not switch anything off."""
        from .models import ScanPreference

        self._prefer(doors=None)
        row = ScanPreference.objects.get(user=self.user)

        self.assertIsNone(row.doors)
        self.assertTrue(ScanPreference.applies(self.user, "svg", "editor"))


class SettingsTabTests(_TabsFixture):
    def test_the_tab_renders_with_explicit_save(self):
        response = self.client.get(reverse("antivirus:settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "savePreference()")
        # Explicit save: no checkbox fires the POST by itself.
        self.assertNotContains(response, '@change="savePreference()"')

    def test_checkboxes_follow_the_theme(self):
        # Native checkboxes ignore the page palette unless told: accent-color
        # picks the checked fill, color-scheme makes the browser draw the box
        # itself dark. Both, on every checkbox — settings and the scan modal.
        self._file()  # the index checkbox lives on a rendered row
        for name in ("settings", "index"):
            with self.subTest(page=name):
                response = self.client.get(reverse(f"antivirus:{name}"))
                self.assertContains(response, "accent-accent-dark")
                self.assertContains(response, "[color-scheme:dark]")

    def test_the_scan_button_binding_yields_a_real_boolean(self):
        """Alpine coerces an undefined bind result to "" when the expression
        contains a dot — and "" STAMPS a boolean attribute instead of removing
        it. `:disabled="a && a.busy"` therefore rendered every Scan button
        disabled from birth. The binding must coerce with !!."""
        self._file()
        html = self.client.get(reverse("antivirus:index")).content.decode()

        self.assertIn(':disabled="!!(results[', html)
        self.assertNotIn(':disabled="results[', html)

    def test_row_results_are_icons_not_sentences(self):
        # The tree stays a list: outcome text lives in tooltips and the
        # modal's outcome list, never inline in a row.
        self._file()
        html = self.client.get(reverse("antivirus:index")).content.decode()

        # No row renders result text inline (the modal's outcome list may).
        self.assertNotIn('x-text="results[', html)
        self.assertIn(':title="results[', html)

    def test_the_files_tab_no_longer_carries_the_preference_card(self):
        self.assertNotContains(self.client.get(reverse("antivirus:index")),
                               "Scan automatically")

    def test_saving_types_and_doors_persists_both(self):
        from .models import ScanPreference

        self.client.post(reverse("antivirus:set_preference"),
                         {"types": ["svg", "pdf"], "doors": ["editor"]})

        row = ScanPreference.objects.get(user=self.user)
        self.assertEqual(sorted(row.types), ["pdf", "svg"])
        self.assertEqual(row.doors, ["editor"])

    def test_the_scanner_form_is_staff_only(self):
        response = self.client.get(reverse("antivirus:settings"))
        self.assertNotContains(response, "Scanner configuration")

        self.assertEqual(
            self.client.post(reverse("antivirus:set_scanner_config"),
                             {"scan_max_mb": "5"}).status_code, 403)

        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("antivirus:settings")),
                            "Scanner configuration")

    def test_only_real_overrides_are_stored(self):
        """A value matching the default stays out of the row, so tightening a
        default later is not pinned by everyone who once pressed Save."""
        from .models import ScannerConfig
        from .scanners.config import DEFAULTS

        self.client.force_login(self.staff)
        self.client.post(reverse("antivirus:set_scanner_config"), {
            "json_max_depth": str(DEFAULTS["json_max_depth"]),
            "scan_max_mb": "5",
        })

        self.assertEqual(ScannerConfig.get().params, {"scan_max_mb": 5})

    def test_a_nonsense_number_is_refused(self):
        self.client.force_login(self.staff)

        response = self.client.post(reverse("antivirus:set_scanner_config"),
                                    {"scan_max_mb": "-3"})

        self.assertEqual(response.status_code, 400)


class ScannerConfigEffectTests(_TabsFixture):
    """The stored parameters actually reach the scanners, at scan time."""

    def _override(self, **params):
        from .models import ScannerConfig

        config = ScannerConfig.get()
        config.params = params
        config.save(update_fields=["params"])

    def test_json_depth_is_configurable(self):
        deep = '{"a":{"b":{"c":{"d":1}}}}'
        self.assertTrue(scan(deep, file_type="json").ok)

        self._override(json_max_depth=2)

        verdict = scan(deep, file_type="json")
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "too-deep")

    def test_the_size_cap_refuses_rather_than_skips(self):
        self._override(scan_max_mb=1)

        verdict = scan("x" * (2 * 1024 * 1024), file_type="html")

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "wrong-shape")
        self.assertIn("scan limit", verdict.detail)

    def test_a_pdf_toggle_relaxes_exactly_one_marker(self):
        base = (b"%PDF-1.4\ntrailer<</Size 1>>\n%%EOF")

        self._override(pdf_refuse_aa=False)

        relaxed = base.replace(b"trailer", b"/AA trailer")
        still_hard = base.replace(b"trailer", b"/JavaScript trailer")
        self.assertTrue(scan(relaxed, file_type="pdf").ok)
        self.assertFalse(scan(still_hard, file_type="pdf").ok)

    def test_the_hard_core_has_no_switch(self):
        """No parameter exists that could let /JavaScript through."""
        from .scanners.config import DEFAULTS

        for key in DEFAULTS:
            self.assertNotIn("javascript", key.lower())
            self.assertNotIn("openaction", key.lower())

    def test_a_broken_row_degrades_to_the_defaults(self):
        from .models import ScannerConfig
        from .scanners.config import DEFAULTS, params

        config = ScannerConfig.get()
        config.params = "not-a-dict"
        config.save(update_fields=["params"])

        self.assertEqual(params()["json_max_depth"],
                         DEFAULTS["json_max_depth"])


class WorkflowsRequiredTests(TestCase):
    def test_the_check_passes_here(self):
        from django.core import checks

        errors = [e for e in checks.run_checks() if e.id == "antivirus.E001"]
        self.assertEqual(errors, [])

    def test_the_check_fails_without_workflows(self):
        from django.core import checks
        from django.test import modify_settings

        with modify_settings(INSTALLED_APPS={"remove": "toto.workflows"}):
            errors = [e for e in checks.run_checks()
                      if e.id == "antivirus.E001"]

        self.assertEqual(len(errors), 1)


class IngressTests(TestCase):
    """The scan workflow is seeded at deploy, not first-Scan.

    Before this command existed the Workflow row was created lazily by the
    first successful dispatch — so an operator opening the workflows app on a
    fresh deploy saw no antivirus workflow at all and read it as "antivirus
    has no worker path".
    """

    def test_ingress_creates_the_workflow(self):
        from django.core.management import call_command

        from toto.workflows.models import Workflow, WorkflowNode

        from .workflow import SCAN_TASK_NAME, SCAN_WORKFLOW_SLUG

        call_command("ingress_antivirus")

        workflow = Workflow.objects.get(slug=SCAN_WORKFLOW_SLUG)
        self.assertTrue(workflow.nodes.filter(
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=SCAN_TASK_NAME).exists())

    def test_ingress_is_idempotent(self):
        from django.core.management import call_command

        from toto.workflows.models import Workflow

        from .workflow import SCAN_WORKFLOW_SLUG

        call_command("ingress_antivirus")
        call_command("ingress_antivirus", full=True)

        self.assertEqual(
            Workflow.objects.filter(slug=SCAN_WORKFLOW_SLUG).count(), 1)
        self.assertEqual(
            Workflow.objects.get(slug=SCAN_WORKFLOW_SLUG).nodes.count(), 1)


class VoidElementDepthTests(TestCase):
    """A void element must not move the sanitiser's depth counter.

    The bug this pins was silent, total, and shipped: `handle_starttag`
    incremented `_depth` for every tag including void ones, which never produce
    an end tag. So `<noscript>` recorded ("noscript", 1), a bare `<img>` inside
    it pushed the depth to 2, and `</noscript>` looked for ("noscript", 2),
    never matched, and left suppression on for THE REST OF THE DOCUMENT.

    `<noscript><img src="pixel.gif"></noscript>` is a tracking pixel or a
    lazy-load fallback and sits near the top of a great many real pages, so the
    common case was "everything after the first few lines disappeared", with a
    200 and no warning. The HTML-to-document converter in `toto.htmlview` seeds
    through this function, which is how a converted page could arrive truncated.

    Both directions are asserted here: content after a void element SURVIVES,
    and everything that must still be suppressed still is. A fix to the first
    that weakened the second would be much worse than the bug.
    """

    def _clean(self, html):
        from toto.antivirus.sanitize.document import sanitize_content

        return sanitize_content(html)

    # -- the truncation ---------------------------------------------------

    def test_a_void_img_inside_noscript_does_not_eat_the_document(self):
        out = self._clean(
            '<p>before</p><noscript><img src="pixel.gif"></noscript><p>after</p>')
        self.assertIn("before", out)
        self.assertIn("after", out)

    def test_a_void_br_inside_template_does_not_eat_the_document(self):
        out = self._clean('<p>before</p><template><br></template><p>after</p>')
        self.assertIn("after", out)

    def test_a_bare_embed_does_not_suppress_forever(self):
        """`embed` is void AND in VOID_CONTENT_TAGS — it used to push a
        suppression nothing could ever pop."""
        out = self._clean('<p>before</p><embed src="x"><p>after</p>')
        self.assertIn("after", out)

    def test_an_ordinary_page_shape_survives_intact(self):
        out = self._clean(
            '<style>body{}</style><p>before</p>'
            '<noscript><img src="a.gif"></noscript><h1>Title</h1><p>after</p>')
        for expected in ("before", "Title", "after"):
            self.assertIn(expected, out)

    def test_several_void_elements_do_not_accumulate_drift(self):
        out = self._clean(
            '<p>a</p><img src="1.gif"><br><hr><input>'
            '<noscript><img src="2.gif"></noscript><p>z</p>')
        self.assertIn("z", out)

    # -- what must NOT have been weakened ---------------------------------

    def test_script_is_still_dropped_with_its_contents(self):
        out = self._clean('<p>a</p><script>alert(1)</script><p>b</p>')
        self.assertNotIn("alert", out)
        self.assertIn("b", out)

    def test_a_tag_inside_a_script_does_not_end_the_suppression_early(self):
        out = self._clean('<p>a</p><script>var x = "<b>no</b>";</script><p>b</p>')
        self.assertNotIn("var x", out)
        self.assertNotIn("no", out)
        self.assertIn("b", out)

    def test_style_svg_iframe_and_object_are_still_dropped(self):
        for markup, needle in (
            ('<style>x{color:red}</style>', "color:red"),
            ('<svg><circle r="1"/></svg>', "circle"),
            ('<iframe src="evil"></iframe>', "evil"),
            ('<object data="evil"></object>', "evil"),
        ):
            with self.subTest(markup=markup):
                out = self._clean(f"<p>a</p>{markup}<p>b</p>")
                self.assertNotIn(needle, out)
                self.assertIn("b", out)

    def test_a_void_element_inside_a_script_is_still_suppressed(self):
        """The void path must not become a way out of suppression."""
        out = self._clean('<p>a</p><script><img src="x.gif"></script><p>b</p>')
        self.assertNotIn("x.gif", out)
        self.assertIn("b", out)

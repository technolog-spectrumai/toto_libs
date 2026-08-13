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
from toto.vault.scanning import Verdict, scan, scanning_enabled

from .scanners import json_scan, markup, scanned_types

User = get_user_model()


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


class RegistryTests(SimpleTestCase):
    def test_the_built_in_scanners_registered(self):
        self.assertEqual(set(scanned_types()), {"svg", "html", "xml", "json"})

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

        response = self.client.post(
            reverse("antivirus:scan_file", args=[vault_file.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["clean"])
        self.assertEqual(vault_file.scan_results.get().verdict, "clean")

    def test_scanning_a_hostile_file_flags_it_and_changes_nothing(self):
        """Flag only. The bytes are untouched and the file still opens."""
        body = '<svg><script>alert(1)</script></svg>'
        vault_file = self._file(body)

        response = self.client.post(
            reverse("antivirus:scan_file", args=[vault_file.pk]))

        self.assertFalse(response.json()["clean"])
        self.assertEqual(vault_file.scan_results.get().verdict, "refused")
        with vault_file.file.open("rb") as handle:
            self.assertEqual(handle.read().decode(), body)

    def test_a_stranger_cannot_scan_your_file(self):
        vault_file = self._file("<svg/>")
        self.client.force_login(self.stranger)
        self.assertEqual(
            self.client.post(
                reverse("antivirus:scan_file", args=[vault_file.pk])).status_code,
            404)

    def test_the_tick_shows_only_for_current_clean_bytes(self):
        from toto.vault.scanning import clean_file_ids

        vault_file = self._file("<svg><rect/></svg>")
        self.assertEqual(clean_file_ids([vault_file]), set())

        self.client.post(reverse("antivirus:scan_file", args=[vault_file.pk]))
        vault_file.refresh_from_db()
        self.assertEqual(clean_file_ids([vault_file]), {vault_file.pk})

        # Edit it: the verdict was about the old bytes, so the tick goes.
        vault_file.content_hash = "0" * 64
        vault_file.save(update_fields=["content_hash"])
        self.assertEqual(clean_file_ids([vault_file]), set())

    def test_a_refused_file_never_gets_a_tick(self):
        from toto.vault.scanning import clean_file_ids

        vault_file = self._file('<svg onload="x()"/>')
        self.client.post(reverse("antivirus:scan_file", args=[vault_file.pk]))
        vault_file.refresh_from_db()
        self.assertEqual(clean_file_ids([vault_file]), set())

    def test_preferences_are_per_user_and_narrow_only_their_own(self):
        from .models import ScanPreference

        self.client.post(reverse("antivirus:set_preference"),
                         {"types": ["svg", "html"]})

        self.assertEqual(set(ScanPreference.types_for(self.user)), {"svg", "html"})
        # Untouched for everyone else.
        self.assertEqual(set(ScanPreference.types_for(self.stranger)),
                         {"svg", "html", "xml", "json"})

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

        verdict = async_to_sync(consumer.write_file)(self.HOSTILE)

        self.assertFalse(verdict.ok)
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
        self.client.post(reverse("antivirus:scan_file", args=[vault_file.pk]))

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
        self.client.post(reverse("antivirus:scan_file", args=[vault_file.pk]))

        self.client.post(reverse("editor:xml_save", args=[vault_file.pk]),
                         {"content": '<svg xmlns="http://www.w3.org/2000/svg"><circle/></svg>'})

        items = self._items(self.client.get(reverse("vault:public_list")))
        self.assertTrue(items["drawing.svg"]["scan_ok"])

    def test_bytes_that_changed_outside_a_door_lose_the_mark(self):
        """No verdict exists for the hash the file now carries."""
        vault_file = self._file()
        self.client.post(reverse("antivirus:scan_file", args=[vault_file.pk]))

        VaultFile.objects.filter(pk=vault_file.pk).update(content_hash="0" * 64)

        items = self._items(self.client.get(reverse("vault:public_list")))
        self.assertFalse(items["drawing.svg"]["scan_ok"])

    def test_the_whole_listing_is_one_extra_query(self):
        from toto.antivirus.models import ScanResult

        for n in range(6):
            vault_file = self._file(f"d{n}.svg")
            self.client.post(reverse("antivirus:scan_file", args=[vault_file.pk]))
        self.assertEqual(ScanResult.objects.count(), 6)

        from toto.vault.scanning import clean_file_ids

        files = list(VaultFile.objects.filter(owner=self.user))
        with self.assertNumQueries(1):
            self.assertEqual(len(clean_file_ids(files)), 6)

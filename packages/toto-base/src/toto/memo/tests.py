"""Tests for the file-based presentation format + vault-wired viewer/editor."""

from __future__ import annotations

import base64
import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.memo import presentation_format as pf
from toto.vault.models import Bucket, VaultDirectory, VaultFile
from toto.vault.plugins import VaultEditorPlugin, VaultPlayPlugin

User = get_user_model()


class PresentationFormatTests(TestCase):
    def test_round_trip_preserves_everything(self):
        p = pf.Presentation(
            title="My Talk",
            slides=[
                pf.Slide(
                    title="Welcome",
                    body=(
                        '<p>Hello & <b>world</b></p>\n'
                        '<img src="data:image/png;base64,iVBORw0KGgo=" alt="pic">\n'
                        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                        '<rect width="10" height="10"/></svg>'
                    ),
                ),
                pf.Slide(title="Edge case", body="contains ]]> a CDATA terminator"),
            ],
        )
        xml = pf.dumps(p)
        back = pf.loads(xml)

        self.assertEqual(back.title, "My Talk")
        self.assertEqual(len(back.slides), 2)
        self.assertEqual(back.slides[0].title, "Welcome")
        self.assertIn("data:image/png;base64,iVBORw0KGgo=", back.slides[0].body)
        self.assertIn("<svg", back.slides[0].body)
        # The literal "]]>" survives the CDATA split/round-trip.
        self.assertEqual(back.slides[1].body, "contains ]]> a CDATA terminator")

    def test_idempotent(self):
        p = pf.new_presentation("Hello")
        p.slides[0].title = "Intro"
        p.slides[0].body = "<p>hi</p>"
        xml = pf.dumps(p)
        self.assertEqual(pf.dumps(pf.loads(xml)), xml)

    def test_empty_input_yields_default(self):
        for raw in ("", "   \n  "):
            p = pf.loads(raw)
            self.assertEqual(len(p.slides), 1)

    def test_invalid_xml_raises(self):
        with self.assertRaises(pf.PresentationParseError):
            pf.loads("<presentation><slide>")

    def test_wrong_root_raises(self):
        with self.assertRaises(pf.PresentationParseError):
            pf.loads("<other></other>")

    def test_is_presentation_detects_root(self):
        self.assertTrue(pf.is_presentation(pf.dumps(pf.new_presentation("x"))))
        self.assertTrue(pf.is_presentation(b'<presentation version="1"></presentation>'))
        self.assertFalse(pf.is_presentation("<notebook></notebook>"))
        self.assertFalse(pf.is_presentation("<other/>"))
        self.assertFalse(pf.is_presentation(""))

    def test_blank_template_matches_dumps(self):
        # The vault CreateEmptyFileView seeds a literal that must round-trip.
        self.assertEqual(
            pf.dumps(pf.new_presentation()),
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<presentation version="1" title="">\n'
            "  <slide>\n"
            "    <title></title>\n"
            "    <body><![CDATA[]]></body>\n"
            "  </slide>\n"
            "</presentation>\n",
        )


class VaultDetectionTests(TestCase):
    def test_pml_extension_retired(self):
        # The dedicated `.pml`/presentation vault type is retired — presentations
        # are ordinary .xml now, so `.pml` no longer maps to a special type.
        self.assertNotEqual(VaultFile.detect_type("", "talk.pml"), "presentation")
        self.assertEqual(VaultFile.detect_type("application/xml", "deck.xml"), "xml")

    def test_generic_xml_stays_xml(self):
        self.assertEqual(VaultFile.detect_type("application/xml", "data.xml"), "xml")
        self.assertEqual(VaultFile.detect_type("text/xml", "feed.xml"), "xml")


class PresentationVaultIntegrationTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)

        Platform.objects.create(
            site_name="Toto", author="Test", publication_year=2026, active=True
        )

        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        self.directory = VaultDirectory.objects.create(
            name="Talks", bucket=self.bucket, owner=self.alice
        )

    def _make_presentation(self, p=None, owner=None, is_public=False) -> VaultFile:
        owner = owner or self.alice
        p = p or pf.new_presentation("Analysis")
        return VaultFile.objects.create(
            owner=owner,
            title="talk.pml",
            file_type="presentation",
            is_public=is_public,
            bucket=self.bucket,
            directory=self.directory,
            file=SimpleUploadedFile("talk.pml", pf.dumps(p).encode("utf-8")),
        )

    def test_plugins_registered_for_presentation(self):
        play = VaultPlayPlugin.for_file_type("presentation")
        editor = VaultEditorPlugin.for_file_type("presentation")
        self.assertIsNotNone(play)
        self.assertIsNotNone(editor)
        vf = self._make_presentation()
        self.assertEqual(play.get_play_url(vf), reverse("memo:present", args=[vf.pk]))
        # The vault Edit button opens the plain-text XML source editor; the
        # structured slide editor stays reachable from the memo app itself.
        self.assertEqual(editor.get_editor_url(vf), reverse("memo:source", args=[vf.pk]))

    def test_view_public_ok_for_anon(self):
        vf = self._make_presentation(is_public=True)
        res = self.client.get(reverse("memo:present", args=[vf.pk]))
        self.assertEqual(res.status_code, 200)

    def test_view_private_denied_for_anon(self):
        vf = self._make_presentation(is_public=False)
        res = self.client.get(reverse("memo:present", args=[vf.pk]))
        # redirect_to_login → 302
        self.assertEqual(res.status_code, 302)

    def test_view_private_ok_for_owner(self):
        p = pf.Presentation(title="T", slides=[pf.Slide(title="S1", body="<p>body one</p>")])
        vf = self._make_presentation(p=p)
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:present", args=[vf.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "body one")

    def test_edit_hydration_and_owner_only(self):
        p = pf.Presentation(title="T", slides=[pf.Slide(title="S1", body="<p>x</p>")])
        vf = self._make_presentation(p=p)

        # non-owner → 404
        self.client.force_login(self.bob)
        self.assertEqual(
            self.client.get(reverse("memo:edit", args=[vf.pk])).status_code, 404
        )

        # owner → 200, hydration payload present
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:edit", args=[vf.pk]))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertIn('id="presentation-data"', body)
        start = body.index('id="presentation-data"')
        snippet = body[start:body.index("</script>", start)]
        payload = json.loads(snippet[snippet.index(">") + 1:])
        self.assertEqual(payload["slides"][0]["title"], "S1")
        self.assertIn(reverse("memo:save", args=[vf.pk]), body)

    def test_save_serialises_to_file(self):
        vf = self._make_presentation()
        self.client.force_login(self.alice)
        payload = {
            "title": "Updated",
            "slides": [
                {"title": "One", "body": '<img src="data:image/png;base64,AAAA" alt="a">'},
                {"title": "Two", "body": "<p>second</p>"},
            ],
        }
        res = self.client.post(
            reverse("memo:save", args=[vf.pk]),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

        vf.refresh_from_db()
        with vf.file.open("r") as f:
            saved = f.read()
        if isinstance(saved, bytes):
            saved = saved.decode("utf-8")
        back = pf.loads(saved)
        self.assertEqual(back.title, "Updated")
        self.assertEqual(len(back.slides), 2)
        self.assertIn("data:image/png;base64,AAAA", back.slides[0].body)

    def test_save_denied_for_non_owner(self):
        vf = self._make_presentation()
        self.client.force_login(self.bob)
        res = self.client.post(
            reverse("memo:save", args=[vf.pk]),
            data=json.dumps({"slides": []}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 404)

    def test_presentation_type_retired_from_vault_new_file(self):
        from toto.vault.views import CREATABLE_TYPES, CreateEmptyFileView
        self.assertNotIn("presentation", {t for t, _ in CREATABLE_TYPES})
        self.assertNotIn("presentation", CreateEmptyFileView._ALLOWED)
        self.assertNotIn("presentation", CreateEmptyFileView._INITIAL)

    def test_present_404_on_non_presentation_xml(self):
        vf = VaultFile.objects.create(
            owner=self.alice, title="notes.xml", file_type="xml",
            is_public=True, bucket=self.bucket,
            file=SimpleUploadedFile("notes.xml", b"<notes><a/></notes>"),
        )
        self.assertEqual(self.client.get(reverse("memo:present", args=[vf.pk])).status_code, 404)

    def test_index_lists_presentations(self):
        self._make_presentation(is_public=True)
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:index"))
        self.assertEqual(res.status_code, 200)

    def test_create_new_presentation_redirects_to_editor(self):
        self.client.force_login(self.alice)
        res = self.client.post(reverse("memo:create"))
        self.assertEqual(res.status_code, 302)
        vf = VaultFile.objects.filter(owner=self.alice, file_type="xml").latest("pk")
        self.assertEqual(res.url, reverse("memo:edit", args=[vf.pk]))
        # Created in the user's personal bucket with a valid blank deck.
        self.assertEqual(vf.bucket.slug, f"personal-{self.alice.username}")
        self.assertTrue(vf.title.endswith(".xml"))
        with vf.file.open("r") as f:
            content = f.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.assertEqual(len(pf.loads(content).slides), 1)

    def test_create_requires_login(self):
        res = self.client.post(reverse("memo:create"))
        self.assertEqual(res.status_code, 302)  # redirect to login
        self.assertIn("/login", res.url)

    def test_create_into_chosen_bucket_and_directory(self):
        self.client.force_login(self.alice)
        res = self.client.post(reverse("memo:create"), data={
            "filename": "My Talk",
            "bucket_id": str(self.bucket.pk),
            "directory_id": str(self.directory.pk),
        })
        self.assertEqual(res.status_code, 302)
        vf = VaultFile.objects.filter(owner=self.alice, file_type="xml").latest("pk")
        self.assertEqual(res.url, reverse("memo:edit", args=[vf.pk]))
        self.assertEqual(vf.bucket, self.bucket)
        self.assertEqual(vf.directory, self.directory)
        self.assertEqual(vf.title, "My Talk.xml")
        self.assertEqual(vf.key, "my-talk")
        with vf.file.open("r") as f:
            content = f.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.assertEqual(len(pf.loads(content).slides), 1)

    def test_create_rejects_foreign_bucket(self):
        self.client.force_login(self.bob)
        res = self.client.post(reverse("memo:create"), data={
            "filename": "sneaky",
            "bucket_id": str(self.bucket.pk),
        })
        self.assertEqual(res.status_code, 404)
        self.assertFalse(VaultFile.objects.filter(owner=self.bob).exists())

    def test_index_shows_location_path(self):
        self._make_presentation(is_public=True)
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:index"))
        self.assertContains(res, "Lab / Talks")

    def test_source_view_owner_only(self):
        p = pf.Presentation(title="T", slides=[pf.Slide(title="S1", body="<p>x</p>")])
        vf = self._make_presentation(p=p)

        # non-owner → 404
        self.client.force_login(self.bob)
        self.assertEqual(
            self.client.get(reverse("memo:source", args=[vf.pk])).status_code, 404
        )

        # owner → 200 with the raw XML in the page
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:source", args=[vf.pk]))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        # The raw XML is hydrated into Ace via |escapejs, so `<presentation`
        # appears as the escaped literal.
        self.assertIn("\\u003Cpresentation", body)
        self.assertIn(reverse("memo:source_save", args=[vf.pk]), body)
        # Toolbar links back into the memo app.
        self.assertIn(reverse("memo:edit", args=[vf.pk]), body)
        self.assertIn(reverse("memo:present", args=[vf.pk]), body)

    def test_source_save_round_trip(self):
        vf = self._make_presentation()
        self.client.force_login(self.alice)
        xml = pf.dumps(
            pf.Presentation(title="Raw", slides=[pf.Slide(title="One", body="<p>hi</p>")])
        )
        res = self.client.post(
            reverse("memo:source_save", args=[vf.pk]), data={"content": xml}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")
        self.assertTrue(res.json()["valid_presentation"])

        vf.refresh_from_db()
        with vf.file.open("r") as f:
            saved = f.read()
        if isinstance(saved, bytes):
            saved = saved.decode("utf-8")
        self.assertEqual(saved, xml)
        self.assertEqual(pf.loads(saved).title, "Raw")

    def test_source_save_accepts_invalid_xml_but_flags_it(self):
        # Plain-text editing must not gate on parse state — the save lands,
        # the response just reports the file no longer parses.
        vf = self._make_presentation()
        self.client.force_login(self.alice)
        res = self.client.post(
            reverse("memo:source_save", args=[vf.pk]), data={"content": "<broken"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")
        self.assertFalse(res.json()["valid_presentation"])

        vf.refresh_from_db()
        with vf.file.open("r") as f:
            saved = f.read()
        if isinstance(saved, bytes):
            saved = saved.decode("utf-8")
        self.assertEqual(saved, "<broken")

    def test_source_save_denied_for_non_owner(self):
        vf = self._make_presentation()
        self.client.force_login(self.bob)
        res = self.client.post(
            reverse("memo:source_save", args=[vf.pk]), data={"content": "x"}
        )
        self.assertEqual(res.status_code, 404)


# 1×1 transparent PNG — small, real raster that Pillow can decode.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class PresentationMediaEmbedTests(TestCase):
    """Insert image/SVG from a vault bucket into a slide (self-contained embed)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)

        Platform.objects.create(
            site_name="Toto", author="Test", publication_year=2026, active=True
        )

        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        self.directory = VaultDirectory.objects.create(
            name="Assets", bucket=self.bucket, owner=self.alice
        )
        self.deck = VaultFile.objects.create(
            owner=self.alice,
            title="talk.pml",
            file_type="presentation",
            bucket=self.bucket,
            directory=self.directory,
            file=SimpleUploadedFile("talk.pml", pf.dumps(pf.new_presentation()).encode()),
        )

    def _make_image(self, owner=None, is_public=False, title="pic.png"):
        owner = owner or self.alice
        return VaultFile.objects.create(
            owner=owner,
            title=title,
            file_type="image",
            is_public=is_public,
            bucket=self.bucket,
            directory=self.directory,
            file=SimpleUploadedFile(title, _TINY_PNG, content_type="image/png"),
        )

    def _make_svg(self, owner=None, is_public=False, title="logo.svg", markup=None):
        owner = owner or self.alice
        markup = markup or '<svg xmlns="http://www.w3.org/2000/svg"><rect width="4" height="4"/></svg>'
        return VaultFile.objects.create(
            owner=owner,
            title=title,
            file_type="svg",
            is_public=is_public,
            bucket=self.bucket,
            directory=self.directory,
            file=SimpleUploadedFile(title, markup.encode("utf-8"), content_type="image/svg+xml"),
        )

    # ── Picker payload in the editor page ───────────────────────────

    def test_edit_page_lists_media_with_location(self):
        self._make_image(title="pic.png")
        self._make_svg(title="logo.svg")
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:edit", args=[self.deck.pk]))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        # The vault-media picker payload + its "location" path are hydrated.
        self.assertIn('id="vault-media-data"', body)
        self.assertIn("pic.png", body)
        self.assertIn("logo.svg", body)
        self.assertIn("Lab / Assets", body)

    # ── Embed endpoint ──────────────────────────────────────────────

    def test_embed_image_returns_data_uri(self):
        img = self._make_image()
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": img.pk})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["kind"], "image")
        self.assertTrue(data["data_uri"].startswith("data:image/"))
        self.assertIn(";base64,", data["data_uri"])
        self.assertEqual(data["alt"], "pic")

    def test_embed_svg_inlines_and_strips_scripts(self):
        svg = self._make_svg(
            markup='<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg">'
            '<script>alert(1)</script><rect width="4" height="4"/></svg>'
        )
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": svg.pk})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["kind"], "svg")
        self.assertIn("<svg", data["markup"])
        self.assertIn("<rect", data["markup"])
        self.assertNotIn("<script", data["markup"])
        self.assertNotIn("<?xml", data["markup"])

    def test_embed_allows_public_file_of_other_user(self):
        img = self._make_image(owner=self.bob, is_public=True, title="shared.png")
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": img.pk})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["kind"], "image")

    def test_embed_denies_private_file_of_other_user(self):
        # Bob's private image in Bob's OWN bucket — Alice has no access path.
        # (A file in Alice's bucket would be readable by her as bucket owner.)
        bob_bucket = Bucket.objects.create(name="Bob", slug="bob", owner=self.bob)
        img = VaultFile.objects.create(
            owner=self.bob,
            title="secret.png",
            file_type="image",
            is_public=False,
            bucket=bob_bucket,
            file=SimpleUploadedFile("secret.png", _TINY_PNG, content_type="image/png"),
        )
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": img.pk})
        self.assertEqual(res.status_code, 404)

    def test_embed_skips_encrypted_file(self):
        img = self._make_image()
        VaultFile.objects.filter(pk=img.pk).update(is_encrypted=True)
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": img.pk})
        self.assertEqual(res.status_code, 404)

    def test_embed_requires_login(self):
        img = self._make_image()
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": img.pk})
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url)

    def test_embed_bad_pk_is_400(self):
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": "abc"})
        self.assertEqual(res.status_code, 400)

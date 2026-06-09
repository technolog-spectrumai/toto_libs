"""Tests for the file-based presentation format + vault-wired viewer/editor."""

from __future__ import annotations

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
    def test_pml_extension_detected_as_presentation(self):
        # Extension-first: .pml → presentation regardless of mime.
        self.assertEqual(VaultFile.detect_type("", "talk.pml"), "presentation")
        self.assertEqual(
            VaultFile.detect_type("application/octet-stream", "deck.pml"), "presentation"
        )

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
        self.assertEqual(editor.get_editor_url(vf), reverse("memo:edit", args=[vf.pk]))

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

    def test_create_presentation_via_vault(self):
        self.client.force_login(self.alice)
        res = self.client.post(reverse("vault:create_file"), data={
            "title": "fresh.pml",
            "file_type": "presentation",
            "directory_id": str(self.directory.pk),
        })
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        vf = VaultFile.objects.get(pk=data["file_pk"])
        self.assertEqual(vf.file_type, "presentation")
        self.assertEqual(data["editor_url"], reverse("memo:edit", args=[vf.pk]))
        with vf.file.open("r") as f:
            content = f.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        p = pf.loads(content)
        self.assertEqual(len(p.slides), 1)

    def test_index_lists_presentations(self):
        self._make_presentation(is_public=True)
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:index"))
        self.assertEqual(res.status_code, 200)

    def test_create_new_presentation_redirects_to_editor(self):
        self.client.force_login(self.alice)
        res = self.client.post(reverse("memo:create"))
        self.assertEqual(res.status_code, 302)
        vf = VaultFile.objects.filter(owner=self.alice, file_type="presentation").latest("pk")
        self.assertEqual(res.url, reverse("memo:edit", args=[vf.pk]))
        # Created in the user's personal bucket with a valid blank deck.
        self.assertEqual(vf.bucket.slug, f"personal-{self.alice.username}")
        self.assertTrue(vf.title.endswith(".pml"))
        with vf.file.open("r") as f:
            content = f.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.assertEqual(len(pf.loads(content).slides), 1)

    def test_create_requires_login(self):
        res = self.client.post(reverse("memo:create"))
        self.assertEqual(res.status_code, 302)  # redirect to login
        self.assertIn("/login", res.url)

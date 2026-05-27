from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client, TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.transcription.models import (
    TranscriptAccessMode,
    TranscriptCollection,
    TranscriptEvent,
    TranscriptSegment,
    TranscriptSource,
    TranscriptionJob,
)
from toto.vault.models import Bucket, VaultFile

User = get_user_model()

# Counter so every test can produce unique bucket/file names without hitting unique constraints.
_counter = 0


def _uid():
    global _counter
    _counter += 1
    return _counter


def _platform():
    """Return the active Platform, creating one if needed."""
    p = Platform.objects.filter(active=True).first()
    if p:
        return p
    return Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)


def _make_bucket(owner, slug=None):
    uid = _uid()
    return Bucket.objects.create(name=f"Bucket-{uid}", slug=slug or f"bucket-{uid}", owner=owner)


def _make_vault_file(owner, bucket, name=None, file_type="audio"):
    uid = _uid()
    fname = name or f"test-{uid}.mp3"
    return VaultFile.objects.create(
        owner=owner, title=fname, bucket=bucket,
        file=ContentFile(b"data", name=fname),
        file_type=file_type, is_encrypted=False,
    )


def _make_collection(owner, slug=None, public=True):
    uid = _uid()
    mode = TranscriptAccessMode.PUBLIC if public else TranscriptAccessMode.PRIVATE
    return TranscriptCollection.objects.create(
        title=f"Collection {uid}", slug=slug or f"col-{uid}", access_mode=mode, owner=owner
    )


def _make_source(collection, vf, slug=None, status=TranscriptSource.Status.TRANSCRIBED):
    uid = _uid()
    return TranscriptSource.objects.create(
        collection=collection, source_file=vf,
        title=f"Source {uid}", slug=slug or f"src-{uid}", status=status,
    )


class HomeViewTests(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()
        self.url = reverse("transcription:home")

    def test_home_accessible_anonymous(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_home_contains_transcription_content(self):
        resp = self.client.get(self.url)
        self.assertIn(b"Transcri", resp.content)


class CollectionListViewTests(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()
        self.owner = User.objects.create_user(username="cl_owner", password="pw")
        bucket = _make_bucket(self.owner)
        self.public_col = _make_collection(self.owner, slug="cl-public", public=True)
        self.private_col = _make_collection(self.owner, slug="cl-private", public=False)
        self.url = reverse("transcription:collection_list")

    def test_anonymous_sees_public_collection(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_search_filters(self):
        resp = self.client.get(self.url, {"q": "cl-public"})
        self.assertEqual(resp.status_code, 200)


class CollectionDetailViewTests(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()
        self.owner = User.objects.create_user(username="cd_owner", password="pw")
        self.stranger = User.objects.create_user(username="cd_stranger", password="pw")
        bucket = _make_bucket(self.owner)
        vf = _make_vault_file(self.owner, bucket)
        self.public_col = _make_collection(self.owner, slug="cd-public", public=True)
        self.private_col = _make_collection(self.owner, slug="cd-private", public=False)
        _make_source(self.public_col, vf)

    def test_public_detail_accessible_anon(self):
        url = reverse("transcription:collection_detail", args=["cd-public"])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_private_detail_forbidden_for_stranger(self):
        self.client.force_login(self.stranger)
        url = reverse("transcription:collection_detail", args=["cd-private"])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_private_detail_accessible_for_owner(self):
        self.client.force_login(self.owner)
        url = reverse("transcription:collection_detail", args=["cd-private"])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)


class CollectionCreateViewTests(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()
        self.user = User.objects.create_user(username="cc_user", password="pw")
        self.url = reverse("transcription:collection_create")

    def test_create_requires_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)

    def test_create_renders_form(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_create_post_creates_collection(self):
        self.client.force_login(self.user)
        resp = self.client.post(self.url, {
            "title": "New Collection", "slug": "new-collection",
            "access_mode": "public", "allow_downloads": True, "position": 0,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(TranscriptCollection.objects.filter(slug="new-collection").exists())


class SourceDetailViewTests(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()
        self.owner = User.objects.create_user(username="sd_owner", password="pw")
        bucket = _make_bucket(self.owner)
        self.vf = _make_vault_file(self.owner, bucket)
        self.col = _make_collection(self.owner, slug="sd-col", public=True)
        self.source = _make_source(self.col, self.vf, slug="sd-src")

    def test_public_source_accessible_by_anon(self):
        url = reverse("transcription:source_detail", args=["sd-col", "sd-src"])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_draft_source_renders_with_access_denied(self):
        bucket2 = _make_bucket(self.owner)
        vf2 = _make_vault_file(self.owner, bucket2)
        draft = _make_source(self.col, vf2, slug="sd-draft", status=TranscriptSource.Status.DRAFT)
        url = reverse("transcription:source_detail", args=["sd-col", "sd-draft"])
        resp = self.client.get(url)
        # Page renders (200) but with access.allowed=False shown
        self.assertEqual(resp.status_code, 200)

    def test_404_for_nonexistent_source(self):
        url = reverse("transcription:source_detail", args=["sd-col", "nonexistent"])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)


class SourceManageViewTests(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()
        self.owner = User.objects.create_user(username="sm_owner", password="pw")
        self.stranger = User.objects.create_user(username="sm_stranger", password="pw")
        bucket = _make_bucket(self.owner)
        vf = _make_vault_file(self.owner, bucket)
        self.col = _make_collection(self.owner, slug="sm-col", public=False)
        self.source = _make_source(self.col, vf, slug="sm-src")
        self.url = reverse("transcription:source_manage", args=["sm-col", "sm-src"])

    def test_stranger_gets_403(self):
        self.client.force_login(self.stranger)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_owner_can_access(self):
        self.client.force_login(self.owner)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_unauthenticated_redirected(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)


class ExportViewTests(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()
        self.owner = User.objects.create_user(username="ex_owner", password="pw")
        bucket = _make_bucket(self.owner)
        vf = _make_vault_file(self.owner, bucket)
        col = _make_collection(self.owner, slug="ex-col", public=True)
        self.source = _make_source(col, vf, slug="ex-src")
        job = TranscriptionJob.objects.create(source=self.source, status=TranscriptionJob.Status.SUCCESS)
        TranscriptSegment.objects.create(
            job=job, source=self.source, index=1, start_ms=0, end_ms=3000, text="Hello export."
        )

    def _url(self, kind):
        return reverse("transcription:source_export", args=["ex-col", "ex-src", kind])

    def test_export_txt(self):
        resp = self.client.get(self._url("txt"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Hello export.", resp.content)

    def test_export_srt(self):
        resp = self.client.get(self._url("srt"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Hello export.", resp.content)

    def test_export_vtt(self):
        resp = self.client.get(self._url("vtt"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"WEBVTT", resp.content)

    def test_export_json(self):
        resp = self.client.get(self._url("json"))
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["source"]["slug"], "ex-src")


class SourceEventApiTests(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()
        self.owner = User.objects.create_user(username="ev_owner", password="pw")
        bucket = _make_bucket(self.owner)
        vf = _make_vault_file(self.owner, bucket)
        col = _make_collection(self.owner, slug="ev-col", public=True)
        self.source = _make_source(col, vf, slug="ev-src")
        self.url = reverse("transcription:source_event_api", args=["ev-col", "ev-src"])

    def test_post_records_event(self):
        resp = self.client.post(self.url, {"event": "play", "seconds_played": "10"})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        self.assertIn("event_uid", data)

    def test_get_not_allowed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_private_source_returns_403_for_anon(self):
        owner2 = User.objects.create_user(username="ev_owner2", password="pw")
        bucket2 = _make_bucket(owner2)
        vf2 = _make_vault_file(owner2, bucket2)
        priv_col = _make_collection(owner2, public=False)
        priv_src = _make_source(priv_col, vf2, status=TranscriptSource.Status.TRANSCRIBED)
        url = reverse("transcription:source_event_api", args=[priv_col.slug, priv_src.slug])
        resp = self.client.post(url, {"event": "play"})
        self.assertEqual(resp.status_code, 403)

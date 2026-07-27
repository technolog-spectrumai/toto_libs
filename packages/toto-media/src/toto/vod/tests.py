from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.models import Bucket, VaultFile

User = get_user_model()


def _make_user(username="user", **kwargs):
    return User.objects.create_user(username=username, password="pass", **kwargs)


def _make_bucket(owner, slug="test-bucket"):
    return Bucket.objects.create(name=slug, slug=slug, owner=owner)


def _make_vault_file(owner, bucket, *, file_type="video", is_public=True, is_encrypted=False, title="test-video.mp4"):
    vf = VaultFile(
        owner=owner,
        title=title,
        bucket=bucket,
        file_type=file_type,
        is_public=is_public,
        is_encrypted=is_encrypted,
    )
    vf.file.save(title, ContentFile(b"fake"), save=False)
    vf.file_size_bytes = 4
    vf.save()
    return vf


# ---------------------------------------------------------------------------
# vault_file_play view
# ---------------------------------------------------------------------------

class VaultFilePlayViewTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.owner = _make_user("owner")
        self.other = _make_user("other")
        self.bucket = _make_bucket(self.owner)

    def _url(self, file_pk):
        return reverse("vod:vault_file_play", args=[file_pk])

    def test_public_video_returns_200_for_anonymous(self):
        vf = _make_vault_file(self.owner, self.bucket, is_public=True)
        resp = self.client.get(self._url(vf.pk))
        self.assertEqual(resp.status_code, 200)

    def test_public_video_returns_200_for_authenticated_user(self):
        vf = _make_vault_file(self.owner, self.bucket, is_public=True)
        self.client.force_login(self.other)
        resp = self.client.get(self._url(vf.pk))
        self.assertEqual(resp.status_code, 200)

    def test_private_video_owner_can_access(self):
        vf = _make_vault_file(self.owner, self.bucket, is_public=False)
        self.client.force_login(self.owner)
        resp = self.client.get(self._url(vf.pk))
        self.assertEqual(resp.status_code, 200)

    def test_private_video_other_user_gets_403(self):
        vf = _make_vault_file(self.owner, self.bucket, is_public=False)
        self.client.force_login(self.other)
        resp = self.client.get(self._url(vf.pk))
        self.assertEqual(resp.status_code, 403)

    def test_private_video_anonymous_redirects_to_login(self):
        vf = _make_vault_file(self.owner, self.bucket, is_public=False)
        resp = self.client.get(self._url(vf.pk))
        self.assertIn(resp.status_code, [302, 301])
        self.assertIn("/login", resp["Location"] if hasattr(resp, "Location") else resp.url)

    def test_encrypted_video_returns_403(self):
        vf = _make_vault_file(self.owner, self.bucket, is_public=True, is_encrypted=True)
        resp = self.client.get(self._url(vf.pk))
        self.assertEqual(resp.status_code, 403)

    def test_nonexistent_file_returns_404(self):
        resp = self.client.get(self._url(999999))
        self.assertEqual(resp.status_code, 404)

    def test_response_uses_vault_file_play_template(self):
        vf = _make_vault_file(self.owner, self.bucket, is_public=True)
        resp = self.client.get(self._url(vf.pk))
        self.assertTemplateUsed(resp, "vod/vault_file_play.html")

    def test_context_contains_vault_file(self):
        vf = _make_vault_file(self.owner, self.bucket, is_public=True)
        resp = self.client.get(self._url(vf.pk))
        self.assertEqual(resp.context["vault_file"].pk, vf.pk)

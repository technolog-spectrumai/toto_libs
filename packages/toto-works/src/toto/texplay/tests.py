from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.models import Bucket, VaultFile
from toto.vault.plugins import VaultPlayPlugin

from .models import TexPlayJob

User = get_user_model()


def _make_user(username="texplay_user"):
    return User.objects.create_user(username=username, password="pass")


def _make_bucket(owner, slug="texplay-bucket"):
    return Bucket.objects.create(name=slug, slug=slug, owner=owner)


def _make_tex_file(owner, bucket, is_public=True, title="hello.tex"):
    vf = VaultFile(
        owner=owner,
        title=title,
        bucket=bucket,
        file_type="latex",
        is_public=is_public,
    )
    vf.file.save(title, ContentFile(b"\\documentclass{article}\\begin{document}Hi\\end{document}"), save=False)
    vf.file_size_bytes = 50
    vf.save()
    return vf


# ---------------------------------------------------------------------------
# Plugin registry
# ---------------------------------------------------------------------------

class LatexPluginRegistryTests(TestCase):
    def test_latex_plugin_registered(self):
        plugin = VaultPlayPlugin.for_file_type("latex")
        self.assertIsNotNone(plugin)

    def test_latex_plugin_url_contains_file_pk(self):
        user = _make_user()
        bucket = _make_bucket(user)
        vf = _make_tex_file(user, bucket)
        plugin = VaultPlayPlugin.for_file_type("latex")
        url = plugin.get_play_url(vf)
        self.assertIn(str(vf.pk), url)
        self.assertIn("texplay", url)

    def test_no_interference_with_video_plugin(self):
        self.assertIsNone(VaultPlayPlugin.for_file_type("pdf"))
        self.assertIsNotNone(VaultPlayPlugin.for_file_type("latex"))


# ---------------------------------------------------------------------------
# latex_play view
# ---------------------------------------------------------------------------

class LatexPlayViewTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.owner = _make_user("tex_owner")
        self.other = _make_user("tex_other")
        self.bucket = _make_bucket(self.owner)

    def _url(self, file_pk):
        return reverse("texplay:latex_play", args=[file_pk])

    def test_public_file_accessible_anonymous(self):
        vf = _make_tex_file(self.owner, self.bucket, is_public=True)
        resp = self.client.get(self._url(vf.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "texplay/latex_play.html")

    def test_private_file_owner_can_access(self):
        vf = _make_tex_file(self.owner, self.bucket, is_public=False)
        self.client.force_login(self.owner)
        resp = self.client.get(self._url(vf.pk))
        self.assertEqual(resp.status_code, 200)

    def test_private_file_other_user_gets_403(self):
        vf = _make_tex_file(self.owner, self.bucket, is_public=False)
        self.client.force_login(self.other)
        resp = self.client.get(self._url(vf.pk))
        self.assertEqual(resp.status_code, 403)

    def test_encrypted_file_gets_403(self):
        vf = _make_tex_file(self.owner, self.bucket)
        vf.is_encrypted = True
        vf.save(update_fields=["is_encrypted"])
        resp = self.client.get(self._url(vf.pk))
        self.assertEqual(resp.status_code, 403)

    def test_nonexistent_file_gets_404(self):
        resp = self.client.get(self._url(999999))
        self.assertEqual(resp.status_code, 404)

    def test_context_contains_vault_file(self):
        vf = _make_tex_file(self.owner, self.bucket, is_public=True)
        resp = self.client.get(self._url(vf.pk))
        self.assertEqual(resp.context["vault_file"].pk, vf.pk)


# ---------------------------------------------------------------------------
# latex_compile view
# ---------------------------------------------------------------------------

class LatexCompileViewTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.owner = _make_user("tex_owner2")
        self.other = _make_user("tex_other2")
        self.bucket = _make_bucket(self.owner, slug="tex-compile-bucket")

    def _compile_url(self, file_pk):
        return reverse("texplay:latex_compile", args=[file_pk])

    def test_unauthenticated_user_redirected(self):
        vf = _make_tex_file(self.owner, self.bucket)
        resp = self.client.post(self._compile_url(vf.pk))
        self.assertIn(resp.status_code, [302, 403])

    def test_non_owner_gets_403(self):
        vf = _make_tex_file(self.owner, self.bucket)
        self.client.force_login(self.other)
        resp = self.client.post(self._compile_url(vf.pk))
        self.assertEqual(resp.status_code, 403)

    def test_owner_creates_job(self):
        vf = _make_tex_file(self.owner, self.bucket)
        self.client.force_login(self.owner)
        resp = self.client.post(self._compile_url(vf.pk))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertTrue(TexPlayJob.objects.filter(vault_file=vf).exists())

    def test_compile_response_has_job_pk(self):
        vf = _make_tex_file(self.owner, self.bucket)
        self.client.force_login(self.owner)
        resp = self.client.post(self._compile_url(vf.pk))
        data = resp.json()
        self.assertIn("job_pk", data)
        self.assertIn("status", data)
        self.assertIn("status_url_prefix", data) if "status_url_prefix" in data else None


# ---------------------------------------------------------------------------
# latex_job_status view
# ---------------------------------------------------------------------------

class LatexJobStatusViewTests(TestCase):
    def setUp(self):
        self.owner = _make_user("tex_status_user")
        self.bucket = _make_bucket(self.owner, slug="tex-status-bucket")

    def test_status_returns_json(self):
        vf = _make_tex_file(self.owner, self.bucket)
        job = TexPlayJob.objects.create(vault_file=vf, status=TexPlayJob.Status.PENDING)
        resp = self.client.get(reverse("texplay:latex_job_status", args=[job.pk]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "pending")
        self.assertFalse(data["finished"])

    def test_success_job_returns_finished_true(self):
        vf = _make_tex_file(self.owner, self.bucket)
        job = TexPlayJob.objects.create(vault_file=vf, status=TexPlayJob.Status.SUCCESS)
        resp = self.client.get(reverse("texplay:latex_job_status", args=[job.pk]))
        data = resp.json()
        self.assertTrue(data["finished"])

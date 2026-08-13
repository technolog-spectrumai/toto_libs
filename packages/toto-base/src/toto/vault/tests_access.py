"""Who may read a vault file — the rule, and the three doors that forgot it.

Every test here corresponds to a real hole that was open:

* the canonical download URL served any private file to any logged-in account;
* nginx served every stored byte to nobody in particular;
* the file-service runner ran ffmpeg on files the caller could not read.

They are one module because they are one rule, and splitting them is how a
fourth door gets a fourth rule.
"""

import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.vault.access import may_read
from toto.vault.models import Bucket, VaultDirectory, VaultFile

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-access-"))
class MayReadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.stranger = User.objects.create_user("stranger", password="pw")
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)

    def _file(self, *, public=False, bucket=None, directory=None, title="secret.txt"):
        vault_file = VaultFile(owner=self.owner, title=title, file_type="text",
                               is_public=public, bucket=bucket or self.bucket,
                               directory=directory)
        vault_file.file.save(title, ContentFile(b"the contents"), save=False)
        vault_file.save()
        return vault_file

    def test_the_owner_reads_their_own(self):
        self.assertTrue(may_read(self.owner, self._file()))

    def test_a_stranger_does_not(self):
        self.assertFalse(may_read(self.stranger, self._file()))

    def test_public_is_public_to_everyone_including_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        vault_file = self._file(public=True)
        self.assertTrue(may_read(self.stranger, vault_file))
        self.assertTrue(may_read(AnonymousUser(), vault_file))

    def test_anonymous_gets_the_public_arm_and_nothing_else(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(may_read(AnonymousUser(), self._file()))

    def test_a_superuser_reads_anything(self):
        self.assertTrue(may_read(self.root, self._file()))

    def test_a_bucket_owner_reads_what_is_in_their_bucket(self):
        theirs = Bucket.objects.create(name="Theirs", slug="theirs",
                                       owner=self.stranger)
        self.assertTrue(may_read(self.stranger, self._file(bucket=theirs)))

    def test_a_directory_acl_grants_it(self):
        directory = VaultDirectory.objects.create(name="shared",
                                                  bucket=self.bucket,
                                                  owner=self.owner)
        vault_file = self._file(directory=directory)
        self.assertFalse(may_read(self.stranger, vault_file))

        directory.allowed_users.add(self.stranger)
        self.assertTrue(may_read(self.stranger, vault_file))

    def test_none_is_not_readable(self):
        self.assertFalse(may_read(self.owner, None))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-download-"))
class DownloadViewTests(TestCase):
    """The canonical download URL — the one get_public_url hands out."""

    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.stranger = User.objects.create_user("stranger", password="pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)

    def _file(self, *, public=False, key="payroll"):
        vault_file = VaultFile(owner=self.owner, title="payroll.txt", key=key,
                               file_type="text", is_public=public,
                               bucket=self.bucket)
        vault_file.file.save("payroll.txt", ContentFile(b"salaries"), save=False)
        vault_file.save()
        return vault_file

    def _url(self, vault_file):
        return reverse("vault:public_file",
                       args=[vault_file.bucket.slug, vault_file.key])

    def test_a_stranger_cannot_download_a_private_file(self):
        """The hole. Bucket slugs are listed on the metrics pages and a key is
        the slug of a title, so both halves of this URL are guessable."""
        vault_file = self._file()
        self.client.force_login(self.stranger)

        response = self.client.get(self._url(vault_file))

        self.assertEqual(response.status_code, 404)

    def test_the_refusal_does_not_confirm_the_file_exists(self):
        """404 and not 403 — otherwise this is an enumeration oracle."""
        self._file(key="realfile")
        self.client.force_login(self.stranger)

        real = self.client.get(reverse("vault:public_file", args=["owned", "realfile"]))
        fake = self.client.get(reverse("vault:public_file", args=["owned", "nofile"]))

        self.assertEqual(real.status_code, fake.status_code)

    def test_the_owner_still_downloads_it(self):
        vault_file = self._file()
        self.client.force_login(self.owner)

        response = self.client.get(self._url(vault_file))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"salaries")

    def test_a_public_file_needs_no_login(self):
        vault_file = self._file(public=True, key="poster")

        response = self.client.get(self._url(vault_file))

        self.assertEqual(response.status_code, 200)

    def test_anonymous_is_told_to_log_in_rather_than_404(self):
        """Here the caller already has a link, so existence is not the secret —
        and "log in" is the actionable answer."""
        vault_file = self._file()

        response = self.client.get(self._url(vault_file))

        self.assertEqual(response.status_code, 403)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-storage-"))
class PrivateStorageTests(TestCase):
    """Vault bytes are not web-served, and that is structural now."""

    def setUp(self):
        self.owner = User.objects.create_user("keeper", password="pw")
        self.bucket = Bucket.objects.create(name="B", slug="b", owner=self.owner)

    def _file(self):
        vault_file = VaultFile(owner=self.owner, title="private.txt",
                               file_type="text", bucket=self.bucket)
        vault_file.file.save("private.txt", ContentFile(b"x"), save=False)
        vault_file.save()
        return vault_file

    def test_a_vault_file_has_no_public_url_at_all(self):
        """The whole point: a leak now fails loudly where somebody writes it,
        instead of quietly working. /media/vault/files/<name> used to be served
        by nginx with no auth and a public cache header."""
        vault_file = self._file()

        with self.assertRaises(ValueError):
            vault_file.file.url  # noqa: B018 - the raise IS the assertion

    def test_the_bytes_are_still_readable_through_the_model(self):
        vault_file = self._file()

        with vault_file.file.open("rb") as handle:
            self.assertEqual(handle.read(), b"x")

    def test_get_public_url_is_the_authorized_door(self):
        vault_file = self._file()
        vault_file.key = "private"
        vault_file.save(update_fields=["key"])

        self.assertEqual(vault_file.get_public_url(),
                         reverse("vault:public_file", args=["b", "private"]))

    def test_nginx_does_not_serve_the_vault_tree(self):
        """The other half of the fix. The storage stops anything MINTING a
        /media/vault/ link; this stops nginx honouring one already in a
        bookmark."""
        from pathlib import Path

        template = Path(__file__).resolve()
        for _ in range(6):
            template = template.parent
        conf = (template / "scripts" / "nginx.conf.j2")
        if not conf.exists():
            self.skipTest("nginx template not in this tree")
        body = conf.read_text()
        self.assertIn("location /media/vault/", body)
        self.assertIn("return 404", body.split("location /media/vault/")[1][:120])

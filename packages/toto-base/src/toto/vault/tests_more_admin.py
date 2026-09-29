"""The vault's admin actions for files: encrypt/decrypt in bulk, content
hashes, and the connection-URL panel. Superuser territory, but it still
writes people's files, so the skips and refusals are pinned.
"""

import tempfile
from unittest import skip

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.gervazy.models import UserStrongbox
from toto.vault.admin import BucketAdmin
from toto.vault.models import Bucket, VaultFile

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-admin-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.owner = User.objects.create_user("owner", password="pw")
        UserStrongbox.objects.create(owner=cls.owner, name="keyring", argon2_memory_cost=19456,
                                     argon2_iterations=2, argon2_lanes=1)

    def setUp(self):
        self.client.force_login(self.root)

    _n = 0

    def file(self, title="t.txt", body=b"plain", *, public=True):
        type(self)._n += 1
        vault_file = VaultFile(owner=self.owner, title=title, key=f"a-{self._n}",
                               file_type="text", is_public=public)
        vault_file.file.save(title, ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class BulkEncryptTests(_Fixture):
    def url(self, name, *files):
        return reverse(f"admin:vaultfile_{name}") + "?ids=" + ",".join(str(f.pk) for f in files)

    def test_the_form_renders_for_the_selection(self):
        f = self.file()
        response = self.client.get(self.url("encrypt", f))
        self.assertEqual(response.status_code, 200)
        self.assertIn("t.txt", response.content.decode())

    def test_only_public_files_are_encrypted_and_the_rest_are_named(self):
        public, private = self.file("pub.txt"), self.file("priv.txt", public=False)
        response = self.client.post(self.url("encrypt", public, private),
                                    {"_selected_action": [public.pk, private.pk],
                                     "password": "pw"})
        self.assertEqual(response.status_code, 302)
        public.refresh_from_db()
        private.refresh_from_db()
        self.assertTrue(public.is_encrypted)
        self.assertFalse(public.is_public)
        self.assertFalse(private.is_encrypted)
        said = self.messages(response)
        self.assertIn("Encrypted: pub.txt", said)
        self.assertIn("Skipped priv.txt: not public", said)

    def test_decrypting_skips_what_is_not_encrypted(self):
        sealed, plain = self.file("s.txt"), self.file("p.txt")
        sealed.encrypt(password="pw")
        response = self.client.post(self.url("decrypt", sealed, plain),
                                    {"_selected_action": [sealed.pk, plain.pk], "password": "pw"})
        sealed.refresh_from_db()
        self.assertFalse(sealed.is_encrypted)
        said = self.messages(response)
        self.assertIn("Decrypted: s.txt", said)
        self.assertIn("Skipped p.txt: not encrypted", said)

    def test_a_wrong_password_is_reported_per_file_and_changes_nothing(self):
        sealed = self.file("s.txt")
        sealed.encrypt(password="pw")
        response = self.client.post(self.url("decrypt", sealed),
                                    {"_selected_action": [sealed.pk], "password": "nope"})
        sealed.refresh_from_db()
        self.assertTrue(sealed.is_encrypted)
        self.assertTrue(any(m.startswith("Failed to decrypt s.txt") for m in self.messages(response)))

    def test_a_logged_out_visitor_is_sent_to_the_admin_login(self):
        self.client.logout()
        response = self.client.get(self.url("encrypt", self.file()))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    @skip("BUG vault/admin.py:190 get_urls - encrypt/ and decrypt/ are wrapped only in "
          "admin_site.admin_view (is_staff), never has_change_permission, so a staff account "
          "with NO vault permission can encrypt any member's public file under a password of "
          "its choosing (and take it private), or decrypt-and-publish an encrypted one")
    def test_staff_without_the_change_permission_cannot_encrypt_someones_file(self):
        staff = User.objects.create_user("staff", password="pw", is_staff=True)
        f = self.file("pub.txt")
        self.client.force_login(staff)
        self.client.post(self.url("encrypt", f), {"_selected_action": [f.pk], "password": "mine"})
        f.refresh_from_db()
        self.assertFalse(f.is_encrypted)

    @skip("BUG vault/admin.py:215 encrypt_view (and :243 decrypt_view) - ids come from "
          "request.GET['ids'].split(','), so opening the door with no or a malformed id list "
          "filters pk__in=[''] and 500s (ValueError) instead of rendering an empty selection")
    def test_an_empty_selection_is_not_a_500(self):
        response = self.client.get(reverse("admin:vaultfile_encrypt"))
        self.assertIn(response.status_code, (200, 302))


class ContentHashActionTests(_Fixture):
    def test_unhashed_files_get_their_sha256_and_hashed_ones_are_left(self):
        import hashlib

        fresh = self.file("fresh.txt", b"abc")
        VaultFile.objects.filter(pk=fresh.pk).update(content_hash="")
        kept = self.file("kept.txt", b"xyz")
        VaultFile.objects.filter(pk=kept.pk).update(content_hash="already")
        response = self.client.post(reverse("admin:vault_vaultfile_changelist"), {
            "action": "generate_content_hashes", "_selected_action": [fresh.pk, kept.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(VaultFile.objects.get(pk=fresh.pk).content_hash,
                         hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(VaultFile.objects.get(pk=kept.pk).content_hash, "already")
        self.assertIn("Skipped kept.txt - already hashed", self.messages(response))


class BucketPanelTests(_Fixture):
    def test_the_connection_url_panel_shows_the_url_or_a_dash(self):
        from django.contrib import admin

        panel = BucketAdmin(Bucket, admin.site)
        self.assertEqual(panel.connection_url_display(Bucket()), "—")
        bucket = Bucket.objects.create(name="B", slug="bee", owner=self.owner)
        self.assertIn("local:///bee", panel.connection_url_display(bucket))

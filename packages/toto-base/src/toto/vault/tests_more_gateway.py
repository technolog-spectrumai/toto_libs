"""Gateway uploads: the page that offers a folder for uploads and the
endpoint that takes them — the allow list, the target-folder choice, the
per-file limits and the rule that one bad file never costs the batch.
"""

import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.models import (Bucket, FileGateway, VaultDirectory, VaultFile,
                               VaultUsageEvent)

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-gateway-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.keeper = User.objects.create_user("keeper", password="pw")
        cls.guest = User.objects.create_user("guest", password="pw")
        cls.outsider = User.objects.create_user("outsider", password="pw")
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.bucket = Bucket.objects.create(name="Inbox", slug="inbox", owner=cls.keeper)
        cls.inbox = VaultDirectory.objects.create(name="Inbox", bucket=cls.bucket,
                                                  owner=cls.keeper)
        cls.sub = VaultDirectory.objects.create(name="Sub", bucket=cls.bucket,
                                                owner=cls.keeper, parent=cls.inbox)
        cls.elsewhere = Bucket.objects.create(name="Else", slug="else", owner=cls.keeper)
        cls.foreign_dir = VaultDirectory.objects.create(name="Far", bucket=cls.elsewhere,
                                                        owner=cls.keeper)
        cls.gateway = FileGateway.objects.create(directory=cls.inbox, name="Drop box",
                                                 max_file_size=1)          # KB

    def upload_url(self, directory=None):
        return reverse("vault:gateway_upload", kwargs={"dir_pk": (directory or self.inbox).pk})

    def page_url(self, directory=None):
        return reverse("vault:gateway_page", kwargs={"dir_pk": (directory or self.inbox).pk})

    def upload(self, *files, user=None, **fields):
        self.client.force_login(user or self.guest)
        return self.client.post(self.upload_url(), {"file": list(files), **fields})


class GatewaySaveTests(_Fixture):
    def test_the_gateway_takes_its_bucket_from_its_folder(self):
        gw = FileGateway(directory=self.foreign_dir, name="x", bucket=self.bucket)
        gw.save()
        self.assertEqual(gw.bucket, self.elsewhere)


class GatewayPageTests(_Fixture):
    def test_anyone_logged_in_may_use_a_gateway_with_no_allow_list(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self.page_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["target_dir_path"], "Inbox")
        self.assertEqual(response.context["target_dir_id"], self.inbox.pk)

    def test_a_logged_out_visitor_is_sent_to_log_in(self):
        response = self.client.get(self.page_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_folder_with_no_gateway_is_404(self):
        self.client.force_login(self.guest)
        self.assertEqual(self.client.get(self.page_url(self.sub)).status_code, 404)

    def test_an_allow_list_keeps_everyone_else_out_but_a_superuser(self):
        self.gateway.allowed_users.add(self.guest)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.page_url()).status_code, 403)
        self.client.force_login(self.guest)
        self.assertEqual(self.client.get(self.page_url()).status_code, 200)
        self.client.force_login(self.root)
        self.assertEqual(self.client.get(self.page_url()).status_code, 200)

    def test_a_subfolder_of_the_same_bucket_can_be_targeted(self):
        self.client.force_login(self.guest)
        response = self.client.get(self.page_url() + f"?target_dir={self.sub.pk}")
        self.assertEqual(response.context["target_dir_path"], "Inbox/Sub")
        self.assertEqual(response.context["target_dir_id"], self.sub.pk)

    def test_a_folder_in_another_bucket_or_garbage_falls_back_to_the_gateways_own(self):
        self.client.force_login(self.guest)
        for value in (self.foreign_dir.pk, "abc", "99999"):
            response = self.client.get(self.page_url() + f"?target_dir={value}")
            self.assertEqual(response.context["target_dir_id"], self.inbox.pk, value)

    def test_recent_uploads_are_only_mine_and_only_this_folder(self):
        def make(owner, directory, title):
            return VaultFile.objects.create(owner=owner, title=title, key=title.replace(".", "-"),
                                            file_type="text", bucket=self.bucket,
                                            directory=directory,
                                            file=SimpleUploadedFile(title, b"x"))

        make(self.guest, self.inbox, "mine.txt")
        make(self.outsider, self.inbox, "theirs.txt")
        make(self.guest, self.sub, "deeper.txt")
        self.client.force_login(self.guest)
        rows = self.client.get(self.page_url()).context["recent_uploads_list"]
        self.assertEqual([r["title"] for r in rows], ["mine.txt"])
        self.assertEqual(rows[0]["location"], "Inbox")


class GatewayUploadTests(_Fixture):
    def test_the_file_is_the_uploaders_in_the_gateways_folder(self):
        response = self.upload(SimpleUploadedFile("note.txt", b"hello"))
        self.assertEqual(response.status_code, 200)
        result = response.json()["results"][0]
        self.assertEqual(result["location"], "Inbox")
        self.assertEqual(result["bucket"], "inbox")
        self.assertFalse(result["public"])
        vault_file = VaultFile.objects.get(title="note.txt")
        self.assertEqual(vault_file.owner, self.guest)
        self.assertEqual(vault_file.directory, self.inbox)
        self.assertEqual(vault_file.file_size_bytes, 5)

    def test_someone_off_the_allow_list_stores_nothing(self):
        self.gateway.allowed_users.add(self.guest)
        response = self.upload(SimpleUploadedFile("x.txt", b"x"), user=self.outsider)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "You are not allowed to use this gateway")
        self.assertFalse(VaultFile.objects.exists())

    def test_a_superuser_is_never_off_the_list(self):
        self.gateway.allowed_users.add(self.guest)
        self.assertEqual(self.upload(SimpleUploadedFile("x.txt", b"x"),
                                     user=self.root).status_code, 200)

    def test_a_subfolder_target_lands_there_and_says_where(self):
        response = self.upload(SimpleUploadedFile("deep.txt", b"x"),
                               target_directory_id=str(self.sub.pk))
        self.assertEqual(response.json()["results"][0]["location"], "Inbox/Sub")
        self.assertEqual(VaultFile.objects.get(title="deep.txt").directory, self.sub)

    def test_a_target_in_another_bucket_is_refused_before_anything_is_stored(self):
        for value in (str(self.foreign_dir.pk), "abc"):
            response = self.upload(SimpleUploadedFile("x.txt", b"x"),
                                   target_directory_id=value)
            self.assertEqual(response.status_code, 400, value)
            self.assertEqual(response.json()["error"], "Invalid target directory.")
        self.assertFalse(VaultFile.objects.exists())

    def test_the_size_limit_is_inclusive(self):
        response = self.upload(SimpleUploadedFile("edge.txt", b"x" * 1024),
                               SimpleUploadedFile("over.txt", b"x" * 1025))
        self.assertEqual([r["title"] for r in response.json()["results"]], ["edge.txt"])
        [error] = response.json()["errors"]
        self.assertTrue(error.startswith("over.txt: too large"))

    def test_a_chosen_type_wins_over_detection_when_it_is_a_real_type(self):
        self.upload(SimpleUploadedFile("readme.txt", b"# hi"), file_type="markdown")
        self.assertEqual(VaultFile.objects.get(title="readme.txt").file_type, "markdown")

    def test_a_made_up_type_falls_back_to_detection(self):
        self.upload(SimpleUploadedFile("notes.md", b"# hi"), file_type="executable")
        self.assertEqual(VaultFile.objects.get(title="notes.md").file_type, "markdown")

    @override_settings(VAULT_REFUSED_FILE_TYPES={"markdown"})
    def test_a_chosen_type_the_host_refuses_is_refused(self):
        response = self.upload(SimpleUploadedFile("readme.txt", b"# hi"), file_type="markdown")
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not accept markdown files", response.json()["errors"][0])

    def test_a_public_gateway_publishes_what_it_takes(self):
        FileGateway.objects.filter(pk=self.gateway.pk).update(make_public=True)
        response = self.upload(SimpleUploadedFile("flyer.txt", b"x"))
        result = response.json()["results"][0]
        self.assertTrue(result["public"])
        vault_file = VaultFile.objects.get(title="flyer.txt")
        self.assertTrue(vault_file.is_public)
        self.assertEqual(result["public_url"], vault_file.get_public_url())

    def test_a_storage_failure_on_one_file_is_that_files_error(self):
        from toto.vault import storage_backends

        real = storage_backends.persist_upload

        def flaky(vault_file, uploaded):
            if uploaded.name == "bad.txt":
                raise OSError("disk full")
            return real(vault_file, uploaded)

        with patch.object(storage_backends, "persist_upload", side_effect=flaky):
            response = self.upload(SimpleUploadedFile("bad.txt", b"x"),
                                   SimpleUploadedFile("good.txt", b"y"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["title"] for r in response.json()["results"]], ["good.txt"])
        self.assertEqual(response.json()["errors"], ["bad.txt: disk full"])
        self.assertEqual(list(VaultFile.objects.values_list("title", flat=True)), ["good.txt"])

    def test_every_stored_file_is_metered_once_on_the_uploaders_meter(self):
        self.upload(SimpleUploadedFile("a.txt", b"aa"), SimpleUploadedFile("b.txt", b"bb"))
        requests = VaultUsageEvent.objects.filter(metric_code="storage.request")
        self.assertEqual(requests.count(), 2)
        self.assertEqual(set(requests.values_list("user", flat=True)), {self.guest.pk})
        pks = set(VaultFile.objects.values_list("pk", flat=True))
        self.assertEqual({int(e.source_id) for e in requests}, pks)
        self.assertEqual({e.idempotency_key for e in requests},
                         {f"vault.upload.request:{pk}" for pk in pks})
        self.assertEqual(VaultUsageEvent.objects.filter(metric_code="storage.transfer_mb").count(), 2)

    def test_a_refused_file_is_never_metered(self):
        self.upload(SimpleUploadedFile("big.txt", b"x" * 4096))
        self.assertFalse(VaultUsageEvent.objects.exists())

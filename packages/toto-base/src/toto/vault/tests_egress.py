"""Bytes OUT: storage.egress_mb at every Django-served download door.

The asymmetry this closes: ``storage.transfer_mb`` counted bytes IN at
three doors while the download paths counted nothing. Egress is now
measured at all four serving doors (public, peer, API, encrypted) and
billed — as a number, never as money — to the file's OWNER, the only
subject that exists when the downloader is anonymous. Cap-only by design:
no default_limit, so nothing is refused until a host creates a policy row,
and a Tariff price on this code deliberately prices nothing.

What nginx serves straight from disk (/media/) never reaches Django and is
honestly uncounted — the same class of limit as gitea pushes.
"""
import json
import tempfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Bucket, VaultFile, VaultQuotaPolicy, VaultUsageEvent

User = get_user_model()

EGRESS = "storage.egress_mb"


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class EgressTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="x")
        cls.bucket = Bucket.objects.create(
            name="served", slug="served", owner=cls.owner)

    def _file(self, key="doc", content=b"x" * 2048, public=False):
        vf = VaultFile(owner=self.owner, title=f"{key}.txt", key=key,
                       file_type="text", bucket=self.bucket, is_public=public)
        vf.file.save(f"{key}.txt", SimpleUploadedFile(f"{key}.txt", content),
                     save=True)
        vf.file_size_bytes = len(content)
        vf.save(update_fields=["file_size_bytes"])
        return vf

    def _events(self):
        return VaultUsageEvent.objects.filter(metric_code=EGRESS)


class PublicDoorTests(EgressTestCase):
    def _get(self, vf):
        return self.client.get(reverse("vault:public_file",
                                       args=[self.bucket.slug, vf.key]))

    def test_a_download_writes_one_event_on_the_owners_meter(self):
        vf = self._file(public=True)
        self.client.force_login(self.owner)
        self.assertEqual(self._get(vf).status_code, 200)
        event = self._events().get()
        self.assertEqual(event.user, self.owner)
        self.assertEqual(event.quantity * (2 ** 20), 2048)

    def test_an_anonymous_download_still_bills_the_owner(self):
        # The downloader has no identity; the owner is the only subject
        # there is — the same rule the peer upload door applies.
        vf = self._file(public=True)
        self.assertEqual(self._get(vf).status_code, 200)
        self.assertEqual(self._events().get().user, self.owner)

    def test_a_block_policy_refuses_with_429_and_records_nothing(self):
        VaultQuotaPolicy.objects.create(metric_code=EGRESS, limit=0)
        vf = self._file(public=True)
        response = self._get(vf)
        self.assertEqual(response.status_code, 429)
        self.assertIn("quota exceeded", response.content.decode())
        self.assertEqual(self._events().count(), 0)

    def test_no_policy_means_measured_never_refused(self):
        vf = self._file(public=True)
        for _ in range(3):
            self.assertEqual(self._get(vf).status_code, 200)
        self.assertEqual(self._events().count(), 3)

    def test_a_frozen_owner_still_reads(self):
        # The arrears freeze stops new metered WORK. A download is a read,
        # and the arrears promise is explicit: nothing is deleted and
        # nothing refuses to be read. The freeze is waved through.
        vf = self._file(public=True)
        self.client.force_login(self.owner)
        with mock.patch("toto.quota.levies.user_is_frozen",
                        return_value=True):
            response = self._get(vf)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._events().count(), 1)

    def test_a_dead_backend_serves_nothing_and_records_nothing(self):
        vf = self._file(public=True)
        with mock.patch("toto.vault.views._storage_backends."
                        "open_file_stream", side_effect=OSError("gone")):
            response = self._get(vf)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self._events().count(), 0)

    def test_a_zero_size_row_records_no_event(self):
        vf = self._file(public=True)
        VaultFile.objects.filter(pk=vf.pk).update(file_size_bytes=0)
        self.assertEqual(self._get(vf).status_code, 200)
        self.assertEqual(self._events().count(), 0)


class ApiDoorTests(EgressTestCase):
    def test_the_api_download_is_metered_and_cappable(self):
        vf = self._file()
        self.client.force_login(self.owner)
        url = reverse("vault:api_file_download", args=[vf.key])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self._events().count(), 1)

        VaultQuotaPolicy.objects.create(metric_code=EGRESS, limit="0.001")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 429)
        self.assertIn("quota exceeded", json.loads(response.content)["error"])
        self.assertEqual(self._events().count(), 1)


class EncryptedDoorTests(EgressTestCase):
    def _post(self, vf):
        return self.client.post(reverse("vault:download_encrypted"),
                                {"file_pk": vf.pk, "password": "pw"})

    def test_the_decrypted_length_is_what_is_metered(self):
        vf = self._file()
        VaultFile.objects.filter(pk=vf.pk).update(is_encrypted=True)
        vf.refresh_from_db()
        self.client.force_login(self.owner)
        strategy = mock.Mock()
        strategy.decrypt_to_bytes.return_value = (b"p" * 4096, "text/plain")
        with mock.patch.object(VaultFile, "get_strategy",
                               return_value=strategy):
            response = self._post(vf)
        self.assertEqual(response.status_code, 200)
        event = self._events().get()
        # 4096 plaintext bytes, not the stored figure (2048): the stored
        # size drifts on encrypt/decrypt and is not what left the wire.
        self.assertEqual(event.quantity * (2 ** 20), 4096)

    def test_the_cap_applies_here_too(self):
        vf = self._file()
        VaultFile.objects.filter(pk=vf.pk).update(is_encrypted=True)
        VaultQuotaPolicy.objects.create(metric_code=EGRESS, limit=0)
        self.client.force_login(self.owner)
        response = self._post(vf)
        self.assertEqual(response.status_code, 429)
        self.assertFalse(json.loads(response.content)["ok"])
        self.assertEqual(self._events().count(), 0)


class CapCountingTests(EgressTestCase):
    def test_the_cap_counts_across_doors_and_days_of_events(self):
        # Two public downloads spend the daily budget; the third refuses.
        VaultQuotaPolicy.objects.create(metric_code=EGRESS,
                                        limit=Decimal("0.005"))  # ~5 KiB
        vf = self._file(public=True)
        url = reverse("vault:public_file", args=[self.bucket.slug, vf.key])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 429)
        self.assertEqual(self._events().count(), 2)

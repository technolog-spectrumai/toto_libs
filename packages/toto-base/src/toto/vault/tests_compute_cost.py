"""A run of the vault's pays for how much it processes, and a download a
little (2026-10-12; ``vault/compute.py``, ``cipher_jobs.dispatch``,
``views._record_egress``). Mana is the fairness of compute; the disk is the
quota's.

    manage.py test toto.vault.tests_compute_cost
"""

import tempfile
from decimal import Decimal as D
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.quota.metrics import registry
from toto.vault import compute
from toto.vault.models import Bucket, VaultFile

MB = 1024 * 1024
NUMBERS = """version: 1
cap: 100
signup: 50
default_regen_per_hour: 0
prices:
  workflows.compute_mb: 0.02
  storage.egress_mb: 0.002
"""


def numbers_file():
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", prefix="compute-", delete=False, encoding="utf-8")
    handle.write(NUMBERS)
    handle.close()
    return handle.name


class ComputeTests(TestCase):
    def test_the_metric_is_declared_by_the_workflows(self):
        metric = registry.get("workflows.compute_mb")
        self.assertIsNotNone(metric)
        self.assertEqual((metric.app_label, metric.unit), ("workflows", "mb"))
        self.assertIsNone(metric.default_limit)             # no policy row seeded

    def test_megabytes_never_round_to_nothing(self):
        self.assertEqual(compute.megabytes(100 * MB), D("100"))
        self.assertEqual(compute.megabytes(0), D("0.01"))
        self.assertEqual(compute.megabytes(10), D("0.01"))
        self.assertEqual(compute.of_files([mock.Mock(file_size_bytes=MB), mock.Mock(file_size_bytes=MB)]), D("2"))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="compute-"), VAULT_ROOT=tempfile.mkdtemp(prefix="compute-v-"),
                   DISCIPLINE_FILE=numbers_file(), QUOTA_CHARGER="discipline")
class ChargedTests(TestCase):
    """On a host with Discipline: what is checked, what is taken."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        User = get_user_model()
        cls.ada = User.objects.create_user("ada", password="pw")
        cls.bucket = Bucket.objects.create(name="Ada", slug="ada", owner=cls.ada)

    def setUp(self):
        if not apps.is_installed("toto.discipline"):
            self.skipTest("no Discipline on this host")
        from toto.discipline import config, services

        config.load(force=True)
        services.signup(self.ada)                            # 50 to start

    def balance(self):
        from toto.discipline import services

        return services.balance(self.ada)

    def make(self, size_mb, title="big.bin"):
        vault_file = VaultFile(title=title, owner=self.ada, bucket=self.bucket, file_type="other")
        vault_file.file.save(title, ContentFile(b"x" * (size_mb * MB)), save=True)
        VaultFile.objects.filter(pk=vault_file.pk).update(file_size_bytes=size_mb * MB)
        vault_file.refresh_from_db()
        return vault_file

    def test_a_small_run_is_checked_and_taken_by_its_megabytes(self):
        before = self.balance()
        compute.check(self.ada, D("100"))                    # 2 of 50: fine
        compute.charge(self.ada, D("100"), source_type="t", source_id=1)
        self.assertEqual(before - self.balance(), D("2"))

    def test_a_run_too_large_for_the_balance_is_refused_before_it_starts(self):
        with self.assertRaises(compute.InsufficientFunds):
            compute.check(self.ada, D("5000"))              # 100 of 50
        self.assertEqual(self.balance(), D("50"))

    def test_encrypting_a_file_costs_its_size_and_is_refused_when_short(self):
        from toto.vault import cipher_jobs

        small = self.make(5, "small.bin")
        with mock.patch.object(cipher_jobs, "worker_ready", return_value=True), \
                mock.patch.object(cipher_jobs, "wants_workflow", return_value=False), \
                mock.patch("toto.vault.tasks.cipher_file_task") as task:
            before = self.balance()
            answer = cipher_jobs.dispatch(small, self.ada, cipher_jobs.ENCRYPT, "pw")
            self.assertTrue(answer["async"])
            self.assertEqual(before - self.balance(), D("0.1"))       # 5 MB at 0.02
            task.delay.assert_called_once()
            # A file past the balance: refused, nothing queued, nothing taken.
            VaultFile.objects.filter(pk=small.pk).update(file_size_bytes=4000 * MB)
            small.refresh_from_db()
            with self.assertRaises(cipher_jobs.InsufficientFunds):
                cipher_jobs.dispatch(small, self.ada, cipher_jobs.ENCRYPT, "pw")
            task.delay.assert_called_once()
            self.assertEqual(before - self.balance(), D("0.1"))

    def test_the_encrypt_door_answers_402(self):
        from toto.vault import cipher_jobs

        huge = self.make(1, "huge.bin")
        VaultFile.objects.filter(pk=huge.pk).update(file_size_bytes=4000 * MB)
        self.client.force_login(self.ada)
        with mock.patch.object(cipher_jobs, "off_request", return_value=True), \
                mock.patch.object(cipher_jobs, "worker_ready", return_value=True):
            response = self.client.post(reverse("vault:encrypt_file"),
                                        {"file_pk": huge.pk, "password": "pw"})
        self.assertEqual(response.status_code, 402)
        self.assertIn("mana", response.json()["error"].lower())

    def test_a_download_costs_whoever_downloads_a_little_after_the_bytes(self):
        from toto.vault import views

        small = self.make(100, "doc.bin")
        before = self.balance()
        # Nobody at the keyboard: nobody pays.
        views._record_egress(small)
        self.assertEqual(self.balance(), before)
        with mock.patch("toto.vault.timeline.acting", return_value=self.ada):
            views._record_egress(small)
        self.assertEqual(before - self.balance(), D("0.2"))         # 100 MB at 0.002
        # A balance of nothing refuses no download: it gives what it has.
        from toto.discipline import services

        services.spend(self.ada, "workflows.compute_mb", D("100000"), strict=False)
        with mock.patch("toto.vault.timeline.acting", return_value=self.ada):
            views._record_egress(small)
        self.assertEqual(self.balance(), D("0"))

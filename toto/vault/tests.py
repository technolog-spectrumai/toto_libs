import shutil
import tempfile
from decimal import Decimal
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.invoice.models import Invoice, InvoiceStatus, BillingCycle, BillingCycleFrequency
from toto.vault.models import Bucket, VaultDirectory, VaultFile


class InvoiceModelTest(TestCase):

    def setUp(self):
        self.staff = User.objects.create_user("staff", password="pass", is_staff=True)
        self.user = User.objects.create_user("alice", password="pass")

    def _make_invoice(self, **kwargs):
        defaults = dict(
            issued_to=self.user,
            issued_by=self.staff,
            title="Test Invoice",
            amount=Decimal("99.99"),
            currency_label="USD",
        )
        defaults.update(kwargs)
        return Invoice.objects.create(**defaults)

    def test_create_pending(self):
        inv = self._make_invoice()
        self.assertEqual(inv.status, InvoiceStatus.PENDING)
        self.assertIsNone(inv.paid_at)

    def test_str(self):
        inv = self._make_invoice()
        self.assertIn("alice", str(inv))
        self.assertIn("99.99", str(inv))
        self.assertIn("USD", str(inv))

    def test_is_paid_false_when_pending(self):
        inv = self._make_invoice()
        self.assertFalse(inv.is_paid)

    def test_is_paid_true_when_paid(self):
        inv = self._make_invoice(status=InvoiceStatus.PAID, paid_at=timezone.now())
        self.assertTrue(inv.is_paid)

    def test_is_overdue_true_when_past_due(self):
        from datetime import date, timedelta
        inv = self._make_invoice(
            status=InvoiceStatus.PENDING,
            due_date=date.today() - timedelta(days=1),
        )
        self.assertTrue(inv.is_overdue)

    def test_is_overdue_false_when_paid(self):
        from datetime import date, timedelta
        inv = self._make_invoice(
            status=InvoiceStatus.PAID,
            due_date=date.today() - timedelta(days=1),
            paid_at=timezone.now(),
        )
        self.assertFalse(inv.is_overdue)

    def test_is_overdue_false_when_future_due(self):
        from datetime import date, timedelta
        inv = self._make_invoice(
            status=InvoiceStatus.PENDING,
            due_date=date.today() + timedelta(days=10),
        )
        self.assertFalse(inv.is_overdue)

    def test_no_due_date_not_overdue(self):
        inv = self._make_invoice(due_date=None)
        self.assertFalse(inv.is_overdue)

    def test_cancelled_not_overdue(self):
        from datetime import date, timedelta
        inv = self._make_invoice(
            status=InvoiceStatus.CANCELLED,
            due_date=date.today() - timedelta(days=5),
        )
        self.assertFalse(inv.is_overdue)

    def test_invoices_isolated_from_assets(self):
        """Invoice has no FK to any assets model."""
        from django.db import models as _m
        fk_targets = [
            f.related_model.__name__
            for f in Invoice._meta.get_fields()
            if isinstance(f, (_m.ForeignKey, _m.OneToOneField))
            and f.related_model is not None
        ]
        for name in fk_targets:
            self.assertNotIn(
                "assets", name.lower(),
                msg=f"Invoice FK targets an assets model: {name}",
            )

    def test_multiple_invoices_for_same_user(self):
        self._make_invoice(title="Invoice A", amount=Decimal("10.00"))
        self._make_invoice(title="Invoice B", amount=Decimal("20.00"))
        self.assertEqual(Invoice.objects.filter(issued_to=self.user).count(), 2)

    def test_no_issued_by_allowed(self):
        inv = self._make_invoice(issued_by=None)
        self.assertIsNone(inv.issued_by)


class BillingCycleModelTest(TestCase):

    def test_hours_per_period(self):
        cases = [
            (BillingCycleFrequency.DAILY, 24),
            (BillingCycleFrequency.WEEKLY, 168),
            (BillingCycleFrequency.MONTHLY, 730),
            (BillingCycleFrequency.YEARLY, 8760),
        ]
        for freq, expected in cases:
            cycle = BillingCycle(name=f"test-{freq}", frequency=freq)
            self.assertEqual(cycle.hours_per_period, expected)

    def test_str(self):
        cycle = BillingCycle.objects.create(name="Monthly Billing", frequency=BillingCycleFrequency.MONTHLY)
        self.assertIn("Monthly Billing", str(cycle))


class InvoiceListViewTest(TestCase):

    def setUp(self):
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.staff = User.objects.create_user("staff", password="pass", is_staff=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.client = Client()

    def _make_invoice(self, user, **kwargs):
        defaults = dict(
            issued_to=user,
            issued_by=self.staff,
            title="Invoice",
            amount=Decimal("50.00"),
            currency_label="PLN",
        )
        defaults.update(kwargs)
        return Invoice.objects.create(**defaults)

    def test_requires_login(self):
        url = reverse("invoice:invoice_list")
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)

    def test_shows_own_invoices(self):
        self._make_invoice(self.alice, title="Alice Invoice")
        self._make_invoice(self.bob, title="Bob Invoice")
        self.client.login(username="alice", password="pass")
        url = reverse("invoice:invoice_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        rows = response.context["invoice_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["invoice"].issued_to, self.alice)

    def test_pending_and_paid_counts(self):
        self._make_invoice(self.alice, status=InvoiceStatus.PENDING)
        self._make_invoice(self.alice, status=InvoiceStatus.PAID, paid_at=timezone.now())
        self.client.login(username="alice", password="pass")
        response = self.client.get(reverse("invoice:invoice_list"))
        self.assertEqual(response.context["pending_count"], 1)
        self.assertEqual(response.context["paid_count"], 1)

    def test_yaml_in_invoice_rows(self):
        self._make_invoice(self.alice)
        self.client.login(username="alice", password="pass")
        response = self.client.get(reverse("invoice:invoice_list"))
        rows = response.context["invoice_rows"]
        self.assertIn("invoice:", rows[0]["yaml"])

    def test_invoice_rows_not_cross_user(self):
        self._make_invoice(self.bob, title="Bob Only")
        self.client.login(username="alice", password="pass")
        response = self.client.get(reverse("invoice:invoice_list"))
        self.assertEqual(len(response.context["invoice_rows"]), 0)


class InvoiceMetricsViewTest(TestCase):

    def setUp(self):
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.user = User.objects.create_user("alice", password="pass")
        self.client = Client()

    def _make_invoice(self, **kwargs):
        defaults = dict(issued_to=self.user, title="Inv", amount=Decimal("10.00"), currency_label="USD")
        defaults.update(kwargs)
        return Invoice.objects.create(**defaults)

    def test_requires_login(self):
        response = self.client.get(reverse("invoice:metrics"))
        self.assertNotEqual(response.status_code, 200)

    def test_metrics_page_loads(self):
        self._make_invoice(status=InvoiceStatus.PENDING)
        self._make_invoice(status=InvoiceStatus.PAID, paid_at=timezone.now())
        self.client.login(username="alice", password="pass")
        response = self.client.get(reverse("invoice:metrics"))
        self.assertEqual(response.status_code, 200)
        totals = response.context["totals"]
        self.assertEqual(totals["total"], 2)
        self.assertEqual(totals["pending"], 1)
        self.assertEqual(totals["paid"], 1)


class BucketQuotaTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("admin", password="pass", is_staff=True)

    def test_bucket_quota_field(self):
        bucket = Bucket.objects.create(
            name="TestBucket",
            slug="testbucket",
            owner=self.user,
            storage_quota_mb=500,
        )
        self.assertEqual(bucket.storage_quota_mb, 500)

    def test_bucket_quota_nullable(self):
        bucket = Bucket.objects.create(
            name="UnlimitedBucket",
            slug="unlimitedbucket",
            owner=self.user,
            storage_quota_mb=None,
        )
        self.assertIsNone(bucket.storage_quota_mb)


class NoVaultAssetsLinkTest(TestCase):
    """Asserts that vault models contain no FK references to the assets app."""

    VAULT_MODELS = [Bucket, VaultDirectory]

    def test_no_assets_fk_in_vault_models(self):
        from django.db import models as _m
        for Model in self.VAULT_MODELS:
            for field in Model._meta.get_fields():
                if isinstance(field, (_m.ForeignKey, _m.OneToOneField, _m.ManyToManyField)):
                    related = getattr(field, 'related_model', None)
                    if related is None:
                        continue
                    app = related._meta.app_label
                    self.assertNotEqual(
                        app, "assets",
                        msg=f"{Model.__name__}.{field.name} points to assets app model {related.__name__}",
                    )


class CopyFilesToBucketTest(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._override = override_settings(MEDIA_ROOT=self.temp_media)
        self._override.enable()

        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.client = Client()

        self.src_bucket = Bucket.objects.create(name="Source", slug="source", owner=self.alice)
        self.dst_bucket = Bucket.objects.create(name="Dest", slug="dest", owner=self.alice)
        self.bob_bucket = Bucket.objects.create(name="Bob", slug="bob-bucket", owner=self.bob)

        self.src_file = VaultFile.objects.create(
            owner=self.alice,
            title="test file",
            key="test-file",
            file=SimpleUploadedFile("test_file.txt", b"hello world"),
            file_type="text",
            bucket=self.src_bucket,
        )

    def tearDown(self):
        self._override.disable()

    def _copy_url(self, slug):
        return reverse("vault:copy_files", kwargs={"source_slug": slug})

    def test_copy_file_creates_new_record_in_target(self):
        self.client.login(username="alice", password="pass")
        response = self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.dst_bucket.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            VaultFile.objects.filter(bucket=self.dst_bucket, title="test file").exists()
        )

    def test_source_file_remains_in_source_bucket(self):
        self.client.login(username="alice", password="pass")
        self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.dst_bucket.pk,
        })
        self.assertTrue(
            VaultFile.objects.filter(pk=self.src_file.pk, bucket=self.src_bucket).exists()
        )

    def test_new_file_exists_only_in_target(self):
        self.client.login(username="alice", password="pass")
        self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.dst_bucket.pk,
        })
        self.assertEqual(VaultFile.objects.filter(bucket=self.dst_bucket).count(), 1)
        self.assertEqual(VaultFile.objects.filter(bucket=self.src_bucket).count(), 1)

    def test_duplicate_key_is_renamed(self):
        VaultFile.objects.create(
            owner=self.alice,
            title="existing",
            key="test-file",
            file=SimpleUploadedFile("existing.txt", b"existing"),
            file_type="text",
            bucket=self.dst_bucket,
        )
        self.client.login(username="alice", password="pass")
        self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.dst_bucket.pk,
        })
        keys = list(VaultFile.objects.filter(bucket=self.dst_bucket).values_list("key", flat=True))
        self.assertEqual(len(keys), 2)
        self.assertIn("test-file", keys)
        self.assertIn("test-file-1", keys)

    def test_cannot_copy_to_other_users_bucket(self):
        self.client.login(username="alice", password="pass")
        self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.bob_bucket.pk,
        })
        self.assertFalse(VaultFile.objects.filter(bucket=self.bob_bucket).exists())

    def test_cannot_copy_from_other_users_bucket(self):
        self.client.login(username="alice", password="pass")
        response = self.client.get(self._copy_url(self.bob_bucket.slug))
        self.assertEqual(response.status_code, 404)

    def test_requires_login(self):
        response = self.client.get(self._copy_url(self.src_bucket.slug))
        self.assertNotEqual(response.status_code, 200)

    def test_redirect_to_target_bucket_on_success(self):
        self.client.login(username="alice", password="pass")
        response = self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.dst_bucket.pk,
        })
        self.assertRedirects(
            response,
            reverse("vault:bucket_metrics", kwargs={"bucket_slug": self.dst_bucket.slug}),
        )


class BucketCopyAjaxViewTest(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._override = override_settings(MEDIA_ROOT=self.temp_media)
        self._override.enable()

        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.client = Client()

        self.src_bucket = Bucket.objects.create(name="Source", slug="source-ajax", owner=self.alice)
        self.dst_bucket = Bucket.objects.create(name="Dest", slug="dest-ajax", owner=self.alice)
        self.bob_bucket = Bucket.objects.create(name="Bob", slug="bob-ajax", owner=self.bob)

        self.src_file = VaultFile.objects.create(
            owner=self.alice,
            title="ajax file",
            key="ajax-file",
            file=SimpleUploadedFile("ajax_file.txt", b"ajax content"),
            file_type="text",
            bucket=self.src_bucket,
        )

    def tearDown(self):
        self._override.disable()

    def _ajax_url(self, slug):
        return reverse("vault:copy_files_ajax", kwargs={"source_slug": slug})

    def _post(self, file_ids, dest_bucket_id):
        return self.client.post(
            self._ajax_url(self.src_bucket.slug),
            {"files": file_ids, "destination_bucket": dest_bucket_id},
        )

    def test_requires_login(self):
        response = self._post([self.src_file.pk], self.dst_bucket.pk)
        self.assertNotEqual(response.status_code, 200)

    def test_successful_copy_returns_ok_json(self):
        self.client.login(username="alice", password="pass")
        response = self._post([self.src_file.pk], self.dst_bucket.pk)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["dest_slug"], self.dst_bucket.slug)
        self.assertIn("dest_url", data)

    def test_copy_creates_file_in_destination(self):
        self.client.login(username="alice", password="pass")
        self._post([self.src_file.pk], self.dst_bucket.pk)
        self.assertTrue(VaultFile.objects.filter(bucket=self.dst_bucket, title="ajax file").exists())

    def test_source_file_unchanged(self):
        self.client.login(username="alice", password="pass")
        self._post([self.src_file.pk], self.dst_bucket.pk)
        self.assertTrue(VaultFile.objects.filter(pk=self.src_file.pk, bucket=self.src_bucket).exists())

    def test_empty_files_returns_error(self):
        self.client.login(username="alice", password="pass")
        response = self._post([], self.dst_bucket.pk)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_missing_destination_returns_error(self):
        self.client.login(username="alice", password="pass")
        response = self.client.post(
            self._ajax_url(self.src_bucket.slug),
            {"files": [self.src_file.pk]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_cannot_copy_to_other_users_bucket(self):
        self.client.login(username="alice", password="pass")
        response = self._post([self.src_file.pk], self.bob_bucket.pk)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertFalse(VaultFile.objects.filter(bucket=self.bob_bucket).exists())

    def test_cannot_copy_from_other_users_bucket(self):
        self.client.login(username="alice", password="pass")
        response = self.client.post(
            self._ajax_url(self.bob_bucket.slug),
            {"files": [self.src_file.pk], "destination_bucket": self.dst_bucket.pk},
        )
        self.assertEqual(response.status_code, 404)

    def test_duplicate_key_renamed(self):
        VaultFile.objects.create(
            owner=self.alice,
            title="existing",
            key="ajax-file",
            file=SimpleUploadedFile("existing.txt", b"x"),
            file_type="text",
            bucket=self.dst_bucket,
        )
        self.client.login(username="alice", password="pass")
        self._post([self.src_file.pk], self.dst_bucket.pk)
        keys = list(VaultFile.objects.filter(bucket=self.dst_bucket).values_list("key", flat=True))
        self.assertEqual(len(keys), 2)
        self.assertIn("ajax-file", keys)
        self.assertIn("ajax-file-1", keys)

    def test_dest_url_points_to_correct_bucket(self):
        self.client.login(username="alice", password="pass")
        response = self._post([self.src_file.pk], self.dst_bucket.pk)
        data = response.json()
        expected_url = reverse("vault:bucket_metrics", kwargs={"bucket_slug": self.dst_bucket.slug})
        self.assertEqual(data["dest_url"], expected_url)


class BucketMetricsViewContextTest(TestCase):

    def setUp(self):
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.client = Client()

        self.bucket = Bucket.objects.create(name="Main", slug="main-bucket", owner=self.alice)
        self.other_bucket = Bucket.objects.create(name="Other", slug="other-bucket", owner=self.alice)
        self.bob_bucket = Bucket.objects.create(name="BobBucket", slug="bob-main", owner=self.bob)

    def _url(self, slug=None):
        return reverse("vault:bucket_metrics", kwargs={"bucket_slug": slug or self.bucket.slug})

    def test_requires_login(self):
        response = self.client.get(self._url())
        self.assertNotEqual(response.status_code, 200)

    def test_page_loads(self):
        self.client.login(username="alice", password="pass")
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)

    def test_dest_buckets_excludes_current(self):
        self.client.login(username="alice", password="pass")
        response = self.client.get(self._url())
        dest_buckets = response.context["dest_buckets"]
        dest_pks = [b.pk for b in dest_buckets]
        self.assertNotIn(self.bucket.pk, dest_pks)
        self.assertIn(self.other_bucket.pk, dest_pks)

    def test_dest_buckets_excludes_other_users(self):
        self.client.login(username="alice", password="pass")
        response = self.client.get(self._url())
        dest_buckets = response.context["dest_buckets"]
        dest_pks = [b.pk for b in dest_buckets]
        self.assertNotIn(self.bob_bucket.pk, dest_pks)

    def test_copy_files_data_contains_only_own_files(self):
        import tempfile, shutil
        from django.test import override_settings
        temp_media = tempfile.mkdtemp()
        try:
            with override_settings(MEDIA_ROOT=temp_media):
                VaultFile.objects.create(
                    owner=self.alice, title="Alice File", key="alice-file",
                    file=SimpleUploadedFile("a.txt", b"a"), file_type="text",
                    bucket=self.bucket,
                )
                VaultFile.objects.create(
                    owner=self.bob, title="Bob File", key="bob-file",
                    file=SimpleUploadedFile("b.txt", b"b"), file_type="text",
                    bucket=self.bucket,
                )
                self.client.login(username="alice", password="pass")
                response = self.client.get(self._url())
                files_data = response.context["copy_files_data"]
                titles = [f["title"] for f in files_data]
                self.assertIn("Alice File", titles)
                self.assertNotIn("Bob File", titles)
        finally:
            shutil.rmtree(temp_media, ignore_errors=True)

    def test_copy_files_data_structure(self):
        import tempfile, shutil
        from django.test import override_settings
        temp_media = tempfile.mkdtemp()
        try:
            with override_settings(MEDIA_ROOT=temp_media):
                VaultFile.objects.create(
                    owner=self.alice, title="Structured File", key="struct-file",
                    file=SimpleUploadedFile("s.txt", b"s"), file_type="text",
                    bucket=self.bucket,
                )
                self.client.login(username="alice", password="pass")
                response = self.client.get(self._url())
                files_data = response.context["copy_files_data"]
                self.assertGreater(len(files_data), 0)
                entry = files_data[0]
                for key in ("id", "title", "file_type", "key"):
                    self.assertIn(key, entry)
        finally:
            shutil.rmtree(temp_media, ignore_errors=True)

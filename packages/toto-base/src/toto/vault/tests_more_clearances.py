"""The bucket-clearances door (``buckets/<slug>/clearances/``) and its helpers,
past what tests_clearances pins: what a forged form can and cannot tick, the
section as each viewer sees it, the one audit record a change leaves, and the
rows themselves (2026-09-30).
"""

import tempfile

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault import clearances
from toto.vault.access import gate_by_bucket, may_read
from toto.vault.models import Bucket, BucketClearance, VaultFile

User = get_user_model()

ACTION = "VAULT.BUCKET.CLEARANCES_CHANGED"


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-clearances-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.internal = Clearance.objects.create(name="internal", slug="internal")
        cls.confidential = Clearance.objects.create(name="confidential", slug="confidential")
        cls.owner = User.objects.create_user("owner", password="pw")
        Person.objects.create(user=cls.owner, display_name="Owner").clearances.add(cls.internal)
        cls.member = User.objects.create_user("member", password="pw")
        Person.objects.create(user=cls.member, display_name="Member").clearances.add(cls.internal)
        cls.stranger = User.objects.create_user("stranger", password="pw")
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)

    _n = 0

    def file(self, *, public=False, title="deck.txt", bucket=None):
        type(self)._n += 1
        vault_file = VaultFile(owner=self.owner, title=title, key=f"m-{self._n}",
                               file_type="text", is_public=public,
                               bucket=bucket or self.bucket)
        vault_file.file.save(title, ContentFile(b"the contents"), save=False)
        vault_file.save()
        return vault_file

    def keep(self, *kept_to, bucket=None):
        for clearance in kept_to:
            BucketClearance.objects.create(bucket=bucket or self.bucket, clearance=clearance)

    def url(self, bucket=None):
        return reverse("vault:bucket_clearances", args=[(bucket or self.bucket).slug])

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class DoorTests(_Fixture):
    def test_a_superuser_may_tick_any_clearance_even_one_they_do_not_hold(self):
        self.client.force_login(self.root)
        self.client.post(self.url(), {"clearance": [self.confidential.pk]})
        self.assertEqual([c.name for c in clearances.clearances_of(self.bucket)], ["confidential"])

    def test_forged_ids_are_dropped(self):
        self.client.force_login(self.root)
        self.client.post(self.url(), {"clearance": [self.internal.pk, "999999", "x", "-1"]})
        self.assertEqual([c.name for c in clearances.clearances_of(self.bucket)], ["internal"])

    def test_the_message_says_whether_anything_changed(self):
        self.client.force_login(self.root)
        response = self.client.post(self.url(), {"clearance": [self.internal.pk]}, follow=True)
        self.assertIn("Saved.", self.messages(response))
        response = self.client.post(self.url(), {"clearance": [self.internal.pk]}, follow=True)
        self.assertIn("Nothing changed.", self.messages(response))
        self.assertEqual(AuditRecord.objects.filter(action=ACTION).count(), 1)

    def test_a_holder_who_owns_the_bucket_still_may_not_set_them(self):
        self.client.force_login(self.owner)                     # holds internal
        self.assertEqual(self.client.post(self.url(), {"clearance": [self.internal.pk]}).status_code,
                         403)
        self.assertEqual(clearances.clearances_of(self.bucket), [])

    def test_an_unknown_bucket_is_a_404(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse("vault:bucket_clearances", args=["nope"]), {})
        self.assertEqual(response.status_code, 404)


class SectionTests(_Fixture):
    def page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("vault:bucket_metrics", args=[self.bucket.slug]))

    def test_the_superuser_sees_every_clearance_with_the_current_ones_ticked(self):
        self.keep(self.internal)
        response = self.page(self.root)
        rows = {row["clearance"].name: row["on"] for row in response.context["bucket_clearance_choices"]}
        self.assertEqual(rows, {"confidential": False, "internal": True})
        self.assertEqual(response.context["bucket_clearances_url"], self.url())

    def test_the_owner_sees_the_names_and_no_form(self):
        self.keep(self.internal, self.confidential)
        response = self.page(self.owner)
        self.assertEqual([c.name for c in response.context["bucket_clearances"]],
                         ["confidential", "internal"])
        self.assertEqual(response.context["bucket_clearance_choices"], [])
        self.assertEqual(response.context["bucket_clearances_url"], "")
        self.assertContains(response, "confidential, internal")
        self.assertNotContains(response, self.url())

    def test_an_open_bucket_says_the_usual_rule_applies(self):
        response = self.page(self.owner)
        self.assertContains(response, 'data-testid="bucket-clearances-open"')
        self.assertNotContains(response, 'data-testid="bucket-clearance-list"')

    def test_a_superuser_with_no_clearance_to_offer_is_told_so(self):
        Clearance.objects.all().delete()
        self.assertContains(self.page(self.root), "There are no clearances yet.")


class SetClearancesTests(_Fixture):
    def test_the_audit_record_names_the_bucket_and_both_sides(self):
        self.keep(self.internal)
        before, after = clearances.set_clearances(self.bucket, [self.confidential, self.internal],
                                                  actor=self.root)
        self.assertEqual((before, after), (["internal"], ["confidential", "internal"]))
        record = AuditRecord.objects.get(action=ACTION)
        self.assertEqual(record.app_label, "vault")
        self.assertEqual(record.metadata["bucket"], self.bucket.pk)
        self.assertEqual(record.metadata["slug"], "owned")
        self.assertEqual(record.metadata["name"], "Owned")
        self.assertEqual(record.metadata["before"], ["internal"])
        self.assertEqual(record.metadata["after"], ["confidential", "internal"])
        self.assertFalse(record.metadata["open"])

    def test_the_files_are_kept_the_moment_the_rows_exist(self):
        f = self.file(public=True)
        self.assertTrue(may_read(self.stranger, f))
        clearances.set_clearances(self.bucket, [self.internal], actor=self.root)
        self.assertFalse(may_read(self.stranger, f))
        self.assertTrue(may_read(self.member, f))
        self.assertTrue(may_read(self.owner, f))                # the owner holds internal

    def test_a_file_moved_out_of_a_kept_bucket_is_read_as_before(self):
        other = Bucket.objects.create(name="Other", slug="other", owner=self.owner)
        f = self.file(public=True)
        self.keep(self.confidential)
        self.assertFalse(may_read(self.stranger, f))
        f.bucket = other
        f.save()
        self.assertTrue(may_read(self.stranger, f))

    def test_clearances_of_is_sorted_by_name(self):
        self.keep(self.internal, self.confidential)
        self.assertEqual([c.name for c in clearances.clearances_of(self.bucket)],
                         ["confidential", "internal"])

    def test_the_gate_and_the_rule_agree_per_bucket(self):
        kept_bucket = Bucket.objects.create(name="K", slug="k", owner=self.owner)
        self.keep(self.confidential, bucket=kept_bucket)
        kept, open_ = self.file(public=True, bucket=kept_bucket), self.file(public=True)
        for user in (self.owner, self.member, self.stranger, self.root):
            listed = set(gate_by_bucket(user, VaultFile.objects.all()))
            self.assertEqual(listed, {f for f in (kept, open_) if may_read(user, f)}, user)


class RowTests(_Fixture):
    def test_deleting_the_bucket_takes_its_rows_and_frees_the_clearance(self):
        self.keep(self.internal, self.confidential)
        self.bucket.delete()
        self.assertFalse(BucketClearance.objects.exists())
        self.internal.delete()                     # no longer protected by anything

    def test_the_row_reads_as_bucket_and_clearance(self):
        self.keep(self.internal)
        self.assertEqual(str(BucketClearance.objects.get()), "Owned — internal")
        self.assertEqual(list(self.internal.bucket_rows.values_list("bucket__slug", flat=True)),
                         ["owned"])


# ---------------------------------------------------------------------------
# The New clearance modal's kind for buckets (2026-09-30): socialhub's
# ClearanceTargetPlugin, provided here, adding through this app's own door.
# ---------------------------------------------------------------------------


class BucketClearanceKindTests(TestCase):
    KEY = "vault.bucket"

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        from toto.core.models import Platform
        from toto.socialhub.models import Clearance

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.root = get_user_model().objects.create_superuser("kindroot", "k@example.com", "pw")
        cls.internal = Clearance.objects.create(name="internal", slug="internal")
        cls.payroll = Clearance.objects.create(name="payroll", slug="payroll")
        from toto.vault.models import Bucket

        cls.first = Bucket.objects.create(name="payroll files", slug="payroll-files", owner=cls.root)
        Bucket.objects.create(name="Payroll archive", slug="payroll-archive", owner=cls.root)
        Bucket.objects.create(name="Photos", slug="photos", owner=cls.root)

    def plugin(self):
        from toto.socialhub.plugins import clearance_plugins

        return clearance_plugins.kind(self.KEY)

    def test_the_kind_is_registered_with_a_title_and_an_icon(self):
        plugin = self.plugin()
        self.assertIsNotNone(plugin)
        self.assertEqual(str(plugin.title), "Buckets")
        self.assertEqual(plugin.icon, "bucket")

    def test_search_finds_by_name_case_blind_ordered_and_capped(self):
        rows = self.plugin().search("PAYROLL", 20)
        self.assertEqual([r["label"] for r in rows], ["Payroll archive", "payroll files"])
        self.assertEqual(set(rows[0]), {"pk", "label", "detail"})
        self.assertEqual(len(self.plugin().search("PAYROLL", 1)), 1)
        self.assertEqual(self.plugin().search("nothing-like-it", 20), [])

    def test_resolve_ignores_what_does_not_exist(self):
        self.assertEqual(self.plugin().resolve({self.first.pk, 10 ** 9}), [self.first])

    def test_keep_adds_the_clearance_and_keeps_the_others(self):
        from toto.audit.models import AuditRecord

        from toto.vault.clearances import set_clearances

        set_clearances(self.first, [self.internal], actor=self.root)
        self.assertEqual(self.plugin().keep([self.first], self.payroll, actor=self.root), 1)
        self.assertEqual(set(self.first.clearance_rows.values_list("clearance__name", flat=True)),
                         {"internal", "payroll"})
        record = AuditRecord.objects.filter(action="VAULT.BUCKET.CLEARANCES_CHANGED").order_by("-sequence").first()
        self.assertEqual(record.actor_user, self.root)

    def test_keeping_twice_changes_nothing(self):
        from toto.audit.models import AuditRecord

        self.plugin().keep([self.first], self.payroll, actor=self.root)
        before = AuditRecord.objects.filter(action="VAULT.BUCKET.CLEARANCES_CHANGED").count()
        self.plugin().keep([self.first], self.payroll, actor=self.root)
        self.assertEqual(self.first.clearance_rows.count(), 1)
        self.assertEqual(AuditRecord.objects.filter(action="VAULT.BUCKET.CLEARANCES_CHANGED").count(), before)

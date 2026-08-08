"""The one door every Time dial posts through: bounds, ownership, resets."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import TestCase

from toto.quota import times
from toto.quota.times import TimeLimit

from .. import timegrants
from ..models import TimeGrant
from .factories import make_user
from .test_times import RegistrySnapshotMixin


def declare_user_dial():
    return times.registry.register(TimeLimit(
        key="test.dial", label="Test dial", app_label="tax", scope="user",
        free_seconds=100, ceiling_seconds=1000,
    ))


def declare_bucket_dial():
    # vault.Bucket stands in for a workspace: installed here, owner FK.
    return times.registry.register(TimeLimit(
        key="test.bucket_dial", label="Bucket dial", app_label="tax",
        scope="workspace", scope_model="vault.Bucket",
        scope_owner_attr="owner_id", free_seconds=100, ceiling_seconds=1000,
    ))


def make_bucket(owner, slug="b1"):
    from toto.vault.models import Bucket

    return Bucket.objects.create(name=slug, slug=slug, owner=owner)


class SetGrantTests(RegistrySnapshotMixin, TestCase):
    def setUp(self):
        super().setUp()
        declare_user_dial()
        declare_bucket_dial()
        self.alice = make_user("alice")
        self.bob = make_user("bob")

    def test_set_and_update(self):
        grant = timegrants.set_grant(actor=self.alice, key="test.dial", seconds=500)
        self.assertEqual(grant.seconds, 500)
        self.assertEqual(grant.user, self.alice)

        again = timegrants.set_grant(actor=self.alice, key="test.dial", seconds=800)
        self.assertEqual(again.pk, grant.pk)
        self.assertEqual(TimeGrant.objects.count(), 1)

    def test_bounds_are_enforced(self):
        with self.assertRaises(ValidationError):
            timegrants.set_grant(actor=self.alice, key="test.dial", seconds=50)
        with self.assertRaises(ValidationError):
            timegrants.set_grant(actor=self.alice, key="test.dial", seconds=5000)
        with self.assertRaises(ValidationError):
            timegrants.set_grant(actor=self.alice, key="no.such", seconds=500)

    def test_set_at_free_default_deletes(self):
        timegrants.set_grant(actor=self.alice, key="test.dial", seconds=500)
        result = timegrants.set_grant(actor=self.alice, key="test.dial", seconds=100)
        self.assertIsNone(result)
        self.assertEqual(TimeGrant.objects.count(), 0)

    def test_clear_grant(self):
        timegrants.set_grant(actor=self.alice, key="test.dial", seconds=500)
        timegrants.clear_grant(actor=self.alice, key="test.dial")
        self.assertEqual(TimeGrant.objects.count(), 0)

    def test_workspace_scope_owner_only_and_bills_owner(self):
        bucket = make_bucket(self.alice)

        with self.assertRaises(PermissionDenied):
            timegrants.set_grant(actor=self.bob, key="test.bucket_dial",
                                 seconds=500, scope_id=bucket.pk)

        grant = timegrants.set_grant(actor=self.alice, key="test.bucket_dial",
                                     seconds=500, scope_id=bucket.pk)
        self.assertEqual(grant.user, self.alice)
        self.assertEqual(grant.scope_id, bucket.pk)

    def test_workspace_scope_requires_scope_and_existence(self):
        with self.assertRaises(ValidationError):
            timegrants.set_grant(actor=self.alice, key="test.bucket_dial",
                                 seconds=500)
        with self.assertRaises(Http404):
            timegrants.set_grant(actor=self.alice, key="test.bucket_dial",
                                 seconds=500, scope_id=999999)

    def test_rows_for_user(self):
        bucket = make_bucket(self.alice)
        timegrants.set_grant(actor=self.alice, key="test.dial", seconds=460)
        timegrants.set_grant(actor=self.alice, key="test.bucket_dial",
                             seconds=820, scope_id=bucket.pk)

        data = timegrants.rows_for_user(self.alice)

        self.assertEqual(len(data["rows"]), 2)
        by_key = {row["grant"].key: row for row in data["rows"]}
        self.assertEqual(by_key["test.dial"]["extra_seconds"], 360)
        self.assertEqual(by_key["test.bucket_dial"]["scope_label"], "b1")
        self.assertEqual(data["total_extra_hours"], round((360 + 720) / 3600, 4))
        self.assertIsNone(data["total_estimate"])  # unpriced

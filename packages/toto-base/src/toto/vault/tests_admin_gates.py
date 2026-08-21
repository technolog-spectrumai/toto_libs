"""The admin gates, checked at the door rather than in the menu.

``has_module_permission`` only decides whether an app appears on the admin
index. A staff user holding the model permission still reaches the change view
by typing its URL — which is why the vault's "superuser-only" contract for
peering had to become ``has_view/add/change_permission`` as well.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from toto.vault.models import Bucket, StorageProvider

User = get_user_model()


def _grant(user, *codenames):
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return User.objects.get(pk=user.pk)      # drop the permission cache


class StorageProviderAdminTests(TestCase):
    """A preset's endpoint repoints EVERY bucket that leans on it."""

    def setUp(self):
        self.staff = User.objects.create_user("sp-staff", password="x", is_staff=True)
        self.provider = StorageProvider.objects.create(
            name="ovh", display_name="OVH",
            endpoint_url_template="https://s3.{region}.example.org")

    def test_staff_with_the_model_permission_cannot_open_the_change_view(self):
        staff = _grant(self.staff, "change_storageprovider", "view_storageprovider")
        self.client.force_login(staff)
        response = self.client.get(
            reverse("admin:vault_storageprovider_change", args=[self.provider.pk]))
        self.assertIn(response.status_code, (302, 403))

    def test_staff_cannot_reach_the_changelist_either(self):
        staff = _grant(self.staff, "view_storageprovider")
        self.client.force_login(staff)
        response = self.client.get(reverse("admin:vault_storageprovider_changelist"))
        self.assertIn(response.status_code, (302, 403))

    def test_a_superuser_still_can(self):
        root = User.objects.create_superuser("sp-root", "r@e.org", "x")
        self.client.force_login(root)
        self.assertEqual(
            self.client.get(
                reverse("admin:vault_storageprovider_change",
                        args=[self.provider.pk])).status_code, 200)


class BucketAdminStorageFieldTests(TestCase):
    """get_fieldsets only decides what is RENDERED; readonly ignores a POST."""

    def setUp(self):
        self.staff = User.objects.create_user("ba-staff", password="x", is_staff=True)
        self.owner = User.objects.create_user("ba-owner", password="x")
        self.bucket = Bucket.objects.create(name="B", slug="ba-b", owner=self.owner)

    def test_storage_fields_are_readonly_for_a_non_superuser(self):
        from django.contrib.admin.sites import site

        from toto.vault.admin import BucketAdmin

        request = type("R", (), {"user": self.staff})()
        readonly = BucketAdmin(Bucket, site).get_readonly_fields(request, self.bucket)
        for field in ("storage_backend", "provider", "peer", "storage_config"):
            self.assertIn(field, readonly)

    def test_a_superuser_may_still_edit_them(self):
        from django.contrib.admin.sites import site

        from toto.vault.admin import BucketAdmin

        root = User.objects.create_superuser("ba-root", "r@e.org", "x")
        request = type("R", (), {"user": root})()
        readonly = BucketAdmin(Bucket, site).get_readonly_fields(request, self.bucket)
        self.assertNotIn("storage_backend", readonly)


class PeeringAdminTests(TestCase):
    """The grant and peer admins hold live credentials."""

    def setUp(self):
        self.staff = User.objects.create_user("pa-staff", password="x", is_staff=True)

    def test_a_staff_user_cannot_reach_the_peer_changelist(self):
        staff = _grant(self.staff, "view_bucketpeer", "change_bucketpeer")
        self.client.force_login(staff)
        response = self.client.get(reverse("admin:vault_bucketpeer_changelist"))
        self.assertIn(response.status_code, (302, 403))

    def test_a_staff_user_cannot_reach_the_grant_changelist(self):
        staff = _grant(self.staff, "view_bucketgrant", "change_bucketgrant")
        self.client.force_login(staff)
        response = self.client.get(reverse("admin:vault_bucketgrant_changelist"))
        self.assertIn(response.status_code, (302, 403))

"""Remote-bucket surfaces: badges, the metrics guard, the Remote card.

Run only where a gate stanza names this module:

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.vault.tests_remote_ui
"""
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform

from .models import Bucket, StorageBackend, StorageProvider
from .peering import BucketPeer

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), VAULT_EXTERNAL_BUCKETS=True)
class RemoteUiTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.owner = User.objects.create_user("owner", password="x")
        cls.stranger = User.objects.create_user("stranger", password="x")
        cls.root = User.objects.create_superuser("root", "r@x.com", "x")
        cls.peer = BucketPeer.objects.create(
            label="Placidia media", base_url="https://peer.example.org",
            grant_uid="00000000-0000-0000-0000-000000000009",
            magic_token="tok")
        cls.local = Bucket.objects.create(
            name="plain", slug="plain", owner=cls.owner)
        cls.mounted = Bucket.objects.create(
            name="mounted", slug="mounted", owner=cls.owner,
            storage_backend=StorageBackend.REMOTE_TOTO, peer=cls.peer)
        cls.s3 = Bucket.objects.create(
            name="cloud", slug="cloud", owner=cls.owner,
            storage_backend="s3", storage_config={"bucket_name": "b"})


class RemoteLabelTests(RemoteUiTestCase):
    def test_labels_never_name_a_host(self):
        # The one leak the property closes by construction: whatever a badge
        # says, it is never the peer's URL or hostname.
        self.assertEqual(self.local.remote_label, "")
        self.assertEqual(self.mounted.remote_label, "Placidia media")
        self.assertNotIn("example.org", self.mounted.remote_label)
        self.assertEqual(self.s3.remote_label, "S3")
        self.assertFalse(self.local.is_remote)
        self.assertTrue(self.mounted.is_remote)
        self.assertTrue(self.s3.is_remote)

    def test_s3_label_prefers_the_provider_display_name(self):
        provider = StorageProvider.objects.create(
            name="ovh", display_name="OVH Object Storage")
        self.s3.provider = provider
        self.assertEqual(self.s3.remote_label, "OVH Object Storage")

    def test_peerless_mount_falls_back_to_a_generic_word(self):
        rogue = Bucket.objects.create(
            name="rogue", slug="rogue", owner=self.owner,
            storage_backend=StorageBackend.REMOTE_TOTO)
        self.assertEqual(rogue.remote_label, "Remote server")


class MetricsGuardTests(RemoteUiTestCase):
    def _get(self, user, slug="plain"):
        self.client.force_login(user)
        return self.client.get(reverse("vault:bucket_metrics", args=[slug]))

    def test_owner_and_superuser_open_strangers_get_404(self):
        self.assertEqual(self._get(self.owner).status_code, 200)
        self.assertEqual(self._get(self.root).status_code, 200)
        # 404, not 403 — a refusal that confirms the bucket exists is an
        # enumeration oracle.
        self.assertEqual(self._get(self.stranger).status_code, 404)

    def test_tree_metrics_link_renders_only_for_the_owner(self):
        url = reverse("vault:public_list") + "?bucket=plain"
        self.client.force_login(self.owner)
        self.assertIn(reverse("vault:bucket_metrics", args=["plain"]),
                      self.client.get(url).content.decode())
        self.client.force_login(self.stranger)
        self.assertNotIn(reverse("vault:bucket_metrics", args=["plain"]),
                         self.client.get(url).content.decode())


class RemoteCardTests(RemoteUiTestCase):
    def _page(self, slug="mounted"):
        self.client.force_login(self.owner)
        return self.client.get(
            reverse("vault:bucket_metrics", args=[slug])).content.decode()

    def test_card_renders_with_never_checked_state(self):
        body = self._page()
        self.assertIn("Remote bucket", body)
        self.assertIn("Placidia media", body)
        self.assertIn("Never contacted yet", body)
        self.assertIn("never been mirrored", body)

    def test_reachable_and_down_states_come_from_stamps(self):
        BucketPeer.objects.filter(pk=self.peer.pk).update(
            last_ok_at=timezone.now())
        self.assertIn("The peer answered at", self._page())
        BucketPeer.objects.filter(pk=self.peer.pk).update(
            last_error="ConnectionError: refused")
        body = self._page()
        self.assertIn("The last contact with the peer failed", body)
        self.assertIn("ConnectionError: refused", body)
        self.assertIn("502", body)

    def test_quota_bar_and_antivirus_absent_for_remote(self):
        body = self._page()
        self.assertNotIn("Quota Usage", body)
        local_body = self._page(slug="plain")
        # The local page keeps its quota bar (default quota set on model).
        if self.local.storage_quota_mb:
            self.assertIn("Quota Usage", local_body)

    def test_no_card_on_a_local_bucket(self):
        self.assertNotIn("Remote bucket", self._page(slug="plain"))

    def test_badges_in_listings(self):
        self.client.force_login(self.owner)
        body = self.client.get(reverse("vault:metrics")).content.decode()
        self.assertIn("Placidia media", body)
        body = self.client.get(reverse("vault:public_list")).content.decode()
        self.assertIn("Remote", body)
        self.assertNotIn("peer.example.org", body)

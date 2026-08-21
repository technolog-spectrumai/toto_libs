"""The Remote tab, the Archive tab, and the gate in front of them."""

import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.models import Bucket, StorageBackend, VaultDirectory, VaultFile
from toto.vault.peering import BucketPeer

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), VAULT_EXTERNAL_BUCKETS=True)
class RemoteTabTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Zen", author="T",
                                publication_year=2026, active=True)
        cls.member = User.objects.create_user("rt-member", password="x")
        cls.operator = User.objects.create_user("rt-op", password="x", is_staff=True)
        cls.root = User.objects.create_superuser("rt-root", "r@e.org", "x")

        cls.local = Bucket.objects.create(name="Local", slug="rt-local",
                                          owner=cls.member)
        cls.cloud = Bucket.objects.create(
            name="Cloud", slug="rt-cloud", owner=cls.member,
            storage_backend=StorageBackend.S3,
            storage_config={"bucket_name": "b", "endpoint_url": "https://s3.example.org",
                            "region_name": "eu", "prefix": "vault/"})
        cls.peer = BucketPeer.objects.create(
            label="Placidia", base_url="https://peer.example.org",
            grant_uid="00000000-0000-0000-0000-00000000ab12",
            magic_token="tok", remote_bucket_slug="theirs")
        cls.mount = Bucket.objects.create(
            name="Mounted", slug="rt-mount", owner=cls.member,
            storage_backend=StorageBackend.REMOTE_TOTO, peer=cls.peer)


class RemoteListingGateTests(RemoteTabTestCase):
    def test_anonymous_is_redirected(self):
        response = self.client.get(reverse("vault:remote_buckets"))
        self.assertEqual(response.status_code, 302)

    def test_a_plain_member_is_refused(self):
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.get(reverse("vault:remote_buckets")).status_code, 403)

    def test_staff_may_read_it(self):
        self.client.force_login(self.operator)
        self.assertEqual(
            self.client.get(reverse("vault:remote_buckets")).status_code, 200)

    def test_a_superuser_who_is_not_staff_is_admitted(self):
        """is_superuser does not imply is_staff in Django."""
        self.root.is_staff = False
        self.root.save(update_fields=["is_staff"])
        self.client.force_login(self.root)
        self.assertEqual(
            self.client.get(reverse("vault:remote_buckets")).status_code, 200)

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_a_local_only_host_hides_the_page_entirely(self):
        self.client.force_login(self.operator)
        self.assertEqual(
            self.client.get(reverse("vault:remote_buckets")).status_code, 404)


class RemoteListingContentTests(RemoteTabTestCase):
    def setUp(self):
        self.client.force_login(self.operator)

    def test_it_lists_remote_buckets_and_omits_local_ones(self):
        body = self.client.get(reverse("vault:remote_buckets")).content.decode()
        self.assertIn("Cloud", body)
        self.assertIn("Mounted", body)
        self.assertNotIn("rt-local", body)

    def test_a_never_contacted_peer_reads_as_never_checked(self):
        body = self.client.get(reverse("vault:remote_buckets")).content.decode()
        self.assertIn("never checked", body)
        self.assertIn("never mirrored", body)

    def test_it_renders_without_contacting_anyone(self):
        """No page render may probe a peer — the rule the surface rests on."""
        import toto.vault.peer_client as peer_client

        def explode(*a, **k):
            raise AssertionError("a page render contacted the network")

        original = peer_client._http
        peer_client._http = explode
        try:
            self.assertEqual(
                self.client.get(reverse("vault:remote_buckets")).status_code, 200)
        finally:
            peer_client._http = original

    def test_the_s3_row_shows_config_but_never_a_credential(self):
        body = self.client.get(reverse("vault:remote_buckets")).content.decode()
        self.assertIn("https://s3.example.org", body)
        self.assertIn("vault/", body)
        self.assertNotIn("secret", body.lower().replace("secret access key", ""))


class TabBarTests(RemoteTabTestCase):
    def test_the_tab_bar_offers_four_tabs_to_an_operator(self):
        self.client.force_login(self.operator)
        body = self.client.get(reverse("vault:archive")).content.decode()
        for label in ("Files", "Metrics", "Remote", "Archive"):
            self.assertIn(f">{label}</a>", body)

    def test_a_member_never_sees_the_remote_tab(self):
        """A tab that could only ever answer 403 is worse than no tab."""
        self.client.force_login(self.member)
        body = self.client.get(reverse("vault:archive")).content.decode()
        self.assertIn(">Files</a>", body)
        self.assertIn(">Archive</a>", body)
        self.assertNotIn(reverse("vault:remote_buckets"), body)

    def test_the_chip_does_not_carry_the_phrase_a_metrics_test_forbids(self):
        """tests_remote_ui pins 'Remote bucket' as absent from a local page."""
        self.client.force_login(self.operator)
        body = self.client.get(reverse("vault:archive")).content.decode()
        self.assertNotIn("Remote bucket", body)


class ArchiveTabTests(RemoteTabTestCase):
    def setUp(self):
        self.client.force_login(self.member)
        self.directory = VaultDirectory.objects.create(
            name="Docs", bucket=self.local, owner=self.member)

    def _file(self, bucket, key, **kwargs):
        return VaultFile.objects.create(
            owner=self.member, bucket=bucket, directory=None,
            title=f"{key}.txt", key=key, file_type="text", **kwargs)

    def test_a_member_may_use_the_archive_tab(self):
        self.assertEqual(self.client.get(reverse("vault:archive")).status_code, 200)

    def test_a_remote_file_is_never_offered_a_zip_button(self):
        """build_file_tree's rule: rows carrying an action use the endpoint's
        own queryset, or the page grows controls that cannot work."""
        self._file(self.local, "keepable")
        self._file(self.mount, "unzippable")
        body = self.client.get(reverse("vault:archive")).content.decode()
        self.assertIn("keepable.txt", body)
        self.assertNotIn("unzippable.txt", body)

    def test_an_encrypted_file_is_not_offered_either(self):
        self._file(self.local, "plain")
        self._file(self.local, "sealed", is_encrypted=True)
        body = self.client.get(reverse("vault:archive")).content.decode()
        self.assertIn("plain.txt", body)
        self.assertNotIn("sealed.txt", body)

    def test_the_files_tab_no_longer_offers_the_archive_action(self):
        """Files loses archiving; the Archive tab carries it.

        Asserted on the flag the button is gated by, not on the URL constant:
        the constant is inert JS that the (now unreachable) modal still names,
        and tearing it out would mean editing a 2000-line Alpine component for
        no behavioural gain.
        """
        self._file(self.local, "shown")
        body = self.client.get(reverse("vault:public_list")).content.decode()
        self.assertIn("_zipEnabled   = false", body)

        archive = self.client.get(reverse("vault:archive")).content.decode()
        self.assertIn(reverse("vault:create_zip"), archive)

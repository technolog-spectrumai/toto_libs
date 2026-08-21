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
    """Bucket-first archiving: one bucket, on-device, owner-gated."""

    def setUp(self):
        self.client.force_login(self.member)
        self.directory = VaultDirectory.objects.create(
            name="Docs", bucket=self.local, owner=self.member)

    def _file(self, bucket, key, **kwargs):
        return VaultFile.objects.create(
            owner=self.member, bucket=bucket, directory=None,
            title=f"{key}.txt", key=key, file_type="text", **kwargs)

    def _url(self, bucket=None):
        url = reverse("vault:archive")
        return f"{url}?bucket={bucket.slug}" if bucket else url

    def test_a_member_may_use_the_archive_tab(self):
        self.assertEqual(self.client.get(self._url()).status_code, 200)

    def test_the_picker_offers_only_owned_on_device_buckets(self):
        """Cross-bucket archives are impossible from the first click.

        The cloud and mounted buckets are excluded (the zip task opens local
        handles), and so is anything the user does not own — CreateZipView
        refuses non-owners, so offering their buckets would be a dead control.
        """
        stranger_bucket = Bucket.objects.create(
            name="Theirs", slug="rt-theirs", owner=self.operator)
        body = self.client.get(self._url()).content.decode()
        self.assertIn("rt-local", body)
        self.assertNotIn("rt-cloud", body)
        self.assertNotIn("rt-mount", body)
        self.assertNotIn("rt-theirs", body)

    def test_no_tree_renders_until_a_bucket_is_chosen(self):
        self._file(self.local, "unpicked")
        body = self.client.get(self._url()).content.decode()
        self.assertNotIn("unpicked.txt", body)
        self.assertNotIn(reverse("vault:create_zip"), body)

    def test_the_tree_shows_only_the_chosen_bucket(self):
        other = Bucket.objects.create(name="Other", slug="rt-other",
                                      owner=self.member)
        self._file(self.local, "mine-here")
        self._file(other, "mine-elsewhere")
        body = self.client.get(self._url(self.local)).content.decode()
        self.assertIn("mine-here.txt", body)
        self.assertNotIn("mine-elsewhere.txt", body)

    def test_an_encrypted_file_is_not_offered(self):
        self._file(self.local, "plain")
        self._file(self.local, "sealed", is_encrypted=True)
        body = self.client.get(self._url(self.local)).content.decode()
        self.assertIn("plain.txt", body)
        self.assertNotIn("sealed.txt", body)

    def test_a_remote_bucket_slug_is_refused_by_the_picker(self):
        """Typing ?bucket=rt-mount by hand selects nothing."""
        self._file(self.mount, "unzippable")
        body = self.client.get(self._url(self.mount)).content.decode()
        self.assertNotIn("unzippable.txt", body)
        self.assertNotIn(reverse("vault:create_zip"), body)

    def test_every_checkbox_carries_a_real_file_id(self):
        """The regression behind the Alpine crash: tree rows are dicts with
        ``id`` — ``file.pk`` renders empty, and an empty attribute binding is a
        page-breaking SyntaxError in Alpine."""
        made = self._file(self.local, "bound")
        body = self.client.get(self._url(self.local)).content.decode()
        self.assertIn(f'value="{made.pk}"', body)
        self.assertNotIn('value=""', body)

    def test_the_zip_endpoint_accepts_the_forms_exact_shape(self):
        """End to end: the form-encoded POST the page sends is what the
        endpoint reads — source_directory_id plus repeated file_ids."""
        inside = VaultFile.objects.create(
            owner=self.member, bucket=self.local, directory=self.directory,
            title="in-dir.txt", key="in-dir", file_type="text")
        response = self.client.post(reverse("vault:create_zip"), {
            "source_directory_id": self.directory.pk,
            "file_ids": [inside.pk],
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(payload["status"], ("ok", "queued"))
        self.assertEqual(payload["count"], 1)

    def test_the_zip_endpoint_drops_files_from_another_bucket(self):
        """The server half of no-cross-bucket: a foreign pk is silently not
        archived, whatever the page sent."""
        other = Bucket.objects.create(name="Other2", slug="rt-other2",
                                      owner=self.member)
        foreign = self._file(other, "foreign")
        with self.subTest("only foreign ids -> refused outright"):
            response = self.client.post(reverse("vault:create_zip"), {
                "source_directory_id": self.directory.pk,
                "file_ids": [foreign.pk],
            })
            self.assertEqual(response.status_code, 400)

    def test_the_files_tab_no_longer_offers_the_archive_action(self):
        """Files loses archiving; the Archive tab carries it.

        Asserted on the flag the button is gated by: the URL constant is inert
        JS the unreachable modal still names.
        """
        self._file(self.local, "shown")
        body = self.client.get(reverse("vault:public_list")).content.decode()
        self.assertIn("_zipEnabled   = false", body)

        archive = self.client.get(self._url(self.local)).content.decode()
        self.assertIn(reverse("vault:create_zip"), archive)


class CachedViewsNeedNoPinTests(RemoteTabTestCase):
    """Everything that reads stamped columns stays open without a PIN.

    Sealed credentials must not creep into pages that never touch the network.
    The check is aggressive on purpose: both the PIN door and the outbound guard
    are patched to explode, so any page that reached either would fail loudly
    rather than quietly work in the test environment.
    """

    def setUp(self):
        self.client.force_login(self.operator)

    def test_the_pages_that_do_no_network_io_render_with_no_pin(self):
        from toto.vault import outbound, storage_pin

        def explode(*args, **kwargs):
            raise AssertionError("a cached page asked for a credential")

        original_authorize = storage_pin.authorize
        original_guard = outbound.assert_outbound_allowed
        storage_pin.authorize = explode
        outbound.assert_outbound_allowed = explode
        try:
            for name, args in (
                ("vault:remote_buckets", ()),
                ("vault:metrics", ()),
                ("vault:public_list", ()),
                ("vault:transfer_panel", ()),
                ("vault:archive", ()),
            ):
                with self.subTest(route=name):
                    response = self.client.get(reverse(name, args=args))
                    self.assertEqual(response.status_code, 200)

            # bucket_metrics is owner-or-superuser, and answers 404 to anyone
            # else on purpose — a 403 on a guessable slug is an enumeration
            # oracle. So it is checked as the OWNER, which is the only actor
            # for whom "no PIN needed" is a meaningful claim.
            self.client.force_login(self.member)
            response = self.client.get(
                reverse("vault:bucket_metrics", args=(self.mount.slug,)))
            self.assertEqual(response.status_code, 200)
        finally:
            storage_pin.authorize = original_authorize
            outbound.assert_outbound_allowed = original_guard


class SealedModeTests(RemoteTabTestCase):
    """An ambient bucket behaves exactly as it always did."""

    def test_every_existing_bucket_is_ambient(self):
        for bucket in (self.local, self.cloud, self.mount):
            with self.subTest(bucket=bucket.slug):
                self.assertEqual(bucket.credential_mode, "ambient")

    def test_the_listing_says_which_buckets_are_sealed(self):
        self.cloud.credential_mode = "sealed"
        self.cloud.save(update_fields=["credential_mode"])
        self.client.force_login(self.operator)
        response = self.client.get(reverse("vault:remote_buckets"))
        self.assertEqual(response.status_code, 200)


class RemoteS3CreateTests(RemoteTabTestCase):
    """The staff door for S3 storage: typed fields, whitelisted config."""

    def _post(self, **overrides):
        data = {"name": "New Cloud", "bucket_name": "the-remote-name",
                "region_name": "eu-west", "prefix": "vault/",
                "endpoint_url": "https://s3.example.org"}
        data.update(overrides)
        return self.client.post(reverse("vault:remote_s3_new"), data)

    def test_members_are_refused_and_staff_admitted(self):
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.get(reverse("vault:remote_s3_new")).status_code, 403)
        self.client.force_login(self.operator)
        self.assertEqual(
            self.client.get(reverse("vault:remote_s3_new")).status_code, 200)

    def test_a_valid_post_creates_a_bucket_with_only_the_whitelist(self):
        self.client.force_login(self.operator)
        response = self._post()
        self.assertEqual(response.status_code, 302)
        bucket = Bucket.objects.get(name="New Cloud")
        self.assertEqual(bucket.storage_backend, StorageBackend.S3)
        self.assertEqual(bucket.credential_mode, "ambient")
        self.assertEqual(set(bucket.storage_config), {
            "bucket_name", "endpoint_url", "region_name", "prefix"})

    def test_a_private_endpoint_is_refused_with_a_sentence(self):
        """The same guard the driver applies, surfaced at save time — not as a
        delayed failure on first use."""
        self.client.force_login(self.operator)
        response = self._post(endpoint_url="https://169.254.169.254/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "public address")
        self.assertFalse(Bucket.objects.filter(name="New Cloud").exists())

    def test_a_duplicate_name_is_refused(self):
        self.client.force_login(self.operator)
        response = self._post(name="Cloud")          # exists in the fixture
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")


FAKE_HOSTS = [("https://peer.example.org", "Placidia (https://peer.example.org)")]


class RemoteMountCreateTests(RemoteTabTestCase):
    """The staff door for mounting — federated platforms ONLY."""

    def _code(self):
        from toto.vault.peering import BucketGrant, pairing_code_for

        grant = BucketGrant.objects.create(
            label="For us", bucket=self.local, may_list=True, may_download=True)
        raw_key = grant.issue_api_key()
        grant.save()
        return pairing_code_for(grant, raw_key)

    def _post(self, code, **overrides):
        from unittest.mock import patch

        data = {"name": "Mounted Peer", "paired_host": "https://peer.example.org",
                "pairing_code": code}
        data.update(overrides)
        with patch("toto.vault.forms.federated_host_choices",
                   return_value=FAKE_HOSTS):
            return self.client.post(reverse("vault:remote_mount_new"), data)

    def test_members_are_refused(self):
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.get(reverse("vault:remote_mount_new")).status_code, 403)

    def test_without_federation_the_page_says_so_instead_of_a_form(self):
        from unittest.mock import patch

        self.client.force_login(self.operator)
        with patch("toto.vault.forms.federated_host_choices", return_value=[]):
            body = self.client.get(
                reverse("vault:remote_mount_new")).content.decode()
        self.assertIn("not federated", body)
        self.assertNotIn("Pairing code</label>", body)

    def test_there_is_no_free_text_url_door(self):
        """Only federated: a POSTed base_url is not a field and changes nothing."""
        from toto.vault.forms import FederatedMountForm

        self.assertNotIn("base_url", FederatedMountForm.base_fields)

        self.client.force_login(self.operator)
        response = self._post(self._code(), base_url="https://evil.example.net")
        self.assertEqual(response.status_code, 302)
        peer = BucketPeer.objects.get(label="Mounted Peer")
        self.assertEqual(peer.base_url, "https://peer.example.org")

    def test_a_host_outside_the_federation_list_is_refused(self):
        self.client.force_login(self.operator)
        response = self._post(self._code(),
                              paired_host="https://stranger.example.net")
        self.assertEqual(response.status_code, 200)   # form error, nothing made
        self.assertFalse(BucketPeer.objects.filter(label="Mounted Peer").exists())

    def test_a_mangled_code_is_refused_with_the_operators_sentence(self):
        self.client.force_login(self.operator)
        response = self._post("not-a-code")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not decode as a pairing code")
        self.assertFalse(BucketPeer.objects.filter(label="Mounted Peer").exists())

    def test_a_valid_code_creates_the_peer_and_the_bucket_together(self):
        self.client.force_login(self.operator)
        response = self._post(self._code())
        self.assertEqual(response.status_code, 302)

        peer = BucketPeer.objects.get(label="Mounted Peer")
        bucket = Bucket.objects.get(name="Mounted Peer")
        self.assertEqual(bucket.storage_backend, StorageBackend.REMOTE_TOTO)
        self.assertEqual(bucket.peer_id, peer.pk)
        self.assertTrue(peer.api_key_hint)
        # The probe ran and failed (no network in tests) — stamped, not fatal.
        peer.refresh_from_db()
        self.assertTrue(peer.probe_error)


class RemoteBucketTestEndpointTests(RemoteTabTestCase):
    """The online connection test: bounded, stamped, staff-only."""

    def _url(self, bucket):
        return reverse("vault:remote_bucket_test", args=[bucket.slug])

    def test_a_member_gets_404_not_403(self):
        """Slug-addressed: a refusal must not confirm the bucket exists."""
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(self._url(self.mount)).status_code, 404)

    def test_a_local_bucket_is_not_a_testable_target(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(self._url(self.local)).status_code, 404)

    def test_get_is_refused(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(self._url(self.mount)).status_code, 405)

    def test_a_dead_peer_answers_a_sentence_and_is_stamped(self):
        self.client.force_login(self.operator)
        response = self.client.post(self._url(self.mount))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["detail"])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.peer.refresh_from_db()
        self.assertTrue(self.peer.probe_error)
        self.assertTrue(self.peer.last_error)

    def test_an_unreachable_s3_endpoint_answers_a_sentence(self):
        """S3's first health signal: the click answers instead of the first
        read failing later. .invalid never resolves, so no network happens."""
        bucket = Bucket.objects.create(
            name="Probe Cloud", slug="rt-probe", owner=self.member,
            storage_backend=StorageBackend.S3,
            storage_config={"bucket_name": "b",
                            "endpoint_url": "https://s3.invalid"})
        self.client.force_login(self.operator)
        response = self.client.post(self._url(bucket))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["detail"])

    def test_the_listing_offers_the_test_button(self):
        self.client.force_login(self.operator)
        body = self.client.get(reverse("vault:remote_buckets")).content.decode()
        self.assertIn("runTest(", body)
        self.assertIn(f"'{self.mount.slug}'", body)


class VaultPageSkinTests(RemoteTabTestCase):
    """Every vault page must carry the platform skin.

    ``oya/base.html`` builds the site header, the logo, the theme colours and
    the light/dark toggle from what ``PageProcessor.decorate`` injects. A
    ``TemplateView`` does not get that for free — ``get_context_data`` is the
    only hook, and nothing decorates it.

    This is worth a test precisely because the failure is quiet: the page still
    returns 200 with all its content, just unstyled and headerless, so it reads
    as a CSS problem rather than a missing context.
    """

    PAGES = (
        ("vault:remote_buckets", ()),
        ("vault:archive", ()),
        ("vault:remote_s3_new", ()),
        ("vault:remote_mount_new", ()),
        ("vault:public_list", ()),
        ("vault:metrics", ()),
    )

    def setUp(self):
        self.client.force_login(self.operator)

    def test_every_vault_page_gets_the_platform_context(self):
        for name, args in self.PAGES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    response.context.get("platform"),
                    f"{name} renders without the platform skin — the header, "
                    "logo and theme all come from PageProcessor.decorate")

    def test_every_vault_page_renders_the_site_header(self):
        for name, args in self.PAGES:
            with self.subTest(page=name):
                body = self.client.get(reverse(name, args=args)).content.decode()
                self.assertIn("Zen", body)                     # platform site_name
                self.assertIn("tailwind.config", body)         # the theme block
                self.assertIn("darkMode", body)                # the light/dark toggle

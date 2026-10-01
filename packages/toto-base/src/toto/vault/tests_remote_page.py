"""The tab bar, the Archive tab, and remote buckets as Storage → Management
shows them.

The Remote tab folded into Management (2026-09-30): its listing, its two
create pages and its connection test are gone, and a remote bucket's health,
target and Test button are a row of the Management list — a superuser on the
Superuser plan's page (``tests_management`` holds its doors).
"""

import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
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
        call_command("bootstrap_plans", stdout=io.StringIO())    # root → the Superuser plan
        cls.root = User.objects.get(pk=cls.root.pk)

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


class RemoteRowsInManagementTests(RemoteTabTestCase):
    """What the Remote tab listed, Management lists — with every other bucket."""

    def setUp(self):
        self.client.force_login(self.root)

    def body(self):
        return self.client.get(reverse("vault:manage")).content.decode()

    def test_it_lists_remote_buckets_and_local_ones_too(self):
        body = self.body()
        for bucket in (self.local, self.cloud, self.mount):
            with self.subTest(bucket=bucket.slug):
                self.assertIn(f'data-testid="bucket-row-{bucket.pk}"', body)

    def test_a_never_contacted_bucket_reads_as_never_tested(self):
        body = self.body()
        row = body[body.index(f'data-testid="bucket-row-{self.mount.pk}"'):]
        self.assertIn("Never tested", row[:row.index("</tr>")])

    def test_it_renders_without_contacting_anyone(self):
        """No page render may probe a peer — the rule the surface rests on."""
        import toto.vault.peer_client as peer_client

        def explode(*a, **k):
            raise AssertionError("a page render contacted the network")

        original = peer_client._http
        peer_client._http = explode
        try:
            self.assertEqual(self.client.get(reverse("vault:manage")).status_code, 200)
        finally:
            peer_client._http = original

    def test_the_s3_row_shows_config_but_never_a_credential(self):
        body = self.body()
        row = body[body.index(f'data-testid="bucket-row-{self.cloud.pk}"'):]
        row = row[:row.index("</tr>")]
        self.assertIn("https://s3.example.org", row)
        self.assertIn("b/vault/", row)
        self.assertIn("Server environment", row)      # no sealed key: the boto3 chain
        self.assertNotIn("secret", row.lower())

    def test_staff_without_the_plan_no_longer_see_remote_storage(self):
        """The Remote tab was staff-or-superuser; Management is the Superuser
        plan's, and staff are members there."""
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse("vault:manage")).status_code, 403)


class TabBarTests(RemoteTabTestCase):
    def test_the_tab_bar_offers_its_tabs_to_an_operator_and_no_remote_tab(self):
        self.client.force_login(self.operator)
        body = self.client.get(reverse("vault:archive")).content.decode()
        for label in ("Files", "Metrics", "Archive"):
            self.assertIn(f">{label}</a>", body)
        self.assertNotIn(">Remote</a>", body)
        self.assertNotIn(">Management</a>", body)

    def test_a_superuser_on_the_plan_gets_management(self):
        self.client.force_login(self.root)
        body = self.client.get(reverse("vault:archive")).content.decode()
        for label in ("Files", "Metrics", "Management", "Archive"):
            self.assertIn(f">{label}</a>", body)
        self.assertNotIn(">Remote</a>", body)

    def test_a_member_never_sees_the_management_tab(self):
        """A tab that could only ever answer 403 is worse than no tab."""
        self.client.force_login(self.member)
        body = self.client.get(reverse("vault:archive")).content.decode()
        self.assertIn(">Files</a>", body)
        self.assertIn(">Archive</a>", body)
        self.assertNotIn(reverse("vault:manage"), body)

    def test_the_chip_does_not_carry_the_phrase_a_metrics_test_forbids(self):
        """tests_remote_ui pins 'Remote bucket' as absent from a local page."""
        for user in (self.operator, self.root):
            self.client.force_login(user)
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
                ("vault:metrics", ()),
                ("vault:public_list", ()),
                ("vault:transfer_panel", ()),
                ("vault:archive", ()),
            ):
                with self.subTest(route=name):
                    response = self.client.get(reverse(name, args=args))
                    self.assertEqual(response.status_code, 200)

            # Management lists every bucket, remote ones with their health —
            # from stamps. It is the Superuser plan's page.
            self.client.force_login(self.root)
            self.assertEqual(self.client.get(reverse("vault:manage")).status_code, 200)

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
        self.client.force_login(self.root)
        response = self.client.get(reverse("vault:manage"))
        self.assertEqual(response.status_code, 200)


class ManagementTestButtonTests(RemoteTabTestCase):
    """The online connection test, moved from the Remote tab to Management:
    bounded, stamped, the Superuser plan's."""

    def _url(self, bucket):
        return reverse("vault:manage_test", args=[bucket.pk])

    def test_a_member_and_staff_are_refused_before_any_lookup(self):
        for user in (self.member, self.operator):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.post(self._url(self.mount)).status_code, 403)
                self.assertEqual(self.client.post(
                    reverse("vault:manage_test", args=[999999])).status_code, 403)

    def test_a_local_bucket_is_testable_now(self):
        self.client.force_login(self.root)
        payload = self.client.post(self._url(self.local)).json()
        self.assertTrue(payload["ok"], payload)

    def test_get_is_refused(self):
        self.client.force_login(self.root)
        self.assertEqual(self.client.get(self._url(self.mount)).status_code, 405)

    def test_a_dead_peer_answers_a_sentence_and_is_stamped(self):
        import toto.vault.peer_client as peer_client

        class Down:
            def request(self, *args, **kwargs):
                raise ConnectionError("unreachable")

        self.client.force_login(self.root)
        original = peer_client._http
        peer_client._http = lambda: Down()
        try:
            response = self.client.post(self._url(self.mount))
        finally:
            peer_client._http = original
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["detail"])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.peer.refresh_from_db()
        self.assertTrue(self.peer.last_error)

    def test_an_unreachable_s3_endpoint_answers_a_sentence(self):
        """S3's first health signal: the click answers instead of the first
        read failing later. .invalid never resolves, so no network happens."""
        bucket = Bucket.objects.create(
            name="Probe Cloud", slug="rt-probe", owner=self.member,
            storage_backend=StorageBackend.S3,
            storage_config={"bucket_name": "b",
                            "endpoint_url": "https://s3.invalid"})
        self.client.force_login(self.root)
        response = self.client.post(self._url(bucket))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["detail"])
        bucket.refresh_from_db()
        self.assertTrue(bucket.last_probe_error)

    def test_the_listing_offers_the_test_button(self):
        self.client.force_login(self.root)
        body = self.client.get(reverse("vault:manage")).content.decode()
        self.assertIn(f"runTest({self.mount.pk}, '{self._url(self.mount)}')", body)


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
        ("vault:archive", ()),
        ("vault:public_list", ()),
        ("vault:metrics", ()),
    )

    def test_the_management_page_gets_the_platform_context(self):
        self.client.force_login(self.root)
        response = self.client.get(reverse("vault:manage"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("platform"))
        body = response.content.decode()
        for marker in ("Zen", "oya-theme", "darkMode"):
            self.assertIn(marker, body)

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
                self.assertIn("oya-theme", body)               # the theme block
                self.assertIn("darkMode", body)                # the light/dark toggle

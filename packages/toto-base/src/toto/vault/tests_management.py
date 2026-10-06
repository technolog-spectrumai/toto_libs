"""Storage → Management (``manage_views``, 2026-09-30).

Every bucket in one list (a table on a wide screen, cards on a narrow one),
and Create / Edit / Test / Delete — for a superuser ON THE SUPERUSER PLAN
only: anybody else is refused by every door, and never sees the tab.

S3 is faked the way ``tests_storage_adapters`` fakes it (boto3 swapped in
``sys.modules``); a secret typed into Create must never come back — not in a
response, not in the session's draft, not on the page after a refusal.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.vault.tests_management
"""

import io
import json
import os
import re
import sys
import tempfile
from unittest import mock, skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform

from . import manage_views
from .models import Bucket, BucketSecret, StorageBackend, VaultDirectory, VaultFile
from .peering import BucketPeer
from .storage_adapters import StorageAdapter
from .tests_storage_adapters import (
    KEY_ID,
    PERSISTENT,
    SECRET,
    assistant,
    audit_actions,
    audit_dump,
    fake_boto3,
    public_dns,
)

User = get_user_model()

_MEDIA = tempfile.mkdtemp(prefix="vault-manage-")

AWS = {"kind": "aws_s3", "name": "Cloud Two", "bucket_name": "my-bucket", "region": "eu-west-1",
       "prefix": "", "access_key_id": KEY_ID, "secret_access_key": SECRET,
       "storage_quota_mb": ""}

TOKEN = "magic-token-0123456789"


def sealed(bucket):
    with override_settings(**PERSISTENT):
        row = BucketSecret(bucket=bucket)
        row.seal({"aws_access_key_id": KEY_ID, "aws_secret_access_key": SECRET})
        row.save()
    return bucket


@override_settings(MEDIA_ROOT=_MEDIA, VAULT_EXTERNAL_BUCKETS=True)
class ManageFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Zen", author="T", publication_year=2026, active=True)
        cls.member = User.objects.create_user("mg-member", password="x", first_name="Mira",
                                              last_name="Member", email="mira@example.org")
        cls.staff = User.objects.create_user("mg-staff", password="x", is_staff=True)
        cls.root = User.objects.create_superuser("mg-root", "root@example.org", "x")
        call_command("bootstrap_plans", stdout=io.StringIO())      # root → the Superuser plan
        cls.root = User.objects.get(pk=cls.root.pk)
        # Made after the bootstrap: a superuser who never took the plan.
        cls.bare = User.objects.create_superuser("mg-bare", "bare@example.org", "x")

        cls.local = Bucket.objects.create(name="Alpha", slug="mg-alpha", owner=cls.member,
                                          created_by=cls.root)
        cls.personal = Bucket.objects.create(name="Personal — mg-member", slug="personal-mg-member",
                                             owner=cls.member)
        aws = StorageAdapter.get("aws_s3")
        cls.cloud = Bucket.objects.create(
            name="Cloud", slug="mg-cloud", owner=cls.member, storage_backend=StorageBackend.S3,
            provider=aws.provider(),
            storage_config={"bucket_name": "my-bucket", "region_name": "eu-west-1",
                            "prefix": "vault/"})
        cls.peer = BucketPeer.objects.create(
            label="Placidia", base_url="https://peer.example.org",
            grant_uid="00000000-0000-0000-0000-00000000ab12",
            magic_token=TOKEN, remote_bucket_slug="theirs")
        cls.mount = Bucket.objects.create(
            name="Mounted", slug="mg-mount", owner=cls.member,
            storage_backend=StorageBackend.REMOTE_TOTO, peer=cls.peer)
        cls.ownerless = Bucket.objects.create(name="Orphan", slug="mg-orphan", owner=None)

    def as_root(self):
        self.client.force_login(self.root)
        return self.client

    def page(self, **params):
        return self.as_root().get(reverse("vault:manage"), params).content.decode()

    def local_file(self, bucket, name="doc", content=b"hello"):
        vf = VaultFile(owner=self.member, title=f"{name}.txt", key=name, file_type="text",
                       bucket=bucket)
        vf.file.save(f"{name}.txt", SimpleUploadedFile(f"{name}.txt", content), save=True)
        return vf

    def draft(self):
        return self.client.session.get(manage_views.DRAFT_KEY)


# ---------------------------------------------------------------------------
# Who may open it
# ---------------------------------------------------------------------------

class GateTests(ManageFixture):
    """Every door, every kind of visitor."""

    def doors(self):
        pk = self.local.pk
        return [
            ("get", reverse("vault:manage"), {}, False),
            ("get", reverse("vault:manage_people"), {"q": "mg"}, True),
            ("post", reverse("vault:manage_create"), {"kind": "local", "name": "Sneaky",
                                                      "owner": self.member.pk}, False),
            ("post", reverse("vault:manage_edit", args=[pk]), {"name": "Renamed"}, False),
            ("post", reverse("vault:manage_delete", args=[pk]), {"confirm_name": "Alpha"}, False),
            ("post", reverse("vault:manage_test", args=[pk]), {}, True),
        ]

    def assert_nothing_changed(self):
        self.local.refresh_from_db()
        self.assertEqual(self.local.name, "Alpha")
        self.assertFalse(self.local.is_being_deleted)
        self.assertIsNone(self.local.last_probe_at)
        self.assertFalse(Bucket.objects.filter(name="Sneaky").exists())

    def test_anonymous_pages_go_to_the_login_and_json_doors_answer_403(self):
        for method, url, data, is_json in self.doors():
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, data)
                if response.status_code == 403:
                    # The door's own answer (a host with no site-wide login gate).
                    self.assertTrue(is_json)
                    self.assertIn("error", response.json())
                else:
                    self.assertEqual(response.status_code, 302)
                    self.assertIn("login", response["Location"])
        self.assert_nothing_changed()

    def test_members_staff_and_a_superuser_without_the_plan_are_refused(self):
        visitors = [("member", self.member), ("staff", self.staff)]
        if apps.is_installed("toto.subscriptions"):
            visitors.append(("superuser without the plan", self.bare))
        for label, user in visitors:
            self.client.force_login(user)
            for method, url, data, is_json in self.doors():
                with self.subTest(visitor=label, url=url):
                    response = getattr(self.client, method)(url, data)
                    self.assertEqual(response.status_code, 403)
                    if is_json:
                        self.assertEqual(response["Content-Type"], "application/json")
                        self.assertIn("Superuser plan", response.json()["error"])
        self.assert_nothing_changed()

    def test_the_refusal_comes_before_the_lookup(self):
        """A door addressed by a bucket answers a stranger the same for a bucket
        that exists and one that does not."""
        self.client.force_login(self.member)
        for name in ("manage_edit", "manage_delete", "manage_test"):
            with self.subTest(door=name):
                self.assertEqual(self.client.post(reverse(f"vault:{name}", args=[999999])).status_code,
                                 403)

    def test_a_superuser_on_the_plan_opens_every_door(self):
        client = self.as_root()
        self.assertEqual(client.get(reverse("vault:manage")).status_code, 200)
        self.assertEqual(client.get(reverse("vault:manage_people"), {"q": "mg"}).status_code, 200)
        self.assertEqual(client.post(reverse("vault:manage_test", args=[self.local.pk])).status_code,
                         200)

    @skipUnless(apps.is_installed("toto.subscriptions"), "the host sells no plan")
    def test_the_plan_alone_is_not_enough_either(self):
        """Stripped of the account, the plan holder is refused (the plan is never
        the privilege)."""
        demoted = User.objects.get(pk=self.root.pk)
        demoted.is_superuser = False
        demoted.save(update_fields=["is_superuser"])
        self.client.force_login(demoted)
        self.assertEqual(self.client.get(reverse("vault:manage")).status_code, 403)

    def test_get_is_refused_on_the_doors_that_change_things(self):
        client = self.as_root()
        for name in ("manage_create",):
            self.assertEqual(client.get(reverse(f"vault:{name}")).status_code, 405)
        for name in ("manage_edit", "manage_delete", "manage_test"):
            self.assertEqual(client.get(reverse(f"vault:{name}", args=[self.local.pk])).status_code,
                             405)


class TabTests(ManageFixture):
    def body(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("vault:archive")).content.decode()

    def test_only_a_superuser_on_the_plan_sees_the_tab(self):
        url = reverse("vault:manage")
        body = self.body(self.root)
        self.assertIn(f'href="{url}"', body)
        self.assertIn(">Management</a>", body)
        others = [self.member, self.staff]
        if apps.is_installed("toto.subscriptions"):
            others.append(self.bare)
        for user in others:
            with self.subTest(user=user.username):
                body = self.body(user)
                self.assertNotIn(f'href="{url}"', body)
                self.assertNotIn(">Management</a>", body)

    def test_the_remote_tab_is_gone_for_everyone(self):
        for user in (self.member, self.staff, self.root):
            with self.subTest(user=user.username):
                body = self.body(user)
                self.assertNotIn(">Remote</a>", body)
                self.assertNotIn("/vault/remote/", body)

    def test_the_tab_sits_between_metrics_and_archive(self):
        body = self.body(self.root)
        self.assertLess(body.index(">Metrics</a>"), body.index(">Management</a>"))
        self.assertLess(body.index(">Management</a>"), body.index(">Archive</a>"))

    def test_the_tab_is_marked_current_on_its_page(self):
        body = self.page()
        start = body.index('data-testid="vault-manage-tab"')
        self.assertIn('aria-current="page"', body[start:body.index("</a>", start)])


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------

class ListTests(ManageFixture):
    def test_every_bucket_is_in_the_table_and_in_the_cards(self):
        body = self.page()
        self.assertIn('data-testid="buckets-table"', body)
        self.assertIn('data-testid="buckets-cards"', body)
        for bucket in Bucket.objects.all():
            with self.subTest(bucket=bucket.slug):
                self.assertIn(f'data-testid="bucket-row-{bucket.pk}"', body)
                self.assertIn(f'data-testid="bucket-card-{bucket.pk}"', body)
        self.assertIn("personal-mg-member", body)

    def test_the_table_is_for_wide_screens_and_the_cards_for_narrow_ones(self):
        body = self.page()
        table = body.index('data-testid="buckets-table"')
        self.assertIn("hidden overflow-x-auto rounded-2xl border shadow-sm md:block",
                      body[body.rindex("<div", 0, table):table])
        cards = body.index('data-testid="buckets-cards"')
        self.assertIn("md:hidden", body[body.rindex("<div", 0, cards):cards])

    def test_a_remote_bucket_wears_the_cloud_badge_and_a_local_one_does_not(self):
        body = self.page()
        badge = '<i class="fa-solid fa-cloud mr-1"></i>Remote · '
        self.assertIn(badge + "Amazon S3", body)
        self.assertIn(badge + "Placidia", body)
        # Twice each: once in the table, once in the card.
        self.assertEqual(body.count(badge), 4)
        self.assertEqual(body.count('data-testid="bucket-local-badge"'), 2 * 3)

    def test_the_columns(self):
        self.local_file(self.local, "one", b"12345")
        self.local_file(self.local, "two", b"123")
        body = self.page()
        row = body[body.index(f'data-testid="bucket-row-{self.local.pk}"'):]
        row = row[:row.index("</tr>")]
        self.assertIn("Alpha", row)
        self.assertIn("This server", row)                         # target
        self.assertIn("Mira Member", row)                         # owner
        self.assertIn("mg-member", row)
        self.assertIn("mg-root", row)                             # creator
        self.assertIn(timezone.localdate(self.local.created_at).strftime("%Y-%m-%d"), row)
        self.assertIn("Never tested", row)                        # health, from stamps
        self.assertIn("2 files", row)
        self.assertIn("8\xa0bytes", row)
        for door in ("bucket-test-", "bucket-edit-", "bucket-delete-"):
            self.assertIn(f'data-testid="{door}{self.local.pk}"', row)
        cloud = body[body.index(f'data-testid="bucket-row-{self.cloud.pk}"'):]
        self.assertIn("my-bucket/vault/ · eu-west-1 · Amazon S3", cloud[:cloud.index("</tr>")])
        mount = body[body.index(f'data-testid="bucket-row-{self.mount.pk}"'):]
        self.assertIn("https://peer.example.org · theirs", mount[:mount.index("</tr>")])

    def test_an_ownerless_bucket_renders(self):
        body = self.page()
        row = body[body.index(f'data-testid="bucket-row-{self.ownerless.pk}"'):]
        row = row[:row.index("</tr>")]
        self.assertIn("Orphan", row)
        self.assertIn("no owner", row)
        card = body[body.index(f'data-testid="bucket-card-{self.ownerless.pk}"'):]
        self.assertIn("no owner", card[:card.index("</article>")])

    def test_a_bucket_being_deleted_is_marked_and_takes_no_edit(self):
        Bucket.objects.filter(pk=self.local.pk).update(
            deletion_requested_at=timezone.now(), deletion_error="Held by another app.")
        body = self.page()
        row = body[body.index(f'data-testid="bucket-row-{self.local.pk}"'):]
        row = row[:row.index("</tr>")]
        self.assertIn("Deletion stopped", row)
        self.assertIn("Held by another app.", row)
        self.assertNotIn(f'data-testid="bucket-edit-{self.local.pk}"', row)
        self.assertIn("Delete again", row)

    def test_a_stamped_test_shows_as_its_answer(self):
        Bucket.objects.filter(pk=self.cloud.pk).update(last_probe_at=timezone.now(),
                                                      last_probe_error="NoSuchBucket 404")
        body = self.page()
        row = body[body.index(f'data-testid="bucket-row-{self.cloud.pk}"'):]
        self.assertIn("Failed", row[:row.index("</tr>")])
        self.assertIn("NoSuchBucket 404", row[:row.index("</tr>")])

    def test_a_sealed_key_shows_its_hint_and_never_itself(self):
        sealed(self.cloud)
        body = self.page()
        self.assertIn("…" + KEY_ID[-4:], body)
        self.assertNotIn(SECRET, body)
        self.assertNotIn(KEY_ID, body)
        self.assertNotIn(TOKEN, body)

    def test_it_renders_without_contacting_anyone(self):
        """No page render may probe, open a sealed key, or call out."""
        from . import outbound, peer_client, storage_pin

        def explode(*args, **kwargs):
            raise AssertionError("a page render reached for the network or a credential")

        sealed(self.cloud)
        with mock.patch.object(peer_client, "_http", explode), \
                mock.patch.object(outbound, "assert_outbound_allowed", explode), \
                mock.patch.object(storage_pin, "authorize", explode), \
                mock.patch.object(BucketSecret, "open", explode):
            response = self.as_root().get(reverse("vault:manage"))
        self.assertEqual(response.status_code, 200)

    def test_the_page_carries_the_platform_skin(self):
        response = self.as_root().get(reverse("vault:manage"))
        self.assertTrue(response.context.get("platform"))
        self.assertIn("darkMode", response.content.decode())

    def test_pagination(self):
        for n in range(4):
            Bucket.objects.create(name=f"Zeta {n}", slug=f"mg-zeta-{n}", owner=self.member)
        pages = -(-Bucket.objects.count() // 3)
        self.assertGreater(pages, 2)
        with mock.patch.object(manage_views, "PER_PAGE", 3):
            first = self.page()
            last = self.page(page=pages)
        zeta = Bucket.objects.get(slug="mg-zeta-3").pk
        # By name, case-blind: Alpha first, the Zetas last — in the table and the cards.
        for testid in ("bucket-row-", "bucket-card-"):
            self.assertIn(f'data-testid="{testid}{self.local.pk}"', first)
            self.assertNotIn(f'data-testid="{testid}{zeta}"', first)
            self.assertIn(f'data-testid="{testid}{zeta}"', last)
            self.assertNotIn(f'data-testid="{testid}{self.local.pk}"', last)
        self.assertIn('href="?page=2"', first)
        self.assertIn(f"1 / {pages}", first)
        self.assertIn(f"{pages} / {pages}", last)

    def test_the_create_form_has_the_registry_kinds_fields_but_not_the_guided_ones(self):
        """The guided kind (another Zenobia) is a card of the dialog since
        2026-10-06, with steps of its own; the form still has no fieldset for
        it, and the page's kinds — what the form may post — do not name it."""
        response = self.as_root().get(reverse("vault:manage"))
        body = response.content.decode()
        for key in ("local", "aws_s3", "ovh_s3"):
            self.assertIn(f'data-testid="bucket-kind-{key}"', body)
            self.assertIn(f'data-testid="bucket-fields-{key}"', body)
        self.assertIn('data-testid="bucket-kind-zenobia_remote"', body)
        self.assertNotIn('data-testid="bucket-fields-zenobia_remote"', body)
        self.assertNotIn('name="pairing_code"', body)
        self.assertEqual([k["key"] for k in response.context["kinds"]], ["local", "aws_s3", "ovh_s3"])
        self.assertNotIn('data-testid="bucket-kind-s3"', body)
        # The S3 kinds' own fields: never a value for the secret.
        self.assertIn('name="secret_access_key"', body)
        self.assertIn('autocomplete="new-password"', body)

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_a_local_only_host_offers_this_server_only(self):
        body = self.page()
        self.assertIn('data-testid="bucket-kind-local"', body)
        self.assertNotIn('data-testid="bucket-kind-aws_s3"', body)
        self.assertNotIn('data-testid="bucket-kind-zenobia_remote"', body)
        self.assertNotIn('data-testid="bucket-connect-root"', body)

    def test_the_delete_modal_says_what_goes_and_a_mount_only_disconnects(self):
        response = self.as_root().get(reverse("vault:manage"))
        rows = {r["pk"]: r for r in response.context["script_rows"]}
        self.assertFalse(rows[self.local.pk]["zenobia_remote"])
        self.assertTrue(rows[self.mount.pk]["zenobia_remote"])
        self.assertIn("nothing is deleted there", " ".join(rows[self.mount.pk]["keeps"]))
        body = response.content.decode()
        self.assertIn("may permanently destroy all the data", body)
        self.assertIn("nothing is deleted on the other Zenobia", body)
        self.assertIn(reverse("vault:manage_delete", args=[self.local.pk]),
                      rows[self.local.pk]["delete_url"])

    def test_no_slot_comment_of_the_share_and_connect_partials_reaches_the_page(self):
        body = self.page()
        self.assertNotIn("SLOT", body)


# ---------------------------------------------------------------------------
# The owner search
# ---------------------------------------------------------------------------

class PeopleTests(ManageFixture):
    def search(self, q):
        response = self.as_root().get(reverse("vault:manage_people"), {"q": q})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        return response.json()["people"]

    def test_any_active_account_by_name_username_or_email(self):
        self.assertEqual([p["username"] for p in self.search("Mira")], ["mg-member"])
        self.assertEqual([p["username"] for p in self.search("mg-sta")], ["mg-staff"])
        self.assertEqual([p["username"] for p in self.search("mira@example")], ["mg-member"])

    def test_an_inactive_account_is_never_offered(self):
        User.objects.create_user("mg-gone", password="x", is_active=False)
        self.assertEqual(self.search("mg-gone"), [])

    def test_no_email_address_is_answered(self):
        people = self.search("mira")
        self.assertEqual(set(people[0]), {"pk", "name", "username"})
        self.assertNotIn("mira@example.org", json.dumps(people))

    def test_an_empty_query_answers_nobody(self):
        self.assertEqual(self.search("   "), [])


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class CreateTests(ManageFixture):
    def post(self, **data):
        return self.as_root().post(reverse("vault:manage_create"), data)

    def test_a_local_bucket(self):
        response = self.post(kind="local", name="Fresh", owner=self.member.pk,
                             storage_quota_mb="50")
        self.assertRedirects(response, reverse("vault:manage"), fetch_redirect_response=False)
        bucket = Bucket.objects.get(name="Fresh")
        self.assertEqual(bucket.owner, self.member)
        self.assertEqual(bucket.created_by, self.root)
        self.assertEqual(bucket.storage_backend, StorageBackend.LOCAL)
        self.assertEqual(bucket.storage_quota_mb, 50)
        self.assertIn("VAULT.BUCKET.CREATED", audit_actions())
        self.assertIsNone(self.draft())

    def test_every_problem_is_named_and_the_modal_reopens_with_what_was_typed(self):
        response = self.post(kind="local", name="Alpha", owner="", storage_quota_mb="lots")
        self.assertRedirects(response, reverse("vault:manage"), fetch_redirect_response=False)
        draft = self.draft()
        self.assertEqual(draft["open"], "create")
        self.assertEqual(set(draft["errors"]), {"name", "owner", "storage_quota_mb"})
        self.assertEqual(Bucket.objects.filter(name="Alpha").count(), 1)
        body = self.client.get(reverse("vault:manage")).content.decode()
        self.assertIn("A bucket with that name already exists.", body)
        self.assertIn("Choose who owns the bucket.", body)
        self.assertIn('value="Alpha"', body)
        # The draft is spent: the next visit is a clean page.
        self.assertIsNone(self.draft())

    def test_an_inactive_owner_is_refused(self):
        gone = User.objects.create_user("mg-inactive", password="x", is_active=False)
        self.post(kind="local", name="Nobodys", owner=gone.pk)
        self.assertFalse(Bucket.objects.filter(name="Nobodys").exists())
        self.assertIn("not active", " ".join(self.draft()["errors"]["owner"]))

    def test_the_guided_kind_and_unknown_kinds_are_refused(self):
        for kind in ("zenobia_remote", "s3", "nonsense", ""):
            with self.subTest(kind=kind):
                self.post(kind=kind, name="Sideways", owner=self.member.pk, pairing_code="x")
                self.assertFalse(Bucket.objects.filter(name="Sideways").exists())
                self.assertIn("kind", self.draft()["errors"])

    @override_settings(**PERSISTENT)
    def test_an_s3_bucket_whose_test_fails_is_not_saved_and_the_secret_never_comes_back(self):
        boto3, session, client, modules = fake_boto3(
            head_error=Exception(f"403 Forbidden for {KEY_ID}"))
        with mock.patch.dict(sys.modules, modules):
            response = self.post(**dict(AWS, owner=self.member.pk))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Bucket.objects.filter(name="Cloud Two").exists())
        self.assertFalse(BucketSecret.objects.exists())
        draft = self.draft()
        dumped = json.dumps(draft)
        self.assertIn("did not answer the connection test", draft["error"])
        self.assertIn("403", draft["error"])
        self.assertNotIn(SECRET, dumped)
        self.assertNotIn(KEY_ID, dumped)
        self.assertEqual(draft["values"], {"bucket_name": "my-bucket", "region": "eu-west-1",
                                           "prefix": ""})
        body = self.client.get(reverse("vault:manage")).content.decode()
        self.assertIn("did not answer the connection test", body)
        self.assertIn('value="my-bucket"', body)
        self.assertNotIn(SECRET, body)
        self.assertNotIn(KEY_ID, body)

    @override_settings(**PERSISTENT)
    def test_an_s3_bucket_whose_test_passes_is_saved_with_its_key_sealed(self):
        boto3, session, client, modules = fake_boto3()
        with mock.patch.dict(sys.modules, modules):
            response = self.post(**dict(AWS, owner=self.member.pk))
        self.assertEqual(response.status_code, 302)
        bucket = Bucket.objects.get(name="Cloud Two")
        self.assertEqual(bucket.created_by, self.root)
        self.assertEqual(bucket.provider.name, "aws")
        self.assertEqual(BucketSecret.objects.get(bucket=bucket).open()["aws_secret_access_key"],
                         SECRET)
        client.head_bucket.assert_called_with(Bucket="my-bucket")
        body = self.client.get(reverse("vault:manage")).content.decode()
        self.assertIn("Cloud Two", body)
        self.assertNotIn(SECRET, body)
        self.assertNotIn(KEY_ID, body)

    def test_an_s3_bucket_on_a_host_without_a_permanent_key_is_refused(self):
        with override_settings(VAULT_FIELD_KEY_PERSISTENT=False), \
                mock.patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": ""}):
            body = self.page()
            self.post(**dict(AWS, owner=self.member.pk))
        self.assertIn("FIELD_ENCRYPTION_KEY", body)            # said in the modal first
        self.assertFalse(Bucket.objects.filter(name="Cloud Two").exists())
        self.assertIn("FIELD_ENCRYPTION_KEY", json.dumps(self.draft()))

    def test_a_bad_s3_field_is_named_beside_its_field(self):
        self.post(**dict(AWS, owner=self.member.pk, bucket_name="Bad_Name", region="mars-1"))
        errors = self.draft()["errors"]
        self.assertIn("bucket_name", errors)
        self.assertIn("region", errors)
        self.assertNotIn(SECRET, json.dumps(self.draft()))


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

class EditTests(ManageFixture):
    def post(self, bucket, **data):
        return self.as_root().post(reverse("vault:manage_edit", args=[bucket.pk]), data)

    def test_the_three_fields_change_and_are_recorded(self):
        response = self.post(self.local, name="Alpha Two", owner=self.staff.pk,
                             storage_quota_mb="10")
        self.assertEqual(response.status_code, 302)
        self.local.refresh_from_db()
        self.assertEqual((self.local.name, self.local.owner, self.local.storage_quota_mb),
                         ("Alpha Two", self.staff, 10))
        from toto.audit.models import AuditRecord

        record = AuditRecord.objects.get(action="VAULT.BUCKET.UPDATED")
        changes = record.metadata["changes"]
        self.assertEqual(changes["name"], {"before": "Alpha", "after": "Alpha Two"})
        self.assertEqual(changes["owner"], {"before": "mg-member", "after": "mg-staff"})
        self.assertEqual(changes["storage_quota_mb"], {"before": None, "after": 10})
        self.assertEqual(sorted(changes), ["name", "owner", "storage_quota_mb"])

    def test_nothing_that_moves_data_can_be_posted(self):
        for field, value in (("storage_backend", "s3"), ("provider", "1"),
                             ("endpoint_url", "https://evil.example.net"), ("prefix", "other/"),
                             ("slug", "moved"), ("region", "us-east-1"), ("peer", "1"),
                             ("created_by", str(self.member.pk))):
            with self.subTest(field=field):
                response = self.post(self.cloud, name="Cloud Renamed", **{field: value})
                self.assertEqual(response.status_code, 302)
                self.cloud.refresh_from_db()
                self.assertEqual(self.cloud.name, "Cloud")
                self.assertEqual(self.cloud.slug, "mg-cloud")
                self.assertEqual(self.cloud.storage_backend, StorageBackend.S3)
                self.assertEqual(self.cloud.storage_config["prefix"], "vault/")
                draft = self.draft()
                self.assertEqual(draft["open"], "edit")
                self.assertIn(field, draft["error"])
                self.assertIn("cannot be changed", draft["error"])
        self.assertNotIn("VAULT.BUCKET.UPDATED", audit_actions())

    def test_an_ownerless_bucket_gets_an_owner(self):
        self.post(self.ownerless, owner=self.member.pk)
        self.ownerless.refresh_from_db()
        self.assertEqual(self.ownerless.owner, self.member)

    def test_the_owner_cannot_be_taken_away(self):
        self.post(self.local, owner="")
        self.local.refresh_from_db()
        self.assertEqual(self.local.owner, self.member)
        self.assertIn("owner", self.draft()["errors"])

    def test_a_taken_name_reopens_the_modal_with_the_reason(self):
        self.post(self.local, name="Cloud")
        draft = self.draft()
        self.assertEqual((draft["open"], draft["pk"], draft["name"]), ("edit", self.local.pk, "Cloud"))
        self.assertIn("already exists", " ".join(draft["errors"]["name"]))
        response = self.client.get(reverse("vault:manage"))
        self.assertEqual(response.context["draft"]["open"], "edit")
        self.local.refresh_from_db()
        self.assertEqual(self.local.name, "Alpha")

    def test_a_refused_bucket_on_another_page_still_reopens(self):
        with mock.patch.object(manage_views, "PER_PAGE", 1):
            self.post(self.ownerless, name="Cloud", page="1")
            response = self.client.get(reverse("vault:manage"))
        self.assertEqual(response.context["draft"]["open"], "edit")
        self.assertIn(self.ownerless.pk, [r["pk"] for r in response.context["script_rows"]])

    def test_a_bucket_being_deleted_refuses_edit(self):
        Bucket.objects.filter(pk=self.local.pk).update(deletion_requested_at=timezone.now())
        self.post(self.local, name="Too Late")
        self.local.refresh_from_db()
        self.assertEqual(self.local.name, "Alpha")
        self.assertIn("being deleted", self.draft()["error"])

    def test_an_unknown_bucket_is_404_for_the_plan_holder(self):
        response = self.as_root().post(reverse("vault:manage_edit", args=[999999]), {"name": "x"})
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@override_settings(VAULT_PURGE_INLINE=True)
class DeleteTests(ManageFixture):
    def post(self, bucket, name):
        with self.captureOnCommitCallbacks(execute=True):
            return self.as_root().post(reverse("vault:manage_delete", args=[bucket.pk]),
                                       {"confirm_name": name})

    def test_the_name_must_be_typed_exactly(self):
        for typed in ("alpha", "Alph", "", "Cloud"):
            with self.subTest(typed=typed):
                response = self.post(self.local, typed)
                self.assertEqual(response.status_code, 302)
                self.local.refresh_from_db()
                self.assertFalse(self.local.is_being_deleted)
                draft = self.draft()
                self.assertEqual((draft["open"], draft["pk"]), ("delete", self.local.pk))
                self.assertIn("Type the bucket's name exactly", draft["error"])
        self.assertTrue(Bucket.objects.filter(pk=self.local.pk).exists())

    def test_the_job_takes_every_row_and_its_bytes_then_the_bucket(self):
        folder = VaultDirectory.objects.create(name="f", bucket=self.local, owner=self.member)
        files = [self.local_file(self.local, "a"), self.local_file(self.local, "b")]
        paths = [f.file.path for f in files]
        self.assertTrue(all(os.path.exists(p) for p in paths))
        response = self.post(self.local, "Alpha")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Bucket.objects.filter(pk=self.local.pk).exists())
        self.assertFalse(VaultFile.objects.filter(pk__in=[f.pk for f in files]).exists())
        self.assertFalse(any(os.path.exists(p) for p in paths))
        self.assertFalse(VaultDirectory.objects.filter(pk=folder.pk).exists())
        self.assertEqual(audit_actions()[-2:], ["VAULT.BUCKET.DELETE_REQUESTED",
                                                "VAULT.BUCKET.DELETED"])
        body = self.client.get(reverse("vault:manage")).content.decode()
        self.assertNotIn(f'data-testid="bucket-row-{self.local.pk}"', body)

    def test_without_a_worker_the_bucket_is_listed_as_being_deleted(self):
        self.local_file(self.local, "kept")
        with override_settings(VAULT_PURGE_INLINE=False), \
                mock.patch("toto.celery_utils.celery_available", return_value=False):
            self.post(self.local, "Alpha")
        self.local.refresh_from_db()
        self.assertTrue(self.local.is_being_deleted)
        self.assertIn("No worker", self.local.deletion_error)
        body = self.client.get(reverse("vault:manage")).content.decode()
        row = body[body.index(f'data-testid="bucket-row-{self.local.pk}"'):]
        row = row[:row.index("</tr>")]
        self.assertIn("Deletion stopped", row)
        self.assertIn("No worker", row)
        self.assertIn("Delete again", row)
        # Confirming again, with a worker this time, finishes the job.
        self.post(self.local, "Alpha")
        self.assertFalse(Bucket.objects.filter(pk=self.local.pk).exists())

    def test_a_mount_only_disconnects(self):
        from . import peer_client
        from .tests_storage_adapters import NoHttp

        transport = NoHttp()
        VaultFile.objects.create(owner=self.member, title="theirs.txt", key="theirs",
                                 file_type="text", bucket=self.mount, origin="mirror")
        with mock.patch.object(peer_client, "_http", lambda: transport):
            self.post(self.mount, "Mounted")
        self.assertEqual(transport.calls, [])
        self.assertFalse(Bucket.objects.filter(pk=self.mount.pk).exists())
        self.assertFalse(VaultFile.objects.filter(key="theirs").exists())
        self.assertFalse(BucketPeer.objects.filter(pk=self.peer.pk).exists())

    def test_an_s3_bucket_deletes_its_objects_with_its_sealed_key(self):
        sealed(self.cloud)
        row = VaultFile(owner=self.member, title="obj", key="obj", file_type="text",
                        bucket=self.cloud, file_size_bytes=3)
        row.file.name = "vault/files/obj.txt"
        row.save()
        boto3, session, client, modules = fake_boto3()
        with mock.patch.dict(sys.modules, modules):
            self.post(self.cloud, "Cloud")
        self.assertFalse(Bucket.objects.filter(pk=self.cloud.pk).exists())
        self.assertEqual([c.kwargs["Bucket"] for c in client.delete_object.call_args_list],
                         ["my-bucket"])
        self.assertFalse(BucketSecret.objects.filter(bucket_id=self.cloud.pk).exists())


# ---------------------------------------------------------------------------
# Test (the connection test)
# ---------------------------------------------------------------------------

class ProbeTests(ManageFixture):
    def post(self, bucket):
        return self.as_root().post(reverse("vault:manage_test", args=[bucket.pk]))

    def test_this_servers_bucket_answers_and_is_stamped(self):
        response = self.post(self.local)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        payload = response.json()
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["health"], "ok")
        self.local.refresh_from_db()
        self.assertIsNotNone(self.local.last_probe_at)
        self.assertIn("Answered", self.page())

    def test_an_s3_bucket_that_does_not_answer_says_why(self):
        sealed(self.cloud)
        boto3, session, client, modules = fake_boto3(head_error=Exception("NoSuchBucket 404"))
        with mock.patch.dict(sys.modules, modules):
            payload = self.post(self.cloud).json()
        self.assertFalse(payload["ok"])
        self.assertIn("404", payload["detail"])
        self.assertEqual(payload["health"], "error")
        self.assertNotIn(SECRET, json.dumps(payload))
        self.cloud.refresh_from_db()
        self.assertIn("404", self.cloud.last_probe_error)

    def test_a_dead_peer_answers_a_sentence_stamped_without_its_token(self):
        from . import peer_client

        class Down:
            def request(self, method, url, **kwargs):
                raise ConnectionError(f"cannot reach {url}")

        with mock.patch("toto.vault.outbound.socket.getaddrinfo", public_dns), \
                mock.patch.object(peer_client, "_http", lambda: Down()):
            response = self.post(self.mount)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["detail"])
        self.assertEqual(payload["health"], "error")
        self.assertNotIn(TOKEN, response.content.decode())
        self.peer.refresh_from_db()
        self.assertTrue(self.peer.last_error)
        self.assertNotIn(TOKEN, self.peer.last_error)

    def test_an_unknown_bucket_is_a_json_404_for_the_plan_holder(self):
        response = self.as_root().post(reverse("vault:manage_test", args=[999999]))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["ok"])

    def test_the_list_offers_the_test_button(self):
        body = self.page()
        self.assertIn(f"runTest({self.mount.pk}, '{reverse('vault:manage_test', args=[self.mount.pk])}')",
                      body)


# ---------------------------------------------------------------------------
# Regressions (review of 2026-09-30)
# ---------------------------------------------------------------------------

class StalledPurgeTests(ManageFixture):
    """A purge killed where no code could say so (a worker restarted
    mid-purge, a lost queue message) stamps no reason: the bucket must not
    sit "Being deleted" for good with no way to resume from the page."""

    def row(self, bucket, body=None):
        body = body or self.page()
        row = body[body.index(f'data-testid="bucket-row-{bucket.pk}"'):]
        return row[:row.index("</tr>")]

    def card(self, bucket, body):
        card = body[body.index(f'data-testid="bucket-card-{bucket.pk}"'):]
        return card[:card.index("</article>")]

    def test_a_purge_just_confirmed_offers_nothing_yet(self):
        Bucket.objects.filter(pk=self.local.pk).update(deletion_requested_at=timezone.now())
        row = self.row(self.local)
        self.assertIn("Being deleted", row)
        self.assertNotIn(f'data-testid="bucket-delete-{self.local.pk}"', row)

    def test_a_purge_that_went_quiet_offers_delete_again(self):
        from datetime import timedelta

        self.local_file(self.local, "left")
        Bucket.objects.filter(pk=self.local.pk).update(
            deletion_requested_at=timezone.now() - timedelta(hours=3))
        body = self.page()
        for part in (self.row(self.local, body), self.card(self.local, body)):
            self.assertIn("Deletion may have stopped", part)
            self.assertIn(f'data-testid="bucket-stalled-{self.local.pk}"', part)
            self.assertIn(f'data-testid="bucket-delete-{self.local.pk}"', part)
            self.assertIn("Delete again", part)
            self.assertNotIn(f'data-testid="bucket-edit-{self.local.pk}"', part)

    @override_settings(VAULT_PURGE_INLINE=True)
    def test_confirming_again_resumes_it(self):
        from datetime import timedelta

        self.local_file(self.local, "left")
        Bucket.objects.filter(pk=self.local.pk).update(
            deletion_requested_at=timezone.now() - timedelta(hours=3))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.as_root().post(reverse("vault:manage_delete", args=[self.local.pk]),
                                           {"confirm_name": "Alpha"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Bucket.objects.filter(pk=self.local.pk).exists())


class LegacyEditModalTests(ManageFixture):
    """The Edit modal posts all three fields; unchanged ones are not re-judged."""

    def test_a_bucket_whose_owner_was_deactivated_takes_a_new_quota(self):
        User.objects.filter(pk=self.member.pk).update(is_active=False)
        response = self.as_root().post(reverse("vault:manage_edit", args=[self.local.pk]), {
            "name": "Alpha", "owner": self.member.pk, "storage_quota_mb": "25"})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.draft())
        self.local.refresh_from_db()
        self.assertEqual((self.local.owner_id, self.local.storage_quota_mb),
                         (self.member.pk, 25))


class ShieldTests(ManageFixture):
    """A bucket has no AI shield (2026-10-06): the field ``ai_protected`` and
    its column are gone from the model and from the vault's one migration.
    No dialog, no list and no admin page names a shield, whether or not the
    assistant counts as installed (``toto.core.assistant.installed``), and a
    posted ``ai_protected`` is what any unknown field is: Create does not
    read it, Edit refuses the whole post."""

    #: What the page drew for the shield, and the field's name.
    WORDS = ("AI shield", "ai_protected", "bucket-new-shield", "bucket-edit-shield",
             "bucket-connect-shield", "never reads files", "ai_shield", "edit.ai")

    def bucket_admin(self):
        from django.contrib import admin as django_admin
        from django.test import RequestFactory

        request = RequestFactory().get("/admin/vault/bucket/")
        request.user = self.root
        return django_admin.site._registry[Bucket], request

    def test_the_model_has_no_such_field(self):
        self.assertNotIn("ai_protected", {f.name for f in Bucket._meta.get_fields()})
        self.assertFalse(hasattr(self.local, "ai_protected"))
        from django.db import connection

        with connection.cursor() as cursor:
            columns = {c.name for c in connection.introspection.get_table_description(
                cursor, Bucket._meta.db_table)}
        self.assertNotIn("ai_protected", columns)
        self.assertIn("storage_quota_mb", columns)

    def test_the_migration_makes_no_such_column(self):
        from django.db.migrations.loader import MigrationLoader

        state = MigrationLoader(None, ignore_no_migrations=True).project_state()
        fields = state.models["vault", "bucket"].fields
        self.assertNotIn("ai_protected", fields)
        self.assertIn("storage_quota_mb", fields)

    def test_the_three_dialogs_and_the_list_name_no_shield(self):
        Bucket.objects.filter(pk=self.local.pk).update(storage_quota_mb=7)
        for present in (False, True):
            with self.subTest(assistant=present), assistant(present):
                response = self.as_root().get(reverse("vault:manage"))
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                # The three dialogs and both lists are on the page.
                for testid in ("bucket-new-dialog", "bucket-edit", "buckets-table",
                               "buckets-cards"):
                    self.assertIn(f'data-testid="{testid}"', body)
                for word in self.WORDS:
                    self.assertNotIn(word, body)
                self.assertNotIn("ai_protected", response.context["draft"])
                for row in response.context["script_rows"]:
                    self.assertNotIn("ai_protected", row)
                # A quota is still drawn on its own, in the table and the card.
                self.assertEqual(body.count("7 MB quota"), 2)
        with assistant(False):
            body = self.page()
            for word in ("assistant", "Assistant"):
                self.assertNotIn(word, body)

    def test_create_does_not_read_a_posted_shield(self):
        for present in (False, True):
            with self.subTest(assistant=present), assistant(present):
                name = f"Plain {present}"
                response = self.as_root().post(reverse("vault:manage_create"), {
                    "kind": "local", "name": name, "owner": self.member.pk,
                    "storage_quota_mb": "", "ai_protected": ["0", "1"]})
                self.assertRedirects(response, reverse("vault:manage"),
                                     fetch_redirect_response=False)
                bucket = Bucket.objects.get(name=name)
                self.assertFalse(hasattr(bucket, "ai_protected"))
                self.assertNotIn("ai_protected", audit_dump())
                # A refused one carries no shield back either.
                self.client.post(reverse("vault:manage_create"), {
                    "kind": "local", "name": name, "owner": self.member.pk,
                    "ai_protected": "1"})
                draft = self.draft()
                self.assertEqual(draft["open"], "create")
                self.assertNotIn("ai_protected", json.dumps(draft))

    def test_edit_refuses_a_posted_shield_like_any_unknown_field(self):
        for present in (False, True):
            with self.subTest(assistant=present), assistant(present):
                Bucket.objects.filter(pk=self.local.pk).update(name="Alpha",
                                                               storage_quota_mb=None)
                for field in ("ai_protected", "colour"):
                    response = self.as_root().post(
                        reverse("vault:manage_edit", args=[self.local.pk]), {
                            "name": "Alpha Renamed", "owner": self.member.pk,
                            "storage_quota_mb": "", field: ["0", "1"]})
                    self.assertEqual(response.status_code, 302)
                    self.local.refresh_from_db()
                    self.assertEqual(self.local.name, "Alpha")
                    draft = self.draft()
                    self.assertEqual(draft["open"], "edit")
                    self.assertIn(f"{field} cannot be changed", draft["error"])
                    self.assertNotIn("ai_protected", draft)
                self.assertNotIn("VAULT.BUCKET.UPDATED", audit_actions())
                # The three fields the modal posts still change.
                self.client.post(reverse("vault:manage_edit", args=[self.local.pk]), {
                    "name": "Alpha Renamed", "owner": self.member.pk, "storage_quota_mb": "5"})
                self.local.refresh_from_db()
                self.assertEqual((self.local.name, self.local.storage_quota_mb),
                                 ("Alpha Renamed", 5))
                self.assertNotIn("ai_protected", audit_dump())
                from toto.audit.models import AuditRecord

                AuditRecord.objects.filter(action="VAULT.BUCKET.UPDATED").delete()

    def test_the_admin_names_no_shield(self):
        for present in (False, True):
            with self.subTest(assistant=present), assistant(present):
                model_admin, request = self.bucket_admin()
                self.assertNotIn("ai_protected", model_admin.get_list_display(request))
                self.assertNotIn("ai_protected", model_admin.get_list_filter(request))
                named = [f for _, options in model_admin.get_fieldsets(request, self.local)
                         for f in options["fields"]]
                self.assertNotIn("ai_protected", named)
                self.assertIn("storage_quota_mb", named)
                self.assertNotIn("ai_protected",
                                 model_admin.get_readonly_fields(request, self.local))
                form = model_admin.get_form(request, self.local)
                self.assertNotIn("ai_protected", form.base_fields)
                self.assertIn("storage_quota_mb", form.base_fields)


class DoubleSubmitTests(ManageFixture):
    """Create runs an S3 kind's connection test before answering — seconds —
    and a second post came back as "the name is taken", about the bucket the
    first one made. Every form of the page submits once."""

    def test_every_form_guards_against_a_second_post(self):
        body = self.page()
        for testid in ("bucket-new", "bucket-edit", "bucket-delete"):
            with self.subTest(form=testid):
                tag = re.search(rf'<form [^>]*data-testid="{testid}"[^>]*>', body, re.S).group(0)
                self.assertIn('@submit="guard($event)"', tag)
        for testid in ("bucket-new-submit", "bucket-edit-submit", "bucket-delete-submit"):
            with self.subTest(button=testid):
                tag = re.search(rf'<button [^>]*data-testid="{testid}"[^>]*>', body, re.S).group(0)
                self.assertRegex(tag, r':disabled="submitting[ "|]')
        self.assertIn("Testing the connection…", body)
        self.assertIn("guard(event) {", body)

    def test_the_kinds_say_which_are_tested_first(self):
        kinds = json.loads(re.search(r'<script id="bucket-manage-kinds" type="application/json">(.*?)</script>',
                                     self.page(), re.S).group(1))
        remote = {k["key"]: k["remote"] for k in kinds}
        self.assertEqual((remote["local"], remote["aws_s3"], remote["ovh_s3"]), (False, True, True))

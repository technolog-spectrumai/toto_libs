"""The vault's Clearances tab (``clearances/``, 2026-09-30): every bucket, the
clearances keeping it and, per clearance, who holds it — for a superuser on the
Superuser plan only (``clearance_tab.py``). Who may open it and see its tab, what
each row says (two clearances and several holders, a clearance nobody holds, an
open bucket), the table and the cards, the filter, the pages, and a query count
that does not grow with the buckets, clearances and holders on the page.
"""

import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault import clearance_tab
from toto.vault.models import Bucket, BucketClearance, StorageBackend

User = get_user_model()

URL = "/vault/clearances/"
TAB = 'data-testid="vault-clearances-tab"'


def _admin_plan_exists() -> bool:
    from toto.subscriptions.plans import admin_plan

    return admin_plan() is not None


def _holder(username, name, *clearances):
    user = User.objects.create_user(username, password="pw")
    Person.objects.create(user=user, display_name=name).clearances.add(*clearances)
    return user


def _keep(bucket, *clearances):
    for clearance in clearances:
        BucketClearance.objects.create(bucket=bucket, clearance=clearance)
    return bucket


@override_settings(VAULT_EXTERNAL_BUCKETS=True)
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.payroll = Clearance.objects.create(name="payroll", slug="payroll")
        cls.confidential = Clearance.objects.create(name="confidential", slug="confidential")
        cls.restricted = Clearance.objects.create(name="restricted", slug="restricted")  # nobody
        cls.alice = _holder("alice", "Alice Payroll", cls.payroll)
        cls.bob = _holder("bob", "Bob Secret", cls.confidential)
        cls.carol = _holder("carol", "carol both", cls.payroll, cls.confidential)
        cls.dave = User.objects.create_user("dave", password="pw")              # no Person
        cls.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        cls.root = User.objects.create_superuser("root", "r@e.org", "pw")
        # The Superuser plan: `bootstrap_plans` puts `root` on it; `bare_root`,
        # made after, is a superuser without it.
        call_command("bootstrap_plans", stdout=io.StringIO())
        cls.root = User.objects.get(pk=cls.root.pk)
        cls.bare_root = User.objects.create_superuser("bareroot", "b@e.org", "pw")

        cls.salaries = _keep(Bucket.objects.create(name="Salaries", slug="salaries",
                                                   owner=cls.alice),
                             cls.payroll, cls.confidential)
        cls.vault = _keep(Bucket.objects.create(name="Vault", slug="vault-b", owner=cls.bob),
                          cls.restricted)
        cls.recipes = Bucket.objects.create(name="Recipes", slug="recipes", owner=cls.carol)
        cls.cloud = Bucket.objects.create(
            name="Cloud", slug="cloud", owner=cls.dave, storage_backend=StorageBackend.S3,
            storage_config={"bucket_name": "b", "endpoint_url": "https://s3.example.org",
                            "region_name": "eu", "prefix": "vault/"})
        cls.orphan = Bucket.objects.create(name="Orphan", slug="orphan", owner=None,
                                           deletion_requested_at=timezone.now())

    def get(self, user, url=URL, **params):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(url, params)

    def rows(self, response):
        return {r["bucket"].slug: r for r in response.context["rows"]}


class DoorTests(_Fixture):
    def test_only_a_superuser_on_the_plan_opens_it(self):
        self.assertEqual(self.get(self.root).status_code, 200)
        for user in (self.alice, self.dave, self.staff):
            with self.subTest(user=user.username):
                self.client.logout()
                self.assertEqual(self.get(user).status_code, 403)

    def test_a_superuser_without_the_plan_is_refused(self):
        if not _admin_plan_exists():
            self.skipTest("this host's ladder has no plan for admins")
        self.assertEqual(self.get(self.bare_root).status_code, 403)

    def test_a_visitor_who_is_not_signed_in_goes_to_the_login_page(self):
        response = self.get(None)
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=", response["Location"])

    def test_the_route_is_named(self):
        self.assertEqual(reverse("vault:clearances_tab"), URL)

    def test_it_only_reads(self):
        self.client.force_login(self.root)
        self.assertEqual(self.client.post(URL).status_code, 405)
        self.assertEqual(self.client.head(URL).status_code, 200)

    def test_a_post_from_someone_else_is_refused_before_anything(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(URL).status_code, 403)

    def test_a_json_caller_without_the_plan_gets_json(self):
        self.client.force_login(self.staff)
        response = self.client.get(URL, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["ok"])


class TabTests(_Fixture):
    def test_the_tab_shows_only_to_a_plan_holder(self):
        cases = [(self.root, True), (self.alice, False), (self.staff, False)]
        if _admin_plan_exists():
            cases.append((self.bare_root, False))
        for user, shown in cases:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                page = self.client.get(reverse("vault:archive")).content.decode()
                self.assertEqual(TAB in page, shown)

    def test_the_tab_is_the_current_one_on_its_page(self):
        page = self.get(self.root).content.decode()
        start = page.index(TAB)
        self.assertIn('aria-current="page"', page[start:page.index("</a>", start)])
        self.assertIn(">Metrics</a>", page)                     # the rest of the strip


class ContentTests(_Fixture):
    def test_a_bucket_with_two_clearances_and_their_holders(self):
        row = self.rows(self.get(self.root))["salaries"]
        self.assertFalse(row["open"])
        self.assertEqual([k["clearance"].name for k in row["keeps"]], ["confidential", "payroll"])
        holders = {k["clearance"].name: k["holders"] for k in row["keeps"]}
        self.assertEqual(holders["payroll"], ["Alice Payroll", "carol both"])     # case-blind order
        self.assertEqual(holders["confidential"], ["Bob Secret", "carol both"])
        self.assertEqual({k["clearance"].name: k["count"] for k in row["keeps"]},
                         {"payroll": 2, "confidential": 2})

    def test_a_clearance_nobody_holds(self):
        response = self.get(self.root)
        keep = self.rows(response)["vault-b"]["keeps"][0]
        self.assertEqual((keep["clearance"].name, keep["holders"], keep["more"]),
                         ("restricted", [], 0))
        page = response.content.decode()
        start = page.index('data-testid="holders-vault-b-restricted"')
        self.assertIn("Nobody holds it.", page[start:page.index("</ul>", start)])

    def test_an_open_bucket(self):
        response = self.get(self.root)
        row = self.rows(response)["recipes"]
        self.assertTrue(row["open"])
        self.assertEqual(row["keeps"], [])
        page = response.content.decode()
        self.assertIn('data-testid="clearances-open-recipes"', page)
        self.assertIn("No clearance: the vault's usual rule applies.", page)

    def test_the_table_and_the_cards_render_every_bucket(self):
        page = self.get(self.root).content.decode()
        self.assertIn('data-testid="clearances-table"', page)
        self.assertIn('data-testid="clearances-cards"', page)
        for bucket in Bucket.objects.all():
            with self.subTest(bucket=bucket.slug):
                self.assertIn(f'data-testid="clearances-bucket-{bucket.slug}"', page)   # the table
                self.assertIn(f'data-testid="clearances-card-{bucket.slug}"', page)     # the cards
        for slug in ("salaries-payroll", "salaries-confidential", "vault-b-restricted"):
            self.assertEqual(page.count(f'data-testid="holders-{slug}"'), 2)       # once in each

    def test_each_bucket_links_to_its_page_where_clearances_are_set(self):
        page = self.get(self.root).content.decode()
        url = reverse("vault:bucket_metrics", args=["salaries"]) + "#clearances"
        self.assertIn(f'href="{url}"', page)
        self.assertEqual(page.count(f'href="{url}"'), 2)                          # table + card

    def test_a_remote_bucket_wears_the_cloud_badge(self):
        page = self.get(self.root).content.decode()
        start = page.index('data-testid="clearances-bucket-cloud"')
        body = page[start:page.index("</tbody>", start)]
        self.assertIn('<i class="fa-solid fa-cloud mr-1"></i>Remote', body)
        self.assertNotIn("s3.example.org", page)                  # where the bytes are is not here

    def test_an_ownerless_bucket_being_deleted_is_listed_as_such(self):
        page = self.get(self.root).content.decode()
        start = page.index('data-testid="clearances-card-orphan"')
        card = page[start:page.index("</article>", start)]
        self.assertIn("Being deleted", card)
        self.assertIn("Owner: —", card)

    def test_names_are_escaped(self):
        Bucket.objects.create(name="<b>bold</b>", slug="bold", owner=self.alice)
        page = self.get(self.root).content.decode()
        self.assertNotIn("<b>bold</b>", page)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", page)

    def test_a_long_list_of_holders_is_cut_short(self):
        with mock.patch.object(clearance_tab, "HOLDERS_SHOWN", 1):
            response = self.get(self.root)
        keep = {k["clearance"].slug: k for k in self.rows(response)["salaries"]["keeps"]}["payroll"]
        self.assertEqual((keep["holders"], keep["count"], keep["more"]), (["Alice Payroll"], 2, 1))
        self.assertIn('data-testid="holders-more-salaries-payroll"', response.content.decode())
        self.assertIn("and 1 more", response.content.decode())

    def test_the_socialhub_is_named_for_making_clearances(self):
        page = self.get(self.root).content.decode()
        self.assertIn(f'href="{reverse("socialhub:clearances")}">Make clearances', page)


class FilterAndPageTests(_Fixture):
    def slugs(self, response):
        return [r["bucket"].slug for r in response.context["rows"]]

    def test_the_counters(self):
        filters = {key: count for key, _label, count in self.get(self.root).context["filters"]}
        self.assertEqual(filters, {"all": 5, "kept": 2, "open": 3})

    def test_kept_and_open(self):
        self.assertEqual(self.slugs(self.get(self.root, show="kept")), ["salaries", "vault-b"])
        self.assertEqual(self.slugs(self.get(self.root, show="open")),
                         ["cloud", "orphan", "recipes"])

    def test_an_unknown_filter_shows_everything(self):
        response = self.get(self.root, show="'><script>")
        self.assertEqual(response.context["show"], "all")
        self.assertEqual(len(self.slugs(response)), 5)

    def test_an_empty_filter_says_so(self):
        BucketClearance.objects.all().delete()
        page = self.get(self.root, show="kept").content.decode()
        self.assertIn('data-testid="clearances-empty"', page)
        self.assertIn("No bucket is kept to a clearance.", page)

    def test_pages(self):
        for n in range(clearance_tab.PER_PAGE):
            Bucket.objects.create(name=f"zz {n:03d}", slug=f"zz-{n:03d}", owner=self.alice)
        first = self.get(self.root)
        self.assertEqual(len(first.context["rows"]), clearance_tab.PER_PAGE)
        self.assertTrue(first.context["is_paginated"])
        self.assertIn("?page=2", first.content.decode())
        second = self.get(self.root, page=2)
        self.assertEqual(len(second.context["rows"]), 5)
        page = second.content.decode()
        for row in second.context["rows"]:
            self.assertIn(f'data-testid="clearances-bucket-{row["bucket"].slug}"', page)
            self.assertIn(f'data-testid="clearances-card-{row["bucket"].slug}"', page)
        self.assertEqual(self.slugs(self.get(self.root, page=99)), self.slugs(second))  # clamped

    def test_the_filter_survives_paging(self):
        for n in range(clearance_tab.PER_PAGE + 1):
            _keep(Bucket.objects.create(name=f"k {n:03d}", slug=f"k-{n:03d}", owner=self.alice),
                  self.payroll)
        page = self.get(self.root, show="kept").content.decode()
        self.assertIn("?page=2&amp;show=kept", page)


class QueryTests(_Fixture):
    def count(self):
        self.client.force_login(self.root)
        self.client.get(URL)                                            # warm the session
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(URL).status_code, 200)
        return len(queries)

    def test_the_query_count_does_not_grow_with_the_page(self):
        few = self.count()
        extra = [Clearance.objects.create(name=f"extra {n}", slug=f"extra-{n}") for n in range(4)]
        for n in range(8):
            _holder(f"h{n}", f"Holder {n}", *extra)
        for n in range(12):
            _keep(Bucket.objects.create(name=f"b {n:02d}", slug=f"b-{n:02d}", owner=self.bob,
                                        storage_backend=StorageBackend.S3 if n % 2 else "local"),
                  *extra[:1 + n % 4])
        self.assertEqual(self.count(), few)

    def test_bucket_rows_takes_three_queries(self):
        with self.assertNumQueries(3):
            rows = clearance_tab.bucket_rows(clearance_tab.buckets_for("all"))
            for row in rows:
                row["bucket"].remote_label, getattr(row["bucket"].owner, "pk", None)

"""The bell's three doors (2026-10-04): session, CSRF, the Fetch-Metadata
guard, and nobody's notifications but the caller's. And that there are
three (2026-10-06): no door that holds a request.

    manage.py test toto.notify.tests_doors
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from toto import notify
from toto.notify.models import Notification
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault.models import Bucket, BucketClearance

User = get_user_model()


class DoorCase(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.bucket = Bucket.objects.create(name="Work", slug="work", owner=self.ada)
        self.mine = self.tell(self.ada, link="/vault/")
        self.theirs = self.tell(self.bob, kind="vault.trashed")
        self.client.force_login(self.ada)

    def tell(self, user, *, kind="vault.uploaded", title="plan.pdf", **more):
        return notify.send(user, kind, title=title, bucket=self.bucket.name,
                           bucket_id=self.bucket.pk, **more)


class ListTests(DoorCase):
    def test_it_lists_only_the_callers_own(self):
        data = self.client.get(reverse("notify:api_list")).json()
        self.assertEqual(data["unread"], 1)
        self.assertEqual([item["id"] for item in data["items"]], [self.mine.pk])
        item = data["items"][0]
        self.assertEqual(item["text"], "plan.pdf was uploaded to Work")
        self.assertEqual((item["link"], item["read"], item["actor"]), ("/vault/", False, ""))

    def test_it_is_never_cached_and_anonymous_gets_nothing(self):
        response = self.client.get(reverse("notify:api_list"))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn(Client().get(reverse("notify:api_list")).status_code, (302, 401))
        self.assertEqual(self.client.post(reverse("notify:api_list")).status_code, 405)

    def test_the_latest_twenty(self):
        for n in range(25):
            self.tell(self.ada, title=f"scan{n}.png")
        data = self.client.get(reverse("notify:api_list")).json()
        self.assertEqual((len(data["items"]), data["unread"]), (20, 26))

    def test_a_row_about_a_bucket_hidden_from_the_reader_now_is_dropped(self):
        owner = User.objects.create_user("owner", password="pw")
        bucket = Bucket.objects.create(name="Payroll", slug="payroll", owner=owner)
        Person.objects.create(user=self.ada, display_name="Ada")
        row = notify.send(self.ada, "vault.uploaded", actor=owner, title="salaries.csv",
                          bucket=bucket.name, bucket_id=bucket.pk)
        ids = [i["id"] for i in self.client.get(reverse("notify:api_list")).json()["items"]]
        self.assertIn(row.pk, ids)
        BucketClearance.objects.create(bucket=bucket,
                                       clearance=Clearance.objects.create(name="Payroll"))
        body = self.client.get(reverse("notify:api_list"))
        self.assertNotIn("salaries.csv", body.content.decode())
        self.assertFalse(Notification.objects.filter(pk=row.pk).exists())


class ReadTests(DoorCase):
    def test_marking_ones_own_read(self):
        data = self.client.post(reverse("notify:api_read"), {"id": self.mine.pk}).json()
        self.assertEqual(data, {"ok": True, "unread": 0})
        self.assertIsNotNone(Notification.objects.get(pk=self.mine.pk).read_at)

    def test_another_members_notification_is_a_404_and_stays_unread(self):
        for value in (self.theirs.pk, 999999, "x", "", "1 OR 1=1"):
            with self.subTest(value=value):
                response = self.client.post(reverse("notify:api_read"), {"id": value})
                self.assertEqual(response.status_code, 404)
        self.assertIsNone(Notification.objects.get(pk=self.theirs.pk).read_at)

    def test_read_all_touches_only_the_callers(self):
        self.tell(self.ada, kind="vault.restored")
        self.assertEqual(self.client.post(reverse("notify:api_read_all")).json()["unread"], 0)
        self.assertEqual(Notification.objects.filter(recipient=self.ada,
                                                     read_at__isnull=True).count(), 0)
        self.assertIsNone(Notification.objects.get(pk=self.theirs.pk).read_at)

    def test_the_writes_are_posts_and_need_the_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.ada)
        for name in ("notify:api_read", "notify:api_read_all"):
            with self.subTest(door=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 405)
                self.assertEqual(strict.post(reverse(name), {"id": self.mine.pk}).status_code, 403)
        self.assertIsNone(Notification.objects.get(pk=self.mine.pk).read_at)
        strict.get(reverse("notify:api_list"))
        token = strict.cookies.get("csrftoken")
        if token is not None:
            response = strict.post(reverse("notify:api_read"), {"id": self.mine.pk},
                                   HTTP_X_CSRFTOKEN=token.value)
            self.assertEqual(response.status_code, 200)

    def test_a_write_a_browser_sent_from_another_site_is_refused(self):
        for site in ("cross-site", "same-site"):
            for name in ("notify:api_read", "notify:api_read_all"):
                with self.subTest(site=site, door=name):
                    response = self.client.post(reverse(name), {"id": self.mine.pk},
                                                HTTP_SEC_FETCH_SITE=site)
                    self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse("notify:api_read_all"),
                                    HTTP_ORIGIN="https://evil.example.com")
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(Notification.objects.get(pk=self.mine.pk).read_at)

    def test_no_door_takes_an_account(self):
        self.client.post(reverse("notify:api_read_all"), {"user": self.bob.pk,
                                                          "recipient": self.bob.pk})
        self.assertIsNone(Notification.objects.get(pk=self.theirs.pk).read_at)


class NoWaitDoorTests(DoorCase):
    """The long-poll door left (2026-10-06): nothing is named wait, nothing
    is mounted there, and no door of the bell is a coroutine."""

    def test_no_url_is_named_wait(self):
        from django.urls import NoReverseMatch

        from toto.notify import urls

        with self.assertRaises(NoReverseMatch):
            reverse("notify:api_wait")
        self.assertEqual(sorted(pattern.name for pattern in urls.urlpatterns),
                         ["api_list", "api_read", "api_read_all"])
        for pattern in urls.urlpatterns:
            self.assertNotIn("wait", str(pattern.pattern))

    def test_the_old_address_answers_404(self):
        address = reverse("notify:api_list") + "wait/"
        self.assertEqual(address, "/notify/api/wait/")
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(address, {"folders": "1"}).status_code, 404)

    def test_every_door_answers_at_once(self):
        import inspect

        from toto.notify import views

        for name in ("api_list", "api_read", "api_read_all"):
            with self.subTest(door=name):
                self.assertFalse(inspect.iscoroutinefunction(getattr(views, name)))
        self.assertFalse(hasattr(views, "api_wait"))
        source = inspect.getsource(views)
        for word in ("asyncio", "sync_to_async", "async def", "sleep("):
            self.assertNotIn(word, source)

    def test_the_doors_still_answer(self):
        listed = self.client.get(reverse("notify:api_list"))
        self.assertEqual((listed.status_code, listed.json()["unread"]), (200, 1))
        read = self.client.post(reverse("notify:api_read"), {"id": self.mine.pk})
        self.assertEqual(read.json(), {"ok": True, "unread": 0})
        self.tell(self.ada, kind="vault.restored")
        self.assertEqual(self.client.get(reverse("notify:api_list")).json()["unread"], 1)
        self.assertEqual(self.client.post(reverse("notify:api_read_all")).json(),
                         {"ok": True, "unread": 0})

    def test_a_read_from_another_site_is_an_ordinary_read(self):
        """The guard on reads existed for the held request alone."""
        response = self.client.get(reverse("notify:api_list"), HTTP_SEC_FETCH_SITE="same-site")
        self.assertEqual(response.status_code, 200)

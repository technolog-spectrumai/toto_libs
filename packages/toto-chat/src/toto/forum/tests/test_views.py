"""HTML view tests: the permission model and that every template actually renders."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.forum import store
from toto.forum.models import ForumChannel, ForumMember

User = get_user_model()


class PageTestCase(TestCase):
    """Base for HTML tests: ``toto.ui.PageProcessor`` 404s without an active Platform row."""

    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Toto", defaults={"author": "Test", "publication_year": 2026}
        )


class ChannelViewPermissionTests(PageTestCase):
    def setUp(self):
        from toto.people.models import Person

        self.user = User.objects.create_user(username="viewer", password="pass")
        self.person = Person.objects.create(user=self.user, display_name="Viewer")
        self.channel = ForumChannel.objects.create(name="Room", slug="room")

    def _join(self):
        return ForumMember.objects.create(
            channel=self.channel, person=self.person, is_active=True
        )

    def test_channel_list_redirects_anonymous(self):
        res = self.client.get("/forum/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url.lower())

    def test_channel_detail_redirects_anonymous(self):
        res = self.client.get(f"/forum/{self.channel.slug}/")
        self.assertEqual(res.status_code, 302)

    def test_channel_list_renders_for_signed_in_user(self):
        self.client.force_login(self.user)
        res = self.client.get("/forum/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Room")

    def test_channel_detail_renders_for_member(self):
        self._join()
        self.client.force_login(self.user)
        res = self.client.get(f"/forum/{self.channel.slug}/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Room")
        # The composer is enabled and the roster is disclosed.
        self.assertTrue(res.context["can_send_messages"])
        self.assertEqual(len(res.context["participants"]), 1)

    def test_channel_detail_hides_roster_from_non_member(self):
        self.client.force_login(self.user)
        res = self.client.get(f"/forum/{self.channel.slug}/")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.context["can_send_messages"])
        self.assertEqual(res.context["participants"], [])

    def test_template_carries_no_encryption_or_crdt_assets(self):
        """The rendered page must not reference the deleted rotor WASM or Yjs bundles."""
        self._join()
        self.client.force_login(self.user)
        html = self.client.get(f"/forum/{self.channel.slug}/").content.decode()
        for needle in ("rotor_wasm", "yjs.mjs", "mls_app", "secure_message", "pin_key"):
            self.assertNotIn(needle, html)

    def test_join_and_leave_round_trip(self):
        self.client.force_login(self.user)
        self.client.post(f"/forum/{self.channel.slug}/join/")
        self.assertTrue(
            ForumMember.objects.filter(
                channel=self.channel, person=self.person, is_active=True
            ).exists()
        )
        self.client.post(f"/forum/{self.channel.slug}/leave/")
        self.assertFalse(
            ForumMember.objects.filter(
                channel=self.channel, person=self.person, is_active=True
            ).exists()
        )


class ChannelCreateViewTests(PageTestCase):
    def setUp(self):
        from toto.people.models import Person

        self.user = User.objects.create_user(username="maker", password="pass")
        self.person = Person.objects.create(user=self.user, display_name="Maker")

    def test_create_channel_and_auto_join(self):
        self.client.force_login(self.user)
        res = self.client.post("/forum/create/", {"name": "New Room"})
        self.assertEqual(res.status_code, 302)
        channel = ForumChannel.objects.get(slug="new-room")
        self.assertEqual(channel.created_by, self.user)
        self.assertTrue(
            ForumMember.objects.filter(
                channel=channel, person=self.person, is_active=True
            ).exists()
        )

    def test_duplicate_name_is_rejected(self):
        ForumChannel.objects.create(name="Taken", slug="taken")
        self.client.force_login(self.user)
        self.client.post("/forum/create/", {"name": "Taken"})
        self.assertEqual(ForumChannel.objects.filter(name="Taken").count(), 1)

    def test_anonymous_cannot_create(self):
        res = self.client.post("/forum/create/", {"name": "Sneaky"})
        self.assertEqual(res.status_code, 302)
        self.assertFalse(ForumChannel.objects.filter(name="Sneaky").exists())


class MessageSearchViewTests(PageTestCase):
    def setUp(self):
        from toto.people.models import Person

        self.user = User.objects.create_user(username="searcher", password="pass")
        self.person = Person.objects.create(user=self.user, display_name="Searcher")
        self.mine = ForumChannel.objects.create(name="Mine", slug="mine")
        self.theirs = ForumChannel.objects.create(name="Theirs", slug="theirs")
        ForumMember.objects.create(
            channel=self.mine, person=self.person, is_active=True
        )
        store.store_message(
            self.mine, msg_type="chat_message", body="the eagle lands at dawn"
        )
        store.store_message(
            self.theirs, msg_type="chat_message", body="the eagle is a secret"
        )

    def test_search_finds_message_in_my_channel(self):
        self.client.force_login(self.user)
        res = self.client.get("/forum/search/", {"q": "eagle"})
        self.assertEqual(res.status_code, 200)
        bodies = [m.body for m in res.context["results"]]
        self.assertEqual(bodies, ["the eagle lands at dawn"])

    def test_search_never_leaks_channels_i_am_not_in(self):
        self.client.force_login(self.user)
        res = self.client.get("/forum/search/", {"q": "secret"})
        self.assertEqual(list(res.context["results"]), [])

    def test_search_reports_which_engine_ran(self):
        self.client.force_login(self.user)
        res = self.client.get("/forum/search/", {"q": "eagle"})
        self.assertIn(res.context["search_mode"], ("fulltext", "substring"))

    def test_empty_query_returns_nothing(self):
        self.client.force_login(self.user)
        res = self.client.get("/forum/search/", {"q": ""})
        self.assertEqual(list(res.context["results"]), [])

    def test_search_requires_login(self):
        res = self.client.get("/forum/search/", {"q": "eagle"})
        self.assertEqual(res.status_code, 302)


class MessagesApiPaginationTests(TestCase):
    def setUp(self):
        from toto.people.models import Person

        self.user = User.objects.create_user(username="pager", password="pass")
        self.person = Person.objects.create(user=self.user, display_name="Pager")
        self.channel = ForumChannel.objects.create(name="Long", slug="long")
        ForumMember.objects.create(
            channel=self.channel, person=self.person, is_active=True
        )
        for i in range(8):
            store.store_message(self.channel, msg_type="chat_message", body=f"m{i}")

    def test_non_member_cannot_read_history(self):
        outsider = User.objects.create_user(username="outsider", password="pass")
        self.client.force_login(outsider)
        res = self.client.get(f"/forum/api/channels/{self.channel.slug}/messages/")
        self.assertEqual(res.status_code, 403)

    def test_member_gets_a_page_and_a_cursor(self):
        self.client.force_login(self.user)
        res = self.client.get(
            f"/forum/api/channels/{self.channel.slug}/messages/", {"limit": 3}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual([m["message"] for m in data["messages"]], ["m5", "m6", "m7"])
        self.assertTrue(data["has_more"])

    def test_before_cursor_walks_backwards_to_exhaustion(self):
        self.client.force_login(self.user)
        url = f"/forum/api/channels/{self.channel.slug}/messages/"
        page = self.client.get(url, {"limit": 5}).json()
        self.assertTrue(page["has_more"])
        older = self.client.get(
            url, {"limit": 5, "before": page["messages"][0]["created_at"]}
        ).json()
        self.assertEqual([m["message"] for m in older["messages"]], ["m0", "m1", "m2"])
        self.assertFalse(older["has_more"])

    def test_bad_cursor_is_rejected(self):
        self.client.force_login(self.user)
        res = self.client.get(
            f"/forum/api/channels/{self.channel.slug}/messages/",
            {"before": "not-a-timestamp"},
        )
        self.assertEqual(res.status_code, 400)

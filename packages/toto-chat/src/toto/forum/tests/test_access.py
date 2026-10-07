"""Who gets through which door (stage 68, 2026-10-07).

Every route carries a mark, and every door is tried by every kind of
visitor the rule tells apart: nobody signed in, a member on Free, a member
of another community, a member, a senior member, the head, an administrator
and plain staff.

    manage.py test toto.forum.tests.test_access
"""

from django.db import IntegrityError, transaction
from django.test import Client, override_settings

from toto.forum import access, urls, views
from toto.forum.models import ForumChannel, ForumMessage
from toto.forum.testing import ForumCase, client_of, member, on_plan, op, send_json


class MarkTests(ForumCase):
    def test_every_route_carries_a_mark(self):
        for pattern in urls.urlpatterns:
            self.assertIn(getattr(pattern.callback, "forum_door", None), views.MARKS,
                          pattern.name)

    def test_the_marks_by_name(self):
        expected = {"channel_list": "member", "channel_detail": "member", "feed": "member",
                    "post": "member", "estimate": "member",
                    "settings": "administrator", "settings_save": "administrator",
                    "cleanup_start": "administrator",
                    "message_image": "member", "message_remove": "author",
                    "poll_open": "member", "poll_vote": "member", "poll_close": "author",
                    "poll_remove": "author"}
        found = {pattern.name: pattern.callback.forum_door for pattern in urls.urlpatterns}
        self.assertEqual(found, expected)

    def test_no_route_is_a_socket_or_names_a_room(self):
        for pattern in urls.urlpatterns:
            self.assertNotIn("ws", str(pattern.pattern).split("/"))
        self.assertFalse({"channel_create", "channel_join", "room_security"}
                         & {pattern.name for pattern in urls.urlpatterns})


class OneChannelTests(ForumCase):
    def test_the_database_refuses_a_second_channel_for_a_community(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ForumChannel.objects.create(community=self.guild)
        self.assertEqual(ForumChannel.objects.filter(community=self.guild).count(), 1)

    def test_a_channel_has_no_name_slug_or_password_of_its_own(self):
        columns = {field.name for field in ForumChannel._meta.get_fields()}
        self.assertFalse({"name", "slug", "access", "password_verifier", "password_salt",
                          "is_encrypted", "expires_at", "people"} & columns)
        self.assertEqual(self.channel.name, "Guild")
        self.assertEqual(self.channel.slug, self.guild.slug)

    def test_ensuring_twice_makes_one(self):
        from toto.forum import channels

        again = channels.ensure_channel(self.guild)
        self.assertEqual(again.pk, self.channel.pk)
        self.assertEqual(ForumChannel.objects.filter(community=self.guild).count(), 1)


class RuleTests(ForumCase):
    def test_who_reads(self):
        for user in (self.member, self.senior, self.head, self.admin):
            self.assertTrue(access.may_read(user, self.guild), user.username)
        for user in (self.free, self.outsider, self.staff):
            self.assertFalse(access.may_read(user, self.guild), user.username)

    def test_who_moderates(self):
        for user in (self.head, self.admin):
            self.assertTrue(access.may_moderate(user, self.guild), user.username)
        for user in (self.member, self.senior, self.free, self.outsider, self.staff):
            self.assertFalse(access.may_moderate(user, self.guild), user.username)

    def test_a_head_on_free_does_not_moderate(self):
        chief, person = member("chief")
        self.other.head = person
        self.other.save(update_fields=["head"])
        self.assertFalse(access.may_moderate(chief, self.other))
        on_plan(chief)
        self.assertTrue(access.may_moderate(chief, self.other))

    def test_a_superuser_without_the_admin_plan_is_no_administrator(self):
        root, _person = member("root", is_superuser=True)
        self.assertFalse(access.may_read(root, self.guild))

    def test_leaving_the_community_closes_the_channel(self):
        self.assertTrue(access.may_read(self.member, self.guild))
        self.member_person.communities.remove(self.guild)
        self.assertFalse(access.may_read(self.member, self.guild))
        self.assertEqual(self.feed(self.member).status_code, 403)

    def test_the_list_of_ones_communities(self):
        self.assertEqual([c.name for c in access.communities_of(self.member)], ["Guild"])
        self.assertEqual([c.name for c in access.communities_of(self.head)], ["Guild"])
        self.assertEqual([c.name for c in access.communities_of(self.senior)], ["Guild"])
        self.assertEqual([c.name for c in access.communities_of(self.admin)], ["Guild", "Other"])
        self.assertEqual(list(access.communities_of(self.free)), [])
        self.assertEqual(list(access.communities_of(self.staff)), [])


class DoorTests(ForumCase):
    """Every door, for every kind of visitor."""

    def setUp(self):
        super().setUp()
        self.message_id = self.say(self.member, "the first word").json()["message"]["id"]
        self.poll = self.open_poll(self.member).json()["poll"]

    def doors(self):
        """``(name, method, url, body kind)`` of every door on the Guild."""
        return [
            ("channel_detail", "GET", self.url("channel_detail"), None),
            ("feed", "GET", self.url("feed"), None),
            ("post", "POST", self.url("post"), "form"),
            ("estimate", "POST", self.url("estimate"), "sizes"),
            ("message_image", "GET", self.url("message_image", self.message_id), None),
            ("message_remove", "POST", self.url("message_remove", self.message_id), "json"),
            ("poll_open", "POST", self.url("poll_open"), "poll"),
            ("poll_vote", "POST", self.url("poll_vote", self.poll["id"]), "vote"),
            ("poll_close", "POST", self.url("poll_close", self.poll["id"]), "json"),
            ("poll_remove", "POST", self.url("poll_remove", self.poll["id"]), "json"),
        ]

    def knock(self, client, method, url, kind):
        if method == "GET":
            return client.get(url)
        if kind == "form":
            return client.post(url, {"op": op(), "text": "knock"})
        if kind == "sizes":
            return client.post(url, {"text_bytes": 5})
        if kind == "poll":
            return send_json(client, url, {"title": "Q?", "options": "a\nb"})
        if kind == "vote":
            return send_json(client, url, {"choice": self.poll["choices"][0]["id"]})
        return send_json(client, url, {})

    def test_nobody_signed_in_gets_nothing(self):
        anonymous = Client()
        self.assertIn(anonymous.get("/forum/").status_code, (302, 401, 403))
        for name, method, url, kind in self.doors():
            with self.subTest(door=name):
                # A host's own login gate may answer before the door does.
                self.assertIn(self.knock(anonymous, method, url, kind).status_code,
                              (302, 401, 403))
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_a_member_on_free_gets_402_everywhere(self):
        client = client_of(self.free)
        self.assertEqual(client.get("/forum/").status_code, 402)
        for name, method, url, kind in self.doors():
            with self.subTest(door=name):
                self.assertEqual(self.knock(client, method, url, kind).status_code, 402)
        self.assertEqual(ForumMessage.objects.count(), 1)

    @override_settings(SUBSCRIPTION_ENFORCEMENT=False)
    def test_free_is_refused_by_the_forum_itself_without_the_plan_gate(self):
        client = client_of(self.free)
        for name, method, url, kind in self.doors():
            with self.subTest(door=name):
                self.assertEqual(self.knock(client, method, url, kind).status_code, 402)

    def test_who_is_no_member_gets_403_everywhere(self):
        for user in (self.outsider, self.staff):
            client = client_of(user)
            for name, method, url, kind in self.doors():
                with self.subTest(user=user.username, door=name):
                    self.assertEqual(self.knock(client, method, url, kind).status_code, 403)
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_a_slug_that_names_no_community_is_404(self):
        client = client_of(self.member)
        self.assertEqual(client.get("/forum/no-such-community/").status_code, 404)
        self.assertEqual(client.get("/forum/no-such-community/feed/").status_code, 404)
        self.assertEqual(client.post("/forum/no-such-community/post/",
                                     {"op": op(), "text": "x"}).status_code, 404)

    def test_members_read_and_post(self):
        for user in (self.member, self.senior, self.head, self.admin):
            client = client_of(user)
            with self.subTest(user=user.username):
                self.assertEqual(client.get(self.url("channel_detail")).status_code, 200)
                self.assertEqual(client.get(self.url("feed")).status_code, 200)
                self.assertEqual(client.get("/forum/").status_code, 200)
                self.assertEqual(self.say(user, f"from {user.username}").status_code, 201)

    def test_the_wrong_method_is_405(self):
        client = client_of(self.member)
        self.assertEqual(client.get(self.url("post")).status_code, 405)
        self.assertEqual(client.post(self.url("feed")).status_code, 405)
        self.assertEqual(client.get(self.url("poll_open")).status_code, 405)

    def test_a_write_from_another_site_is_refused(self):
        client = client_of(self.member)
        response = client.post(self.url("post"), {"op": op(), "text": "forged"},
                               HTTP_SEC_FETCH_SITE="cross-site")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_only_the_author_the_head_and_an_administrator_remove_a_message(self):
        url = self.url("message_remove", self.message_id)
        for user in (self.second, self.senior):
            self.assertEqual(send_json(client_of(user), url).status_code, 403, user.username)
        self.assertIsNone(ForumMessage.objects.get(pk=self.message_id).removed_at)
        self.assertEqual(send_json(client_of(self.head), url).status_code, 200)
        self.assertIsNotNone(ForumMessage.objects.get(pk=self.message_id).removed_at)
        for remover in (self.member, self.admin):
            mine = self.say(self.member, "another").json()["message"]["id"]
            self.assertEqual(send_json(client_of(remover),
                                       self.url("message_remove", mine)).status_code, 200)

    def test_only_the_opener_the_head_and_an_administrator_manage_a_poll(self):
        for door in ("poll_close", "poll_remove"):
            url = self.url(door, self.poll["id"])
            for user in (self.second, self.senior):
                self.assertEqual(send_json(client_of(user), url).status_code, 403,
                                 (door, user.username))
        for manager in (self.member, self.head, self.admin):
            poll = self.open_poll(self.second).json()["poll"]
            if manager is self.member:
                poll = self.open_poll(self.member).json()["poll"]
            self.assertEqual(send_json(client_of(manager),
                                       self.url("poll_close", poll["id"])).status_code, 200)
            self.assertEqual(send_json(client_of(manager),
                                       self.url("poll_remove", poll["id"])).status_code, 200)

    def test_a_row_of_another_channel_is_not_found_here(self):
        """A message's id under another community's slug names nothing."""
        self.outsider_person.communities.add(self.guild)   # reads both channels
        from toto.forum import channels

        channels.ensure_channel(self.other)
        url = self.url("message_remove", self.message_id, community=self.other)
        self.assertEqual(send_json(client_of(self.admin), url).status_code, 404)
        url = self.url("poll_vote", self.poll["id"], community=self.other)
        self.assertEqual(send_json(client_of(self.outsider), url,
                                   {"choice": self.poll["choices"][0]["id"]}).status_code, 404)

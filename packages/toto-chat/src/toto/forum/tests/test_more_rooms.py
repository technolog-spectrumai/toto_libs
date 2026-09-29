"""Rooms as people use them: the pages, the JSON doors, and the rules behind both.

What is pinned here and nowhere in the gate's other forum suites:

* the room-making and joining rules at their edges (a name with no slug, a
  lifetime nobody offers, rejoining after leaving, an expired room);
* the room pages' refusals and sentences — the chat page's observer reasons,
  the Members and Security tabs for the creator, for staff and for a member;
* the JSON doors a desktop client uses — history over HTTP, search, join,
  leave, upload, attachment — each refusing the way the page does, including
  a temporary room from the instant it expires.
"""

import json
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.forum import creation, permissions, presence, search, store
from toto.forum.models import ForumChannel, ForumMember, ForumMessage

User = get_user_model()

FAST = dict(FORUM_PASSWORD_KDF={"memory_cost": 8, "iterations": 1, "lanes": 1},
            FORUM_VAULT_PASSWORD="forum-test-vault-passphrase",
            FORUM_ALLOW_LOCAL_KEY_STORE=True)

SMALL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0c"
    b"IDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)


def _person(user, name):
    from toto.people.models import Person

    return Person.objects.create(user=user, display_name=name)


@override_settings(**FAST)
class RoomFixture(TestCase):
    def setUp(self):
        cache.clear()
        root = tempfile.mkdtemp(prefix="forum-more-")
        self.addCleanup(shutil.rmtree, root, True)
        override = override_settings(MEDIA_ROOT=f"{root}/media",
                                     FORUM_ATTACHMENT_ROOT=f"{root}/attachments")
        override.enable()
        self.addCleanup(override.disable)
        from toto.core.models import Platform

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        self.owner = User.objects.create_user("owner", password="x")
        self.bob = User.objects.create_user("bob", password="x")
        self.carol = User.objects.create_user("carol", password="x")
        self.staff = User.objects.create_user("staff", password="x", is_staff=True)
        self.nobody = User.objects.create_user("nobody", password="x")   # no Person
        for user, name in ((self.owner, "Owner"), (self.bob, "Bob"),
                           (self.carol, "Carol"), (self.staff, "Staff")):
            _person(user, name)

    def room(self, name="Talk", **kw):
        return creation.create_room(self.owner, name=name, **kw)

    def expire(self, room):
        ForumChannel.objects.filter(pk=room.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        room.refresh_from_db()
        return room

    def say(self, room, user, body, **kw):
        return store.store_message(room, msg_type="chat_message", body=body, sender=user,
                                   sender_name=user.username, **kw)


class RoomRuleTests(RoomFixture):
    def test_a_room_needs_a_name_that_makes_a_slug(self):
        for name in ("", "   ", "!!!"):
            with self.subTest(name=name), self.assertRaises(creation.RoomRefused):
                self.room(name=name)
        self.assertFalse(ForumChannel.objects.exists())

    def test_a_room_named_after_a_forum_address_is_refused(self):
        for name in ("Search", "API", "export"):
            with self.subTest(name=name), self.assertRaises(creation.RoomRefused) as caught:
                self.room(name=name)
            self.assertIn("forum's own addresses", str(caught.exception))

    def test_only_the_offered_lifetimes_can_be_chosen(self):
        with self.assertRaises(creation.RoomRefused):
            self.room(expires_in="90d")
        week = self.room(name="Week", expires_in="7d")
        self.assertTrue(week.is_temporary)
        self.assertAlmostEqual((week.expires_at - timezone.now()).total_seconds(),
                               7 * 86400, delta=60)
        self.assertFalse(self.room(name="Forever").is_temporary)

    def test_a_temporary_encrypted_room_needs_the_shared_cache(self):
        with patch("toto.forum.rooms.shared_key_store_available", return_value=False):
            with self.assertRaises(creation.RoomRefused) as caught:
                self.room(name="Brief", encrypted=True, expires_in="1h")
        self.assertIn("shared cache", str(caught.exception))
        self.assertFalse(ForumChannel.objects.filter(name="Brief").exists())

    def test_the_creator_is_its_first_member(self):
        room = self.room()
        self.assertTrue(permissions.is_member(self.owner, room))
        self.assertTrue(permissions.can_manage_members(self.owner, room))

    def test_a_creator_without_a_person_makes_a_room_they_are_not_in(self):
        room = creation.create_room(self.nobody, name="Orphan")
        self.assertFalse(room.forum_members.exists())
        self.assertTrue(permissions.can_manage_members(self.nobody, room))

    def test_joining_needs_a_person_profile(self):
        room = self.room()
        with self.assertRaises(creation.RoomRefused) as caught:
            creation.join(self.nobody, room)
        self.assertEqual(caught.exception.status, 403)

    def test_an_expired_room_cannot_be_joined(self):
        room = self.expire(self.room(expires_in="1h"))
        with self.assertRaises(creation.RoomRefused) as caught:
            creation.join(self.bob, room)
        self.assertEqual(caught.exception.status, 410)

    def test_joining_twice_is_not_a_second_membership(self):
        room = self.room()
        self.assertTrue(creation.join(self.bob, room))
        self.assertFalse(creation.join(self.bob, room))
        self.assertEqual(ForumMember.objects.filter(channel=room, person__user=self.bob).count(),
                         1)

    def test_someone_who_left_rejoins_on_the_same_membership(self):
        room = self.room()
        creation.join(self.bob, room)
        member = ForumMember.objects.get(channel=room, person__user=self.bob)
        creation.remove_member(self.owner, room, member.pk)
        self.assertFalse(permissions.is_member(self.bob, room))
        self.assertFalse(creation.join(self.bob, room))   # not created: reactivated
        member.refresh_from_db()
        self.assertTrue(member.is_active)

    def test_removing_someone_who_is_not_a_member_is_a_404(self):
        room = self.room()
        with self.assertRaises(creation.RoomRefused) as caught:
            creation.remove_member(self.owner, room, 999999)
        self.assertEqual(caught.exception.status, 404)

    def test_staff_remove_members_from_a_room_they_did_not_make(self):
        room = self.room()
        creation.join(self.bob, room)
        member = ForumMember.objects.get(channel=room, person__user=self.bob)
        creation.remove_member(self.staff, room, member.pk)
        self.assertFalse(permissions.is_member(self.bob, room))

    def test_an_open_room_has_no_password_to_change(self):
        room = self.room()
        with self.assertRaises(creation.RoomRefused):
            creation.change_password(self.owner, room, "correct horse battery")

    def test_a_member_who_is_not_the_creator_cannot_change_the_password(self):
        room = self.room(access="password", password="correct horse")
        creation.join(self.bob, room, password="correct horse")
        with self.assertRaises(creation.RoomRefused) as caught:
            creation.change_password(self.bob, room, "new horse battery")
        self.assertEqual(caught.exception.status, 403)

    def test_changing_an_encrypted_rooms_password_keeps_its_key_openable(self):
        from toto.forum import rooms

        room = self.room(access="password", password="correct horse", encrypted=True)
        key = rooms.open_key(room)
        creation.change_password(self.owner, room, "new horse battery")
        room.refresh_from_db()
        self.assertTrue(rooms.verify_password(room, "new horse battery"))
        self.assertEqual(rooms.open_key(room), key)

    @override_settings(FORUM_MAX_CHANNELS=1)
    def test_the_platform_holds_a_fixed_number_of_rooms(self):
        self.room(name="One")
        with self.assertRaises(creation.RoomRefused) as caught:
            self.room(name="Two")
        self.assertEqual(caught.exception.status, 409)

    @override_settings(FORUM_RATE_LIMITS={"password_user": (1, 300)})
    def test_a_setting_overrides_one_rate_limit_and_keeps_the_rest(self):
        limits = creation.limits()
        self.assertEqual(limits["password_user"], (1, 300))
        self.assertEqual(limits["send"], creation.DEFAULT_LIMITS["send"])


class PermissionTests(RoomFixture):
    def test_membership_can_be_asked_by_slug(self):
        room = self.room()
        self.assertIsNotNone(permissions.member_for(self.owner, room.slug))
        self.assertIsNone(permissions.member_for(self.bob, room.slug))
        self.assertIsNone(permissions.member_for(self.nobody, room.slug))

    def test_an_expired_room_refuses_reading_before_the_sweep_removes_it(self):
        room = self.room(expires_in="1h")
        self.assertTrue(permissions.can_read(self.owner, room))
        self.expire(room)
        self.assertFalse(permissions.can_read(self.owner, room))
        self.assertFalse(permissions.can_send(self.owner, room.slug))
        self.assertEqual(permissions.join_verdict(self.bob, room), "closed")

    def test_an_unknown_slug_is_not_readable(self):
        self.assertFalse(permissions.can_read(self.owner, "no-such-room"))

    def test_authors_edit_their_own_messages_and_staff_any(self):
        room = self.room()
        creation.join(self.bob, room)
        message = self.say(room, self.bob, "hello")
        self.assertTrue(permissions.can_moderate(self.bob, message))
        self.assertTrue(permissions.can_moderate(self.staff, message))
        self.assertFalse(permissions.can_moderate(self.owner, message))
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(permissions.can_moderate(AnonymousUser(), message))

    def test_a_superuser_is_an_operator_without_being_staff(self):
        root = User.objects.create_superuser("root", "root@example.com", "x")
        root.is_staff = False
        self.assertTrue(permissions.is_operator(root))
        self.assertFalse(permissions.is_operator(self.bob))

    def test_someone_without_a_person_reads_no_room(self):
        self.room()
        self.assertFalse(permissions.readable_channels(self.nobody).exists())


class ChannelPageTests(RoomFixture):
    def detail(self, user, room):
        self.client.force_login(user)
        return self.client.get(reverse("forum:channel_detail", args=[room.slug]))

    def test_a_non_member_is_invited_to_join_and_sees_no_roster(self):
        room = self.room()
        response = self.detail(self.bob, room)
        self.assertTrue(response.context["can_join"])
        self.assertFalse(response.context["can_send_messages"])
        self.assertEqual(response.context["participants"], [])
        self.assertEqual(response.context["observer_reason"],
                         "Join this channel to read and send messages.")

    def test_someone_without_a_person_is_told_why_they_only_observe(self):
        response = self.detail(self.nobody, self.room())
        self.assertFalse(response.context["can_join"])
        self.assertIn("not linked to a person profile", response.context["observer_reason"])

    def test_a_member_of_an_expired_room_can_no_longer_send(self):
        room = self.expire(self.room(expires_in="1h"))
        response = self.detail(self.owner, room)
        self.assertFalse(response.context["can_send_messages"])
        self.assertFalse(response.context["can_join"])
        self.assertEqual(response.context["observer_reason"], "You are observing this channel.")

    def test_a_member_sees_the_roster_and_the_price_code_of_the_room(self):
        room = self.room(access="password", password="correct horse", encrypted=True)
        response = self.detail(self.owner, room)
        self.assertEqual([p["username"] for p in response.context["participants"]], ["Owner"])
        self.assertEqual(response.context["forum_price_code"], "forum.encrypt")
        self.assertEqual(self.detail(self.owner, self.room(name="Plain")).context[
            "forum_price_code"], "forum.message")

    def test_the_list_filters_by_name_and_marks_what_i_joined(self):
        mine = self.room(name="Garden Club")
        creation.create_room(self.bob, name="Chess")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("forum:channel_list"), {"q": "garden"})
        channels = list(response.context["channels"])
        self.assertEqual([c.pk for c in channels], [mine.pk])
        self.assertTrue(channels[0].is_joined)
        self.assertEqual(channels[0].member_count, 1)
        self.assertFalse(response.context["is_operator"])
        self.client.force_login(self.staff)
        self.assertTrue(self.client.get(reverse("forum:channel_list")).context["is_operator"])

    def test_joining_from_the_page_says_so_once(self):
        room = self.room()
        self.client.force_login(self.bob)
        first = self.client.post(reverse("forum:channel_join", args=[room.slug]), follow=True)
        self.assertContains(first, "You joined Talk as a member.")
        again = self.client.post(reverse("forum:channel_join", args=[room.slug]), follow=True)
        self.assertContains(again, "You are a member of Talk.")

    def test_joining_an_unknown_room_is_a_404(self):
        self.client.force_login(self.bob)
        self.assertEqual(self.client.post(reverse("forum:channel_join",
                                                  args=["nowhere"])).status_code, 404)

    def test_leaving_from_the_page_ends_the_membership(self):
        room = self.room()
        creation.join(self.bob, room)
        self.client.force_login(self.bob)
        response = self.client.post(reverse("forum:channel_leave", args=[room.slug]),
                                    follow=True)
        self.assertContains(response, "You left Talk.")
        self.assertFalse(permissions.is_member(self.bob, room))

    def test_a_refused_room_is_explained_on_the_list(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("forum:channel_create"),
                                    {"name": "Short", "access": "password",
                                     "password": "123"}, follow=True)
        self.assertContains(response, "at least 8 characters")
        self.assertFalse(ForumChannel.objects.filter(name="Short").exists())

    def test_a_temporary_room_is_made_from_the_page(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("forum:channel_create"),
                                    {"name": "Standup", "expires_in": "24h"})
        room = ForumChannel.objects.get(name="Standup")
        self.assertRedirects(response, reverse("forum:channel_detail", args=[room.slug]),
                             fetch_redirect_response=False)
        self.assertTrue(room.is_temporary)


class MembersTabTests(RoomFixture):
    def test_a_non_member_is_kept_out(self):
        room = self.room()
        self.client.force_login(self.carol)
        self.assertEqual(self.client.get(reverse("forum:room_members",
                                                 args=[room.slug])).status_code, 403)

    def test_staff_manage_members_without_joining(self):
        room = self.room()
        creation.join(self.bob, room)
        member = ForumMember.objects.get(channel=room, person__user=self.bob)
        self.client.force_login(self.staff)
        url = reverse("forum:room_members", args=[room.slug])
        self.assertTrue(self.client.get(url).context["can_manage_members"])
        self.client.post(url, {"action": "remove", "member": member.pk})
        member.refresh_from_db()
        self.assertFalse(member.is_active)

    def test_a_member_who_did_not_make_the_room_cannot_remove_anyone(self):
        room = self.room()
        creation.join(self.bob, room)
        creation.join(self.carol, room)
        carol = ForumMember.objects.get(channel=room, person__user=self.carol)
        self.client.force_login(self.bob)
        response = self.client.post(reverse("forum:room_members", args=[room.slug]),
                                    {"action": "remove", "member": carol.pk}, follow=True)
        self.assertContains(response, "Only the room&#x27;s creator or staff remove members.")
        carol.refresh_from_db()
        self.assertTrue(carol.is_active)

    def test_removing_a_stale_row_says_no_such_member(self):
        room = self.room()
        self.client.force_login(self.owner)
        response = self.client.post(reverse("forum:room_members", args=[room.slug]),
                                    {"action": "remove", "member": "12345"}, follow=True)
        self.assertContains(response, "No such member.")


class SecurityTabTests(RoomFixture):
    def url(self, room):
        return reverse("forum:room_security", args=[room.slug])

    def test_staff_set_the_first_password_of_a_room_that_has_none(self):
        room = self.room(name="Board")
        ForumChannel.objects.filter(pk=room.pk).update(access="password")
        self.client.force_login(self.staff)
        response = self.client.post(self.url(room), {"action": "password",
                                                     "password": "correct horse"}, follow=True)
        self.assertContains(response, "Password set.")
        room.refresh_from_db()
        self.assertTrue(creation.join(self.bob, room, password="correct horse"))

    def test_a_post_that_is_not_about_the_password_does_nothing(self):
        room = self.room(access="password", password="correct horse")
        self.client.force_login(self.owner)
        response = self.client.post(self.url(room), {"action": "encrypt"}, follow=True)
        self.assertContains(response, "Nothing to do.")
        room.refresh_from_db()
        from toto.forum import rooms

        self.assertTrue(rooms.verify_password(room, "correct horse"))

    def test_an_open_room_refuses_a_password(self):
        room = self.room()
        self.client.force_login(self.owner)
        response = self.client.post(self.url(room), {"action": "password",
                                                     "password": "correct horse"}, follow=True)
        self.assertContains(response, "This room has no password.")

    @override_settings(FORUM_RATE_LIMITS={"password_user": (3, 60), "password_room": (9, 60)})
    def test_the_tab_states_the_real_limits_and_costs(self):
        room = self.room(access="password", password="correct horse")
        self.client.force_login(self.owner)
        context = self.client.get(self.url(room)).context
        self.assertEqual(context["password_limits"], {"user": (3, 60), "room": (9, 60)})
        self.assertEqual(context["kdf"], {"memory_mib": 0, "iterations": 1, "lanes": 1})
        self.assertEqual(context["message_price_code"], "forum.message")
        self.assertIsNone(context["room_key"])
        self.assertTrue(context["has_password"])

    def test_an_encrypted_room_shows_its_key(self):
        room = self.room(access="password", password="correct horse", encrypted=True)
        self.client.force_login(self.owner)
        context = self.client.get(self.url(room)).context
        self.assertIsNotNone(context["room_key"])
        self.assertTrue(context["password_wraps_key"])
        self.assertEqual(context["message_price_code"], "forum.encrypt")


class MessagesApiTests(RoomFixture):
    def get(self, user, room_slug, **params):
        self.client.force_login(user)
        return self.client.get(reverse("forum:api_channel_messages", args=[room_slug]), params)

    def test_history_is_for_members_only(self):
        room = self.room()
        self.say(room, self.owner, "first")
        self.assertEqual(self.get(self.bob, room.slug).status_code, 403)
        self.assertEqual(self.get(self.owner, "no-such-room").status_code, 404)
        data = self.get(self.owner, room.slug).json()
        self.assertEqual([m["message"] for m in data["messages"]], ["first"])
        self.assertFalse(data["has_more"])

    def test_an_anonymous_caller_learns_nothing(self):
        room = self.room()
        response = self.client.get(reverse("forum:api_channel_messages", args=[room.slug]))
        self.assertEqual(response.status_code, 401)
        missing = self.client.get(reverse("forum:api_channel_messages", args=["no-such-room"]))
        self.assertEqual(missing.status_code, 401)

    def test_a_bad_cursor_or_limit_is_a_400(self):
        room = self.room()
        self.assertEqual(self.get(self.owner, room.slug, before="yesterday").status_code, 400)
        self.assertEqual(self.get(self.owner, room.slug, limit="many").status_code, 400)

    def test_history_pages_backwards_from_the_oldest_message_held(self):
        room = self.room()
        base = timezone.now() - timedelta(hours=1)
        for n in range(5):
            row = self.say(room, self.owner, f"m{n}")
            ForumMessage.objects.filter(pk=row.pk).update(created_at=base + timedelta(minutes=n))
        newest = self.get(self.owner, room.slug, limit=2).json()
        self.assertEqual([m["message"] for m in newest["messages"]], ["m3", "m4"])
        self.assertTrue(newest["has_more"])
        oldest = newest["messages"][0]
        older = self.get(self.owner, room.slug, limit=10, before=oldest["created_at"],
                         before_id=oldest["id"]).json()
        self.assertEqual([m["message"] for m in older["messages"]], ["m0", "m1", "m2"])
        self.assertFalse(older["has_more"])

    def test_an_expired_room_has_no_history_to_give(self):
        room = self.room(expires_in="1h")
        self.say(room, self.owner, "before the end")
        self.expire(room)
        self.assertEqual(self.get(self.owner, room.slug).status_code, 403)

    def test_an_encrypted_rooms_history_arrives_readable_to_a_member(self):
        room = self.room(access="password", password="correct horse", encrypted=True)
        from toto.forum import rooms

        self.say(room, self.owner, "sealed words", key=rooms.open_key(room))
        stored = ForumMessage.objects.get(channel=room)
        self.assertEqual(stored.body, "")
        (message,) = self.get(self.owner, room.slug).json()["messages"]
        self.assertEqual((message["message"], message["sealed"]), ("sealed words", True))


class SearchApiTests(RoomFixture):
    def search(self, user, **params):
        self.client.force_login(user)
        return self.client.get(reverse("forum:api_message_search"), params).json()

    def test_an_empty_query_finds_nothing_and_names_the_engine(self):
        data = self.search(self.owner, q="  ")
        self.assertEqual((data["results"], data["count"]), ([], 0))
        self.assertEqual(data["search_mode"], "substring")

    def test_only_rooms_i_am_in_are_searched(self):
        mine = self.room(name="Mine")
        theirs = creation.create_room(self.bob, name="Theirs")
        self.say(mine, self.owner, "the blue kettle")
        self.say(theirs, self.bob, "the blue teapot")
        data = self.search(self.owner, q="blue")
        self.assertEqual([r["message"] for r in data["results"]], ["the blue kettle"])
        self.assertEqual(data["results"][0]["channel_slug"], mine.slug)

    def test_a_room_filter_narrows_and_a_deleted_message_is_not_found(self):
        a, b = self.room(name="A"), self.room(name="B")
        self.say(a, self.owner, "apple one")
        gone = self.say(b, self.owner, "apple two")
        self.assertEqual(self.search(self.owner, q="apple", channel=b.slug)["count"], 1)
        ForumMessage.objects.filter(pk=gone.pk).update(deleted_at=timezone.now())
        self.assertEqual(self.search(self.owner, q="apple", channel=b.slug)["count"], 0)

    def test_encrypted_rooms_are_skipped_and_counted(self):
        from toto.forum import rooms

        sealed = self.room(name="Sealed", access="password", password="correct horse",
                           encrypted=True)
        self.say(sealed, self.owner, "secret apple", key=rooms.open_key(sealed))
        self.assertEqual(self.search(self.owner, q="apple")["count"], 0)
        self.assertEqual(search.encrypted_rooms_skipped(self.owner), 1)
        self.assertFalse(search.search_messages(self.owner, "").exists())

    def test_the_search_page_says_how_many_encrypted_rooms_it_skipped(self):
        self.room(name="Sealed", access="password", password="correct horse", encrypted=True)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("forum:message_search"), {"q": "x"})
        self.assertEqual(response.context["encrypted_rooms_skipped"], 1)
        self.assertFalse(response.context["searchable_channels"].exists())


class JoinLeaveApiTests(RoomFixture):
    def post(self, user, name, slug, body=None, content_type="application/json"):
        self.client.force_login(user)
        return self.client.post(reverse(name, args=[slug]) if slug else reverse(name),
                                data=body if body is not None else "",
                                content_type=content_type)

    def test_the_password_rides_in_the_json_body(self):
        room = self.room(access="password", password="correct horse")
        wrong = self.post(self.bob, "forum:api_channel_join", room.slug,
                          json.dumps({"password": "nope"}))
        self.assertEqual(wrong.status_code, 403)
        right = self.post(self.bob, "forum:api_channel_join", room.slug,
                          json.dumps({"password": "correct horse"}))
        self.assertEqual(right.json(), {"ok": True, "joined": True})

    def test_joining_an_unknown_or_expired_room_is_refused(self):
        self.assertEqual(self.post(self.bob, "forum:api_channel_join", "nowhere").status_code,
                         404)
        room = self.expire(self.room(expires_in="1h"))
        self.assertEqual(self.post(self.bob, "forum:api_channel_join", room.slug).status_code,
                         410)

    def test_leaving_an_unknown_room_is_a_404(self):
        self.assertEqual(self.post(self.bob, "forum:api_channel_leave", "nowhere").status_code,
                         404)

    def test_leaving_everything_without_a_person_leaves_nothing(self):
        response = self.post(self.nobody, "forum:api_channel_leave_all", None)
        self.assertEqual(response.json(), {"ok": True, "left": 0})

    def test_a_cross_site_join_is_refused(self):
        room = self.room()
        self.client.force_login(self.bob)
        response = self.client.post(reverse("forum:api_channel_join", args=[room.slug]),
                                    HTTP_SEC_FETCH_SITE="cross-site")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(permissions.is_member(self.bob, room))

    def test_creating_a_room_with_a_body_that_is_not_json_is_a_400(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("forum:api_channel_list"), data="{nope",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)


@patch("toto.forum.api_views.get_channel_layer", return_value=None)
class UploadAndAttachmentApiTests(RoomFixture):
    def upload(self, user, room, name="shot.png", raw=SMALL_PNG, mime="image/png"):
        self.client.force_login(user)
        return self.client.post(reverse("forum:api_image_upload", args=[room.slug]),
                                {"image": SimpleUploadedFile(name, raw, content_type=mime)})

    def attachment(self, user, message_id):
        self.client.force_login(user)
        return self.client.get(reverse("forum:api_message_attachment", args=[message_id]))

    def test_an_expired_room_takes_no_uploads(self, _layer):
        room = self.expire(self.room(expires_in="1h"))
        self.assertEqual(self.upload(self.owner, room).status_code, 403)

    def test_an_upload_over_ten_megabytes_is_refused(self, _layer):
        room = self.room()
        big = b"\x89PNG" + b"0" * (10 * 1024 * 1024)
        self.assertEqual(self.upload(self.owner, room, raw=big).status_code, 413)
        self.assertFalse(ForumMessage.objects.filter(channel=room).exists())

    @override_settings(FORUM_RATE_LIMITS={"api_post": (1, 60)})
    def test_uploads_are_rate_limited_per_person(self, _layer):
        room = self.room()
        self.assertEqual(self.upload(self.owner, room).status_code, 200)
        self.assertEqual(self.upload(self.owner, room).status_code, 429)
        self.assertEqual(ForumMessage.objects.filter(channel=room).count(), 1)

    def test_an_upload_to_an_encrypted_room_is_sealed_at_rest_and_opens_for_a_member(self, _l):
        room = self.room(access="password", password="correct horse", encrypted=True)
        response = self.upload(self.owner, room)
        self.assertEqual(response.status_code, 200, response.content)
        row = ForumMessage.objects.get(channel=room)
        self.assertTrue(row.attachment_sealed)
        with row.attachment.open("rb") as fh:
            self.assertNotEqual(fh.read(), SMALL_PNG)
        opened = self.attachment(self.owner, row.id)
        self.assertEqual(opened.content, SMALL_PNG)
        self.assertEqual(opened["X-Content-Type-Options"], "nosniff")
        creation.join(self.bob, room, password="correct horse")
        self.assertEqual(self.attachment(self.bob, row.id).content, SMALL_PNG)
        self.assertEqual(self.attachment(self.carol, row.id).status_code, 403)

    def test_a_voice_message_needs_an_audio_type(self, _layer):
        room = self.room()
        self.client.force_login(self.owner)
        url = reverse("forum:api_audio_upload", args=[room.slug])
        refused = self.client.post(url, {"audio": SimpleUploadedFile(
            "v.png", SMALL_PNG, content_type="image/png")})
        self.assertEqual(refused.status_code, 415)
        accepted = self.client.post(url, {"audio": SimpleUploadedFile(
            "v.webm", b"\x1aE\xdf\xa3", content_type="audio/webm;codecs=opus")})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["type"], "voice_message")

    def test_a_message_without_a_file_has_no_attachment(self, _layer):
        room = self.room()
        row = self.say(room, self.owner, "just words")
        self.assertEqual(self.attachment(self.owner, row.id).status_code, 404)

    def test_a_file_missing_from_disk_is_a_404_not_a_500(self, _layer):
        room = self.room()
        self.upload(self.owner, room)
        row = ForumMessage.objects.get(channel=room)
        row.attachment.storage.delete(row.attachment.name)
        self.assertEqual(self.attachment(self.owner, row.id).status_code, 404)

    def test_an_expired_rooms_files_stop_opening(self, _layer):
        room = self.room(expires_in="1h")
        self.upload(self.owner, room)
        row = ForumMessage.objects.get(channel=room)
        self.expire(room)
        self.assertEqual(self.attachment(self.owner, row.id).status_code, 403)


class PresenceTests(TestCase):
    """Who is connected right now — soft state that forgets the dead."""

    def setUp(self):
        cache.clear()

    def test_arriving_and_departing(self):
        presence.arrive("room", "Bob")
        presence.arrive("room", "Ann")
        self.assertEqual(presence.online("room"), ["Ann", "Bob"])
        presence.depart("room", "Bob")
        self.assertEqual(presence.online("room"), ["Ann"])
        self.assertEqual(presence.online("elsewhere"), [])

    def test_a_nameless_socket_is_not_recorded(self):
        presence.arrive("room", "")
        presence.depart("room", "")
        self.assertEqual(presence.online("room"), [])

    def test_someone_not_seen_for_the_staleness_window_is_gone(self):
        with patch("toto.forum.presence.time.time", return_value=1_000_000.0):
            presence.arrive("room", "Ghost")
        later = 1_000_000.0 + presence.STALE_AFTER_SECONDS + 1
        with patch("toto.forum.presence.time.time", return_value=later):
            self.assertEqual(presence.online("room"), [])

    def test_a_broken_cache_means_nobody_rather_than_an_error(self):
        with patch("toto.forum.presence.cache.get", side_effect=RuntimeError("down")), \
                patch("toto.forum.presence.cache.set", side_effect=RuntimeError("down")):
            presence.arrive("room", "Bob")
            self.assertEqual(presence.online("room"), [])


class ModelTests(RoomFixture):
    def test_badges_say_what_a_room_is_in_order(self):
        room = self.room(access="password", password="correct horse", encrypted=True,
                         expires_in="1h")
        self.assertEqual([b["key"] for b in room.badges()],
                         ["password", "encrypted", "temporary"])
        self.assertEqual([b["key"] for b in self.room(name="Plain").badges()], ["open"])

    def test_a_password_room_without_a_password_does_not_validate(self):
        from django.core.exceptions import ValidationError

        room = ForumChannel(name="Pw", slug="pw", access="password")
        with self.assertRaises(ValidationError):
            room.clean()

    @override_settings(FORUM_MAX_CHANNELS=0)
    def test_the_cap_holds_for_rooms_made_outside_the_forms(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            ForumChannel.objects.create(name="Shell", slug="shell")
        with self.assertRaises(ValidationError):
            ForumChannel(name="Admin", slug="admin").clean()

    def test_a_member_shows_the_default_avatar_without_a_picture(self):
        room = self.room()
        member = ForumMember.objects.get(channel=room)
        self.assertEqual(member.avatar_url, "/static/img/avatars/default.png")
        self.assertEqual(str(member), "Owner in Talk")


class SweepTaskTests(RoomFixture):
    """The worker entry points: what they do with a run or a room that moved on."""

    def test_a_cleanup_run_already_closed_is_skipped_by_the_worker(self):
        from toto.forum import cleanup
        from toto.forum.models import ForumCleanupRun, RunStatus, TriggeredBy
        from toto.forum.tasks import forum_cleanup_run

        room = self.room()
        run = cleanup.trigger(triggered_by=TriggeredBy.MANUAL, user=self.staff, channel=room)
        ForumCleanupRun.objects.filter(pk=run.pk).update(status=RunStatus.FAILED)
        self.assertEqual(forum_cleanup_run(run.pk), {"skipped": True, "run": run.pk})
        self.assertEqual(forum_cleanup_run(987654), {"skipped": True, "run": 987654})

    def test_the_worker_finishes_a_run_the_page_claimed(self):
        from toto.forum import cleanup
        from toto.forum.models import TriggeredBy
        from toto.forum.tasks import forum_cleanup_run

        room = self.room()
        run = cleanup.trigger(triggered_by=TriggeredBy.MANUAL, user=self.staff, channel=room)
        result = forum_cleanup_run(run.pk)
        self.assertEqual(result["run"], run.pk)
        self.assertEqual(result["status"], "success")
        self.assertFalse(cleanup.in_flight(room))

    def test_one_room_that_fails_to_expire_does_not_stop_the_next(self):
        from toto.forum import expiry
        from toto.forum.models import ForumCleanupRun
        from toto.forum.tasks import forum_expire

        stuck = self.expire(self.room(name="Stuck", expires_in="1h"))
        done = self.expire(self.room(name="Done", expires_in="1h"))
        from toto.forum import rooms

        real_shred = rooms.shred

        def shred(channel):
            if channel.pk == stuck.pk:
                raise RuntimeError("key store unreachable")
            return real_shred(channel)

        with patch("toto.forum.rooms.shred", side_effect=shred):
            results = {r["room"]: r for r in forum_expire()}
        self.assertFalse(results["Stuck"]["ok"])
        self.assertIn("key store unreachable", results["Stuck"]["error"])
        self.assertTrue(results["Done"]["ok"])
        self.assertTrue(ForumChannel.objects.filter(pk=stuck.pk).exists())
        self.assertFalse(ForumChannel.objects.filter(pk=done.pk).exists())
        failed = ForumCleanupRun.objects.get(channel_name="Stuck")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(expiry.expire_due(), [{"room": "Stuck", "ok": True, "messages": 0}])

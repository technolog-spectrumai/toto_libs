"""Who joins what: open, password (rate limited); and what is listed.
There are no invitations (2026-09-28): a password will do. Legacy invite-only
rooms became password rooms with no password (migration 0007)."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.forum import creation
from toto.forum.models import ForumChannel, ForumMember

User = get_user_model()
FAST = dict(FORUM_PASSWORD_KDF={"memory_cost": 8, "iterations": 1, "lanes": 1},
            FORUM_VAULT_PASSWORD="forum-test-vault-passphrase", FORUM_ALLOW_LOCAL_KEY_STORE=True)


def _person(user, name):
    from toto.people.models import Person

    return Person.objects.create(user=user, display_name=name)


@override_settings(**FAST)
class AccessTests(TestCase):
    def setUp(self):
        cache.clear()
        from toto.core.models import Platform

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        self.owner = User.objects.create_user("owner", password="x")
        self.bob = User.objects.create_user("bob", password="x")
        self.staff = User.objects.create_user("staff", password="x", is_staff=True)
        for u, n in ((self.owner, "Owner"), (self.bob, "Bob"), (self.staff, "Staff")):
            _person(u, n)

    def room(self, **kw):
        return creation.create_room(self.owner, **kw)

    def former_invite_room(self, name):
        """What migration 0007 leaves of an invite-only room: a password room
        with no password yet."""
        room = creation.create_room(self.owner, name=name)
        ForumChannel.objects.filter(pk=room.pk).update(access="password")
        room.refresh_from_db()
        return room

    def test_a_new_room_cannot_be_invite_only(self):
        with self.assertRaises(creation.RoomRefused):
            self.room(name="Board", access="invite")
        self.client.force_login(self.owner)
        self.client.post(reverse("forum:channel_create"), {"name": "Board", "access": "invite"})
        self.assertFalse(ForumChannel.objects.filter(name="Board").exists())

    def test_the_create_form_is_a_modal_without_invite_only(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("forum:channel_list"))
        self.assertContains(response, 'data-testid="room-create-open"')
        self.assertContains(response, 'role="dialog"')
        self.assertNotContains(response, 'value="invite"')

    def test_joining_a_password_room_needs_the_password(self):
        room = self.room(name="Secret", access="password", password="correct horse")
        self.client.force_login(self.bob)
        self.client.post(reverse("forum:channel_join", args=[room.slug]), {"password": "nope"})
        self.assertFalse(ForumMember.objects.filter(channel=room, person__user=self.bob).exists())
        self.client.post(reverse("forum:channel_join", args=[room.slug]), {"password": "correct horse"})
        self.assertTrue(ForumMember.objects.filter(channel=room, person__user=self.bob, is_active=True).exists())

    def test_wrong_passwords_are_rate_limited(self):
        room = self.room(name="Secret", access="password", password="correct horse")
        for _ in range(5):
            with self.assertRaises(creation.RoomRefused):
                creation.join(self.bob, room, password="nope")
        with self.assertRaises(creation.RoomRefused) as caught:
            creation.join(self.bob, room, password="correct horse")
        self.assertEqual(caught.exception.status, 429)

    def test_nobody_is_added_by_name(self):
        room = self.room(name="Board", access="password", password="correct horse")
        self.assertFalse(hasattr(creation, "add_member"))
        self.client.force_login(self.owner)
        response = self.client.post(reverse("forum:room_members", args=[room.slug]),
                                    {"action": "add", "username": "bob"}, follow=True)
        self.assertContains(response, "nobody is added by name")
        self.assertFalse(ForumMember.objects.filter(channel=room, person__user=self.bob).exists())
        self.assertNotContains(self.client.get(reverse("forum:room_members", args=[room.slug])),
                               'name="username"')

    def test_a_former_invite_room_is_listed_and_closed_until_it_has_a_password(self):
        room = self.former_invite_room("Board")
        self.client.force_login(self.bob)
        self.assertContains(self.client.get(reverse("forum:channel_list")), "Board")
        with self.assertRaises(creation.RoomRefused) as caught:
            creation.join(self.bob, room, password="anything at all")
        self.assertIn("no password yet", str(caught.exception))
        self.client.force_login(self.owner)
        page = self.client.get(reverse("forum:room_members", args=[room.slug]))
        self.assertContains(page, "no password yet")
        self.assertContains(page, reverse("forum:room_security", args=[room.slug]))
        self.assertContains(self.client.get(reverse("forum:room_security", args=[room.slug])),
                            'data-testid="no-password-yet"')
        self.client.post(reverse("forum:room_security", args=[room.slug]),
                         {"action": "password", "password": "correct horse"})
        room.refresh_from_db()
        self.assertTrue(creation.join(self.bob, room, password="correct horse"))

    def test_the_migration_turns_invite_rooms_into_password_rooms(self):
        import importlib

        room = creation.create_room(self.owner, name="Legacy")
        ForumChannel.objects.filter(pk=room.pk).update(access="invite")
        migration = importlib.import_module("toto.forum.migrations.0007_no_invitations")
        from django.apps import apps as global_apps

        migration.invite_to_password(global_apps, None)
        room.refresh_from_db()
        self.assertEqual(room.access, "password")
        self.assertFalse(room.password_verifier)
        self.assertTrue(ForumMember.objects.filter(channel=room, person__user=self.owner,
                                                   is_active=True).exists())

    def test_only_the_owner_or_staff_remove_members(self):
        room = self.room(name="Board")
        creation.join(self.bob, room)
        member = ForumMember.objects.get(channel=room, person__user=self.bob)
        with self.assertRaises(creation.RoomRefused):
            creation.remove_member(self.bob, room, member.pk)
        creation.remove_member(self.owner, room, member.pk)
        member.refresh_from_db()
        self.assertFalse(member.is_active)

    def test_a_short_password_and_a_duplicate_name_are_refused(self):
        with self.assertRaises(creation.RoomRefused):
            self.room(name="Short", access="password", password="1234")
        self.room(name="Once")
        with self.assertRaises(creation.RoomRefused) as caught:
            self.room(name="Once")
        self.assertEqual(caught.exception.status, 409)

    def test_the_api_refuses_at_capacity_with_409_not_500(self):
        self.client.force_login(self.owner)
        with self.settings(FORUM_MAX_CHANNELS=ForumChannel.objects.count()):
            response = self.client.post(reverse("forum:api_channel_list"), {"name": "Full"},
                                        content_type="application/json")
        self.assertEqual(response.status_code, 409)

    def test_the_api_creates_an_encrypted_password_room(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("forum:api_channel_list"),
                                    {"name": "API room", "access": "password",
                                     "password": "correct horse", "is_encrypted": True},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual((data["access"], data["is_encrypted"]), ("password", True))

    def test_a_cross_site_cookie_post_is_refused(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("forum:api_channel_list"), {"name": "Forged"},
                                    content_type="application/json", HTTP_SEC_FETCH_SITE="cross-site")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ForumChannel.objects.filter(name="Forged").exists())

    def test_the_room_page_shows_what_the_room_is(self):
        room = self.room(name="Vault", access="password", password="correct horse", encrypted=True)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("forum:channel_detail", args=[room.slug]))
        for badge in ("password", "encrypted"):
            self.assertContains(response, f'data-badge="{badge}"')
        self.assertContains(response, 'data-testid="encrypted-notice"')
        self.assertNotContains(response, 'id="header-search"')
        self.client.force_login(self.bob)
        response = self.client.get(reverse("forum:channel_detail", args=[room.slug]))
        self.assertContains(response, 'name="password"')


@override_settings(**FAST)
class MembersTabTests(TestCase):
    """The Members tab lists who has WRITTEN, with the data each sent."""

    def setUp(self):
        from toto.core.models import Platform

        cache.clear()
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        self.owner = User.objects.create_user("owner", password="x")
        self.bob = User.objects.create_user("bob", password="x")
        self.quiet = User.objects.create_user("quiet", password="x")
        for u, n in ((self.owner, "Owner"), (self.bob, "Bob"), (self.quiet, "Quiet")):
            _person(u, n)
        self.room = creation.create_room(self.owner, name="Talk")
        creation.join(self.bob, self.room)
        creation.join(self.quiet, self.room)

    def say(self, user, body, attachment_size=None):
        from toto.forum.models import ForumMessage

        return ForumMessage.objects.create(channel=self.room, sender=user,
                                           sender_name=user.username, body=body,
                                           attachment_size=attachment_size)

    def test_only_writers_are_listed_with_their_data_most_first(self):
        self.say(self.owner, "hi")
        self.say(self.bob, "x" * 1000)
        self.say(self.bob, "y" * 500, attachment_size=2000)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("forum:room_members", args=[self.room.slug]))
        writers = response.context["writers"]
        self.assertEqual([w["username"] for w in writers], ["bob", "owner"])
        bob = writers[0]
        self.assertEqual((bob["messages"], bob["bytes"]), (2, 1000 + 500 + 2000))
        self.assertEqual(writers[1]["bytes"], 2)
        self.assertContains(response, 'data-testid="room-writers"')
        self.assertNotContains(response, 'data-writer="quiet"')
        self.assertEqual(response.context["silent_members"], 1)
        self.assertContains(response, "1 more member has not written anything yet.")

    def test_data_is_shown_in_megabytes_with_three_significant_digits(self):
        self.say(self.bob, "z" * 1234)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("forum:room_members", args=[self.room.slug]))
        self.assertEqual(response.context["writers"][0]["mb"], "0.00118")
        self.assertContains(response, "0.00118&nbsp;MB")

    def test_someone_who_left_still_counts_as_having_written(self):
        self.say(self.bob, "bye")
        member = ForumMember.objects.get(channel=self.room, person__user=self.bob)
        creation.remove_member(self.owner, self.room, member.pk)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("forum:room_members", args=[self.room.slug]))
        self.assertEqual([w["username"] for w in response.context["writers"]], ["bob"])
        self.assertContains(response, "(left)")


@override_settings(**FAST)
class RoomLayoutTests(TestCase):
    """The chat page fits its width: one shrinkable column and three rows."""

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        self.owner = User.objects.create_user("owner", password="x")
        _person(self.owner, "Owner")
        self.room = creation.create_room(self.owner, name="Talk")
        self.client.force_login(self.owner)

    def test_the_chat_column_can_shrink_and_nothing_is_pinned_to_a_row(self):
        page = self.client.get(reverse("forum:channel_detail", args=[self.room.slug])).content.decode()
        self.assertIn("grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)_auto]", page)
        self.assertNotIn("[grid-row:", page)
        self.assertIn("xl:flex xl:flex-col", page)   # members beside the chat from xl

    def test_tab_labels_fold_into_icons_on_a_narrow_screen(self):
        page = self.client.get(reverse("forum:channel_detail", args=[self.room.slug])).content.decode()
        self.assertIn('<span class="sr-only sm:not-sr-only">Members</span>', page)


@override_settings(**FAST)
class SecurityTabTests(TestCase):
    """Everything about how a room is protected, on its own tab (2026-09-28);
    the password is changed there, not among the members."""

    def setUp(self):
        from toto.core.models import Platform

        cache.clear()
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        self.owner = User.objects.create_user("owner", password="x")
        self.bob = User.objects.create_user("bob", password="x")
        self.carol = User.objects.create_user("carol", password="x")
        for u, n in ((self.owner, "Owner"), (self.bob, "Bob"), (self.carol, "Carol")):
            _person(u, n)
        self.room = creation.create_room(self.owner, name="Vault", access="password",
                                         password="correct horse", encrypted=True)
        creation.join(self.bob, self.room, password="correct horse")
        self.url = reverse("forum:room_security", args=[self.room.slug])

    def test_a_member_reads_every_section_and_gets_no_password_form(self):
        self.client.force_login(self.bob)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for section in ("security-access", "security-encryption", "security-lifetime",
                        "security-costs", "security-transit"):
            self.assertContains(response, f'data-testid="{section}"')
        self.assertContains(response, "AES-256-GCM")
        self.assertContains(response, "also wrapped under the room password")
        self.assertContains(response, "A password is set.")
        self.assertNotContains(response, 'data-testid="security-password-form"')
        self.assertContains(response, 'data-testid="room-security-tab"')

    def test_a_non_member_is_kept_out(self):
        self.client.force_login(self.carol)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_only_the_creator_or_staff_change_the_password(self):
        self.client.force_login(self.bob)
        self.assertEqual(self.client.post(self.url, {"action": "password",
                                                     "password": "new horse battery"}).status_code, 403)
        self.client.force_login(self.owner)
        page = self.client.get(self.url)
        self.assertContains(page, 'data-testid="security-password-form"')
        response = self.client.post(self.url, {"action": "password", "password": "new horse battery"},
                                    follow=True)
        self.assertContains(response, "Password changed.")
        self.room.refresh_from_db()
        from toto.forum import rooms

        self.assertTrue(rooms.verify_password(self.room, "new horse battery"))
        self.assertFalse(rooms.verify_password(self.room, "correct horse"))

    def test_a_short_password_is_refused_with_a_sentence(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {"action": "password", "password": "short"}, follow=True)
        self.assertContains(response, "at least")

    def test_the_members_tab_no_longer_changes_the_password(self):
        self.client.force_login(self.owner)
        members = reverse("forum:room_members", args=[self.room.slug])
        self.assertNotContains(self.client.get(members), 'name="password"')
        self.client.post(members, {"action": "password", "password": "new horse battery"})
        self.room.refresh_from_db()
        from toto.forum import rooms

        self.assertTrue(rooms.verify_password(self.room, "correct horse"))

    def test_an_open_plaintext_room_says_so(self):
        room = creation.create_room(self.owner, name="Lobby")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("forum:room_security", args=[room.slug]))
        self.assertContains(response, "Any member of the platform may join")
        self.assertContains(response, "Not encrypted.")
        self.assertNotContains(response, 'data-testid="security-password-form"')

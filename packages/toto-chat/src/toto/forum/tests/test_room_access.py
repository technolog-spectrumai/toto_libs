"""Who joins what: open, password (rate limited), invite; and what is listed."""

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

    def test_an_invite_room_is_joined_only_by_invitation_and_hidden_otherwise(self):
        room = self.room(name="Board", access="invite")
        with self.assertRaises(creation.RoomRefused):
            creation.join(self.bob, room)
        self.client.force_login(self.bob)
        self.assertNotContains(self.client.get(reverse("forum:channel_list")), "Board")
        self.assertEqual(self.client.post(reverse("forum:channel_join", args=[room.slug])).status_code, 404)
        creation.add_member(self.owner, room, "bob")
        self.assertContains(self.client.get(reverse("forum:channel_list")), "Board")

    def test_only_the_owner_or_staff_manage_members(self):
        room = self.room(name="Board", access="invite")
        with self.assertRaises(creation.RoomRefused):
            creation.add_member(self.bob, room, "staff")
        creation.add_member(self.staff, room, "bob")
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

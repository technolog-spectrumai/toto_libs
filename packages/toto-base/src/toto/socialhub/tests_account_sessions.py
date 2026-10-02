"""Your account, the sessions section (2026-09-30; on the own profile's
Security tab since 2026-10-02, stage 50): a ``UserSession`` row per
sign-in (``toto.core.user_sessions``), the list (own sessions only, this one
marked), End one, Sign out everywhere else (a token dies with it), the chain
records, and the "new sign-in" notice once per new (user agent, address).
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.api.tokens import user_for_session_key
from toto.audit.models import AuditRecord
from toto.core import user_sessions
from toto.core.models import KnownSignIn, Platform, UserSession
from toto.people.models import Person

User = get_user_model()
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
PW = "Correct-horse-9"


@override_settings(EMAIL_BACKEND=LOCMEM)
class SessionsTestCase(TestCase):
    def setUp(self):
        cache.clear()
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("ada", "ada@example.test", PW)
        person = Person.objects.create(user=self.user, display_name="Ada")
        #: The own profile: 200 for a signed-in session, a login redirect else.
        self.profile_url = reverse("socialhub:profile_details", args=[person.slug])
        #: Where the session doors go back to: the Security tab's section.
        self.back = self.profile_url + "?tab=security#sessions"
        self.client.force_login(self.user)

    def browser(self, user=None):
        other = Client()
        other.force_login(user or self.user)
        return other

    def token(self, username="ada", password=PW, **headers):
        """The key a desktop is handed at /api/login/."""
        response = Client(**headers).post(
            "/api/login/", json.dumps({"username": username, "password": password}),
            content_type="application/json")
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def listed(self, client=None):
        response = (client or self.client).get(reverse("account:home") + "?tab=security",
                                               follow=True)
        self.assertEqual(response.status_code, 200)
        return response, list(response.context["sessions"])


class RowTests(SessionsTestCase):
    def test_a_sign_in_writes_a_row_and_a_sign_out_removes_it(self):
        other = self.browser()
        key = other.session.session_key
        row = UserSession.objects.get(session_key=key)
        self.assertEqual(row.user, self.user)
        self.assertEqual(row.kind, UserSession.KIND_BROWSER)
        other.logout()
        self.assertFalse(UserSession.objects.filter(session_key=key).exists())

    def test_a_desktop_sign_in_is_a_token_row(self):
        key = self.token(HTTP_USER_AGENT="Enigma/3.1", HTTP_X_REAL_IP="203.0.113.7")
        row = UserSession.objects.get(session_key=key)
        self.assertEqual(row.kind, UserSession.KIND_TOKEN)
        self.assertEqual(row.user_agent, "Enigma/3.1")
        self.assertEqual(row.ip, "203.0.113.7")

    def test_last_seen_is_written_at_most_every_few_minutes(self):
        key = self.client.session.session_key
        old = timezone.now() - timedelta(hours=1)
        UserSession.objects.filter(session_key=key).update(last_seen_at=old)
        self.client.get(reverse("account:home"))
        first = UserSession.objects.get(session_key=key).last_seen_at
        self.assertGreater(first, old)
        UserSession.objects.filter(session_key=key).update(last_seen_at=old)
        self.client.get(reverse("account:home"))
        # The marker is still live: nothing written the second time.
        self.assertEqual(UserSession.objects.get(session_key=key).last_seen_at, old)

    def test_a_session_from_before_the_rows_gets_one_when_used(self):
        key = self.client.session.session_key
        UserSession.objects.filter(session_key=key).delete()
        self.client.get(reverse("account:home"))
        self.assertTrue(UserSession.objects.filter(session_key=key, user=self.user).exists())

    def test_a_token_in_use_is_touched_at_its_door(self):
        key = self.token()
        old = timezone.now() - timedelta(hours=1)
        UserSession.objects.filter(session_key=key).update(last_seen_at=old)
        cache.clear()
        self.assertEqual(user_for_session_key(key, door="api"), self.user)
        self.assertGreater(UserSession.objects.get(session_key=key).last_seen_at, old)

    def test_a_password_change_keeps_this_sessions_row(self):
        before = UserSession.objects.get(session_key=self.client.session.session_key)
        self.client.post(reverse("account:password"), {
            "old_password": PW, "new_password1": "Battery-staple-41",
            "new_password2": "Battery-staple-41"})
        after = UserSession.objects.get(user=self.user)
        self.assertEqual(after.pk, before.pk)
        self.assertEqual(after.session_key, self.client.session.session_key)
        self.assertNotEqual(after.session_key, before.session_key)


class ListTests(SessionsTestCase):
    def test_the_list_shows_own_sessions_only_this_one_marked(self):
        other = self.browser()
        bob = User.objects.create_user("bob", "bob@example.test", "pw")
        bobs = self.browser(bob)
        token = self.token()
        response, rows = self.listed()
        keys = {row.session_key for row in rows}
        self.assertEqual(keys, {self.client.session.session_key,
                                other.session.session_key, token})
        self.assertNotIn(bobs.session.session_key, keys)
        self.assertTrue(rows[0].is_current)
        self.assertEqual(sum(row.is_current for row in rows), 1)
        self.assertContains(response, "this device")
        # The key is a credential: the page names a session by its row id.
        for key in keys:
            self.assertNotContains(response, key)

    def test_expired_and_vanished_sessions_drop_out(self):
        gone = self.browser()
        expired = self.browser()
        Session.objects.filter(session_key=gone.session.session_key).delete()
        Session.objects.filter(session_key=expired.session.session_key).update(
            expire_date=timezone.now() - timedelta(minutes=1))
        _, rows = self.listed()
        self.assertEqual([row.session_key for row in rows], [self.client.session.session_key])
        # And their rows are gone with them.
        self.assertEqual(UserSession.objects.filter(user=self.user).count(), 1)


class EndTests(SessionsTestCase):
    def test_end_one_of_mine(self):
        other = self.browser()
        row = UserSession.objects.get(session_key=other.session.session_key)
        response = self.client.post(reverse("account:session_end", args=[row.pk]))
        self.assertRedirects(response, self.back, fetch_redirect_response=False)
        self.assertFalse(SessionStore().exists(row.session_key))
        self.assertFalse(UserSession.objects.filter(pk=row.pk).exists())
        self.assertNotEqual(other.get(self.profile_url).status_code, 200)
        record = AuditRecord.objects.get(action="AUTH.SESSION_ENDED")
        self.assertEqual(record.actor_user, self.user)
        self.assertEqual(record.metadata, {"kind": "browser", "session_id": row.pk})
        self.assertNotIn(row.session_key, json.dumps([record.metadata, record.request_source]))

    def test_another_members_session_is_a_404_and_stays(self):
        bob = User.objects.create_user("bob", "bob@example.test", "pw")
        bobs = self.browser(bob)
        row = UserSession.objects.get(session_key=bobs.session.session_key)
        response = self.client.post(reverse("account:session_end", args=[row.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(SessionStore().exists(row.session_key))
        self.assertTrue(UserSession.objects.filter(pk=row.pk).exists())
        self.assertFalse(AuditRecord.objects.filter(action="AUTH.SESSION_ENDED").exists())

    def test_an_unknown_id_is_a_404(self):
        self.assertEqual(self.client.post(
            reverse("account:session_end", args=[999999])).status_code, 404)

    def test_the_session_in_use_is_not_ended_here(self):
        row = UserSession.objects.get(session_key=self.client.session.session_key)
        response = self.client.post(reverse("account:session_end", args=[row.pk]))
        self.assertRedirects(response, self.back, fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.profile_url).status_code, 200)

    def test_ending_is_post_only(self):
        other = self.browser()
        row = UserSession.objects.get(session_key=other.session.session_key)
        self.assertEqual(self.client.get(
            reverse("account:session_end", args=[row.pk])).status_code, 405)
        self.assertTrue(SessionStore().exists(row.session_key))

    def test_sign_out_everywhere_else_keeps_this_one_and_kills_a_token(self):
        other = self.browser()
        token = self.token()
        self.assertEqual(user_for_session_key(token, door="api"), self.user)
        mine = self.client.session.session_key
        response = self.client.post(reverse("account:sessions_end_others"))
        self.assertRedirects(response, self.back, fetch_redirect_response=False)
        self.assertTrue(SessionStore().exists(mine))
        self.assertFalse(SessionStore().exists(other.session.session_key))
        self.assertFalse(SessionStore().exists(token))
        self.assertIsNone(user_for_session_key(token, door="api"))
        self.assertEqual(self.client.get(self.profile_url).status_code, 200)
        self.assertEqual(list(UserSession.objects.filter(user=self.user)
                              .values_list("session_key", flat=True)), [mine])
        record = AuditRecord.objects.get(action="AUTH.SIGNED_OUT_EVERYWHERE")
        self.assertEqual(record.metadata["sessions_ended"], 2)

    def test_sign_out_everywhere_else_leaves_other_members_alone(self):
        bob = User.objects.create_user("bob", "bob@example.test", "pw")
        bobs = self.browser(bob)
        self.client.post(reverse("account:sessions_end_others"))
        self.assertTrue(SessionStore().exists(bobs.session.session_key))


class NewSignInNoticeTests(SessionsTestCase):
    def sign_in(self, agent, address):
        self.token(HTTP_USER_AGENT=agent, HTTP_X_REAL_IP=address)

    def notices(self):
        return [m for m in mail.outbox if m.extra_headers.get("X-Toto-Notice") == "new_sign_in"]

    def test_once_per_new_pair(self):
        # setUp's sign-in was the first: it is the baseline, not news.
        self.assertEqual(self.notices(), [])
        self.sign_in("Firefox/140", "203.0.113.7")
        self.assertEqual(len(self.notices()), 1)
        notice = self.notices()[0]
        self.assertEqual(notice.to, ["ada@example.test"])
        self.assertIn("203.0.113.7", notice.body)
        self.assertIn("Firefox/140", notice.body)
        self.sign_in("Firefox/140", "203.0.113.7")
        self.assertEqual(len(self.notices()), 1)
        # Another address with the same browser is another pair.
        self.sign_in("Firefox/140", "198.51.100.2")
        self.assertEqual(len(self.notices()), 2)

    def test_a_pair_unseen_for_90_days_is_new_again(self):
        self.sign_in("Firefox/140", "203.0.113.7")
        KnownSignIn.objects.filter(user=self.user).update(
            last_seen_at=timezone.now() - timedelta(days=user_sessions.KNOWN_FOR_DAYS + 1))
        mail.outbox.clear()
        self.sign_in("Firefox/140", "203.0.113.7")
        self.assertEqual(len(self.notices()), 1)
        self.sign_in("Firefox/140", "203.0.113.7")
        self.assertEqual(len(self.notices()), 1)
        # The lapsed rows went; the pair just seen stays.
        self.assertEqual(KnownSignIn.objects.filter(user=self.user).count(), 1)

    def test_only_a_hash_of_the_pair_is_kept(self):
        self.sign_in("Firefox/140", "203.0.113.7")
        for fingerprint in KnownSignIn.objects.values_list("fingerprint", flat=True):
            self.assertNotIn("203.0.113", fingerprint)
            self.assertEqual(len(fingerprint), 64)

    def test_a_failing_mail_does_not_fail_the_sign_in(self):
        from unittest import mock

        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("down")):
            self.sign_in("Firefox/140", "203.0.113.7")
        self.assertEqual(UserSession.objects.filter(user=self.user, kind="token").count(), 1)

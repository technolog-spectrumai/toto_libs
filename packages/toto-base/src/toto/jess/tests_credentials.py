"""Session custody of the email password — the in-memory store and its page.

What matters here is what is NOT anywhere: no test in this module ever writes
the password to the database, to ``request.session`` (Postgres-backed on real
hosts) or to disk, and the assertions on the store's lifecycle — logout,
expiry, ``lock_all`` — are the custody contract itself.
"""
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from . import credentials
from . import status as jess_status
from .models import EmailProvider, MailMessage

User = get_user_model()


class CredentialStoreTests(TestCase):
    def setUp(self):
        credentials.lock_all()

    def tearDown(self):
        credentials.lock_all()

    def test_unlock_then_credential_then_lock(self):
        self.assertIsNone(credentials.credential())
        credentials.unlock("sess-a", "hunter2")
        self.assertEqual(credentials.credential(), "hunter2")
        self.assertTrue(credentials.held_for("sess-a"))
        credentials.lock("sess-a")
        self.assertIsNone(credentials.credential())

    def test_the_newest_session_credential_wins(self):
        credentials.unlock("sess-a", "old")
        time.sleep(0.01)
        credentials.unlock("sess-b", "new")
        self.assertEqual(credentials.credential(), "new")

    def test_ttl_expiry_drops_the_credential(self):
        with override_settings(JESS_CREDENTIAL_TTL_SECONDS=1):
            credentials.unlock("sess-a", "hunter2")
            with patch("toto.jess.credentials.time.time",
                       return_value=time.time() + 2):
                self.assertIsNone(credentials.credential())
                self.assertFalse(credentials.held_for("sess-a"))

    def test_logout_signal_drops_only_that_session(self):
        credentials.unlock("sess-a", "a")
        credentials.unlock("sess-b", "b")

        class _Session:
            session_key = "sess-a"

        class _Request:
            session = _Session()

        credentials.on_user_logged_out(sender=None, request=_Request())
        self.assertFalse(credentials.held_for("sess-a"))
        self.assertTrue(credentials.held_for("sess-b"))

    @override_settings(JESS_EMAIL_PASSWORD="boot-pw")
    def test_bootstrap_env_credential_is_read_once_into_memory(self):
        self.assertTrue(credentials.available())
        self.assertEqual(credentials.credential(), "boot-pw")
        self.assertTrue(credentials.bootstrap_present())

    @override_settings(JESS_EMAIL_PASSWORD="boot-pw")
    def test_a_typed_session_credential_outranks_the_bootstrap(self):
        # Typing on /jess/unlock/ is how an operator rotates the account
        # password without a redeploy, so the fresher fact must win.
        credentials.unlock("sess-a", "typed-pw")
        self.assertEqual(credentials.credential(), "typed-pw")
        credentials.lock("sess-a")
        self.assertEqual(credentials.credential(), "boot-pw")


class CanSendInlineTests(TestCase):
    """``can_send_inline`` opens flow 1's inline branch — exactly when the
    active provider has a username, NO stored secret, and this process holds
    a credential."""

    def setUp(self):
        credentials.lock_all()

    def tearDown(self):
        credentials.lock_all()

    def _provider(self, **kwargs):
        defaults = {
            "label": "Relay", "backend": "smtp", "host": "smtp.example.org",
            "username": "mailer", "active": True,
        }
        defaults.update(kwargs)
        return EmailProvider.objects.create(**defaults)

    def test_yes_with_provider_and_credential(self):
        self._provider()
        credentials.unlock("sess-a", "pw")
        self.assertTrue(jess_status.can_send_inline())

    def test_no_without_a_credential(self):
        self._provider()
        self.assertFalse(jess_status.can_send_inline())

    def test_no_without_a_provider(self):
        credentials.unlock("sess-a", "pw")
        self.assertFalse(jess_status.can_send_inline())

    def test_no_when_the_password_is_stored_instead(self):
        # A stored secret means the ordinary custody modes apply — the queue
        # path can decrypt it, so inline sending has nothing to add.
        from . import vault

        vault.clear_cache()
        provider = self._provider()
        provider.secret = vault.store_secret("stored-pw",
                                             name=vault.unique_secret_name())
        provider.save(update_fields=["secret"])
        credentials.unlock("sess-a", "pw")
        self.assertFalse(jess_status.can_send_inline())

    def test_no_for_an_open_relay(self):
        self._provider(username="")
        credentials.unlock("sess-a", "pw")
        self.assertFalse(jess_status.can_send_inline())


class UnlockPageTests(TestCase):
    def setUp(self):
        credentials.lock_all()
        Platform.objects.create(site_name="Toto", author="T", publication_year=2026)
        self.staff = User.objects.create_user(
            "molly", email="m@x.test", password="pw", is_staff=True,
        )
        self.plain = User.objects.create_user("ted", email="t@x.test", password="pw")
        self.provider = EmailProvider.objects.create(
            label="Relay", backend="smtp", host="smtp.example.org",
            username="mailer", active=True,
        )
        self.client = Client()
        self.url = reverse("jess:unlock")

    def tearDown(self):
        credentials.lock_all()

    def test_the_gate_refuses_anonymous_and_members(self):
        # 403 from _staff_only; a host running LoginRequiredEverywhereMiddleware
        # answers the anonymous half with its own 302 first. Both are refusals
        # (same convention as toto.monit's view tests).
        self.assertIn(self.client.get(self.url).status_code, (302, 403))
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_staff_see_the_form(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unlock for this session")

    def test_a_proven_password_is_held_for_this_session(self):
        self.client.force_login(self.staff)
        with patch("toto.jess.views.delivery.build_connection") as build:
            build.return_value.open.return_value = True
            response = self.client.post(self.url, {"password": "hunter2"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(credentials.credential(), "hunter2")
        session_key = self.client.session.session_key
        self.assertTrue(credentials.held_for(session_key))

    def test_a_refused_handshake_holds_nothing(self):
        self.client.force_login(self.staff)
        with patch("toto.jess.views.delivery.build_connection") as build:
            build.return_value.open.side_effect = OSError("535 auth failed")
            response = self.client.post(self.url, {"password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "535 auth failed")
        self.assertIsNone(credentials.credential())

    def test_lock_drops_this_sessions_credential(self):
        self.client.force_login(self.staff)
        with patch("toto.jess.views.delivery.build_connection") as build:
            build.return_value.open.return_value = True
            self.client.post(self.url, {"password": "hunter2"})
        self.assertIsNotNone(credentials.credential())
        self.client.post(self.url, {"action": "lock"})
        self.assertIsNone(credentials.credential())

    def test_logging_out_drops_the_credential(self):
        self.client.force_login(self.staff)
        with patch("toto.jess.views.delivery.build_connection") as build:
            build.return_value.open.return_value = True
            self.client.post(self.url, {"password": "hunter2"})
        self.assertIsNotNone(credentials.credential())
        self.client.post(reverse("sso:logout"))
        self.assertIsNone(credentials.credential())

    def test_nothing_lands_in_the_django_session_or_the_database(self):
        """The custody claim itself: the password exists in the process dict
        and NOWHERE a dump could find it."""
        self.client.force_login(self.staff)
        with patch("toto.jess.views.delivery.build_connection") as build:
            build.return_value.open.return_value = True
            self.client.post(self.url, {"password": "hunter2-needle"})
        for value in self.client.session.values():
            self.assertNotIn("hunter2-needle", str(value))
        from django.contrib.sessions.models import Session

        for row in Session.objects.all():
            self.assertNotIn("hunter2-needle", row.session_data)
        self.provider.refresh_from_db()
        self.assertIsNone(self.provider.secret_id)


class ProviderSeedTests(TestCase):
    """``ingress_jess``: the deploy-owned account row, seeded from env on every
    boot — idempotent, never a password, never a coup against the operator."""

    def _run(self, **env):
        import os
        from unittest.mock import patch as _patch

        from django.core.management import call_command

        with _patch.dict(os.environ, env, clear=False):
            call_command("ingress_jess")

    def test_no_env_seeds_nothing(self):
        self._run()
        self.assertEqual(EmailProvider.objects.count(), 0)

    def test_host_and_user_seed_one_active_row_without_a_secret(self):
        self._run(JESS_EMAIL_HOST="smtp.example.org", JESS_EMAIL_USER="mailer",
                  JESS_EMAIL_FROM="noreply@example.org")
        provider = EmailProvider.objects.get()
        self.assertEqual(provider.label, "Deploy config")
        self.assertEqual(provider.host, "smtp.example.org")
        self.assertEqual(provider.username, "mailer")
        self.assertEqual(provider.from_address, "noreply@example.org")
        self.assertTrue(provider.active)
        self.assertIsNone(provider.secret_id)

    def test_reboot_updates_the_same_row(self):
        self._run(JESS_EMAIL_HOST="a.example.org", JESS_EMAIL_USER="mailer")
        self._run(JESS_EMAIL_HOST="b.example.org", JESS_EMAIL_USER="mailer")
        provider = EmailProvider.objects.get()
        self.assertEqual(provider.host, "b.example.org")

    def test_an_operators_active_provider_is_not_deposed(self):
        theirs = EmailProvider.objects.create(
            label="Mine", backend="smtp", host="mine.example.org", active=True,
        )
        self._run(JESS_EMAIL_HOST="smtp.example.org", JESS_EMAIL_USER="mailer")
        theirs.refresh_from_db()
        self.assertTrue(theirs.active)
        self.assertFalse(EmailProvider.objects.get(label="Deploy config").active)

    def test_a_deliberate_all_off_state_survives_reboots(self):
        # An operator who deactivated EVERY provider (mail off, resets on the
        # patron flow) must not find the deploy row re-armed each morning:
        # activation happens on first seed only.
        self._run(JESS_EMAIL_HOST="smtp.example.org", JESS_EMAIL_USER="mailer")
        EmailProvider.objects.update(active=False)
        self._run(JESS_EMAIL_HOST="smtp.example.org", JESS_EMAIL_USER="mailer")
        self.assertFalse(EmailProvider.objects.filter(active=True).exists())

    def test_a_hand_made_duplicate_label_does_not_break_the_boot(self):
        # label is not unique; update_or_create here once meant
        # MultipleObjectsReturned out of ingress_all — a failed boot.
        EmailProvider.objects.create(label="Deploy config", backend="console")
        EmailProvider.objects.create(label="Deploy config", backend="console")
        self._run(JESS_EMAIL_HOST="smtp.example.org", JESS_EMAIL_USER="mailer")
        seeded = EmailProvider.objects.filter(label="Deploy config").order_by("pk").first()
        self.assertEqual(seeded.host, "smtp.example.org")


class SendSingleNowTests(TestCase):
    """The inline sender: one outbox row, the caller's credential, the same
    SENT/FAILED story the queue path records."""

    def _provider(self):
        return EmailProvider.objects.create(
            label="Relay", backend="smtp", host="smtp.example.org",
            username="mailer", active=True,
        )

    def test_a_sent_message_records_sent_with_an_outbox_row(self):
        from django.core.mail import get_connection

        from .delivery import send_single_now

        self._provider()
        locmem = get_connection("django.core.mail.backends.locmem.EmailBackend")
        with patch("toto.jess.delivery.build_connection", return_value=locmem):
            ok = send_single_now(
                subject="Reset", body="the link", to=["u@example.org"],
                purpose=MailMessage.PURPOSE_PASSWORD_RESET, password="pw",
            )
        self.assertTrue(ok)
        row = MailMessage.objects.get()
        self.assertEqual(row.status, MailMessage.SENT)
        self.assertEqual(row.purpose, MailMessage.PURPOSE_PASSWORD_RESET)
        from django.core import mail

        self.assertEqual(len(mail.outbox), 1)

    def test_a_refused_send_records_failed_and_returns_false(self):
        from .delivery import send_single_now

        self._provider()
        with patch("toto.jess.delivery.build_connection",
                   side_effect=OSError("connection refused")):
            ok = send_single_now(
                subject="Reset", body="the link", to=["u@example.org"],
                purpose=MailMessage.PURPOSE_PASSWORD_RESET, password="pw",
            )
        self.assertFalse(ok)
        row = MailMessage.objects.get()
        self.assertEqual(row.status, MailMessage.FAILED)
        self.assertIn("connection refused", row.error)

    def test_no_provider_records_failed_rather_than_raising(self):
        from .delivery import send_single_now

        ok = send_single_now(
            subject="Reset", body="x", to=["u@example.org"],
            purpose=MailMessage.PURPOSE_PASSWORD_RESET, password="pw",
        )
        self.assertFalse(ok)
        self.assertEqual(MailMessage.objects.get().status, MailMessage.FAILED)

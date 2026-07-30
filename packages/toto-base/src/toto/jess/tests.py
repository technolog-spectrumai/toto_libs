"""Jess's suite.

Run against Jess's own settings module, which points ``EMAIL_BACKEND`` at Jess the way a
``BUILD_JESS=1`` host does::

    DJANGO_SETTINGS_MODULE=toto.jess.testing.settings python -m django test toto.jess

The vault is real here — Argon2id, the four-tier gervazy envelope, an actual strongbox.
Mocking it would leave the one question that matters unanswered: can a password stored
through the admin be read back by a worker in a different process?

Celery is NOT eager (see the settings module). ``.delay()`` is patched where a test cares
that a dispatch happened, and the task function is called directly where a test cares what
the task does. A suite that ran the task inline would be testing a program in which the
request DOES block on SMTP, which is the opposite of Jess's contract.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import EmailMessage, EmailMultiAlternatives, get_connection, send_mail
from django.test import TestCase, modify_settings, override_settings
from django.urls import reverse

from toto.core.email_config import email_delivery_configured
from toto.core.models import Platform

from . import status as jess_status
from . import vault
from .backend import PURPOSE_HEADER, JessEmailBackend
from .models import EmailProvider, MailMessage

User = get_user_model()

JESS_BACKEND = "toto.jess.backend.JessEmailBackend"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
CONSOLE = "django.core.mail.backends.console.EmailBackend"


class JessTestCase(TestCase):
    """Shared fixtures.

    ``PageProcessor`` raises ``Http404`` without an active Platform (``ui/page.py:31-35``),
    so every view test needs one — and the vault session cache is keyed on strongbox pk,
    which a second TestCase reusing pk 1 against a fresh strongbox would poison. Hence the
    ``clear_cache()``.
    """

    def setUp(self):
        super().setUp()
        vault.clear_cache()
        self.platform = Platform.objects.create(
            site_name="Jess Test Platform", author="Test", publication_year=2026,
        )
        self.staff = User.objects.create_user(
            "molly", email="molly@jess.test", password="pw", is_staff=True,
        )
        # is_superuser does NOT imply is_staff in Django. This account is the one that
        # catches an is_staff-only gate.
        self.root = User.objects.create_user(
            "pat", email="pat@jess.test", password="pw", is_superuser=True,
        )
        self.plain = User.objects.create_user(
            "ted", email="ted@jess.test", password="pw",
        )

    def tearDown(self):
        vault.clear_cache()
        super().tearDown()

    # -- helpers -----------------------------------------------------------------

    def _provider(self, **kwargs):
        defaults = {
            "label": "Test relay",
            "backend": EmailProvider._meta.get_field("backend").default,
            "active": True,
        }
        defaults.update(kwargs)
        return EmailProvider.objects.create(**defaults)

    def _smtp_provider(self, **kwargs):
        defaults = {
            "label": "SMTP relay",
            "backend": "smtp",
            "host": "smtp.example.org",
            "active": True,
        }
        defaults.update(kwargs)
        return EmailProvider.objects.create(**defaults)

    def _locmem_provider(self, **kwargs):
        kwargs.setdefault("label", "Locmem")
        return self._provider(backend="locmem", **kwargs)


# ---------------------------------------------------------------------------
# The one-active-row invariant
# ---------------------------------------------------------------------------

class ProviderActivationTests(JessTestCase):
    def test_activating_a_provider_deactivates_the_others(self):
        first = self._provider(label="First")
        second = self._provider(label="Second")
        first.refresh_from_db()
        self.assertFalse(first.active)
        self.assertTrue(second.active)
        self.assertEqual(EmailProvider.objects.filter(active=True).count(), 1)

    def test_an_inactive_save_leaves_the_active_one_alone(self):
        live = self._provider(label="Live")
        self._provider(label="Draft", active=False)
        live.refresh_from_db()
        self.assertTrue(live.active)

    def test_active_provider_returns_none_when_nothing_is_active(self):
        self._provider(label="Draft", active=False)
        self.assertIsNone(EmailProvider.active_provider())

    def test_smtp_without_a_host_is_refused(self):
        from django.core.exceptions import ValidationError

        provider = EmailProvider(label="Broken", backend="smtp", host="")
        with self.assertRaises(ValidationError):
            provider.full_clean()

    def test_tls_and_ssl_together_are_refused(self):
        from django.core.exceptions import ValidationError

        provider = EmailProvider(
            label="Both", backend="smtp", host="smtp.example.org",
            use_tls=True, use_ssl=True,
        )
        with self.assertRaises(ValidationError):
            provider.full_clean()

    def test_no_provider_can_name_jesss_own_backend(self):
        """The recursion guard, at the level where it is actually enforced.

        A provider that resolved to Jess would queue a message for every message it
        dispatched, forever. The choices field cannot express it and BACKEND_PATHS has no
        entry for it, so the state is unrepresentable rather than merely checked.
        """
        from .models import BACKEND_PATHS

        self.assertNotIn(JESS_BACKEND, BACKEND_PATHS.values())
        valid = {choice for choice, _ in EmailProvider._meta.get_field("backend").choices}
        self.assertEqual(valid, set(BACKEND_PATHS))


# ---------------------------------------------------------------------------
# The backend: what it records
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=JESS_BACKEND)
class BackendRecordingTests(JessTestCase):
    def setUp(self):
        super().setUp()
        self._provider(label="Console")

    def test_a_send_writes_one_row_and_nothing_reaches_the_outbox(self):
        with patch("toto.jess.tasks.send_mail_message.delay") as delay:
            sent = send_mail("Hello", "Body", None, ["a@example.org"])
        self.assertEqual(sent, 1)
        self.assertEqual(MailMessage.objects.count(), 1)
        # The deliberate semantic change: nothing was handed to locmem, so mail.outbox
        # stays empty even though the send "succeeded".
        self.assertEqual(len(mail.outbox), 0)
        delay.assert_called_once()

    def test_three_recipients_are_one_row_not_three(self):
        with patch("toto.jess.tasks.send_mail_message.delay"):
            send_mail("Hi", "B", None, ["a@x.test", "b@x.test", "c@x.test"])
        row = MailMessage.objects.get()
        self.assertEqual(row.to, ["a@x.test", "b@x.test", "c@x.test"])
        self.assertEqual(MailMessage.objects.count(), 1)

    def test_cc_bcc_headers_and_the_html_alternative_all_survive(self):
        email = EmailMultiAlternatives(
            subject="Sub", body="Text", to=["a@x.test"],
            cc=["c@x.test"], bcc=["b@x.test"], reply_to=["r@x.test"],
            headers={"X-Custom": "keep me"},
        )
        email.attach_alternative("<p>Rich</p>", "text/html")
        with patch("toto.jess.tasks.send_mail_message.delay"):
            email.send()

        row = MailMessage.objects.get()
        self.assertEqual(row.cc, ["c@x.test"])
        self.assertEqual(row.bcc, ["b@x.test"])
        self.assertEqual(row.reply_to, "r@x.test")
        self.assertEqual(row.html_body, "<p>Rich</p>")
        self.assertEqual(row.headers, {"X-Custom": "keep me"})

    def test_the_purpose_header_tags_the_row_and_is_stripped(self):
        email = EmailMessage(
            subject="S", body="B", to=["a@x.test"],
            headers={PURPOSE_HEADER: MailMessage.PURPOSE_PASSWORD_RESET,
                     "X-Other": "kept"},
        )
        with patch("toto.jess.tasks.send_mail_message.delay"):
            email.send()

        row = MailMessage.objects.get()
        self.assertEqual(row.purpose, MailMessage.PURPOSE_PASSWORD_RESET)
        # Popped, so it never reaches the wire.
        self.assertNotIn(PURPOSE_HEADER, row.headers)
        self.assertEqual(row.headers, {"X-Other": "kept"})

    def test_an_unknown_purpose_falls_back_rather_than_breaking_the_send(self):
        email = EmailMessage(
            subject="S", body="B", to=["a@x.test"],
            headers={PURPOSE_HEADER: "invented-by-a-caller"},
        )
        with patch("toto.jess.tasks.send_mail_message.delay"):
            email.send()
        self.assertEqual(MailMessage.objects.get().purpose, MailMessage.PURPOSE_OTHER)

    def test_the_active_providers_label_is_stamped_on_the_row(self):
        with patch("toto.jess.tasks.send_mail_message.delay"):
            send_mail("S", "B", None, ["a@x.test"])
        self.assertEqual(MailMessage.objects.get().provider_label, "Console")

    def test_an_attachment_is_refused_with_the_reason_recorded(self):
        email = EmailMessage(subject="S", body="B", to=["a@x.test"])
        email.attach("notes.txt", b"data", "text/plain")
        with patch("toto.jess.tasks.send_mail_message.delay"):
            email.send()

        row = MailMessage.objects.get()
        self.assertIn("attachment", row.error)
        self.assertIn("does not carry attachments", row.error)

    def test_sending_no_messages_writes_nothing(self):
        self.assertEqual(JessEmailBackend().send_messages([]), 0)
        self.assertEqual(MailMessage.objects.count(), 0)

    def test_recorded_ids_identifies_this_connections_own_rows(self):
        """The race the compose view would otherwise have.

        "The newest row that looks like mine" is wrong the moment two sends overlap.
        """
        connection = get_connection()
        with patch("toto.jess.tasks.send_mail_message.delay"):
            for i in range(3):
                EmailMessage(
                    subject=f"S{i}", body="B", to=["a@x.test"], connection=connection,
                ).send()
        self.assertEqual(len(connection.recorded_ids), 3)
        self.assertEqual(
            connection.recorded_ids,
            list(MailMessage.objects.order_by("pk").values_list("pk", flat=True)),
        )

    def test_send_mail_with_explicit_credentials_does_not_raise(self):
        """``send_mail(auth_user=..., auth_password=...)`` is public Django API.

        ``get_connection`` forwards those as kwargs, and ``BaseEmailBackend.__init__``
        accepts only ``fail_silently`` — so a backend that did not absorb them would turn
        a documented call into a TypeError.
        """
        with patch("toto.jess.tasks.send_mail_message.delay"):
            sent = send_mail(
                "S", "B", None, ["a@x.test"],
                auth_user="user", auth_password="pw",
            )
        self.assertEqual(sent, 1)

    def test_a_broker_failure_records_the_row_failed_instead_of_raising(self):
        """A password-reset POST must not 500 because redis blinked."""
        with patch("toto.jess.tasks.send_mail_message.delay",
                   side_effect=OSError("connection refused")):
            sent = send_mail("S", "B", None, ["a@x.test"])

        # Still "accepted" — the row is the truth, and it says what happened.
        self.assertEqual(sent, 1)
        row = MailMessage.objects.get()
        self.assertEqual(row.status, MailMessage.FAILED)
        self.assertIn("connection refused", row.error)
        self.assertIsNotNone(row.finished_at)


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------

class TaskTests(JessTestCase):
    """The task is called directly. It is a function; celery is not needed to run it."""

    def _queue(self, **kwargs):
        defaults = {"to": ["a@x.test"], "subject": "S", "body": "B"}
        defaults.update(kwargs)
        return MailMessage.objects.create(**defaults)

    def _run(self, row):
        from .tasks import send_mail_message

        return send_mail_message(row.pk)

    def test_a_locmem_provider_takes_a_message_all_the_way_to_sent(self):
        """The full happy path, with no SMTP server and no mocking of the send.

        This is what the ``locmem`` provider choice is for: it exercises
        ``resolve_provider`` -> ``build_connection`` -> ``send_now`` for real.
        """
        self._locmem_provider()
        row = self._queue()
        mail.outbox = []

        self._run(row)

        row.refresh_from_db()
        self.assertEqual(row.status, MailMessage.SENT)
        self.assertEqual(row.attempts, 1)
        self.assertEqual(row.error, "")
        self.assertIsNotNone(row.started_at)
        self.assertIsNotNone(row.finished_at)
        self.assertEqual(row.provider_label, "Locmem")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "S")

    def test_the_provider_from_address_is_used_when_the_message_has_none(self):
        self._locmem_provider(from_address="relay@jess.test")
        mail.outbox = []
        self._run(self._queue())
        self.assertEqual(mail.outbox[0].from_email, "relay@jess.test")

    def test_default_from_email_applies_when_neither_supplies_one(self):
        self._locmem_provider()
        mail.outbox = []
        self._run(self._queue())
        self.assertEqual(mail.outbox[0].from_email, "platform@jess.test")

    def test_the_html_alternative_is_attached_not_substituted(self):
        self._locmem_provider()
        mail.outbox = []
        self._run(self._queue(body="Plain", html_body="<p>Rich</p>"))
        sent = mail.outbox[0]
        self.assertEqual(sent.body, "Plain")
        self.assertEqual(sent.alternatives, [("<p>Rich</p>", "text/html")])

    def test_no_active_provider_fails_the_row_legibly_and_does_not_raise(self):
        row = self._queue()
        self._run(row)
        row.refresh_from_db()
        self.assertEqual(row.status, MailMessage.FAILED)
        self.assertIn("No active email provider", row.error)
        self.assertIsNotNone(row.finished_at)
        self.assertEqual(row.attempts, 1)

    def test_a_message_naming_its_own_provider_ignores_the_active_one(self):
        """What makes "prove it before activating it" possible."""
        self._locmem_provider(label="Live")
        chosen = EmailProvider.objects.create(
            label="Candidate", backend="locmem", active=False,
        )
        mail.outbox = []
        row = self._queue(provider=chosen)
        self._run(row)
        row.refresh_from_db()
        self.assertEqual(row.status, MailMessage.SENT)
        self.assertEqual(row.provider_label, "Candidate")

    def test_the_provider_error_is_recorded_verbatim(self):
        """Diagnosing SMTP is exactly when a paraphrase is useless."""
        import smtplib

        self._smtp_provider()
        row = self._queue()
        with patch("toto.jess.delivery.build_connection",
                   side_effect=smtplib.SMTPAuthenticationError(
                       535, b"5.7.8 Username and Password not accepted")):
            self._run(row)

        row.refresh_from_db()
        self.assertEqual(row.status, MailMessage.FAILED)
        self.assertIn("535", row.error)
        self.assertIn("Username and Password not accepted", row.error)
        self.assertIn("SMTPAuthenticationError", row.error)

    def test_a_vault_failure_names_the_passphrase_that_would_fix_it(self):
        provider = self._smtp_provider(username="mailer")
        row = self._queue(provider=provider)
        with patch("toto.jess.delivery.build_connection",
                   side_effect=vault.VaultUnavailable(
                       "JESS_VAULT_PASSWORD is not set, so Jess cannot decrypt "
                       "an SMTP password.")):
            self._run(row)

        row.refresh_from_db()
        self.assertEqual(row.status, MailMessage.FAILED)
        self.assertIn("JESS_VAULT_PASSWORD", row.error)

    def test_attempts_increments_on_every_run_so_a_retry_loop_is_visible(self):
        row = self._queue()
        self._run(row)
        self._run(row)
        self._run(row)
        row.refresh_from_db()
        self.assertEqual(row.attempts, 3)

    def test_a_deleted_row_is_not_an_error(self):
        from .tasks import send_mail_message

        result = send_mail_message(999999)
        self.assertEqual(result["status"], "missing")


# ---------------------------------------------------------------------------
# The vault
# ---------------------------------------------------------------------------

class VaultTests(JessTestCase):
    def test_a_stored_password_reads_back(self):
        """The whole point of the app. Real Argon2id, real envelope, no mocks."""
        secret = vault.store_secret("hunter2", name=vault.unique_secret_name())
        self.assertEqual(vault.read_secret(secret), "hunter2")

    def test_the_plaintext_is_not_in_the_stored_row(self):
        secret = vault.store_secret("s3kr3t-passphrase", name=vault.unique_secret_name())
        secret.refresh_from_db()
        blob = " ".join(
            str(getattr(secret, f.name, "")) for f in secret._meta.fields
        )
        self.assertNotIn("s3kr3t-passphrase", blob)

    def test_the_strongbox_is_jesss_own_not_gervazys(self):
        vault.ensure_strongbox()
        box = vault.system_strongbox()
        self.assertEqual(box.name, "jess-system")
        self.assertEqual(box.owner.username, "jess-vault")

    def test_the_service_account_cannot_log_in(self):
        vault.ensure_strongbox()
        owner = vault.system_strongbox().owner
        self.assertFalse(owner.is_active)
        self.assertFalse(owner.has_usable_password())

    def test_ensure_strongbox_is_idempotent(self):
        first = vault.ensure_strongbox()
        second = vault.ensure_strongbox()
        self.assertEqual(first.pk, second.pk)

    def test_a_rotated_secret_holds_the_same_value_under_a_new_key(self):
        original = vault.store_secret("keep-me", name=vault.unique_secret_name())
        rotated = vault.reencrypt_secret(original, name=vault.unique_secret_name())
        self.assertEqual(vault.read_secret(rotated), "keep-me")
        self.assertNotEqual(rotated.pk, original.pk)
        self.assertNotEqual(rotated.wrapped_key_id, original.wrapped_key_id)

    def test_retiring_a_secret_marks_it_rather_than_deleting_it(self):
        secret = vault.store_secret("x", name=vault.unique_secret_name())
        vault.retire_secret(secret)
        secret.refresh_from_db()
        self.assertEqual(secret.state, "retired")

    @override_settings(JESS_VAULT_PASSWORD="")
    def test_no_passphrase_is_a_typed_error_naming_the_variable(self):
        vault.clear_cache()
        with self.assertRaises(vault.VaultUnavailable) as caught:
            vault.load_vault_password()
        self.assertIn("JESS_VAULT_PASSWORD", str(caught.exception))

    @override_settings(JESS_VAULT_PASSWORD="")
    def test_is_available_is_false_rather_than_raising(self):
        vault.clear_cache()
        self.assertFalse(vault.is_available())

    def test_the_read_only_path_refuses_instead_of_provisioning(self):
        """``can_deliver()`` runs on an anonymous login page — it must not write."""
        with self.assertRaises(vault.VaultUnavailable):
            vault.open_session(create=False)
        self.assertIsNone(vault.system_strongbox())

    def test_the_init_command_reports_that_the_passphrase_actually_opens_the_box(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("jess_init_vault", stdout=out)
        self.assertIsNotNone(vault.system_strongbox())


class VaultBackedSendTests(JessTestCase):
    """An SMTP password stored through the vault reaches the connection."""

    def test_the_decrypted_password_is_handed_to_the_smtp_backend(self):
        secret = vault.store_secret("relay-password", name=vault.unique_secret_name())
        provider = self._smtp_provider(username="mailer", secret=secret)

        from . import delivery

        with patch("toto.jess.delivery.get_connection") as get_conn:
            delivery.build_connection(provider)

        kwargs = get_conn.call_args.kwargs
        self.assertEqual(kwargs["password"], "relay-password")
        self.assertEqual(kwargs["username"], "mailer")
        self.assertEqual(kwargs["host"], "smtp.example.org")
        self.assertEqual(kwargs["timeout"], 10)
        self.assertFalse(kwargs["fail_silently"])

    def test_a_non_smtp_provider_is_handed_no_transport_kwargs(self):
        """console/dummy/locmem/filebased raise TypeError on host/port/username."""
        from . import delivery

        with patch("toto.jess.delivery.get_connection") as get_conn:
            delivery.build_connection(self._locmem_provider())

        kwargs = get_conn.call_args.kwargs
        self.assertEqual(set(kwargs), {"backend", "fail_silently"})

    def test_an_unreadable_secret_fails_the_row_rather_than_the_worker(self):
        secret = vault.store_secret("x", name=vault.unique_secret_name())
        provider = self._smtp_provider(username="mailer", secret=secret)
        row = MailMessage.objects.create(to=["a@x.test"], subject="S", body="B",
                                         provider=provider)
        # A changed passphrase looks exactly like this.
        vault.clear_cache()
        with override_settings(JESS_VAULT_PASSWORD="a-different-passphrase"):
            from .tasks import send_mail_message

            send_mail_message(row.pk)

        row.refresh_from_db()
        self.assertEqual(row.status, MailMessage.FAILED)
        self.assertTrue(row.error, "the failure must say something")


# ---------------------------------------------------------------------------
# email_delivery_configured() — the "Forgot password?" decision
# ---------------------------------------------------------------------------

class DeliveryConfiguredTests(JessTestCase):
    """The hook, and the reason the existing password-reset suites stay green.

    ``sso_master/tests/test_password_reset.py`` and
    ``sso_core/federation/tests/test_local_accounts.py`` override EMAIL_BACKEND to locmem
    or console and then assert on the reset link and ``mail.outbox``. The dispatch in
    ``core/email_config.py`` is on the EMAIL_BACKEND STRING precisely so those overrides
    bypass Jess entirely — asserted here rather than assumed, because if it were wrong
    those suites would fail for a reason that has nothing to do with what they test.
    """

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_a_locmem_override_bypasses_jess_even_with_no_provider_at_all(self):
        self.assertEqual(EmailProvider.objects.count(), 0)
        self.assertTrue(email_delivery_configured())

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_a_console_override_is_false_even_with_a_delivering_provider(self):
        self._smtp_provider()
        self.assertFalse(email_delivery_configured())

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_under_an_override_a_send_reaches_the_outbox_and_writes_no_row(self):
        """The structural statement: with the override, Jess is not in the call path."""
        mail.outbox = []
        send_mail("S", "B", None, ["a@x.test"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(MailMessage.objects.count(), 0)

    @override_settings(EMAIL_BACKEND=JESS_BACKEND)
    def test_no_provider_means_no_delivery(self):
        self.assertFalse(email_delivery_configured())

    @override_settings(EMAIL_BACKEND=JESS_BACKEND)
    def test_an_active_console_provider_means_no_delivery(self):
        self._provider(backend="console")
        self.assertFalse(email_delivery_configured())

    @override_settings(EMAIL_BACKEND=JESS_BACKEND)
    def test_an_active_dummy_provider_means_no_delivery(self):
        self._provider(backend="dummy")
        self.assertFalse(email_delivery_configured())

    @override_settings(EMAIL_BACKEND=JESS_BACKEND)
    def test_an_active_smtp_provider_with_no_auth_means_delivery(self):
        self._smtp_provider()
        self.assertTrue(email_delivery_configured())

    @override_settings(EMAIL_BACKEND=JESS_BACKEND)
    def test_a_username_with_no_stored_password_means_no_delivery(self):
        self._smtp_provider(username="mailer")
        self.assertFalse(email_delivery_configured())

    @override_settings(EMAIL_BACKEND=JESS_BACKEND)
    def test_a_readable_password_means_delivery(self):
        secret = vault.store_secret("pw", name=vault.unique_secret_name())
        self._smtp_provider(username="mailer", secret=secret)
        self.assertTrue(email_delivery_configured())

    @override_settings(EMAIL_BACKEND=JESS_BACKEND)
    def test_an_unreadable_password_means_no_delivery(self):
        """A reset that cannot be delivered is worse than an absent link.

        The user would be told to check their email, and nothing would arrive.

        The password is stored with the passphrase WORKING and only then taken away —
        which is the real sequence (a rotated or lost ``JESS_VAULT_PASSWORD``), and the
        only way to reach the state at all: storing a secret needs an openable vault.
        """
        secret = vault.store_secret("pw", name=vault.unique_secret_name())
        self._smtp_provider(username="mailer", secret=secret)
        self.assertTrue(email_delivery_configured())

        with override_settings(JESS_VAULT_PASSWORD=""):
            vault.clear_cache()
            self.assertFalse(email_delivery_configured())

    @override_settings(EMAIL_BACKEND=JESS_BACKEND)
    @modify_settings(INSTALLED_APPS={"remove": ["toto.jess"]})
    def test_pointing_at_jess_without_installing_it_is_false_not_an_ImportError(self):
        self.assertFalse(email_delivery_configured())

    @override_settings(EMAIL_BACKEND=JESS_BACKEND)
    def test_the_hook_never_raises_even_when_jess_itself_does(self):
        """This renders on both login pages, for anonymous users."""
        with patch("toto.jess.status.can_deliver",
                   side_effect=RuntimeError("the database is mid-migration")):
            self.assertFalse(email_delivery_configured())

    def test_can_deliver_does_not_provision_a_strongbox(self):
        """It runs on an anonymous GET; a write there is not acceptable."""
        secret = vault.store_secret("pw", name=vault.unique_secret_name())
        self._smtp_provider(username="mailer", secret=secret)
        from toto.gervazy.models import UserStrongbox

        before = UserStrongbox.objects.count()
        jess_status.can_deliver()
        self.assertEqual(UserStrongbox.objects.count(), before)

    def test_describe_names_the_problem_in_each_state(self):
        self.assertIn("No active email provider", jess_status.describe())
        console = self._provider(label="Console", backend="console")
        self.assertIn("does not deliver", jess_status.describe())
        console.delete()
        self._smtp_provider(label="Relay", username="mailer")
        self.assertIn("no stored password", jess_status.describe())


# ---------------------------------------------------------------------------
# The staff pages, and the gate
# ---------------------------------------------------------------------------

class StaffGateTests(JessTestCase):
    """403, never 302 — see the views module docstring for the trap this avoids."""

    def setUp(self):
        super().setUp()
        self.message = MailMessage.objects.create(
            to=["a@x.test"], subject="S", body="B",
        )
        self.urls = [
            reverse("jess:outbox"),
            reverse("jess:compose"),
            reverse("jess:message_detail", args=[self.message.pk]),
            reverse("jess:message_status", args=[self.message.pk]),
        ]

    def test_anonymous_gets_403_on_every_page(self):
        for url in self.urls:
            with self.subTest(url=url):
                res = self.client.get(url)
                self.assertEqual(res.status_code, 403)
                # The assertion that matters. A 302 to LOGIN_URL is what makes the
                # poller loop forever against an HTML login page.
                self.assertNotEqual(res.status_code, 302)

    def test_the_json_endpoint_specifically_answers_403_not_a_login_page(self):
        """The poller can only act on a status code it receives.

        Under the redirect-based gate the response would be status 200 with an HTML
        body, ``res.json()`` would throw, and the house ``catch { keep polling }`` would
        swallow it forever.
        """
        url = reverse("jess:message_status", args=[self.message.pk])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 403)

        self.client.force_login(self.plain)
        res = self.client.get(url)
        self.assertEqual(res.status_code, 403)

    def test_an_ordinary_signed_in_user_gets_403(self):
        self.client.force_login(self.plain)
        for url in self.urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_staff_gets_in(self):
        self.client.force_login(self.staff)
        for url in self.urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_a_superuser_who_is_not_staff_gets_in(self):
        """``is_superuser`` does not imply ``is_staff``. An is_staff-only gate 403s here."""
        self.assertFalse(self.root.is_staff)
        self.client.force_login(self.root)
        for url in self.urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_retry_is_post_only(self):
        self.client.force_login(self.staff)
        url = reverse("jess:message_retry", args=[self.message.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_retry_refuses_an_ordinary_user(self):
        self.client.force_login(self.plain)
        url = reverse("jess:message_retry", args=[self.message.pk])
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_an_unknown_message_is_404_not_500(self):
        self.client.force_login(self.staff)
        res = self.client.get(reverse("jess:message_status", args=[999999]))
        self.assertEqual(res.status_code, 404)


@override_settings(EMAIL_BACKEND=JESS_BACKEND)
class StaffPageTests(JessTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self._provider(label="Console")

    def test_the_outbox_renders_with_the_platform_in_context(self):
        """``PageProcessor`` is what supplies it; skipping it is an Http404."""
        res = self.client.get(reverse("jess:outbox"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("platform", res.context)

    def test_compose_posts_one_row_owned_by_the_acting_user(self):
        with patch("toto.jess.tasks.send_mail_message.delay"):
            res = self.client.post(reverse("jess:compose"), {
                "to": "a@x.test, b@x.test",
                "subject": "Typed by hand",
                "body": "Hello",
                "html_body": "",
            })
        row = MailMessage.objects.get()
        self.assertEqual(row.to, ["a@x.test", "b@x.test"])
        self.assertEqual(row.created_by, self.staff)
        self.assertEqual(row.purpose, MailMessage.PURPOSE_TEST)
        self.assertRedirects(
            res, reverse("jess:message_detail", args=[row.pk]),
            fetch_redirect_response=False,
        )

    def test_a_bad_address_is_a_form_error_and_writes_nothing(self):
        res = self.client.post(reverse("jess:compose"), {
            "to": "not-an-address", "subject": "S", "body": "B", "html_body": "",
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(MailMessage.objects.count(), 0)

    def test_the_detail_payload_has_the_shape_the_poller_reads(self):
        row = MailMessage.objects.create(to=["a@x.test"], subject="S", body="B")
        res = self.client.get(reverse("jess:message_status", args=[row.pk]))
        payload = res.json()
        self.assertEqual(
            set(payload),
            {"id", "status", "status_display", "is_terminal", "attempts", "error",
             "provider_label", "queued_at", "started_at", "finished_at"},
        )
        self.assertFalse(payload["is_terminal"])
        self.assertIsNone(payload["finished_at"])

    def test_is_terminal_flips_and_finished_at_appears_once_it_is_done(self):
        from django.utils import timezone

        row = MailMessage.objects.create(
            to=["a@x.test"], subject="S", body="B",
            status=MailMessage.SENT, finished_at=timezone.now(),
        )
        payload = self.client.get(
            reverse("jess:message_status", args=[row.pk])).json()
        self.assertTrue(payload["is_terminal"])
        self.assertIsNotNone(payload["finished_at"])

    def test_retry_requeues_a_failed_message_and_keeps_its_history(self):
        row = MailMessage.objects.create(
            to=["a@x.test"], subject="S", body="B",
            status=MailMessage.FAILED, error="535 nope", attempts=1,
        )
        with patch("toto.jess.tasks.send_mail_message.delay") as delay:
            self.client.post(reverse("jess:message_retry", args=[row.pk]))

        row.refresh_from_db()
        self.assertEqual(row.status, MailMessage.QUEUED)
        self.assertEqual(row.error, "")
        self.assertIsNone(row.finished_at)
        # attempts is NOT reset: a person hammering the button should be visible.
        self.assertEqual(row.attempts, 1)
        delay.assert_called_once_with(row.pk)

    def test_retry_refuses_a_message_that_is_already_sending(self):
        row = MailMessage.objects.create(
            to=["a@x.test"], subject="S", body="B", status=MailMessage.SENDING,
        )
        with patch("toto.jess.tasks.send_mail_message.delay") as delay:
            self.client.post(reverse("jess:message_retry", args=[row.pk]))
        row.refresh_from_db()
        self.assertEqual(row.status, MailMessage.SENDING)
        delay.assert_not_called()

    def test_the_outbox_counts_mail_that_is_going_nowhere(self):
        """The only place a worker-less host shows a symptom.

        With no celery worker every send is accepted and nothing leaves, and this count
        is the sole visible sign of it — so it is counted across the whole table, not
        just the capped page.
        """
        MailMessage.objects.create(to=["a@x.test"], subject="waiting", body="B")
        MailMessage.objects.create(to=["b@x.test"], subject="also", body="B",
                                   status=MailMessage.SENDING)
        MailMessage.objects.create(to=["c@x.test"], subject="done", body="B",
                                   status=MailMessage.SENT)
        MailMessage.objects.create(to=["d@x.test"], subject="dead", body="B",
                                   status=MailMessage.FAILED)

        res = self.client.get(reverse("jess:outbox"))
        self.assertEqual(res.context["waiting"], 2)
        self.assertContains(res, "still waiting to be sent")

    def test_the_outbox_says_nothing_when_the_queue_is_draining(self):
        MailMessage.objects.create(to=["a@x.test"], subject="done", body="B",
                                   status=MailMessage.SENT)
        res = self.client.get(reverse("jess:outbox"))
        self.assertEqual(res.context["waiting"], 0)
        self.assertNotContains(res, "still waiting to be sent")

    def test_no_template_leaks_a_django_comment_as_visible_text(self):
        """A ``{# #}`` comment is single-line only; a multi-line one renders as text.

        A recurring trap in this tree, and one that only shows up in a browser.
        """
        row = MailMessage.objects.create(to=["a@x.test"], subject="S", body="B")
        for url in (reverse("jess:outbox"), reverse("jess:compose"),
                    reverse("jess:message_detail", args=[row.pk])):
            with self.subTest(url=url):
                body = self.client.get(url).content.decode()
                self.assertNotIn("{#", body)
                self.assertNotIn("#}", body)


# ---------------------------------------------------------------------------
# The admin
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=JESS_BACKEND)
class AdminTests(JessTestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = User.objects.create_superuser(
            "granny", email="granny@jess.test", password="pw",
        )
        self.client.force_login(self.admin_user)

    def _change_url(self, provider):
        return reverse("admin:jess_emailprovider_change", args=[provider.pk])

    def _post_data(self, provider, **overrides):
        data = {
            "label": provider.label,
            "backend": provider.backend,
            "host": provider.host,
            "port": provider.port,
            "timeout": provider.timeout,
            "username": provider.username,
            "from_address": provider.from_address,
            "reply_to": provider.reply_to,
            "new_password": "",
        }
        if provider.use_tls:
            data["use_tls"] = "on"
        if provider.use_ssl:
            data["use_ssl"] = "on"
        if provider.active:
            data["active"] = "on"
        data.update(overrides)
        return data

    def test_the_write_only_field_stores_a_password_and_repoints_the_provider(self):
        provider = self._smtp_provider(username="mailer")
        self.client.post(
            self._change_url(provider),
            self._post_data(provider, new_password="a-real-password"),
        )
        provider.refresh_from_db()
        self.assertIsNotNone(provider.secret_id)
        self.assertEqual(vault.read_secret(provider.secret), "a-real-password")

    def test_replacing_a_password_retires_the_old_secret(self):
        provider = self._smtp_provider(username="mailer")
        self.client.post(self._change_url(provider),
                         self._post_data(provider, new_password="first"))
        provider.refresh_from_db()
        old = provider.secret

        self.client.post(self._change_url(provider),
                         self._post_data(provider, new_password="second"))
        provider.refresh_from_db()
        old.refresh_from_db()
        self.assertNotEqual(provider.secret_id, old.pk)
        self.assertEqual(old.state, "retired")
        self.assertEqual(vault.read_secret(provider.secret), "second")

    def test_a_blank_password_field_leaves_the_stored_one_alone(self):
        provider = self._smtp_provider(username="mailer")
        self.client.post(self._change_url(provider),
                         self._post_data(provider, new_password="keep"))
        provider.refresh_from_db()
        stored = provider.secret_id

        self.client.post(self._change_url(provider), self._post_data(provider))
        provider.refresh_from_db()
        self.assertEqual(provider.secret_id, stored)

    def test_the_plaintext_is_never_rendered_back(self):
        provider = self._smtp_provider(username="mailer")
        self.client.post(self._change_url(provider),
                         self._post_data(provider, new_password="never-show-me"))
        body = self.client.get(self._change_url(provider)).content.decode()
        self.assertNotIn("never-show-me", body)

    @override_settings(JESS_VAULT_PASSWORD="")
    def test_a_locked_vault_leaves_the_secret_unchanged_and_says_so(self):
        """It must not 500, and it must not claim success."""
        provider = self._smtp_provider(username="mailer")
        vault.clear_cache()
        res = self.client.post(
            self._change_url(provider),
            self._post_data(provider, new_password="doomed"), follow=True,
        )
        provider.refresh_from_db()
        self.assertIsNone(provider.secret_id)
        self.assertContains(res, "was NOT changed")

    def test_the_outbox_is_read_only_in_the_admin(self):
        from django.contrib import admin as django_admin

        from .admin import MailMessageAdmin

        model_admin = MailMessageAdmin(MailMessage, django_admin.site)
        request = None
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertEqual(
            set(model_admin.get_readonly_fields(request)),
            {f.name for f in MailMessage._meta.fields},
        )

    def test_the_send_test_action_names_the_selected_provider_explicitly(self):
        """So a provider can be proven BEFORE it is switched on."""
        candidate = EmailProvider.objects.create(
            label="Candidate", backend="locmem", active=False,
        )
        with patch("toto.jess.tasks.send_mail_message.delay"):
            self.client.post(reverse("admin:jess_emailprovider_changelist"), {
                "action": "send_test_message",
                "_selected_action": [str(candidate.pk)],
            })
        row = MailMessage.objects.get()
        self.assertEqual(row.provider_id, candidate.pk)
        self.assertEqual(row.purpose, MailMessage.PURPOSE_TEST)
        self.assertEqual(row.to, ["granny@jess.test"])

    def test_the_rotate_action_keeps_the_value_under_a_new_key(self):
        provider = self._smtp_provider(username="mailer")
        self.client.post(self._change_url(provider),
                         self._post_data(provider, new_password="rotate-me"))
        provider.refresh_from_db()
        old = provider.secret_id

        self.client.post(reverse("admin:jess_emailprovider_changelist"), {
            "action": "reencrypt_password",
            "_selected_action": [str(provider.pk)],
        })
        provider.refresh_from_db()
        self.assertNotEqual(provider.secret_id, old)
        self.assertEqual(vault.read_secret(provider.secret), "rotate-me")


# ---------------------------------------------------------------------------
# The socialhub endorsement path — which has never sent a message
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=JESS_BACKEND)
class SocialhubEndorsementMailTests(JessTestCase):
    """The first automated assertion that endorsement mail happens at all.

    Before Jess, ``api.EmailService.send_email`` declared ``smtp_password`` keyword-only
    and both call sites omitted it, so every accept and every reject raised ``TypeError``
    into a bare ``except Exception`` and was logged as "Failed to send approval email".
    No test covered it, which is how it stayed broken.
    """

    def setUp(self):
        super().setUp()
        from django.utils import timezone

        from toto.people.models import Person
        from toto.socialhub.models import (
            Community, MembershipApplication, ReferenceRequest,
        )

        self._provider(label="Console")
        self.community = Community.objects.create(
            name="Greendale", email="post@greendale.test",
        )
        self.applicant = User.objects.create(
            username="alf", email="applicant@example.test", is_active=False,
        )
        application = MembershipApplication.objects.create(
            email="applicant@example.test",
            community=self.community,
            code="111222",
            verified_at=timezone.now(),
            status="verified",
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )
        member = User.objects.create_user(username="member", password="x")
        self.referrer = Person.objects.create(user=member, display_name="Member")
        self.referrer.communities.add(self.community)
        self.ref = ReferenceRequest.objects.create(
            application=application, referrer=self.referrer,
        )
        self.client.force_login(member)

    def test_accepting_an_endorsement_queues_a_message(self):
        with patch("toto.jess.tasks.send_mail_message.delay"):
            self.client.post(
                reverse("socialhub:reference_accept", args=[self.ref.pk]))

        row = MailMessage.objects.get()
        self.assertEqual(row.purpose, MailMessage.PURPOSE_SOCIALHUB_ENDORSEMENT)
        self.assertEqual(row.to, ["applicant@example.test"])
        self.assertIn("Approved", row.subject)
        # The useful half of the per-community sender identity that went away with
        # api.EmailService — without a second place to keep an SMTP credential.
        self.assertEqual(row.reply_to, "post@greendale.test")

    def test_rejecting_an_endorsement_queues_a_message(self):
        with patch("toto.jess.tasks.send_mail_message.delay"):
            self.client.post(
                reverse("socialhub:reference_reject", args=[self.ref.pk]))

        row = MailMessage.objects.get()
        self.assertEqual(row.purpose, MailMessage.PURPOSE_SOCIALHUB_ENDORSEMENT)
        self.assertEqual(row.to, ["applicant@example.test"])

    def test_the_endorsement_still_succeeds_when_there_is_no_provider(self):
        """Accepting a reference must not fail because mail did."""
        EmailProvider.objects.all().delete()
        with patch("toto.jess.tasks.send_mail_message.delay"):
            res = self.client.post(
                reverse("socialhub:reference_accept", args=[self.ref.pk]))

        self.assertEqual(res.status_code, 302)
        self.ref.refresh_from_db()
        self.assertEqual(self.ref.status, "accepted")
        # The row still exists, so the operator can see who was not written to.
        self.assertEqual(MailMessage.objects.count(), 1)


# ---------------------------------------------------------------------------
# The datalink refusal
# ---------------------------------------------------------------------------

class DatalinkPolicyTests(TestCase):
    def test_both_jess_models_are_refused(self):
        """A provider holds a credential; an outbox holds who was emailed what."""
        from toto.datalink.registry import IDENTITY_REFUSE, load_registry, policy_for

        load_registry()
        for label in ("jess.EmailProvider", "jess.MailMessage"):
            with self.subTest(label=label):
                policy = policy_for(label)
                self.assertEqual(policy.identity, IDENTITY_REFUSE)
                self.assertTrue(policy.refuse_reason)

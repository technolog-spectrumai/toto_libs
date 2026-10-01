"""REQUIRE_SMTP (2026-10-01, ``toto.core.checks``): with it on, a process
whose mail settings could not send refuses to start — each way a setting
can fall short is its own Error, and a complete SMTP setup over TLS is
silent; with it off, nothing is looked at."""

from __future__ import annotations

from io import StringIO

from django.core import checks
from django.core.mail.backends.smtp import EmailBackend
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import SimpleTestCase, override_settings

from toto.core.checks import check_required_smtp

#: What a cloud deployment sends with.
COMPLETE = dict(
    REQUIRE_SMTP=True,
    EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    EMAIL_HOST="smtp.example.org", EMAIL_PORT=587,
    EMAIL_USE_TLS=True, EMAIL_USE_SSL=False,
    DEFAULT_FROM_EMAIL="Portal <noreply@example.org>",
    SERVER_EMAIL="noreply@example.org",
    EMAIL_TIMEOUT=15,
)


class HostBackend(EmailBackend):
    """A host's own backend over Django's (zenobia's reads a secret file)."""


class RequireSmtpCheckTests(SimpleTestCase):
    def ids(self, **changes) -> list[str]:
        with override_settings(**{**COMPLETE, **changes}):
            return [error.id for error in check_required_smtp(None)]

    def test_off_nothing_is_looked_at(self):
        self.assertEqual(self.ids(
            REQUIRE_SMTP=False, EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
            EMAIL_HOST="", EMAIL_USE_TLS=False, DEFAULT_FROM_EMAIL="webmaster@localhost",
            EMAIL_TIMEOUT=None), [])

    def test_a_complete_setup_over_tls_is_silent(self):
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.ids(EMAIL_USE_TLS=False, EMAIL_USE_SSL=True, EMAIL_PORT=465), [])

    def test_a_hosts_own_smtp_backend_counts(self):
        self.assertEqual(self.ids(EMAIL_BACKEND=f"{__name__}.HostBackend"), [])

    def test_a_backend_that_keeps_mail_here_is_refused(self):
        for backend in ("console", "filebased", "locmem", "dummy"):
            with self.subTest(backend=backend):
                self.assertEqual(
                    self.ids(EMAIL_BACKEND=f"django.core.mail.backends.{backend}.EmailBackend"),
                    ["core.E001"])

    def test_a_backend_that_does_not_import_is_refused(self):
        self.assertEqual(self.ids(EMAIL_BACKEND="no.such.Backend"), ["core.E001"])

    def test_no_mail_server_is_refused(self):
        for host in ("", "localhost", "127.0.0.1"):
            with self.subTest(host=host):
                self.assertEqual(self.ids(EMAIL_HOST=host), ["core.E002"])

    def test_no_tls_is_refused(self):
        self.assertEqual(self.ids(EMAIL_USE_TLS=False, EMAIL_USE_SSL=False), ["core.E003"])

    def test_djangos_placeholder_senders_are_refused(self):
        self.assertEqual(self.ids(DEFAULT_FROM_EMAIL="webmaster@localhost"), ["core.E004"])
        self.assertEqual(self.ids(DEFAULT_FROM_EMAIL="Portal <noreply@localhost>"),
                         ["core.E004"])
        self.assertEqual(self.ids(SERVER_EMAIL="root@localhost"), ["core.E005"])

    def test_no_timeout_is_refused(self):
        for timeout in (None, 0, -1):
            with self.subTest(timeout=timeout):
                self.assertEqual(self.ids(EMAIL_TIMEOUT=timeout), ["core.E006"])

    def test_every_shortfall_is_named_at_once(self):
        self.assertEqual(self.ids(
            EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
            EMAIL_HOST="", EMAIL_USE_TLS=False, DEFAULT_FROM_EMAIL="webmaster@localhost",
            SERVER_EMAIL="root@localhost", EMAIL_TIMEOUT=None),
            ["core.E001", "core.E002", "core.E003", "core.E004", "core.E005", "core.E006"])


class RequireSmtpStopsAStartTests(SimpleTestCase):
    """Registered, so `manage.py check` — and every command that runs the
    checks before it starts, a container's start among them — stops."""

    def test_the_registry_runs_it(self):
        with override_settings(**{**COMPLETE, "EMAIL_HOST": ""}):
            found = [e.id for e in checks.run_checks() if e.id.startswith("core.E00")]
        self.assertEqual(found, ["core.E002"])

    def test_check_refuses_and_says_why(self):
        with override_settings(**{**COMPLETE,
                                  "EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"}):
            with self.assertRaises(SystemCheckError) as caught:
                call_command("check", stdout=StringIO(), stderr=StringIO())
        self.assertIn("core.E001", str(caught.exception))
        self.assertIn("console", str(caught.exception))

    def test_check_passes_a_complete_setup(self):
        with override_settings(**COMPLETE):
            call_command("check", stdout=StringIO(), stderr=StringIO())

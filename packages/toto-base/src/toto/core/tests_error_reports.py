"""What an error mail may carry (2026-10-01, ``toto.core.error_reports``): no
setting, header or cookie that is a secret, no posted value, no secret URL
parameter — not even when the exception's own message echoes one — and one
mail per crash, not one per visit."""

from __future__ import annotations

import logging
import sys
from unittest import mock

from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, override_settings

from toto.core import error_reports
from toto.core.error_reports import (SUBSTITUTE, CrashMailFilter,
                                     PlatformExceptionReporter, crash_signature)

FILTER = "toto.core.error_reports.PlatformExceptionReporterFilter"
STARS = repr(SUBSTITUTE)


def _report(request, raise_with="the view fell over"):
    try:
        raise RuntimeError(raise_with)
    except RuntimeError:
        exc_info = sys.exc_info()
    return PlatformExceptionReporter(request, *exc_info, is_email=True).get_traceback_text()


@override_settings(DEFAULT_EXCEPTION_REPORTER_FILTER=FILTER)
class ReportTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(FIELD_ENCRYPTION_KEY="fek-0123456789abcdef",
                       WALLET_VAULT_SECRET="wallet-secret-value",
                       GITEA_SVC_PASSWORD="gitea-service-pw",
                       ZENOBIA_API_TOKEN="api-token-value",
                       MONETARY_ISSUER_KEY="issuer-key-value",
                       SENTRY_DSN="https://public-dsn-key@errors.example.org/1",
                       SHARE_LINK_SALT="share-link-salt",
                       VAULT_STORAGE={"bucket": "main", "credentials": "creds-value"})
    def test_a_secret_setting_is_starred_by_its_name(self):
        text = _report(self.factory.get("/"))
        for secret in ("fek-0123456789abcdef", "wallet-secret-value", "gitea-service-pw",
                       "api-token-value", "issuer-key-value", "public-dsn-key",
                       "share-link-salt", "creds-value"):
            self.assertNotIn(secret, text)
        self.assertIn(f"FIELD_ENCRYPTION_KEY = {STARS}", text)
        # A harmless value under a harmless key is still there to debug with.
        self.assertIn("'bucket': 'main'", text)

    @override_settings(CELERY_BROKER_URL="redis://:broker-pw-0001@redis:6379/0",
                       REPORTS_DATABASE_URL="postgres://zenobia:db-pw-0002@postgres/zenobia",
                       CHANNEL_LAYERS={"default": {"CONFIG": {
                           "hosts": ["redis://:channel-pw-0003@redis:6379/0"]}}})
    def test_a_password_inside_a_url_is_starred_the_rest_kept(self):
        text = _report(self.factory.get("/"))
        for secret in ("broker-pw-0001", "db-pw-0002", "channel-pw-0003"):
            self.assertNotIn(secret, text)
        self.assertIn(f"redis://:{SUBSTITUTE}@redis:6379/0", text)
        self.assertIn(f"postgres://zenobia:{SUBSTITUTE}@postgres/zenobia", text)

    def test_every_cookie_value_is_starred_whatever_its_name(self):
        request = self.factory.get("/")
        request.COOKIES.update({"sessionid": "session-key-0004", "csrftoken": "csrf-0005",
                                "django_language": "pl", "theme": "dark-theme-0006"})
        text = _report(request)
        for value in ("session-key-0004", "csrf-0005", "dark-theme-0006"):
            self.assertNotIn(value, text)
        self.assertIn(f"theme = {STARS}", text)

    def test_every_posted_value_is_starred_and_named(self):
        request = self.factory.post("/sign-in/", {"username": "ada", "haslo": "hunter2-0007",
                                                  "note": "my own words"})
        text = _report(request)
        # A password under a name no list could guess, and a member's words.
        self.assertNotIn("hunter2-0007", text)
        self.assertNotIn("my own words", text)
        self.assertIn(f"haslo = {STARS}", text)
        self.assertIn(f"username = {STARS}", text)

    @override_settings(DEBUG=True)
    def test_debug_still_shows_the_developer_what_was_posted(self):
        request = self.factory.post("/", {"note": "visible-0008"})
        self.assertIn("visible-0008", _report(request))

    def test_a_secret_header_is_starred(self):
        request = self.factory.get("/", HTTP_AUTHORIZATION="Bearer bearer-0009",
                                   HTTP_X_API_KEY="header-key-0010")
        request.META["CSRF_COOKIE"] = "csrf-secret-0011"
        text = _report(request)
        for value in ("bearer-0009", "header-key-0010", "csrf-secret-0011"):
            self.assertNotIn(value, text)
        self.assertIn(f"HTTP_AUTHORIZATION = {STARS}", text)

    def test_a_secret_url_parameter_is_starred_the_rest_shown(self):
        request = self.factory.get("/files/", {"token": "link-token-0012", "page": "2",
                                               "code": "oauth-code-0013"})
        text = _report(request)
        self.assertNotIn("link-token-0012", text)
        self.assertNotIn("oauth-code-0013", text)
        self.assertIn("page = '2'", text)
        # The request URL and the raw query string say the same.
        self.assertIn(f"token={SUBSTITUTE}", text)
        self.assertIn("page=2", text)

    @override_settings(WALLET_VAULT_SECRET="wallet-secret-0014")
    def test_the_exception_message_is_scrubbed_of_what_is_starred(self):
        request = self.factory.post("/", {"password": "echoed-pw-0015"})
        text = _report(request, raise_with=(
            "sign-in failed for echoed-pw-0015 with wallet-secret-0014 via "
            "https://ada:url-pw-0016@api.example.org/v1?api_key=query-key-0017&page=3"))
        for value in ("echoed-pw-0015", "wallet-secret-0014", "url-pw-0016", "query-key-0017"):
            self.assertNotIn(value, text)
        self.assertIn("sign-in failed for", text)
        self.assertIn("page=3", text)

    def test_a_cause_is_scrubbed_like_the_exception(self):
        posted = "cause-pw-0018"
        request = self.factory.post("/", {"passphrase": posted})
        try:
            try:
                # Not a literal: the report shows the line that raised.
                raise ValueError(f"bad passphrase {posted}")
            except ValueError as inner:
                raise RuntimeError("could not unlock") from inner
        except RuntimeError:
            exc_info = sys.exc_info()
        text = PlatformExceptionReporter(request, *exc_info, is_email=True).get_traceback_text()
        self.assertIn("bad passphrase", text)
        self.assertNotIn("cause-pw-0018", text)


def _record(exc_info=None):
    return logging.LogRecord("django.request", logging.ERROR, __file__, 1,
                             "Internal Server Error: %s", ("/x/",), exc_info)


def _crash(message="boom"):
    try:
        raise RuntimeError(message)
    except RuntimeError:
        return sys.exc_info()


def _other_crash():
    try:
        raise RuntimeError("elsewhere")
    except RuntimeError:
        return sys.exc_info()


@override_settings(ADMINS=[("ops@example.test", "ops@example.test")])
class CrashMailFilterTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.filter = CrashMailFilter()

    def test_a_record_without_an_exception_is_not_a_crash(self):
        # A page that answers 503 on purpose is logged at ERROR with no exception.
        self.assertFalse(self.filter.filter(_record()))
        self.assertFalse(self.filter.filter(_record((None, None, None))))

    @override_settings(ADMINS=[])
    def test_nobody_to_mail_means_no_report_is_built(self):
        self.assertFalse(self.filter.filter(_record(_crash())))

    def test_the_same_crash_is_mailed_once_and_another_one_too(self):
        self.assertTrue(self.filter.filter(_record(_crash("first visitor"))))
        # The same bug met again — another message, the same lines of code.
        self.assertFalse(self.filter.filter(_record(_crash("second visitor"))))
        self.assertTrue(self.filter.filter(_record(_other_crash())))

    def test_the_signature_is_the_type_and_the_lines(self):
        self.assertEqual(crash_signature(_crash("a")), crash_signature(_crash("b")))
        self.assertNotEqual(crash_signature(_crash()), crash_signature(_other_crash()))

    def test_the_quiet_time_is_an_hour_shared_through_the_cache(self):
        with mock.patch("django.core.cache.cache.add", return_value=True) as add:
            self.assertTrue(self.filter.filter(_record(_crash())))
        self.assertEqual(add.call_args.kwargs["timeout"], error_reports.REPEAT_SECONDS)
        self.assertEqual(error_reports.REPEAT_SECONDS, 60 * 60)
        self.assertTrue(add.call_args.args[0].startswith("toto:crash-mail:"))

    def test_a_cache_that_fails_never_silences_a_crash(self):
        with mock.patch("django.core.cache.cache.add", side_effect=ConnectionError("down")):
            self.assertTrue(self.filter.filter(_record(_crash())))
        # django-redis with IGNORE_EXCEPTIONS answers None instead of raising.
        with mock.patch("django.core.cache.cache.add", return_value=None):
            self.assertTrue(self.filter.filter(_record(_crash())))

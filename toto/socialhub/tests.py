import base64

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.api.models import EmailService
from toto.core.auth_cooldown import CAPTCHA_RETRY_COOLDOWN_SESSION_KEY
from toto.core.models import Platform
from toto.socialhub.captcha import generate_code_captcha, resolve_email_service
from toto.socialhub.models import Community, MembershipApplication


class CodeCaptchaTests(TestCase):
    def test_returns_png_data_uri(self):
        uri = generate_code_captcha("482915")
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        raw = base64.b64decode(uri.split(",", 1)[1])
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")

    def test_spurious_letters_count_is_configurable(self):
        # Both call forms should succeed; the override just changes decoy count.
        self.assertTrue(generate_code_captcha("123456", spurious_letters=0))
        with override_settings(SOCIALHUB_CAPTCHA_SPURIOUS_LETTERS=12):
            self.assertTrue(generate_code_captcha("123456"))


class ResolveEmailServiceTests(TestCase):
    def setUp(self):
        self.community = Community.objects.create(name="Riverside Guild")

    def test_none_when_no_service_configured(self):
        self.assertIsNone(resolve_email_service(self.community))

    def test_falls_back_to_default_service(self):
        svc = EmailService.objects.create(
            name="default-email-service", email_address="a@b.com", host="smtp"
        )
        self.assertEqual(resolve_email_service(self.community), svc)

    def test_prefers_community_service(self):
        EmailService.objects.create(
            name="default-email-service", email_address="a@b.com", host="smtp"
        )
        own = EmailService.objects.create(
            name="guild-mail", email_address="g@b.com", host="smtp"
        )
        self.community.email_service = own
        self.community.save()
        self.assertEqual(resolve_email_service(self.community), own)


class VerifyCaptchaViewTests(TestCase):
    def setUp(self):
        # PageProcessor (used by the view) requires an active Platform.
        Platform.objects.create(site_name="Toto", author="Test", publication_year=2026)
        self.community = Community.objects.create(name="Hill Collective")
        self.application = MembershipApplication.objects.create(
            email="newcomer@example.com",
            community=self.community,
            code="246810",
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )

    def _verify_url(self):
        return reverse(
            "socialhub:membership_verification", args=[self.application.email]
        )

    def test_shows_captcha_when_no_email_service(self):
        res = self.client.get(self._verify_url())
        self.assertEqual(res.status_code, 200)
        self.assertIn("captcha_image", res.context)
        self.assertTrue(res.context["captcha_image"].startswith("data:image/png;base64,"))

    def test_no_captcha_when_email_service_available(self):
        EmailService.objects.create(
            name="default-email-service", email_address="a@b.com", host="smtp"
        )
        res = self.client.get(self._verify_url())
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.context.get("captcha_image"))

    def test_wrong_code_starts_cooldown_in_captcha_mode(self):
        res = self.client.post(self._verify_url(), {"code": "000000"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["error"], "Invalid code or username.")
        self.assertGreater(res.context["cooldown_remaining"], 0)
        self.assertIn(CAPTCHA_RETRY_COOLDOWN_SESSION_KEY, self.client.session)

    def test_second_attempt_blocked_during_cooldown(self):
        self.client.post(self._verify_url(), {"code": "000000"})
        # Even the *correct* code is refused while the cooldown is active.
        res = self.client.post(self._verify_url(), {"code": "246810"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("Please wait", res.context["error"])
        self.application.refresh_from_db()
        self.assertNotEqual(self.application.status, "verified")

    def test_correct_code_redirects_and_leaves_no_cooldown(self):
        res = self.client.post(self._verify_url(), {"code": "246810"})
        self.assertEqual(res.status_code, 302)
        self.assertNotIn(CAPTCHA_RETRY_COOLDOWN_SESSION_KEY, self.client.session)

    @override_settings(CAPTCHA_RETRY_COOLDOWN_SECONDS=0)
    def test_zero_setting_disables_cooldown(self):
        self.client.post(self._verify_url(), {"code": "000000"})
        # With the cooldown disabled, the correct code works on the next try.
        res = self.client.post(self._verify_url(), {"code": "246810"})
        self.assertEqual(res.status_code, 302)

    def test_no_cooldown_when_email_service_available(self):
        EmailService.objects.create(
            name="default-email-service", email_address="a@b.com", host="smtp"
        )
        res = self.client.post(self._verify_url(), {"code": "000000"})
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.context.get("cooldown_remaining"))
        self.assertNotIn(CAPTCHA_RETRY_COOLDOWN_SESSION_KEY, self.client.session)

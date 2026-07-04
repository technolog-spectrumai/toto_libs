from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from toto.core.models import Platform

User = get_user_model()


def _platform():
    p = Platform.objects.filter(active=True).first()
    if p:
        return p
    return Platform.objects.create(
        site_name="Test Site", author="Test", publication_year=2024, active=True
    )


def _user(username="alice", email="alice@example.com", password="s3cr3t!XYZ"):
    return User.objects.create_user(username=username, email=email, password=password)


def _reset_url(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return reverse("sso:password_reset_confirm", kwargs={"uidb64": uid, "token": token})


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetRequestTests(TestCase):
    def setUp(self):
        _platform()
        self.user = _user()
        self.client = Client()

    def test_get_renders_form(self):
        response = self.client.get(reverse("sso:password_reset"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "sso/password_reset.html")

    def test_post_known_email_redirects_to_done(self):
        response = self.client.post(
            reverse("sso:password_reset"), {"email": self.user.email}
        )
        self.assertRedirects(response, reverse("sso:password_reset_done"))

    def test_post_known_email_sends_one_email(self):
        self.client.post(reverse("sso:password_reset"), {"email": self.user.email})
        self.assertEqual(len(mail.outbox), 1)

    def test_post_known_email_sends_to_correct_address(self):
        self.client.post(reverse("sso:password_reset"), {"email": self.user.email})
        self.assertIn(self.user.email, mail.outbox[0].to)

    def test_post_known_email_includes_reset_link(self):
        self.client.post(reverse("sso:password_reset"), {"email": self.user.email})
        self.assertIn("password-reset", mail.outbox[0].body)

    def test_post_unknown_email_still_redirects_to_done(self):
        """No information leak: unknown addresses silently redirect."""
        response = self.client.post(
            reverse("sso:password_reset"), {"email": "nobody@example.com"}
        )
        self.assertRedirects(response, reverse("sso:password_reset_done"))

    def test_post_unknown_email_sends_no_email(self):
        self.client.post(
            reverse("sso:password_reset"), {"email": "nobody@example.com"}
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_post_blank_email_stays_on_form(self):
        response = self.client.post(reverse("sso:password_reset"), {"email": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "sso/password_reset.html")

    def test_post_invalid_email_stays_on_form(self):
        response = self.client.post(
            reverse("sso:password_reset"), {"email": "not-an-email"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "sso/password_reset.html")

    def test_post_invalid_email_sends_no_email(self):
        self.client.post(reverse("sso:password_reset"), {"email": "not-an-email"})
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetDoneTests(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()

    def test_get_renders_done_template(self):
        response = self.client.get(reverse("sso:password_reset_done"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "sso/password_reset_done.html")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetConfirmTests(TestCase):
    def setUp(self):
        _platform()
        self.user = _user()
        self.client = Client()
        self.confirm_url = _reset_url(self.user)

    def test_get_valid_token_renders_form(self):
        response = self.client.get(self.confirm_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "sso/password_reset_confirm.html")

    def test_get_valid_token_sets_validlink_true(self):
        response = self.client.get(self.confirm_url)
        self.assertTrue(response.context["validlink"])

    def test_get_invalid_token_shows_error(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        url = reverse(
            "sso:password_reset_confirm",
            kwargs={"uidb64": uid, "token": "invalid-token"},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["validlink"])

    def test_get_bad_uid_shows_error(self):
        url = reverse(
            "sso:password_reset_confirm",
            kwargs={"uidb64": "badddd", "token": "bad-token"},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["validlink"])

    def test_post_valid_passwords_redirects_to_complete(self):
        response = self.client.post(
            self.confirm_url,
            {"new_password1": "N3wPassw0rd!", "new_password2": "N3wPassw0rd!"},
        )
        self.assertRedirects(response, reverse("sso:password_reset_complete"))

    def test_post_valid_passwords_actually_changes_password(self):
        old_hash = self.user.password
        self.client.post(
            self.confirm_url,
            {"new_password1": "N3wPassw0rd!", "new_password2": "N3wPassw0rd!"},
        )
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.password, old_hash)

    def test_post_password_mismatch_stays_on_form(self):
        response = self.client.post(
            self.confirm_url,
            {"new_password1": "N3wPassw0rd!", "new_password2": "Different1!"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "sso/password_reset_confirm.html")

    def test_post_password_mismatch_does_not_change_password(self):
        old_hash = self.user.password
        self.client.post(
            self.confirm_url,
            {"new_password1": "N3wPassw0rd!", "new_password2": "Different1!"},
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, old_hash)

    def test_post_invalid_token_stays_on_form(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        url = reverse(
            "sso:password_reset_confirm",
            kwargs={"uidb64": uid, "token": "tampered"},
        )
        response = self.client.post(
            url, {"new_password1": "N3wPassw0rd!", "new_password2": "N3wPassw0rd!"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["validlink"])

    def test_token_cannot_be_reused_after_password_change(self):
        """Token is consumed once the password changes."""
        self.client.post(
            self.confirm_url,
            {"new_password1": "N3wPassw0rd!", "new_password2": "N3wPassw0rd!"},
        )
        # Second use of the same URL
        response = self.client.get(self.confirm_url)
        self.assertFalse(response.context["validlink"])


@override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
class PasswordResetUnavailableTests(TestCase):
    """Without a delivering email backend the reset flow is hidden entirely."""

    def setUp(self):
        _platform()
        self.client = Client()

    def test_reset_view_redirects_to_login(self):
        response = self.client.get(reverse("sso:password_reset"))
        self.assertRedirects(response, reverse("sso:login"))

    def test_sso_login_page_hides_forgot_password_link(self):
        response = self.client.get(reverse("sso:login"))
        self.assertNotContains(response, reverse("sso:password_reset"))

    def test_core_login_page_hides_forgot_password_link(self):
        response = self.client.get(reverse("core:login"))
        self.assertNotContains(response, reverse("sso:password_reset"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetAvailableTests(TestCase):
    """With a delivering email backend the reset link is offered on login pages."""

    def setUp(self):
        _platform()
        self.client = Client()

    def test_sso_login_page_shows_forgot_password_link(self):
        response = self.client.get(reverse("sso:login"))
        self.assertContains(response, reverse("sso:password_reset"))

    def test_core_login_page_shows_forgot_password_link(self):
        response = self.client.get(reverse("core:login"))
        self.assertContains(response, reverse("sso:password_reset"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetCompleteTests(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()

    def test_get_renders_complete_template(self):
        response = self.client.get(reverse("sso:password_reset_complete"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "sso/password_reset_complete.html")

    def test_complete_page_contains_sign_in_link(self):
        response = self.client.get(reverse("sso:password_reset_complete"))
        self.assertContains(response, reverse("sso:login"))

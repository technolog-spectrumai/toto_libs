"""The shared password-reset views on a provider host: the email flow's edges.

Named tests_more_reset.py (sibling of tests_recovery.py) and meant for the
gate's host-owned block. tests_recovery.py covers the patron-ticket flow and
sso_master's suite the email happy path; this module holds the refusals and
the pages in between.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from toto.core.models import Platform

User = get_user_model()
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


@override_settings(EMAIL_BACKEND=LOCMEM, RESET_REQUEST_COOLDOWN_SECONDS=60)
class EmailFlowTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Toto", author="T", publication_year=2026,
                                active=True)
        self.user = User.objects.create_user("ada", "ada@x.test", "old-password")

    def test_the_page_offers_the_email_form_when_mail_can_leave(self):
        response = self.client.get(reverse("sso:password_reset"))
        self.assertEqual(response.context["flow"], "email")

    def test_a_second_request_inside_the_cooldown_sends_nothing(self):
        first = self.client.post(reverse("sso:password_reset"), {"email": "ada@x.test"})
        self.assertRedirects(first, reverse("sso:password_reset_done"),
                             fetch_redirect_response=False)
        second = self.client.post(reverse("sso:password_reset"), {"email": "ada@x.test"})

        self.assertEqual(second.status_code, 200)
        self.assertIn("wait", second.context["error"])
        self.assertEqual(len(mail.outbox), 1)

    def test_a_federated_account_is_never_sent_a_reset(self):
        federated = User.objects.create_user("oidc_sub-1", "fed@x.test")
        federated.set_unusable_password()
        federated.save()

        response = self.client.post(reverse("sso:password_reset"), {"email": "fed@x.test"})

        self.assertRedirects(response, reverse("sso:password_reset_done"),
                             fetch_redirect_response=False)
        self.assertEqual(mail.outbox, [])

    def test_an_inactive_account_is_never_sent_a_reset(self):
        self.user.is_active = False
        self.user.save()
        self.client.post(reverse("sso:password_reset"), {"email": "ada@x.test"})
        self.assertEqual(mail.outbox, [])

    def test_the_done_page_names_the_flow_it_was_sent_from(self):
        email = self.client.get(reverse("sso:password_reset_done"))
        ticket = self.client.get(reverse("sso:password_reset_done"), {"flow": "ticket"})
        other = self.client.get(reverse("sso:password_reset_done"), {"flow": "zzz"})
        self.assertEqual(email.context["page_title"], "Check Your Email")
        self.assertEqual(ticket.context["page_title"], "Recovery Requested")
        self.assertEqual(other.context["flow"], "email")
        self.assertGreater(ticket.context["link_ttl_hours"], 0)

    def _confirm_url(self, user, token=None):
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        return reverse("sso:password_reset_confirm",
                       args=[uid, token or default_token_generator.make_token(user)])

    def test_a_link_for_a_missing_account_is_an_invalid_link(self):
        ghost = User(pk=987654, username="ghost", password="!")
        response = self.client.get(self._confirm_url(ghost, token="1-abc"))
        self.assertFalse(response.context["validlink"])
        self.assertIsNone(response.context["form"])

    def test_a_valid_link_sets_the_password_and_then_dies(self):
        url = self._confirm_url(self.user)
        response = self.client.post(url, {"new_password1": "a-much-better-pass-42",
                                          "new_password2": "a-much-better-pass-42"})
        self.assertRedirects(response, reverse("sso:password_reset_complete"),
                             fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("a-much-better-pass-42"))
        self.assertFalse(self.client.get(url).context["validlink"])

    def test_a_reset_by_email_is_on_the_auth_trail_without_its_token(self):
        # 2026-09-30: the reset used to leave no AUTH record at all. The link's
        # token is in the request path the record keeps, so the path is
        # scrubbed; the uid (a primary key) may stay.
        from django.apps import apps

        if not apps.is_installed("toto.audit"):
            self.skipTest("audit not installed on this host")
        import json

        from toto.audit.models import AuditRecord

        token = default_token_generator.make_token(self.user)
        self.client.post(self._confirm_url(self.user, token),
                         {"new_password1": "a-much-better-pass-42",
                          "new_password2": "a-much-better-pass-42"})
        record = AuditRecord.objects.get(action="AUTH.PASSWORD_RESET")
        self.assertEqual(record.object_id, str(self.user.pk))
        self.assertEqual(record.actor_user, self.user)
        self.assertEqual(record.metadata, {"flow": "email"})
        self.assertIn("[token]", record.request_source["path"])
        self.assertNotIn(token, json.dumps([record.request_source, record.metadata]))
        self.assertNotIn("a-much-better-pass-42", json.dumps(record.metadata))

    def test_a_reset_mails_the_password_changed_notice(self):
        # Review 2026-10-01: a reset told the member nothing.
        token = default_token_generator.make_token(self.user)
        self.client.post(self._confirm_url(self.user, token),
                         {"new_password1": "a-much-better-pass-42",
                          "new_password2": "a-much-better-pass-42"})
        notices = [m for m in mail.outbox
                   if m.extra_headers.get("X-Toto-Notice") == "password_changed"]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].to, ["ada@x.test"])
        self.assertNotIn(token, notices[0].body)

    def test_a_refused_reset_is_not_on_the_trail(self):
        from django.apps import apps

        if not apps.is_installed("toto.audit"):
            self.skipTest("audit not installed on this host")
        from toto.audit.models import AuditRecord

        self.client.post(self._confirm_url(self.user),
                         {"new_password1": "a-much-better-pass-42",
                          "new_password2": "something-else-42"})
        self.assertFalse(AuditRecord.objects.filter(action="AUTH.PASSWORD_RESET").exists())

    def test_the_complete_page_renders(self):
        response = self.client.get(reverse("sso:password_reset_complete"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_title"], "Password Reset Complete")

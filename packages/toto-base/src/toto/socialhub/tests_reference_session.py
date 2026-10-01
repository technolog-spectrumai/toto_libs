"""The reference step belongs to the browser that verified the code
(2026-10-01, the review of stage 35; ``views/application.py``).

The public reference/submit/<id>/ page set the password of a pending
applicant's account for anybody holding the application's id — a number
counted up from 1 — and showed the applicant's address. Now typing the code
puts the application in that browser's session, and the step and its
thank-you page answer only there: anywhere else a sentence, no form, no
address, nothing set. A renewal starts the binding over. Since 37c.21 the
URLs carry no id at all: every step finds the application this browser
applied with (``tests_application_urls``).

    manage.py test toto.socialhub.tests_reference_session
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community, MembershipApplication, PrivacyNotice, ReferenceRequest
from toto.socialhub.tests import applied_here
from toto.socialhub.views.application import VERIFIED_SESSION_KEY

User = get_user_model()
EMAIL = "newbie@example.com"


class ReferenceSessionCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.guild = Community.objects.create(name="Cedar Guild", slug="cedar")
        PrivacyNotice.objects.create(version=1, text_pl="Informacja", text_en="Notice")
        # What the application view leaves behind: an inactive account with
        # no usable password, and the application.
        self.applicant = User.objects.create(username="newbie", email=EMAIL, is_active=False)
        self.application = MembershipApplication.objects.create(
            email=EMAIL, community=self.guild, code="424242",
            expires_at=timezone.now() + timedelta(days=7), privacy_version=1,
            privacy_accepted_at=timezone.now())
        self.referrer = Person.objects.create(
            user=User.objects.create_user("voucher", "voucher@example.com", "pw"),
            display_name="Voucher")
        self.referrer.communities.add(self.guild)
        self.stranger = Client()

    def verify(self, client, code="424242", *, applied=True):
        # The code's page is the applying browser's (37c.21): ``applied``
        # puts the application in this one first, as applying there does.
        if applied:
            applied_here(client, self.application)
        return client.post(reverse("socialhub:membership_verification"), {"code": code})

    def step_url(self):
        return reverse("socialhub:reference_request")

    def ask(self, client, password="chosen-pw-1"):
        return client.post(self.step_url(), {"referrer": self.referrer.pk,
                                             "message": "vouch", "password": password})

    def assertRefused(self, response):
        self.assertEqual(response.status_code, 403)
        self.assertIn("Only the browser in which this application",
                      response.context["refusal"])
        self.assertContains(response, "can ask for its references", status_code=403)
        self.assertNotContains(response, EMAIL, status_code=403)
        self.assertNotIn("form", response.context)

    def assertNothingSet(self):
        self.assertFalse(ReferenceRequest.objects.exists())
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.password, "")          # as the application left it


class TheVerifyingBrowserTests(ReferenceSessionCase):
    def test_it_asks_for_a_reference_and_sets_the_password(self):
        self.assertRedirects(self.verify(self.client), self.step_url(),
                             fetch_redirect_response=False)
        page = self.client.get(self.step_url())
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, EMAIL)                  # 37c.21: no address shown
        self.assertContains(page, "Cedar Guild")
        response = self.ask(self.client)
        next_url = reverse("socialhub:reference_next")
        self.assertRedirects(response, next_url, fetch_redirect_response=False)
        self.assertEqual(ReferenceRequest.objects.get().referrer, self.referrer)
        self.applicant.refresh_from_db()
        self.assertTrue(self.applicant.check_password("chosen-pw-1"))
        self.assertFalse(self.applicant.is_active)
        thanks = self.client.get(next_url)
        self.assertEqual(thanks.status_code, 200)
        self.assertNotContains(thanks, EMAIL)

    def test_the_session_holds_the_id_and_the_moment_not_the_code(self):
        self.verify(self.client)
        self.application.refresh_from_db()
        self.assertEqual(self.client.session[VERIFIED_SESSION_KEY],
                         {str(self.application.pk): self.application.verified_at.isoformat()})
        self.assertNotIn("424242", str(dict(self.client.session)))


class AnotherBrowserTests(ReferenceSessionCase):
    def test_a_stranger_with_the_id_sets_nothing(self):
        self.verify(self.client)
        self.assertRefused(self.stranger.get(self.step_url()))
        self.assertRefused(self.ask(self.stranger, password="taken-over-1"))
        self.assertNothingSet()

    def test_nobody_reaches_the_step_before_the_code_is_typed(self):
        self.assertRefused(self.client.get(self.step_url()))
        self.assertRefused(self.ask(self.client))
        self.assertNothingSet()

    def test_the_thank_you_page_is_the_verifying_browsers_too(self):
        self.verify(self.client)
        self.ask(self.client)
        self.assertRefused(self.stranger.get(reverse("socialhub:reference_next")))

    def test_typing_the_code_again_elsewhere_opens_nothing(self):
        # Even a browser holding the application (it applied in an earlier
        # round, say) only hears "already verified" once it is verified.
        self.verify(self.client)
        response = self.verify(self.stranger)
        self.assertEqual(response.context["message"], "This application is already verified.")
        self.assertRefused(self.ask(self.stranger, password="taken-over-1"))
        self.assertNothingSet()

    def test_a_browser_that_did_not_apply_is_never_shown_the_code(self):
        # (37c.21) The page answered whoever typed the address into its URL.
        response = self.verify(self.stranger, applied=False)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("captcha_image", response.context)
        self.assertRefused(self.ask(self.stranger, password="taken-over-1"))
        self.assertNothingSet()


class RenewalTests(ReferenceSessionCase):
    def lapse_and_apply_again(self, client):
        MembershipApplication.objects.filter(pk=self.application.pk).update(
            expires_at=timezone.now() - timedelta(days=1))
        client.post(reverse("socialhub:membership_application"), {
            "username": "newbie", "email": EMAIL, "community": self.guild.pk,
            "privacy_version": 1, "privacy_accept": "on"})
        self.application.refresh_from_db()
        self.assertEqual((self.application.status, self.application.verified_at),
                         ("pending", None))

    def test_a_renewal_cuts_off_the_browser_that_verified_the_lapsed_round(self):
        self.verify(self.client)
        self.lapse_and_apply_again(self.stranger)
        self.assertRefused(self.client.get(self.step_url()))
        # The new round's code is not shown to the browser of the old one.
        self.assertEqual(self.client.get(reverse("socialhub:membership_verification"))
                         .status_code, 403)
        # The renewing browser holds it from the form alone (37c.21).
        self.verify(self.stranger, code=self.application.code, applied=False)
        self.assertEqual(self.stranger.get(self.step_url()).status_code, 200)
        self.assertRefused(self.ask(self.client, password="taken-over-1"))
        self.assertNothingSet()

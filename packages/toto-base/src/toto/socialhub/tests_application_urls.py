"""No step of an application names the applicant (2026-10-01, 37c.21;
``views/application.py``).

The pages after the form were /apply/success/<e-mail>/, /verify/user/<e-mail>/
and /reference/submit/<application number>/: the address went into nginx's
access log and, as the Referer, to the next site; the verification page
showed the code's picture to anybody who typed an address into its URL; and
the log kept the address, the username and the code. Now every step finds
the application in the session of the browser that applied, the pages show
no address, and the log names the application by its id.

    manage.py test toto.socialhub.tests_application_urls
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import NoReverseMatch, reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import (Community, MembershipApplication, PrivacyNotice,
                                   ReferenceRequest)

User = get_user_model()
EMAIL = "newbie@example.com"
LOGGER = "toto.socialhub.views.application"


class ApplicationUrlCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.guild = Community.objects.create(name="Cedar Guild", slug="cedar")
        PrivacyNotice.objects.create(version=1, text_pl="Informacja", text_en="Notice")
        self.voucher_user = User.objects.create_user("voucher", "voucher@example.com", "pw")
        self.voucher = Person.objects.create(user=self.voucher_user, display_name="Voucher")
        self.voucher.communities.add(self.guild)

    def apply(self, client=None):
        return (client or self.client).post(reverse("socialhub:membership_application"), {
            "username": "newbie", "email": EMAIL, "community": self.guild.pk,
            "privacy_version": 1, "privacy_accept": "on"})

    def application(self):
        return MembershipApplication.objects.get(email=EMAIL)


class TheApplicantsWayTests(ApplicationUrlCase):
    def test_apply_verify_ask_and_be_admitted_with_no_address_on_the_way(self):
        seen = []

        def get(url, client=None):
            response = (client or self.client).get(url)
            seen.append((url, response))
            return response

        with self.assertLogs(LOGGER, level="INFO") as logs:
            response = self.apply()
            success = reverse("socialhub:application_success")
            self.assertRedirects(response, success, fetch_redirect_response=False)
            page = get(success)
            verify = reverse("socialhub:membership_verification")
            self.assertContains(page, f'href="{verify}"')
            page = get(verify)
            self.assertEqual(page.status_code, 200)
            self.assertTrue(page.context["captcha_image"].startswith("data:image/png"))
            self.assertContains(page, "Cedar Guild")
            application = self.application()
            response = self.client.post(verify, {"code": application.code})
            step = reverse("socialhub:reference_request")
            self.assertRedirects(response, step, fetch_redirect_response=False)
            self.assertEqual(get(step).status_code, 200)
            response = self.client.post(step, {"referrer": self.voucher.pk, "message": "vouch",
                                               "password": "Chosen-pw-41",
                                               "password2": "Chosen-pw-41"})
            thanks = reverse("socialhub:reference_next")
            self.assertRedirects(response, thanks, fetch_redirect_response=False)
            self.assertEqual(get(thanks).status_code, 200)

        for url, response in seen:
            with self.subTest(url=url):
                self.assertNotIn("newbie", url)
                self.assertNotIn(str(application.pk), url)
                self.assertNotContains(response, EMAIL)
        text = "\n".join(logs.output)
        for secret in (EMAIL, "newbie", application.code):
            self.assertNotIn(secret, text)
        self.assertIn(f"Application {application.pk} created.", text)
        self.assertIn(f"Application {application.pk} verified.", text)

        # The referrer accepts: the account the form made is the member's.
        referrer = Client()
        referrer.force_login(self.voucher_user)
        ref = ReferenceRequest.objects.get(application=application)
        referrer.post(reverse("socialhub:reference_accept", args=[ref.pk]))
        applicant = User.objects.get(username="newbie")
        self.assertTrue(applicant.is_active)
        self.assertTrue(applicant.check_password("Chosen-pw-41"))
        self.assertTrue(Person.objects.filter(user=applicant, communities=self.guild).exists())

    def test_a_wrong_code_is_logged_without_the_code_or_the_address(self):
        self.apply()
        with self.assertLogs(LOGGER, level="INFO") as logs:
            response = self.client.post(reverse("socialhub:membership_verification"),
                                        {"code": "000000"})
        self.assertEqual(response.context["error"], "That is not the code in the picture.")
        text = "\n".join(logs.output)
        self.assertNotIn("000000", text)
        self.assertNotIn(EMAIL, text)
        self.assertIn(f"a wrong code for application {self.application().pk}", text)


class NoAddressInTheUrlTests(ApplicationUrlCase):
    def test_the_steps_take_no_argument(self):
        for name in ("application_success", "membership_verification", "reference_request",
                     "reference_next"):
            with self.subTest(name=name), self.assertRaises(NoReverseMatch):
                reverse(f"socialhub:{name}", args=[EMAIL])

    def test_the_old_addresses_are_gone(self):
        self.apply()
        pk = self.application().pk
        for path in (f"/socialhub/apply/success/{EMAIL}/", f"/socialhub/verify/user/{EMAIL}/",
                     f"/socialhub/reference/submit/{pk}/", f"/socialhub/reference/next/{pk}/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


class OnlyTheApplyingBrowserTests(ApplicationUrlCase):
    def test_another_browser_is_not_shown_the_code(self):
        self.apply()
        stranger = Client()
        response = stranger.get(reverse("socialhub:membership_verification"))
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("captcha_image", response.context)
        self.assertIn("This browser has no application waiting", response.context["refusal"])
        response = stranger.post(reverse("socialhub:membership_verification"),
                                 {"code": self.application().code})
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.application().verified_at)

    def test_the_success_page_offers_the_code_only_to_the_applying_browser(self):
        self.apply()
        verify = reverse("socialhub:membership_verification")
        self.assertContains(self.client.get(reverse("socialhub:application_success")),
                            f'href="{verify}"')
        self.assertNotContains(Client().get(reverse("socialhub:application_success")),
                               f'href="{verify}"')
        # Coming back to the form finds the way to the code, too.
        self.assertContains(self.client.get(reverse("socialhub:membership_application")),
                            f'href="{verify}"')

    def test_the_session_holds_the_id_and_the_week_not_the_address_or_the_code(self):
        self.apply()
        application = self.application()
        session = dict(self.client.session)
        self.assertEqual(session["socialhub_application"],
                         {"id": application.pk, "round": application.expires_at.isoformat()})
        self.assertNotIn(EMAIL, str(session))
        self.assertNotIn(application.code, str(session))

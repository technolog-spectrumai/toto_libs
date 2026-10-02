"""The password set at the reference step (2026-10-02, the crown bug hunt).

An applicant may choose their password while asking for a reference, so that
they can sign in the moment a referrer accepts. It was a bare field: no
``AUTH_PASSWORD_VALIDATORS`` saw it — ``1`` or ``password`` was taken, and the
account signed in with it once admitted — while every other door that sets a
password asks them (the sign-up API, Django's change and reset forms). Now it
goes through the validators with the applicant's own account (so a password
like the username is refused too), is typed twice, and a refusal is shown on
the page with nothing made or set.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_reference_password
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community, MembershipApplication, PrivacyNotice, ReferenceRequest
from toto.socialhub.tests import applied_here

User = get_user_model()

#: The host's validators (zenobia's settings), named here so the rule under
#: test does not depend on which host runs it.
VALIDATORS = [{"NAME": f"django.contrib.auth.password_validation.{name}"} for name in (
    "UserAttributeSimilarityValidator", "MinimumLengthValidator",
    "CommonPasswordValidator", "NumericPasswordValidator")]


@override_settings(AUTH_PASSWORD_VALIDATORS=VALIDATORS)
class ReferencePasswordTests(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.guild = Community.objects.create(name="Cedar Guild", slug="cedar")
        PrivacyNotice.objects.create(version=1, text_pl="Informacja", text_en="Notice")
        # What the application view leaves: an inactive account, no password.
        self.applicant = User.objects.create(username="newbie", email="newbie@example.com",
                                             is_active=False)
        self.application = MembershipApplication.objects.create(
            email="newbie@example.com", community=self.guild, code="424242",
            expires_at=timezone.now() + timedelta(days=7), privacy_version=1,
            privacy_accepted_at=timezone.now())
        self.referrer = Person.objects.create(
            user=User.objects.create_user("voucher", "voucher@example.com", "pw"),
            display_name="Voucher")
        self.referrer.communities.add(self.guild)
        # The browser that applied types the code: the step is its own.
        applied_here(self.client, self.application)
        self.client.post(reverse("socialhub:membership_verification"), {"code": "424242"})

    def ask(self, password, again=None):
        data = {"referrer": self.referrer.pk, "message": "vouch"}
        if password is not None:
            data["password"] = password
        if again is not None:
            data["password2"] = again
        return self.client.post(reverse("socialhub:reference_request"), data)

    def assertRefusedWithNothingSet(self, response, field):
        self.assertEqual(response.status_code, 200)
        self.assertIn(field, response.context["form"].errors)
        self.assertFalse(ReferenceRequest.objects.exists())
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.password, "")        # as the application left it

    def test_a_password_no_validator_would_take_is_refused(self):
        for weak in ("1", "12345678", "password"):
            with self.subTest(password=weak):
                self.assertRefusedWithNothingSet(self.ask(weak, weak), "password")

    def test_a_password_like_the_applicants_own_username_is_refused(self):
        # The validators are given the applicant's account, not a blank one.
        self.assertRefusedWithNothingSet(self.ask("newbie2026", "newbie2026"), "password")

    def test_the_password_is_typed_twice(self):
        self.assertRefusedWithNothingSet(self.ask("Chosen-pw-41", "Chosen-pw-42"), "password2")
        self.assertRefusedWithNothingSet(self.ask("Chosen-pw-41"), "password2")
        self.assertRefusedWithNothingSet(self.ask("", "Chosen-pw-41"), "password2")

    def test_why_it_was_refused_is_on_the_page(self):
        response = self.ask("1234", "1234")
        self.assertContains(response, "This password is too short.")
        self.assertContains(response, "This password is entirely numeric.")
        self.assertContains(response, 'name="password2"')
        response = self.ask("Chosen-pw-41", "Chosen-pw-42")
        self.assertContains(response, "The two password fields didn’t match.")

    def test_a_good_password_typed_twice_is_set(self):
        response = self.ask("Chosen-pw-41", "Chosen-pw-41")
        self.assertRedirects(response, reverse("socialhub:reference_next"),
                             fetch_redirect_response=False)
        self.applicant.refresh_from_db()
        self.assertTrue(self.applicant.check_password("Chosen-pw-41"))
        self.assertFalse(self.applicant.is_active)

    def test_no_password_at_all_is_still_a_reference(self):
        response = self.ask(None)
        self.assertRedirects(response, reverse("socialhub:reference_next"),
                             fetch_redirect_response=False)
        self.assertTrue(ReferenceRequest.objects.exists())
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.password, "")

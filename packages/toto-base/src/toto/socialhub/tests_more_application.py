"""The membership flow past the CAPTCHA: verifying, asking for a reference,
and the referrer's answer — the one door into a functional community.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_more_application
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community, MembershipApplication, PrivacyNotice, ReferenceRequest

User = get_user_model()


class FlowCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.guild = Community.objects.create(name="Cedar Guild", slug="cedar",
                                              email="board@cedar.example")
        self.other = Community.objects.create(name="Pine Guild", slug="pine")
        self.applicant = User.objects.create(username="newbie", email="newbie@example.com",
                                             is_active=False)
        self.application = MembershipApplication.objects.create(
            email="newbie@example.com", community=self.guild, code="424242",
            expires_at=timezone.now() + timedelta(days=7))
        referrer_user = User.objects.create_user("voucher", "voucher@example.com", "pw")
        self.referrer = Person.objects.create(user=referrer_user, display_name="Voucher")
        self.referrer.communities.add(self.guild)
        self.outsider = Person.objects.create(
            user=User.objects.create_user("outsider", password="pw"), display_name="Outsider")
        self.outsider.communities.add(self.other)
        # An application needs a notice to accept (2026-10-01).
        PrivacyNotice.objects.create(version=1, text_pl="Informacja", text_en="Notice")

    def verify(self, code):
        return self.client.post(reverse("socialhub:membership_verification",
                                        args=[self.application.email]), {"code": code})


class VerificationTests(FlowCase):
    def test_the_right_code_verifies_and_leads_to_the_reference_step(self):
        response = self.verify("424242")
        self.assertRedirects(response, reverse("socialhub:reference_request",
                                               args=[self.application.pk]),
                             fetch_redirect_response=False)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "verified")
        self.assertIsNotNone(self.application.verified_at)

    def test_an_expired_code_is_refused(self):
        MembershipApplication.objects.filter(pk=self.application.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1))
        response = self.verify("424242")
        # Applying again renews it since 2026-10-01 (tests_application_housekeeping).
        self.assertEqual(response.context["error"], "This code has expired. Apply again with "
                         "the same e-mail address to get a new one.")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "pending")

    def test_a_verified_application_is_not_verified_twice(self):
        stamp = timezone.now() - timedelta(days=1)
        MembershipApplication.objects.filter(pk=self.application.pk).update(
            verified_at=stamp, status="endorsed")
        response = self.verify("424242")
        self.assertEqual(response.context["message"], "This application is already verified.")
        self.application.refresh_from_db()
        self.assertEqual((self.application.status, self.application.verified_at), ("endorsed", stamp))

    def test_a_code_belongs_to_its_own_email(self):
        MembershipApplication.objects.create(
            email="someone@example.com", community=self.guild, code="777777",
            expires_at=timezone.now() + timedelta(days=7))
        response = self.verify("777777")
        self.assertEqual(response.context["error"], "Invalid code or username.")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "pending")

    def test_an_email_nobody_applied_with_shows_no_captcha_and_starts_no_cooldown(self):
        response = self.client.post(reverse("socialhub:membership_verification",
                                            args=["ghost@example.com"]), {"code": "424242"})
        self.assertNotIn("captcha_image", response.context)
        self.assertNotIn("cooldown_remaining", response.context)
        self.assertEqual(response.context["error"], "Invalid code or username.")


class ApplyAgainTests(FlowCase):
    def test_applying_again_with_the_same_email_is_refused_and_makes_no_account(self):
        response = self.client.post(reverse("socialhub:membership_application"), {
            "username": "newbie2", "email": "newbie@example.com", "community": self.other.pk,
            "privacy_version": 1, "privacy_accept": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="newbie2").exists())
        self.application.refresh_from_db()
        self.assertEqual((self.application.community, self.application.code),
                         (self.guild, "424242"))

    def test_the_address_in_another_case_is_the_same_address(self):
        # Unique only as typed until 2026-10-01: a second application and a
        # second account for one mailbox.
        response = self.client.post(reverse("socialhub:membership_application"), {
            "username": "newbie2", "email": "NewBie@Example.com", "community": self.other.pk,
            "privacy_version": 1, "privacy_accept": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="newbie2").exists())
        self.assertEqual(MembershipApplication.objects.filter(
            email__iexact="newbie@example.com").count(), 1)

    def test_a_new_application_gets_its_own_code_and_a_week(self):
        self.client.post(reverse("socialhub:membership_application"), {
            "username": "fresh", "email": "fresh@example.com", "community": self.guild.pk,
            "privacy_version": 1, "privacy_accept": "on"})
        made = MembershipApplication.objects.get(email="fresh@example.com")
        self.assertRegex(made.code, r"^\d{6}$")
        self.assertAlmostEqual((made.expires_at - timezone.now()).total_seconds(),
                               timedelta(days=7).total_seconds(), delta=60)
        self.assertEqual(made.status, "pending")


class ReferenceRequestPageTests(FlowCase):
    def test_only_members_of_the_community_applied_to_may_be_named(self):
        form = self.client.get(reverse("socialhub:reference_request",
                                       args=[self.application.pk])).context["form"]
        self.assertEqual(list(form.fields["referrer"].queryset), [self.referrer])
        response = self.client.post(reverse("socialhub:reference_request",
                                            args=[self.application.pk]),
                                    {"referrer": self.outsider.pk, "message": "hi"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ReferenceRequest.objects.exists())

    def test_a_request_is_made_and_the_thank_you_page_follows(self):
        response = self.client.post(reverse("socialhub:reference_request",
                                            args=[self.application.pk]),
                                    {"referrer": self.referrer.pk, "message": "vouch"})
        next_url = reverse("socialhub:reference_next", args=[self.application.pk])
        self.assertRedirects(response, next_url, fetch_redirect_response=False)
        self.assertEqual(ReferenceRequest.objects.get().referrer, self.referrer)
        self.assertEqual(self.client.get(next_url).status_code, 200)

    def member_with_the_address(self):
        """A member whose account has the application's address and is older
        than the applicant's — the first one an exact lookup found."""
        self.applicant.delete()
        member = User.objects.create_user("elder", "newbie@example.com", "elder-pw")
        self.applicant = User.objects.create(username="newbie", email="newbie@example.com",
                                             is_active=False)
        return member

    def test_the_password_goes_to_the_waiting_account_never_a_members(self):
        # (2026-10-01) This page is public, and it set the password of the
        # first account at the address — a member's.
        member = self.member_with_the_address()
        self.client.post(reverse("socialhub:reference_request", args=[self.application.pk]),
                         {"referrer": self.referrer.pk, "message": "vouch", "password": "chosen-pw-1"})
        member.refresh_from_db()
        self.applicant.refresh_from_db()
        self.assertTrue(member.check_password("elder-pw"))
        self.assertTrue(self.applicant.check_password("chosen-pw-1"))

    def test_an_admitted_applicants_password_is_not_set_from_here(self):
        self.applicant.is_active = True
        self.applicant.set_password("own-pw")
        self.applicant.save()
        self.client.post(reverse("socialhub:reference_request", args=[self.application.pk]),
                         {"referrer": self.referrer.pk, "message": "again", "password": "other-pw-1"})
        self.applicant.refresh_from_db()
        self.assertTrue(self.applicant.check_password("own-pw"))

    def test_an_unknown_application_is_a_404(self):
        self.assertEqual(self.client.get(reverse("socialhub:reference_request",
                                                 args=[999999])).status_code, 404)


class ReferenceAnswerTests(FlowCase):
    def setUp(self):
        super().setUp()
        self.ref = ReferenceRequest.objects.create(application=self.application,
                                                   referrer=self.referrer)

    def answer(self, verb, as_user):
        self.client.force_login(as_user)
        return self.client.post(reverse(f"socialhub:reference_{verb}", args=[self.ref.pk]))

    def test_accepting_admits_the_applicant_and_mails_them(self):
        response = self.answer("accept", self.referrer.user)
        self.assertRedirects(response, reverse("socialhub:profile_details",
                                               args=[self.referrer.slug]),
                             fetch_redirect_response=False)
        self.ref.refresh_from_db()
        self.assertEqual(self.ref.status, "accepted")
        self.assertIsNotNone(self.ref.responded_at)
        self.applicant.refresh_from_db()
        self.assertTrue(self.applicant.is_active)
        self.assertTrue(self.guild.members.filter(user=self.applicant).exists())
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["newbie@example.com"])
        self.assertEqual(sent.reply_to, ["board@cedar.example"])
        self.assertEqual(sent.extra_headers["X-Jess-Purpose"], "socialhub_endorsement")
        self.assertIn("Approved", sent.subject)

    def test_the_applicants_account_is_found_whatever_the_case(self):
        User.objects.filter(pk=self.applicant.pk).update(email="NewBie@Example.com")
        self.answer("accept", self.referrer.user)
        self.applicant.refresh_from_db()
        self.assertTrue(self.applicant.is_active)
        self.assertTrue(self.guild.members.filter(user=self.applicant).exists())

    def test_the_waiting_account_is_admitted_not_a_member_with_the_address(self):
        # (2026-10-01) The first account at the address was activated and
        # enrolled — a member's — and the applicant's stayed shut.
        self.applicant.delete()
        member = User.objects.create_user("elder", "newbie@example.com", "pw")
        self.applicant = User.objects.create(username="newbie", email="newbie@example.com",
                                             is_active=False)
        self.answer("accept", self.referrer.user)
        self.applicant.refresh_from_db()
        self.assertTrue(self.applicant.is_active)
        self.assertTrue(self.guild.members.filter(user=self.applicant).exists())
        self.assertFalse(self.guild.members.filter(user=member).exists())

    def test_declining_keeps_the_applicant_out_and_says_so(self):
        self.answer("reject", self.referrer.user)
        self.ref.refresh_from_db()
        self.assertEqual(self.ref.status, "declined")
        self.applicant.refresh_from_db()
        self.assertFalse(self.applicant.is_active)
        self.assertFalse(self.guild.members.filter(user=self.applicant).exists())
        self.assertIn("not approved", mail.outbox[0].body)

    def test_a_community_without_an_address_mails_without_a_reply_to(self):
        self.application.community = self.other
        self.application.save()
        self.answer("reject", self.referrer.user)
        self.assertEqual(mail.outbox[0].reply_to, [])

    def test_nobody_but_the_referrer_may_answer(self):
        for verb in ("accept", "reject"):
            with self.subTest(verb=verb):
                self.assertEqual(self.answer(verb, self.outsider.user).status_code, 403)
        self.ref.refresh_from_db()
        self.assertEqual(self.ref.status, "pending")
        self.assertEqual(mail.outbox, [])

    def test_the_answer_is_a_post(self):
        self.client.force_login(self.referrer.user)
        self.assertEqual(self.client.get(reverse("socialhub:reference_accept",
                                                 args=[self.ref.pk])).status_code, 405)
        self.ref.refresh_from_db()
        self.assertEqual(self.ref.status, "pending")

    def test_an_application_with_no_account_is_admitted_to_nothing_and_mails_nobody(self):
        self.applicant.delete()
        response = self.answer("accept", self.referrer.user)
        self.assertEqual(response.status_code, 302)
        self.ref.refresh_from_db()
        self.assertEqual(self.ref.status, "accepted")
        self.assertEqual(self.guild.members.count(), 1)             # the referrer alone
        self.assertEqual(mail.outbox, [])

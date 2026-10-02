"""Accepting the privacy notice on the membership application (2026-10-01,
RODO): the tick is required, the version shown is the version recorded, it is
audited, and it goes with the applicant to their Person on admission.
Members from before acceptance existed are not asked.

    manage.py test toto.socialhub.tests_privacy_acceptance
"""

from __future__ import annotations

from datetime import timedelta

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.people.models import Person

from .models import (Community, MembershipApplication, PrivacyAcceptance, PrivacyNotice,
                     ReferenceRequest)

User = get_user_model()


def _audit_records(action):
    if not apps.is_installed("toto.audit"):
        return None
    from toto.audit.models import AuditRecord

    return list(AuditRecord.objects.filter(action=action).order_by("sequence"))


class AcceptanceCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.guild = Community.objects.create(name="Cedar Guild", slug="cedar")
        self.notice = PrivacyNotice.objects.create(version=1, text_pl="Informacja",
                                                   text_en="Notice")

    def apply(self, **extra):
        data = {"username": "fresh", "email": "fresh@example.com",
                "community": self.guild.pk, "privacy_version": self.notice.version,
                "privacy_accept": "on", **extra}
        return self.client.post(reverse("socialhub:membership_application"),
                                {k: v for k, v in data.items() if v is not None})


class ApplicationFormTests(AcceptanceCase):
    def test_the_form_links_the_current_version_and_asks_for_the_tick(self):
        PrivacyNotice.objects.create(version=2, text_pl="Nowa", text_en="New")
        response = self.client.get(reverse("socialhub:membership_application"))
        self.assertContains(response, reverse("socialhub:privacy_notice_version", args=[2]))
        self.assertContains(response, 'name="privacy_accept"')
        self.assertContains(response, 'name="privacy_version" value="2"')

    def test_without_the_tick_nothing_is_made(self):
        response = self.apply(privacy_accept=None)
        self.assertEqual(response.status_code, 200)
        self.assertIn("privacy_accept", response.context["form"].errors)
        self.assertFalse(MembershipApplication.objects.exists())
        self.assertFalse(User.objects.filter(username="fresh").exists())

    def test_the_version_accepted_and_when_are_recorded_and_audited(self):
        response = self.apply()
        self.assertEqual(response.status_code, 302)
        made = MembershipApplication.objects.get(email="fresh@example.com")
        self.assertEqual(made.privacy_version, 1)
        self.assertAlmostEqual(made.privacy_accepted_at.timestamp(), timezone.now().timestamp(),
                               delta=60)
        records = _audit_records("PRIVACY.NOTICE_ACCEPTED")
        if records is not None:
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].metadata["version"], 1)
            self.assertEqual(records[0].object_id, str(made.pk))

    def test_a_version_published_while_the_page_was_open_is_shown_not_accepted(self):
        PrivacyNotice.objects.create(version=2, text_pl="Nowa", text_en="New")
        response = self.apply(privacy_version=1)
        self.assertEqual(response.status_code, 200)
        self.assertIn("privacy_accept", response.context["form"].errors)
        self.assertFalse(MembershipApplication.objects.exists())
        # Drawn again with the new version and the box clear.
        self.assertContains(response, 'name="privacy_version" value="2"')
        self.assertFalse(response.context["form"]["privacy_accept"].value())
        self.assertEqual(self.apply(privacy_version=2).status_code, 302)
        self.assertEqual(MembershipApplication.objects.get().privacy_version, 2)

    def test_with_no_notice_published_applications_are_closed(self):
        PrivacyNotice.objects.all().delete()
        page = self.client.get(reverse("socialhub:membership_application"))
        self.assertContains(page, "Applications are closed until a privacy notice is published.")
        response = self.apply()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MembershipApplication.objects.exists())
        self.assertFalse(User.objects.filter(username="fresh").exists())


class AdmissionTests(AcceptanceCase):
    def setUp(self):
        super().setUp()
        referrer_user = User.objects.create_user("voucher", "voucher@example.com", "pw")
        self.referrer = Person.objects.create(user=referrer_user, display_name="Voucher")
        self.referrer.communities.add(self.guild)

    def admit(self, **application):
        User.objects.create(username="newbie", email="newbie@example.com", is_active=False)
        made = MembershipApplication.objects.create(
            email="newbie@example.com", community=self.guild, code="424242",
            verified_at=timezone.now(), status="verified",
            expires_at=timezone.now() + timedelta(days=7), **application)
        ref = ReferenceRequest.objects.create(application=made, referrer=self.referrer)
        ref.status = "accepted"
        ref.save()
        return made, Person.objects.get(user__email="newbie@example.com")

    def test_the_acceptance_is_carried_to_the_person(self):
        stamp = timezone.now() - timedelta(days=2)
        made, person = self.admit(privacy_version=1, privacy_accepted_at=stamp)
        acceptance = person.privacy_acceptances.get()
        self.assertEqual((acceptance.version, acceptance.accepted_at), (1, stamp))

    def test_admitting_twice_writes_one_row(self):
        made, person = self.admit(privacy_version=1, privacy_accepted_at=timezone.now())
        ref = made.reference_requests.get()
        ref.status = "pending"
        ref.save()
        ref.status = "accepted"
        ref.save()
        self.assertEqual(PrivacyAcceptance.objects.filter(person=person).count(), 1)

    def test_an_application_from_before_acceptance_still_admits_with_no_row(self):
        made, person = self.admit()
        self.assertTrue(person.user.is_active)
        self.assertFalse(person.privacy_acceptances.exists())

    def test_the_referrer_sees_the_version_the_applicant_accepted(self):
        MembershipApplication.objects.create(
            email="other@example.com", community=self.guild, code="515151",
            expires_at=timezone.now() + timedelta(days=7),
            privacy_version=1, privacy_accepted_at=timezone.now())
        ReferenceRequest.objects.create(
            application=MembershipApplication.objects.get(email="other@example.com"),
            referrer=self.referrer)
        self.client.force_login(self.referrer.user)
        # The reference requests: the Activity tab of the referrer's own
        # profile (stage 50).
        page = self.client.get(reverse("socialhub:profile_details", args=[self.referrer.slug])
                               + "?tab=activity")
        self.assertContains(page, reverse("socialhub:privacy_notice_version", args=[1]))
        self.assertContains(page, "Privacy notice v1 accepted")


class ExistingMemberTests(AcceptanceCase):
    def test_a_member_who_never_accepted_is_not_asked(self):
        user = User.objects.create_user("elder", "elder@example.com", "pw")
        Person.objects.create(user=user, display_name="Elder")
        self.assertTrue(self.client.login(username="elder", password="pw"))
        # /account/ leads to the elder's own profile, which opens (stage 50).
        response = self.client.get(reverse("account:home"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0],
                         reverse("socialhub:profile_details", args=["elder"]))
        self.assertFalse(PrivacyAcceptance.objects.exists())


class AdminTests(TestCase):
    def test_the_admin_shows_the_version_and_does_not_let_it_be_edited(self):
        application_admin = admin.site._registry[MembershipApplication]
        self.assertIn("privacy_version", application_admin.list_display)
        self.assertIn("privacy_version", application_admin.readonly_fields)
        self.assertIn("privacy_accepted_at", application_admin.readonly_fields)
        acceptance_admin = admin.site._registry[PrivacyAcceptance]
        self.assertFalse(acceptance_admin.has_add_permission(None))

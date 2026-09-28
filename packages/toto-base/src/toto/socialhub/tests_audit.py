"""Communities, circles and who is in them, on the chain (2026-09-28).

One record per change, from either side of ``Person.communities``, every
community record saying whether it is a circle; applications and references
from asking to being admitted."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from toto.audit.models import AuditRecord
from toto.people.models import Person
from toto.socialhub.models import (Community, CommunityPrivilege, MembershipApplication,
                                   ReferenceRequest)

User = get_user_model()


class CommunityAuditTests(TestCase):
    def setUp(self):
        self.devs = Community.objects.create(name="devs", slug="devs")
        self.seniors = Community.objects.create(name="seniors", slug="seniors", is_circle=True)
        self.ada = Person.objects.create(user=User.objects.create_user("ada", password="pw"),
                                         display_name="Ada")

    def records(self, action, **filters):
        return AuditRecord.objects.filter(action=action, **filters)

    def test_a_community_made_changed_and_removed(self):
        made = self.records("SOCIALHUB.COMMUNITY_CREATED", object_id=str(self.seniors.pk)).get()
        self.assertTrue(made.metadata["is_circle"])
        self.devs.name = "developers"
        self.devs.save()
        changed = self.records("SOCIALHUB.COMMUNITY_CHANGED").get()
        self.assertEqual(changed.metadata["changed"], ["name"])
        self.assertEqual(changed.metadata["before"]["name"], "devs")
        self.devs.save()                                          # nothing changed: nothing recorded
        self.assertEqual(self.records("SOCIALHUB.COMMUNITY_CHANGED").count(), 1)
        pk = self.devs.pk
        self.devs.delete()
        self.assertTrue(self.records("SOCIALHUB.COMMUNITY_DELETED", object_id=str(pk)).exists())

    def test_joining_and_leaving_from_either_side(self):
        self.ada.communities.add(self.seniors)
        self.ada.communities.add(self.seniors)                    # already in: no second record
        added = self.records("SOCIALHUB.MEMBER_ADDED").get()
        self.assertEqual(added.metadata["person"], self.ada.slug)
        self.assertTrue(added.metadata["is_circle"])
        self.devs.members.add(self.ada)
        self.assertEqual(self.records("SOCIALHUB.MEMBER_ADDED").count(), 2)
        self.seniors.members.remove(self.ada)
        self.assertEqual(self.records("SOCIALHUB.MEMBER_REMOVED").count(), 1)

    def test_removing_somebody_who_is_not_there_records_nothing(self):
        self.seniors.members.remove(self.ada)
        self.assertFalse(self.records("SOCIALHUB.MEMBER_REMOVED").exists())

    def test_a_clear_names_everybody_it_removed(self):
        self.ada.communities.add(self.devs, self.seniors)
        self.ada.communities.clear()
        removed = self.records("SOCIALHUB.MEMBER_REMOVED")
        self.assertEqual({r.metadata["community"] for r in removed}, {"devs", "seniors"})

    def test_senior_members(self):
        self.devs.senior_members.add(self.ada)
        self.assertEqual(self.records("SOCIALHUB.SENIOR_ADDED").get().metadata["person"], self.ada.slug)
        self.ada.senior_communities.remove(self.devs)
        self.assertEqual(self.records("SOCIALHUB.SENIOR_REMOVED").count(), 1)

    def test_a_privilege(self):
        CommunityPrivilege.objects.create(community=self.devs)
        self.assertTrue(self.records("SOCIALHUB.PRIVILEGE_CHANGED").exists())


class ApplicationAuditTests(TestCase):
    def setUp(self):
        self.devs = Community.objects.create(name="devs", slug="devs")
        self.referrer = Person.objects.create(
            user=User.objects.create_user("ref", password="pw"), display_name="Referrer")
        self.applicant = User.objects.create_user("newbie", email="newbie@example.com",
                                                  password="pw", is_active=False)
        self.application = MembershipApplication.objects.create(
            email="newbie@example.com", community=self.devs,
            expires_at=timezone.now() + timedelta(days=3))

    def records(self, action):
        return AuditRecord.objects.filter(action=action)

    def test_an_application_submitted_then_rejected(self):
        self.assertEqual(self.records("SOCIALHUB.APPLICATION_SUBMITTED").get().metadata["community"],
                         "devs")
        self.application.status = "rejected"
        self.application.save()
        self.assertEqual(self.records("SOCIALHUB.APPLICATION_REJECTED").count(), 1)

    def test_a_reference_asked_then_given_admits_the_applicant(self):
        ref = ReferenceRequest.objects.create(application=self.application, referrer=self.referrer)
        self.assertEqual(self.records("SOCIALHUB.REFERENCE_REQUESTED").get().metadata["referrer"],
                         self.referrer.slug)
        ref.status = "accepted"
        ref.save()
        given = self.records("SOCIALHUB.REFERENCE_GIVEN").get()
        self.assertTrue(given.metadata["admitted"])
        self.assertTrue(self.records("SOCIALHUB.MEMBER_ADDED").filter(
            metadata__community="devs").exists())
        self.assertTrue(self.records("AUTH.ACCOUNT_ACTIVATED").exists())

    def test_a_reference_declined(self):
        ref = ReferenceRequest.objects.create(application=self.application, referrer=self.referrer)
        ref.status = "declined"
        ref.save()
        self.assertEqual(self.records("SOCIALHUB.REFERENCE_DECLINED").count(), 1)
        self.assertFalse(self.records("SOCIALHUB.REFERENCE_GIVEN").exists())

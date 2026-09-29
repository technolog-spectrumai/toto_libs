"""Communities, clearances and who is in them, on the chain (2026-09-28).

One record per change, from either side of ``Person.communities`` and of
``Person.clearances`` (its own actions, 2026-09-29); applications and
references from asking to being admitted."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from toto.audit.models import AuditRecord
from toto.people.models import Person
from toto.socialhub.models import (Clearance, Community, CommunityPrivilege,
                                   MembershipApplication, ReferenceRequest)

User = get_user_model()


class CommunityAuditTests(TestCase):
    def setUp(self):
        self.devs = Community.objects.create(name="devs", slug="devs")
        self.internal = Clearance.objects.create(name="internal", slug="internal")
        self.ada = Person.objects.create(user=User.objects.create_user("ada", password="pw"),
                                         display_name="Ada")

    def records(self, action, **filters):
        return AuditRecord.objects.filter(action=action, **filters)

    def test_a_community_made_changed_and_removed(self):
        made = self.records("SOCIALHUB.COMMUNITY_CREATED", object_id=str(self.devs.pk)).get()
        self.assertEqual(made.metadata["community"], "devs")
        self.assertNotIn("is_clearance", made.metadata)
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

    def test_a_clearance_made_changed_and_removed(self):
        made = self.records("SOCIALHUB.CLEARANCE_CREATED", object_id=str(self.internal.pk)).get()
        self.assertEqual(made.metadata["clearance"], "internal")
        self.assertEqual(made.object_type, "socialhub.clearance")
        self.internal.name = "internal docs"
        self.internal.save()
        changed = self.records("SOCIALHUB.CLEARANCE_CHANGED").get()
        self.assertEqual(changed.metadata["changed"], ["name"])
        self.assertEqual(changed.metadata["before"]["name"], "internal")
        self.internal.save()                                      # nothing changed: nothing recorded
        self.assertEqual(self.records("SOCIALHUB.CLEARANCE_CHANGED").count(), 1)
        pk = self.internal.pk
        self.internal.delete()
        self.assertTrue(self.records("SOCIALHUB.CLEARANCE_DELETED", object_id=str(pk)).exists())

    def test_joining_and_leaving_from_either_side(self):
        self.ada.communities.add(self.devs)
        self.ada.communities.add(self.devs)                       # already in: no second record
        added = self.records("SOCIALHUB.MEMBER_ADDED").get()
        self.assertEqual(added.metadata["person"], self.ada.slug)
        self.assertNotIn("is_clearance", added.metadata)
        self.devs.members.remove(self.ada)
        self.assertEqual(self.records("SOCIALHUB.MEMBER_REMOVED").count(), 1)

    def test_holding_and_losing_a_clearance_from_either_side(self):
        self.ada.clearances.add(self.internal)
        self.ada.clearances.add(self.internal)                    # already in: no second record
        given = self.records("SOCIALHUB.CLEARANCE_MEMBER_ADDED").get()
        self.assertEqual(given.metadata["person"], self.ada.slug)
        self.assertEqual(given.metadata["clearance"], "internal")
        self.assertFalse(self.records("SOCIALHUB.MEMBER_ADDED").exists())   # not a community
        self.internal.members.remove(self.ada)
        self.assertEqual(self.records("SOCIALHUB.CLEARANCE_MEMBER_REMOVED").count(), 1)

    def test_removing_somebody_who_is_not_there_records_nothing(self):
        self.internal.members.remove(self.ada)
        self.devs.members.remove(self.ada)
        self.assertFalse(self.records("SOCIALHUB.CLEARANCE_MEMBER_REMOVED").exists())
        self.assertFalse(self.records("SOCIALHUB.MEMBER_REMOVED").exists())

    def test_a_clear_names_everybody_it_removed(self):
        other = Community.objects.create(name="testers", slug="testers")
        self.ada.communities.add(self.devs, other)
        self.ada.clearances.add(self.internal)
        self.ada.communities.clear()
        self.ada.clearances.clear()
        removed = self.records("SOCIALHUB.MEMBER_REMOVED")
        self.assertEqual({r.metadata["community"] for r in removed}, {"devs", "testers"})
        lost = self.records("SOCIALHUB.CLEARANCE_MEMBER_REMOVED")
        self.assertEqual({r.metadata["clearance"] for r in lost}, {"internal"})

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

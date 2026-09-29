"""The socialhub's audit signals, event by event (2026-09-28).

Every writer is covered because the records come from signals — so each test
here makes the change the way some writer would (a manager call from either
side, a ``set``, a ``clear``, a bulk delete) and reads what reached the chain.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_more_audit
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from toto.audit.models import AuditRecord
from toto.people.models import Person
from toto.socialhub import audit
from toto.socialhub.models import (Clearance, Community, CommunityPrivilege,
                                   MembershipApplication, ReferenceRequest)

User = get_user_model()


def make_person(name):
    return Person.objects.create(user=User.objects.create_user(name, password="pw"),
                                 display_name=name.title())


class AuditCase(TestCase):
    def setUp(self):
        self.devs = Community.objects.create(name="devs", slug="devs")
        self.testers = Community.objects.create(name="testers", slug="testers")
        self.internal = Clearance.objects.create(name="internal", slug="internal")
        self.ada = make_person("ada")
        self.bob = make_person("bob")

    def records(self, action, **filters):
        return AuditRecord.objects.filter(action=f"SOCIALHUB.{action}", **filters)


class CommunityChangeTests(AuditCase):
    def test_a_record_names_every_tracked_field_that_moved(self):
        self.devs.parent = Community.objects.create(name="toto", slug="toto")
        self.devs.head = self.ada
        self.devs.org_type = Community.COMPANY
        self.devs.save()
        changed = self.records("COMMUNITY_CHANGED", object_id=str(self.devs.pk)).get()
        self.assertEqual(changed.metadata["changed"], ["head_id", "org_type", "parent_id"])
        self.assertIsNone(changed.metadata["before"]["head_id"])
        self.assertEqual(changed.metadata["after"]["head_id"], str(self.ada.pk))
        self.assertEqual(changed.metadata["before"]["org_type"], Community.OTHER)
        self.assertEqual(changed.metadata["after"]["org_type"], Community.COMPANY)

    def test_a_field_nobody_tracks_changes_without_a_record(self):
        self.devs.email = "devs@example.com"
        self.devs.established_year = 1999
        self.devs.is_autonomous = True
        self.devs.save()
        self.assertFalse(self.records("COMMUNITY_CHANGED").exists())

    def test_a_speed_saved_again_at_the_same_value_records_nothing(self):
        self.internal.regen_security = Decimal("8")
        self.internal.save()
        self.internal.refresh_from_db()                # Decimal('8.0000') now
        self.internal.regen_security = Decimal("8")
        self.internal.save()
        self.assertEqual(self.records("CLEARANCE_CHANGED").count(), 1)

    def test_a_speed_cleared_is_recorded_as_none_after(self):
        self.internal.regen_compute = Decimal("2.5")
        self.internal.save()
        self.internal.regen_compute = None
        self.internal.save()
        cleared = self.records("CLEARANCE_CHANGED").order_by("-sequence").first()
        self.assertEqual(Decimal(cleared.metadata["before"]["regen_compute"]), Decimal("2.5"))
        self.assertIsNone(cleared.metadata["after"]["regen_compute"])

    def test_a_created_record_names_the_parent(self):
        child = Community.objects.create(name="devs-api", slug="devs-api", parent=self.devs,
                                         org_type=Community.GUILD)
        made = self.records("COMMUNITY_CREATED", object_id=str(child.pk)).get()
        self.assertEqual(made.metadata["parent"], self.devs.pk)
        self.assertEqual(made.metadata["org_type"], Community.GUILD)
        self.assertNotIn("is_clearance", made.metadata)
        self.assertEqual(made.object_type, "socialhub.community")

    def test_a_created_clearance_names_its_speeds(self):
        fast = Clearance.objects.create(name="restricted", slug="restricted",
                                        regen_compute=Decimal("12"))
        made = self.records("CLEARANCE_CREATED", object_id=str(fast.pk)).get()
        self.assertEqual(made.metadata["clearance"], "restricted")
        self.assertEqual(Decimal(made.metadata["speeds"]["compute"]), Decimal("12"))
        self.assertNotIn("security", made.metadata["speeds"])
        self.assertEqual(made.object_type, "socialhub.clearance")


class MembershipSideTests(AuditCase):
    def test_a_clearance_cleared_from_its_own_side_names_everybody_who_left(self):
        self.internal.members.add(self.ada, self.bob)
        self.internal.members.clear()
        removed = self.records("CLEARANCE_MEMBER_REMOVED")
        self.assertEqual({r.metadata["person"] for r in removed}, {self.ada.slug, self.bob.slug})
        self.assertTrue(all(r.metadata["clearance"] == "internal" for r in removed))
        self.assertTrue(all(r.object_id == str(self.internal.pk) for r in removed))
        self.assertFalse(self.records("MEMBER_REMOVED").exists())

    def test_clearing_an_empty_list_records_nothing(self):
        self.internal.members.clear()
        self.devs.members.clear()
        self.ada.communities.clear()
        self.ada.clearances.clear()
        self.assertFalse(self.records("MEMBER_REMOVED").exists())
        self.assertFalse(self.records("CLEARANCE_MEMBER_REMOVED").exists())

    def test_set_records_the_ones_that_left_and_the_ones_that_came(self):
        self.ada.communities.add(self.devs)
        self.ada.communities.set([self.testers])
        self.assertEqual(list(self.records("MEMBER_REMOVED").values_list("metadata__community",
                                                                         flat=True)), ["devs"])
        added = self.records("MEMBER_ADDED").order_by("sequence")
        self.assertEqual([r.metadata["community"] for r in added], ["devs", "testers"])

    def test_set_on_the_clearance_side_records_the_same_way(self):
        confidential = Clearance.objects.create(name="confidential", slug="confidential")
        self.ada.clearances.add(self.internal)
        self.ada.clearances.set([confidential])
        self.assertEqual(list(self.records("CLEARANCE_MEMBER_REMOVED")
                              .values_list("metadata__clearance", flat=True)), ["internal"])
        given = self.records("CLEARANCE_MEMBER_ADDED").order_by("sequence")
        self.assertEqual([r.metadata["clearance"] for r in given], ["internal", "confidential"])

    def test_removing_somebody_from_their_own_side_who_is_not_a_member_records_nothing(self):
        self.ada.clearances.remove(self.internal)
        self.ada.communities.remove(self.devs)
        self.assertFalse(self.records("CLEARANCE_MEMBER_REMOVED").exists())
        self.assertFalse(self.records("MEMBER_REMOVED").exists())

    def test_a_partial_remove_names_only_who_was_really_there(self):
        self.internal.members.add(self.ada)
        self.internal.members.remove(self.ada, self.bob)
        removed = self.records("CLEARANCE_MEMBER_REMOVED")
        self.assertEqual([r.metadata["person"] for r in removed], [self.ada.slug])

    def test_a_member_record_carries_who_they_are(self):
        self.internal.members.add(self.ada)
        added = self.records("CLEARANCE_MEMBER_ADDED").get()
        self.assertEqual(added.metadata["display_name"], "Ada")
        self.assertEqual(added.metadata["user"], self.ada.user_id)
        self.assertEqual(added.metadata["name"], "internal")
        self.devs.members.add(self.ada)
        added = self.records("MEMBER_ADDED").get()
        self.assertEqual(added.metadata["display_name"], "Ada")
        self.assertEqual(added.metadata["name"], "devs")


class SeniorTests(AuditCase):
    def test_seniors_cleared_from_the_community_side(self):
        self.devs.senior_members.add(self.ada, self.bob)
        self.assertEqual(self.records("SENIOR_ADDED").count(), 2)
        self.devs.senior_members.clear()
        self.assertEqual({r.metadata["person"] for r in self.records("SENIOR_REMOVED")},
                         {self.ada.slug, self.bob.slug})

    def test_seniors_added_from_the_person_side_name_the_community(self):
        self.ada.senior_communities.add(self.devs)
        added = self.records("SENIOR_ADDED").get()
        self.assertEqual(added.metadata["community"], "devs")
        self.assertEqual(added.metadata["person"], self.ada.slug)
        self.ada.senior_communities.clear()
        self.assertEqual(self.records("SENIOR_REMOVED").get().metadata["community"], "devs")

    def test_a_senior_is_not_a_member(self):
        self.devs.senior_members.add(self.ada)
        self.assertFalse(self.records("MEMBER_ADDED").exists())


class PrivilegeTests(AuditCase):
    def test_a_grant_records_every_right_as_it_stands(self):
        privilege = CommunityPrivilege.objects.create(community=self.devs,
                                                      may_operate_mint=True)
        grants = self.records("PRIVILEGE_CHANGED").get().metadata["grants"]
        self.assertEqual(grants, {"may_see_community_chain": False,
                                  "may_administer_communities": False,
                                  "may_manage_community_news": False,
                                  "may_operate_mint": True})
        privilege.may_operate_mint = False
        privilege.save()
        latest = self.records("PRIVILEGE_CHANGED").order_by("-sequence").first()
        self.assertFalse(latest.metadata["grants"]["may_operate_mint"])

    def test_a_grant_removed_is_recorded(self):
        CommunityPrivilege.objects.create(community=self.devs).delete()
        self.assertEqual(self.records("PRIVILEGE_REMOVED").get().object_id, str(self.devs.pk))

    def test_removing_a_granting_community_records_both(self):
        CommunityPrivilege.objects.create(community=self.devs)
        pk = self.devs.pk
        self.devs.delete()
        self.assertTrue(self.records("COMMUNITY_DELETED", object_id=str(pk)).exists())
        self.assertTrue(self.records("PRIVILEGE_REMOVED", object_id=str(pk)).exists())


class ApplicationTests(AuditCase):
    def setUp(self):
        super().setUp()
        self.application = MembershipApplication.objects.create(
            email="newbie@example.com", community=self.devs,
            expires_at=timezone.now() + timedelta(days=3))

    def test_every_status_the_application_moves_to_is_named(self):
        for status in ("verified", "endorsed", "invited"):
            self.application.status = status
            self.application.save()
            record = self.records(f"APPLICATION_{status.upper()}").get()
            self.assertEqual(record.metadata["status"], status)
        self.assertEqual(
            self.records("APPLICATION_INVITED").get().metadata["before"], "endorsed")

    def test_saving_without_a_status_change_records_nothing(self):
        self.application.code = "999999"
        self.application.save()
        self.assertEqual(AuditRecord.objects.filter(
            action__startswith="SOCIALHUB.APPLICATION_").count(), 1)       # the submission

    def test_the_submission_names_the_email_and_the_community(self):
        record = self.records("APPLICATION_SUBMITTED").get()
        self.assertEqual(record.object_type, "socialhub.membershipapplication")
        self.assertEqual(record.metadata["email"], "newbie@example.com")
        self.assertNotIn("is_clearance", record.metadata)
        self.assertIsNone(record.metadata["before"])

    def test_a_reference_moved_back_to_pending_or_saved_again_records_nothing(self):
        ref = ReferenceRequest.objects.create(application=self.application, referrer=self.ada)
        ref.message = "I vouch."
        ref.save()
        ref.status = "declined"
        ref.save()
        ref.save()
        ref.status = "pending"
        ref.save()
        self.assertEqual(self.records("REFERENCE_REQUESTED").count(), 1)
        self.assertEqual(self.records("REFERENCE_DECLINED").count(), 1)
        declined = self.records("REFERENCE_DECLINED").get()
        self.assertFalse(declined.metadata["admitted"])
        self.assertEqual(declined.metadata["referrer_name"], "Ada")


class WithoutAChainTests(AuditCase):
    def test_nothing_is_recorded_where_audit_is_not_installed(self):
        before = AuditRecord.objects.count()
        with mock.patch.object(audit, "installed", return_value=False):
            self.internal.members.add(self.ada)
            self.devs.members.add(self.ada)
            Community.objects.create(name="quiet", slug="quiet")
            Clearance.objects.create(name="quieter", slug="quieter")
        self.assertEqual(AuditRecord.objects.count(), before)
        self.assertIn(self.internal, self.ada.clearances.all())
        self.assertIn(self.devs, self.ada.communities.all())

    def test_a_record_that_cannot_be_written_never_fails_the_change(self):
        with mock.patch("toto.audit.services.record", side_effect=RuntimeError("disk full")), \
                self.assertLogs("toto.socialhub", "ERROR") as logged:
            self.internal.members.add(self.ada)
            self.devs.members.add(self.ada)
        self.assertIn(self.internal, self.ada.clearances.all())
        self.assertIn(self.devs, self.ada.communities.all())
        self.assertIn("socialhub.clearance_member_added", "\n".join(logged.output))
        self.assertIn("socialhub.member_added", "\n".join(logged.output))
        self.assertFalse(self.records("CLEARANCE_MEMBER_ADDED").exists())
        self.assertFalse(self.records("MEMBER_ADDED").exists())

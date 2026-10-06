"""Companies on the audit chain (stage 65): every change of an ID number
and of a holding writes one record with who made it; a refusal and a save
that changes nothing write none.

    manage.py test toto.companies.tests_audit
"""

from unittest import mock, skipUnless

from django.apps import apps

from toto.companies import audit
from toto.companies.models import CompanyRecord, ShareHolding
from toto.companies.testing import CompaniesTestCase, audit_records, client_of


@skipUnless(apps.is_installed("toto.audit"), "no audit chain on this host")
class AuditTests(CompaniesTestCase):
    def actions(self):
        return [record.action for record in audit_records()]

    def number(self, typed, user=None):
        return client_of(user or self.head_user).post(self.number_url(self.acme),
                                                      {"id_number": typed})

    # -- the ID number -----------------------------------------------------

    def test_setting_the_number_is_recorded_with_before_and_after(self):
        self.number("0001")
        self.number("0002", user=self.root_user)
        first, second = audit_records()
        self.assertEqual((first.action, second.action), (audit.NUMBER_CHANGED,) * 2)
        self.assertEqual(first.metadata, {"community": "acme", "before": "", "after": "0001"})
        self.assertEqual(second.metadata,
                         {"community": "acme", "before": "0001", "after": "0002"})
        self.assertEqual(first.actor_user, self.head_user)
        self.assertEqual(second.actor_user, self.root_user)
        self.assertEqual((first.app_label, first.object_type, first.object_id),
                         ("companies", "socialhub.community", str(self.acme.pk)))
        self.assertTrue(first.success)

    def test_clearing_the_number_is_recorded(self):
        self.number("0001")
        self.number("")
        self.assertEqual(audit_records()[-1].metadata,
                         {"community": "acme", "before": "0001", "after": ""})

    def test_saving_the_same_number_records_nothing(self):
        self.number("0001")
        self.number("0001")
        self.number("  0001 ")
        self.assertEqual(self.actions(), [audit.NUMBER_CHANGED])

    def test_a_refused_number_records_nothing(self):
        self.number("1" * 65)
        self.number("1\n2")
        self.number("0001", user=self.mia_user)
        self.number("0001", user=self.stef_user)
        self.assertEqual(self.actions(), [])

    # -- holdings ----------------------------------------------------------

    def test_a_holding_recorded_changed_and_removed(self):
        self.record(self.acme, self.hold, 10)
        holding = ShareHolding.objects.get()
        self.record(self.acme, self.hold, 25, user=self.root_user)
        client_of(self.head_user).post(self.delete_url(self.acme, holding.pk))
        recorded, changed, removed = audit_records()
        self.assertEqual([recorded.action, changed.action, removed.action],
                         [audit.HOLDING_RECORDED, audit.HOLDING_CHANGED,
                          audit.HOLDING_REMOVED])
        base = {"community": "acme", "person": "hold", "holding": holding.pk}
        self.assertEqual(recorded.metadata, {**base, "after": 10})
        self.assertEqual(changed.metadata, {**base, "before": 10, "after": 25})
        self.assertEqual(removed.metadata, {**base, "before": 25})
        self.assertEqual([recorded.actor_user, changed.actor_user, removed.actor_user],
                         [self.head_user, self.root_user, self.head_user])
        for record in (recorded, changed, removed):
            self.assertEqual((record.app_label, record.object_type, record.object_id),
                             ("companies", "companies.shareholding", str(holding.pk)))

    def test_a_zero_holding_is_recorded_with_its_zero(self):
        self.record(self.acme, self.hold, 0)
        self.assertEqual(audit_records()[0].metadata["after"], 0)

    def test_the_same_quantity_again_records_nothing(self):
        self.record(self.acme, self.hold, 10)
        self.record(self.acme, self.hold, 10)
        self.assertEqual(self.actions(), [audit.HOLDING_RECORDED])

    def test_a_refused_holding_records_nothing(self):
        self.record(self.acme, self.hold, "1.5")
        self.record(self.acme, self.hold, "-1")
        self.record(self.acme, self.hold, 5, user=self.mia_user)
        self.record(self.acme, self.hold, 5, user=self.hold_user)
        self.record(self.guild, self.hold, 5)
        other = self.hold_shares(self.globex, self.stan, 3)
        client_of(self.head_user).post(self.delete_url(self.acme, other.pk))
        self.assertEqual(self.actions(), [])

    def test_each_company_is_named_in_its_own_records(self):
        self.record(self.acme, self.hold, 1)
        self.record(self.globex, self.hold, 2, user=self.other_head_user)
        self.assertEqual([record.metadata["community"] for record in audit_records()],
                         ["acme", "globex"])

    # -- the chain never breaks a door -------------------------------------

    def test_a_record_that_cannot_be_written_does_not_undo_the_change(self):
        with mock.patch("toto.audit.services.record", side_effect=RuntimeError("chain down")):
            with self.assertLogs("toto.companies.audit", level="ERROR"):
                self.assertEqual(self.record(self.acme, self.hold, 10).status_code, 302)
            with self.assertLogs("toto.companies.audit", level="ERROR"):
                self.assertEqual(self.number("0001").status_code, 302)
        self.assertEqual(ShareHolding.objects.get().quantity, 10)
        self.assertEqual(CompanyRecord.objects.get().id_number, "0001")

    def test_the_four_actions_are_all_this_app_writes(self):
        self.assertEqual(set(audit.ALL), {
            "COMPANIES.NUMBER.CHANGED", "COMPANIES.HOLDING.RECORDED",
            "COMPANIES.HOLDING.CHANGED", "COMPANIES.HOLDING.REMOVED"})
        self.number("0001")
        self.record(self.acme, self.hold, 1)
        self.record(self.acme, self.hold, 2)
        client_of(self.head_user).post(
            self.delete_url(self.acme, ShareHolding.objects.get().pk))
        self.assertEqual(set(self.actions()), set(audit.ALL))


class WithoutTheChainTests(CompaniesTestCase):
    def test_a_host_without_the_chain_records_nothing_and_still_saves(self):
        with mock.patch.object(audit, "installed", return_value=False):
            self.assertIsNone(audit.number_changed(self.head_user, self.acme,
                                                   before="", after="1"))
            self.assertEqual(self.record(self.acme, self.hold, 4).status_code, 302)
        self.assertEqual(ShareHolding.objects.get().quantity, 4)
        self.assertEqual(audit_records(), [])

"""The sample company the seed makes (2026-10-06): ``gizmo.inc``, in
realistic and full alike, held by persons that exist and by nobody the seed
invented; made once, and then left as an administrator leaves it.

    manage.py test toto.companies.tests_ingress
"""

import io
import os
from decimal import Decimal
from unittest import mock, skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import TestCase

from toto.companies import access
from toto.companies.management.commands import ingress_companies as seed
from toto.companies.models import CompanyRecord, ShareHolding
from toto.companies.register import register_of
from toto.companies.testing import audit_records, community, member
from toto.core.models import BootstrapMarker, Platform
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.socialhub.permissions import is_community_member

User = get_user_model()
D = Decimal


def run(mode="realistic", **environment) -> str:
    out = io.StringIO()
    with mock.patch.dict(os.environ, environment):
        call_command("ingress_companies", mode=mode, stdout=out, stderr=out)
    return out.getvalue()


def gizmo():
    return Community.objects.filter(name=seed.NAME).first()


def register() -> dict:
    """Who holds what in the sample company, by the holder's slug."""
    return dict(ShareHolding.objects.filter(community__name=seed.NAME)
                .values_list("person__slug", "quantity"))


def rows() -> dict:
    """Every row a run could make or take away."""
    counted = {model._meta.label: model.objects.count()
               for model in (Community, CompanyRecord, ShareHolding, Person, User,
                             BootstrapMarker, Permission)}
    counted["memberships"] = Person.communities.through.objects.count()
    counted["user permissions"] = User.user_permissions.through.objects.count()
    counted["groups of users"] = User.groups.through.objects.count()
    counted["companies audit records"] = len(audit_records())
    return counted


def administrator(username="admin"):
    """The platform's admin with a person: a superuser, on the Superuser
    plan where the host sells it."""
    bare, person = member(username, name="Founder", is_superuser=True, is_staff=True)
    if apps.is_installed("toto.subscriptions"):
        call_command("bootstrap_plans", stdout=io.StringIO())
    return User.objects.get(pk=bare.pk), person


class SeedTestCase(TestCase):
    """A platform, and the economy every ingress command makes sure of
    already there, so what a test counts is this seed's alone."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "A", "publication_year": 2026})
        run("realistic", SEED_SAMPLE_COMPANY="0")


class TheSampleCompanyTests(SeedTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ann_user, cls.ann = member("ann")
        cls.bob_user, cls.bob = member("bob")
        cls.admin_user, cls.admin = administrator()       # made last: the highest key
        cls.cy_user, cls.cy = member("cy")
        cls.dee_user, cls.dee = member("dee")

    def test_realistic_makes_the_company_with_its_record_and_holdings(self):
        before = rows()
        out = run("realistic")
        company = gizmo()
        self.assertEqual(company.name, "gizmo.inc")
        self.assertEqual(company.slug, "gizmoinc")
        self.assertEqual(company.org_type, Community.COMPANY)
        self.assertTrue(access.is_company(company))
        self.assertEqual(company.company_record.id_number, "0000123456")
        self.assertEqual(register(), {"admin": 500, "ann": 300, "bob": 150, "cy": 50})
        after = rows()
        for label in ("people.Person", "auth.User", "auth.Permission", "memberships",
                      "user permissions", "groups of users"):
            self.assertEqual(after[label], before[label], label)
        self.assertEqual(after["socialhub.Community"], before["socialhub.Community"] + 1)
        self.assertEqual(after["companies.CompanyRecord"], 1)
        self.assertEqual(after["companies.ShareHolding"], 4)
        for line in ("Created sample company: gizmo.inc (slug gizmoinc, head: Founder)",
                     "Company ID number of gizmo.inc: 0000123456",
                     "Founder holds 500 shares of gizmo.inc",
                     "Ann holds 300 shares of gizmo.inc",
                     "Bob holds 150 shares of gizmo.inc",
                     "Cy holds 50 shares of gizmo.inc"):
            self.assertIn(line, out)
        self.assertNotIn("Dee", out)

    def test_full_makes_it_too(self):
        run("full")
        self.assertEqual(register(), {"admin": 500, "ann": 300, "bob": 150, "cy": 50})
        self.assertEqual(gizmo().company_record.id_number, "0000123456")

    def test_none_makes_nothing(self):
        before = rows()
        out = run("none")
        self.assertEqual(rows(), before)
        self.assertIsNone(gizmo())
        self.assertIn("skipped (ingress mode none)", out)

    def test_the_switch_keeps_it_out(self):
        before = rows()
        for word in ("0", "false", "off", "no"):
            with self.subTest(word=word):
                self.assertIn("no sample company", run("realistic", SEED_SAMPLE_COMPANY=word))
                self.assertEqual(rows(), before)
        run("realistic", SEED_SAMPLE_COMPANY="1")
        self.assertIsNotNone(gizmo())

    def test_the_percentages_add_up(self):
        run()
        book = register_of(gizmo())
        self.assertEqual(book.total, 1000)
        self.assertEqual(book.holders, 4)
        self.assertEqual([(row.holding.person.slug, row.quantity, row.percentage)
                          for row in book.rows],
                         [("admin", 500, D("50.00")), ("ann", 300, D("30.00")),
                          ("bob", 150, D("15.00")), ("cy", 50, D("5.00"))])
        self.assertEqual(sum(row.percentage for row in book.rows), D("100.00"))

    def test_a_second_run_changes_nothing(self):
        run()
        before, held = rows(), register()
        stamps = list(ShareHolding.objects.order_by("pk").values_list("pk", "updated_at"))
        out = run()
        self.assertEqual(rows(), before)
        self.assertEqual(register(), held)
        self.assertEqual(list(ShareHolding.objects.order_by("pk")
                              .values_list("pk", "updated_at")), stamps)
        self.assertIn("was settled at an earlier start (seeded); nothing to do", out)
        self.assertNotIn("✔", out)

    def test_what_an_administrator_changed_survives_the_next_start(self):
        run()
        company = gizmo()
        ShareHolding.objects.filter(community=company, person=self.ann).update(quantity=7)
        ShareHolding.objects.filter(community=company, person=self.bob).delete()
        CompanyRecord.objects.filter(community=company).update(id_number="KRS 0000999")
        member("late")                                   # joined after the seed
        before = rows()
        run()
        run("full")
        self.assertEqual(rows(), before)
        self.assertEqual(register(), {"admin": 500, "ann": 7, "cy": 50})
        self.assertEqual(gizmo().company_record.id_number, "KRS 0000999")

    def test_a_deleted_company_stays_deleted(self):
        run()
        gizmo().delete()
        before = rows()
        run()
        self.assertIsNone(gizmo())
        self.assertEqual(rows(), before)
        self.assertEqual(ShareHolding.objects.count(), 0)

    def test_before_its_mark_it_creates_what_is_missing_and_updates_nothing(self):
        """A company of that name made by hand, half filled in."""
        company = community("gizmo.inc", head=self.ann)
        CompanyRecord.objects.create(community=company, id_number="by hand")
        ShareHolding.objects.create(community=company, person=self.ann, quantity=9)
        out = run()
        self.assertEqual(Community.objects.filter(name="gizmo.inc").count(), 1)
        company.refresh_from_db()
        self.assertEqual(company.head, self.ann)
        self.assertEqual(company.company_record.id_number, "by hand")
        self.assertEqual(register(), {"admin": 500, "ann": 9, "bob": 150, "cy": 50})
        self.assertIn("Company already exists: gizmo.inc", out)
        self.assertNotIn("Ann holds", out)
        before = rows()
        run()
        self.assertEqual(rows(), before)

    def test_a_community_of_that_name_that_is_no_company_is_left_alone(self):
        guild = community("gizmo.inc", head=self.ann, org_type=Community.GUILD)
        before = rows()
        out = run()
        self.assertIn("A community named gizmo.inc exists and is no company: left alone", out)
        guild.refresh_from_db()
        self.assertEqual(guild.org_type, Community.GUILD)
        self.assertEqual(guild.head, self.ann)
        self.assertEqual(Community.objects.filter(name="gizmo.inc").count(), 1)
        self.assertFalse(CompanyRecord.objects.exists())
        self.assertFalse(ShareHolding.objects.exists())
        after = rows()
        self.assertEqual(after.pop("core.BootstrapMarker"), before.pop("core.BootstrapMarker") + 1)
        self.assertEqual(after, before)
        # ... and for good: made a company later, it is still theirs alone.
        Community.objects.filter(pk=guild.pk).update(org_type=Community.COMPANY)
        self.assertIn("(name taken); nothing to do", run())
        self.assertFalse(ShareHolding.objects.exists())

    def test_nobody_is_made_a_member_and_nobody_gains_a_say(self):
        run()
        company = gizmo()
        self.assertEqual(company.members.count(), 0)
        self.assertEqual(company.senior_members.count(), 0)
        self.assertEqual(company.head, self.admin)
        for user in (self.ann_user, self.bob_user, self.cy_user, self.dee_user):
            with self.subTest(user=user.username):
                self.assertFalse(is_community_member(user, company))
                self.assertFalse(access.may_manage(user, company))
                self.assertTrue(access.may_see_register(user, company))
                fresh = User.objects.get(pk=user.pk)
                self.assertFalse(fresh.is_staff or fresh.is_superuser)
                self.assertFalse(fresh.get_all_permissions())
        self.assertTrue(access.may_manage(self.admin_user, company))

    def test_a_person_without_an_active_account_holds_nothing(self):
        User.objects.filter(pk=self.ann_user.pk).update(is_active=False)
        Person.objects.create(display_name="Nobody's", slug="nobodys")     # no account
        run()
        self.assertEqual(register(), {"admin": 500, "bob": 300, "cy": 150, "dee": 50})

    @skipUnless(apps.is_installed("toto.audit"), "no audit chain on this host")
    def test_the_chain_gets_the_records_with_no_actor(self):
        run()
        records = audit_records()
        self.assertEqual([record.action for record in records],
                         ["COMPANIES.NUMBER.CHANGED"] + ["COMPANIES.HOLDING.RECORDED"] * 4)
        self.assertEqual({record.actor_user_id for record in records}, {None})
        self.assertEqual(records[0].metadata,
                         {"community": "gizmoinc", "before": "", "after": "0000123456"})
        self.assertEqual([(record.metadata["person"], record.metadata["after"])
                          for record in records[1:]],
                         [("admin", 500), ("ann", 300), ("bob", 150), ("cy", 50)])
        run()
        self.assertEqual(len(audit_records()), 5)


class FewPersonsTests(SeedTestCase):
    def test_one_person_alone_holds_a_thousand(self):
        administrator()
        before = rows()
        out = run()
        self.assertEqual(register(), {"admin": 1000})
        book = register_of(gizmo())
        self.assertEqual((book.total, book.rows[0].percentage), (1000, D("100.00")))
        self.assertEqual(rows()["people.Person"], before["people.Person"])
        self.assertEqual(rows()["auth.User"], before["auth.User"])
        self.assertIn("Founder holds 1000 shares of gizmo.inc", out)

    def test_two_persons_hold_the_first_two_quantities(self):
        administrator()
        member("ann")
        run()
        self.assertEqual(register(), {"admin": 500, "ann": 300})
        self.assertEqual([row.percentage for row in register_of(gizmo()).rows],
                         [D("62.50"), D("37.50")])

    def test_no_person_at_all_is_a_company_with_an_empty_register(self):
        self.assertFalse(Person.objects.exists())
        out = run()
        company = gizmo()
        self.assertTrue(access.is_company(company))
        self.assertIsNone(company.head)
        self.assertEqual(company.company_record.id_number, "0000123456")
        self.assertTrue(register_of(company).empty)
        self.assertEqual(register_of(company).rows, [])
        self.assertFalse(Person.objects.exists())
        self.assertFalse(User.objects.exists())
        self.assertIn("the register of gizmo.inc starts empty", out)
        # The persons who come later are given nothing.
        member("ann")
        run()
        self.assertFalse(ShareHolding.objects.exists())

    def test_the_admin_under_another_name(self):
        member("ann")
        administrator("founder")
        run(ADMIN_USERNAME="founder")
        self.assertEqual(register(), {"founder": 500, "ann": 300})
        self.assertEqual(gizmo().head.slug, "founder")

    def test_an_admin_account_that_is_no_administrator_heads_nothing(self):
        """Staff alone: the first holder, and no head, so no say."""
        user, person = member("admin", is_staff=True)
        run()
        company = gizmo()
        self.assertIsNone(company.head)
        self.assertEqual(register(), {"admin": 1000})
        self.assertFalse(access.may_manage(user, company))

    def test_with_no_admin_the_first_person_by_key_comes_first(self):
        member("ann")
        member("bob")
        run()
        self.assertIsNone(gizmo().head)
        self.assertEqual(register(), {"ann": 500, "bob": 300})

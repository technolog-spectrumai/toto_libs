"""``manage.py community_members`` — the console door into communities and
clearances (2026-09-29), which ``deploy.py <config> communities`` / ``join`` and
``tools/create_user.py`` drive over SSH.

It answers in one JSON line and exits 1 on a refusal with nothing changed;
communities and clearances are named by separate flags so a typo cannot land
somebody in the wrong kind.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_more_members_command
"""

import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from toto.audit.models import AuditRecord
from toto.people.models import Person
from toto.socialhub.models import Clearance, Community

User = get_user_model()


class CommandCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.devs = Community.objects.create(name="devs", slug="devs")
        cls.testers = Community.objects.create(name="testers", slug="testers")
        cls.internal = Clearance.objects.create(name="internal", slug="internal")
        cls.confidential = Clearance.objects.create(name="confidential", slug="confidential")
        cls.ada = User.objects.create_user("ada", "ada@example.com", "pw",
                                           first_name="Ada", last_name="Lovelace")

    def run_command(self, *args):
        out = StringIO()
        call_command("community_members", *args, stdout=out)
        return json.loads(out.getvalue())

    def refused(self, *args):
        out = StringIO()
        with self.assertRaises(SystemExit) as caught:
            call_command("community_members", *args, stdout=out)
        self.assertEqual(caught.exception.code, 1)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        return payload["error"]


class ListTests(CommandCase):
    def test_the_two_kinds_are_listed_apart_with_their_member_counts(self):
        ada = Person.objects.create(user=self.ada, display_name="Ada")
        ada.communities.add(self.devs)
        ada.clearances.add(self.internal)
        payload = self.run_command("list")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["communities"], [
            {"slug": "devs", "name": "devs", "members": 1},
            {"slug": "testers", "name": "testers", "members": 0}])
        self.assertEqual(payload["clearances"], [
            {"slug": "confidential", "name": "confidential", "members": 0},
            {"slug": "internal", "name": "internal", "members": 1}])

    def test_the_answer_is_one_json_line(self):
        out = StringIO()
        call_command("community_members", "list", stdout=out)
        self.assertEqual(len(out.getvalue().strip().splitlines()), 1)


class JoinTests(CommandCase):
    def test_an_account_without_a_person_gets_one_and_joins_both_kinds(self):
        payload = self.run_command("join", "ada", "--community", "devs",
                                   "--clearance", "internal", "--clearance", "confidential")
        self.assertEqual(payload, {"ok": True, "username": "ada", "person_created": True,
                                   "communities": ["devs"],
                                   "clearances": ["confidential", "internal"]})
        person = Person.objects.get(user=self.ada)
        self.assertEqual(person.display_name, "Ada Lovelace")
        self.assertEqual(person.email, "ada@example.com")
        self.assertEqual(set(person.communities.all()), {self.devs})
        self.assertEqual(set(person.clearances.all()), {self.internal, self.confidential})

    def test_a_nameless_account_is_named_by_its_username(self):
        User.objects.create_user("plain")
        self.run_command("join", "plain", "--community", "testers")
        self.assertEqual(Person.objects.get(user__username="plain").display_name, "plain")

    def test_joining_again_changes_nothing_and_keeps_the_person(self):
        self.run_command("join", "ada", "--community", "devs")
        payload = self.run_command("join", "ada", "--community", "devs")
        self.assertFalse(payload["person_created"])
        self.assertEqual(Person.objects.filter(user=self.ada).count(), 1)
        self.assertEqual(AuditRecord.objects.filter(action="SOCIALHUB.MEMBER_ADDED").count(), 1)

    def test_every_membership_reaches_the_chain(self):
        self.run_command("join", "ada", "--community", "devs", "--clearance", "internal")
        joined = AuditRecord.objects.filter(action="SOCIALHUB.MEMBER_ADDED")
        self.assertEqual([r.metadata["community"] for r in joined], ["devs"])
        given = AuditRecord.objects.filter(action="SOCIALHUB.CLEARANCE_MEMBER_ADDED")
        self.assertEqual([r.metadata["clearance"] for r in given], ["internal"])

    def test_an_existing_membership_is_kept(self):
        person = Person.objects.create(user=self.ada, display_name="Ada")
        person.communities.add(self.testers)
        self.run_command("join", "ada", "--clearance", "confidential")
        self.assertEqual(set(person.communities.all()), {self.testers})
        self.assertEqual(set(person.clearances.all()), {self.confidential})


class RefusalTests(CommandCase):
    def assertNothingChanged(self):
        self.assertFalse(Person.objects.filter(user=self.ada).exists())
        self.assertFalse(AuditRecord.objects.filter(
            action__in=("SOCIALHUB.MEMBER_ADDED", "SOCIALHUB.CLEARANCE_MEMBER_ADDED")).exists())

    def test_join_needs_a_username(self):
        self.assertIn("needs a username", self.refused("join", "--community", "devs"))

    def test_an_unknown_account_is_refused(self):
        self.assertIn("no account called 'nobody'",
                      self.refused("join", "nobody", "--community", "devs"))

    def test_a_clearance_named_as_a_community_is_refused(self):
        error = self.refused("join", "ada", "--community", "internal")
        self.assertIn("community 'internal'", error)
        self.assertNothingChanged()

    def test_a_community_named_as_a_clearance_is_refused(self):
        error = self.refused("join", "ada", "--clearance", "devs")
        self.assertIn("clearance 'devs'", error)
        self.assertNothingChanged()

    def test_one_unknown_name_refuses_the_whole_join_and_names_every_miss(self):
        error = self.refused("join", "ada", "--community", "devs", "--community", "ghosts",
                             "--clearance", "internal", "--clearance", "cabal")
        self.assertIn("community 'ghosts'", error)
        self.assertIn("clearance 'cabal'", error)
        self.assertIn("communities` lists them", error)
        self.assertNothingChanged()

    def test_an_unknown_action_is_refused_by_the_parser(self):
        from django.core.management import CommandError

        with self.assertRaises(CommandError):
            call_command("community_members", "leave", "ada", stdout=StringIO())

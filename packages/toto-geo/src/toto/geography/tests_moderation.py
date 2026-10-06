"""Moderation (stage 64, 2026-10-06): a community's head or an administrator
hides, restores or deletes any pin or zone of the community and withdraws any
comment under them; never edits another member's words. The author sees
their own hidden row with a line saying so. Each act is on the audit chain.

    manage.py test toto.geography.tests_moderation
"""

from django.apps import apps

from toto.geography.models import CommunityPin, CommunityZone
from toto.geography.testing import client_of, post
from toto.geography.locations_testing import LocationsCase


class ModerationTests(LocationsCase):
    def setUp(self):
        super().setUp()
        self.row = self.pin()
        self.area = self.zone()

    def act(self, user, name, row=None):
        return post(client_of(user), self.url(name, row or self.row), {})

    def test_the_head_hides_and_restores(self):
        self.assertEqual(self.act(self.head_user, "pin_hide").json(), {"changed": True})
        self.row.refresh_from_db()
        self.assertTrue(self.row.is_hidden)
        self.assertEqual(self.row.hidden_by, self.head_user)
        self.assertEqual(self.act(self.head_user, "pin_hide").json(), {"changed": False})
        self.assertEqual(self.act(self.head_user, "pin_restore").json(), {"changed": True})
        self.row.refresh_from_db()
        self.assertFalse(self.row.is_hidden)
        self.assertEqual(self.act(self.head_user, "zone_hide", self.area).json(),
                         {"changed": True})

    def test_an_administrator_moderates_every_community(self):
        self.assertEqual(self.act(self.root, "pin_hide").status_code, 200)
        self.assertEqual(self.act(self.root, "zone_delete", self.area).json(), {"removed": True})
        self.assertFalse(CommunityZone.objects.exists())

    def test_who_does_not_moderate(self):
        # A senior member and the author are members: 403. Staff without the
        # plan and the head of another community do not belong: 403 too.
        for user in (self.senior_user, self.member_user, self.staff_user, self.other_user):
            with self.subTest(user=user.username):
                self.assertEqual(self.act(user, "pin_hide").status_code, 403)
                self.assertEqual(self.act(user, "pin_restore").status_code, 403)
        for user in (self.senior_user, self.staff_user, self.other_user):
            self.assertEqual(self.act(user, "pin_delete").status_code, 403)
        self.row.refresh_from_db()
        self.assertFalse(self.row.is_hidden)
        self.assertEqual(CommunityPin.objects.count(), 1)

    def test_the_head_deletes_another_member_s_pin(self):
        self.assertEqual(self.act(self.head_user, "pin_delete").json(), {"removed": True})
        self.assertFalse(CommunityPin.objects.exists())

    def test_the_author_sees_their_hidden_row_with_a_line_saying_so(self):
        self.act(self.head_user, "pin_hide")
        own = client_of(self.member_user).get(self.url("pin_detail", self.row))
        self.assertEqual(own.status_code, 200)
        self.assertIn("geography-hidden-line", own.content.decode())
        self.assertNotIn('data-geo-act="restore"', own.content.decode())
        head = client_of(self.head_user).get(self.url("pin_detail", self.row)).content.decode()
        self.assertIn('data-geo-act="restore"', head)
        self.assertEqual(client_of(self.senior_user).get(
            self.url("pin_detail", self.row)).status_code, 404)
        page = client_of(self.senior_user).get(self.page_url).content.decode()
        self.assertNotIn(str(self.row.uid), page)

    def test_each_act_is_on_the_audit_chain_without_coordinates(self):
        if not apps.is_installed("toto.audit"):
            self.skipTest("this host keeps no audit chain")
        from toto.audit.models import AuditRecord

        self.act(self.head_user, "pin_hide")
        self.act(self.head_user, "pin_restore")
        self.act(self.head_user, "pin_delete")
        records = AuditRecord.objects.filter(action__startswith="GEOGRAPHY.PIN.")
        self.assertEqual(sorted(records.values_list("action", flat=True)),
                         ["GEOGRAPHY.PIN.CREATED", "GEOGRAPHY.PIN.DELETED",
                          "GEOGRAPHY.PIN.HIDDEN", "GEOGRAPHY.PIN.RESTORED"])
        for record in records:
            self.assertEqual(record.metadata["community"], self.guild.slug)
            self.assertNotIn("52.2", str(record.metadata) + record.object_description)

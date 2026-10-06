"""Audit, charges and personal data of members' contributions (stage 64,
2026-10-06): no coordinate, outline, name, note or comment text in any audit
record, usage event or ledger text; "Download my data" holds the member's
pins, zones and comments; erasing an account leaves them with no author.

    manage.py test toto.geography.tests_contribution_data
"""

import json

from django.apps import apps

from toto.comments.models import Comment
from toto.geography import audit
from toto.geography.erasure import contribution_rows
from toto.geography.models import (Address, CommunityPin, CommunityZone, GeographyUsageEvent,
                                   Zone)
from toto.geography.testing import client_of, op, post
from toto.geography.testing_locations import LocationsCase

SECRETS = ("52.22", "21.01", "52.1", "21.1", "Well", "Rynek", "open on Sundays", "Meadow",
           "between the river", "Seen it", "Old well")


class AuditAndChargeTests(LocationsCase):
    billed = None       # with the host's ledger where there is one

    def walk(self):
        row = self.pin()
        area = self.zone()
        client = client_of(self.member_user)
        post(client, self.url("pin_detail", row),
             {"name": "Old well", "postal_address": "", "note": "", "op": op()})
        post(client, self.url("zone_detail", area),
             {"name": "Meadow", "description": "", "op": op()})
        self.comment(self.member_user, row, "Seen it")
        comment = Comment.objects.get()
        client.post(self.url("pin_comment_edit", row, pk=comment.pk), {"body": "Seen it twice"})
        post(client_of(self.head_user), self.url("pin_hide", row), {})
        post(client_of(self.head_user), self.url("pin_restore", row), {})
        client_of(self.head_user).post(self.url("pin_comment_withdraw", row, pk=comment.pk))
        post(client, self.url("pin_delete", row), {})
        post(client, self.url("zone_delete", area), {})

    def test_every_act_is_recorded_and_no_record_says_where_or_what(self):
        if not apps.is_installed("toto.audit"):
            self.skipTest("this host keeps no audit chain")
        from toto.audit.models import AuditRecord

        self.walk()
        records = AuditRecord.objects.filter(action__startswith="GEOGRAPHY.")
        self.assertEqual(
            sorted(set(records.values_list("action", flat=True))),
            sorted([audit.PIN_CREATED, audit.PIN_EDITED, audit.PIN_HIDDEN, audit.PIN_RESTORED,
                    audit.PIN_DELETED, audit.ZONE_CREATED, audit.ZONE_EDITED,
                    audit.ZONE_DELETED, audit.COMMENT_CREATED, audit.COMMENT_EDITED,
                    audit.COMMENT_WITHDRAWN]))
        rows = [(record.action, record.object_description, record.object_type, record.object_id,
                 record.metadata) for record in records]
        for record in records:
            self.assertEqual(record.metadata.get("community"), self.guild.slug)
            self.assertLessEqual(set(record.metadata),
                                 {"metric", "amount", "outcome", "community", "link"})
        for event in GeographyUsageEvent.objects.all():
            self.assertEqual(set(event.metadata), {"request"})
            rows.append((event.source_label, event.source_type, event.source_id,
                         event.idempotency_key, event.metadata))
        if apps.is_installed("toto.tariffs"):
            from toto.assets.models import LedgerTransaction
            from toto.tariffs.models import UsageRecord

            for model in (UsageRecord, LedgerTransaction):
                for row in model.objects.all():
                    rows.append({field.name: getattr(row, field.attname)
                                 for field in row._meta.concrete_fields
                                 if field.get_internal_type() in ("CharField", "TextField",
                                                                  "JSONField")})
        text = json.dumps(rows, default=str, ensure_ascii=False)
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def test_the_charges_of_the_walk(self):
        self.walk()
        counts = {metric: self.events(metric).count() for metric in
                  ("geography.pin", "geography.zone", "geography.note", "geography.comment")}
        # Two changes (the pin's name, the zone's description), each charged
        # once; hiding, restoring, withdrawing and deleting charge nothing.
        self.assertEqual(counts, {"geography.pin": 1, "geography.zone": 1,
                                  "geography.note": 2, "geography.comment": 1})


class BilledTests(LocationsCase):
    billed = True

    def test_each_act_takes_storage_mana_once_and_free_acts_take_none(self):
        held = lambda: self.economy.held(self.member_user, "storage")      # noqa: E731
        start = held()
        row = self.pin()
        after_pin = held()
        self.assertLess(after_pin, start)
        key = op()
        for _ in range(2):
            post(client_of(self.member_user), self.url("pin_create", community=self.guild),
                 {"lat": 50.0, "lng": 20.0, "name": "Twice", "postal_address": "", "note": "",
                  "op": key})
        self.assertEqual(start - after_pin, after_pin - held())             # one more, once
        before = held()
        client_of(self.member_user).get(self.page_url)
        client_of(self.member_user).get(self.url("pin_detail", row))
        post(client_of(self.member_user), self.url("pin_delete", row), {})
        self.assertEqual(held(), before)                                   # looking, deleting
        self.assertEqual(self.economy.charges("geography.pin"), 2)

    def test_an_empty_pool_is_refused_with_the_mana_sentence(self):
        self.economy.empty(self.member_user, "storage")
        response = self.save_pin()
        self.assertEqual(response.status_code, 402)
        self.assertTrue(response.json()["error"])
        self.assertFalse(CommunityPin.objects.exists())
        self.assertFalse(Address.objects.exists())


class PersonalDataTests(LocationsCase):
    def setUp(self):
        super().setUp()
        self.row = self.pin()
        self.area = self.zone()
        self.comment(self.member_user, self.row, "Seen it")
        self.pin(self.head_user, name="Head's")

    def test_download_my_data_holds_the_member_s_own_rows(self):
        mine = contribution_rows(self.member_user)
        self.assertEqual([(pin["community"], pin["name"], pin["address"]) for pin in mine["pins"]],
                         [("Guild", "Well", "Rynek 1")])
        self.assertEqual([zone["name"] for zone in mine["zones"]], ["Meadow"])
        self.assertEqual([(c["on"], c["name"], c["text"]) for c in mine["comments"]],
                         [("pin", "Well", "Seen it")])
        self.assertEqual(contribution_rows(self.senior_user),
                         {"pins": [], "zones": [], "comments": []})

    def test_the_plugin_names_four_tables(self):
        from toto.geography.plugins.personal_data_plugins import GeographyData

        tables = GeographyData().tables(self.member_user)
        self.assertEqual([table.name for table in tables],
                         ["geography_address", "geography_pins", "geography_zones",
                          "geography_comments"])
        self.assertEqual(len(tables[1].rows), 1)

    def test_an_erased_account_leaves_its_contributions_with_no_author(self):
        self.member_user.delete()
        pin = CommunityPin.objects.get(pk=self.row.pk)
        self.assertIsNone(pin.author)
        self.assertIsNone(CommunityZone.objects.get(pk=self.area.pk).author)
        self.assertIsNone(Comment.objects.get(body="Seen it").author)
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (2, 1))
        text = client_of(self.head_user).get(self.url("pin_detail", pin)).content.decode()
        self.assertIn("by a former member", text)

"""Community zones (stage 64, 2026-10-06): the same doors and rules as a pin,
for a drawn outline saved as one ``Zone`` and one ``CommunityZone``, charged
``geography.zone``; a change is charged ``geography.note`` under its own op.

    manage.py test toto.geography.tests_zones
"""

import math

from toto.geography.models import Address, CommunityZone, Zone
from toto.geography.testing import BOWTIE, client_of, no_funds, op, post, refusing_ledger
from toto.geography.locations_testing import AREA, LocationsCase


def ring(n):
    return [[52 + 0.1 * math.sin(2 * math.pi * i / n), 21 + 0.1 * math.cos(2 * math.pi * i / n)]
            for i in range(n)]


class ZoneTests(LocationsCase):
    def test_a_zone_is_one_zone_row_and_one_link(self):
        response = self.save_zone()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["charged"])
        self.assertEqual(response.json()["zone"]["outline"], AREA["outline"])
        row = CommunityZone.objects.get()
        self.assertEqual((row.community, row.author, row.zone.name),
                         (self.guild, self.member_user, "Meadow"))
        self.assertTrue(row.zone.outline.valid)
        self.assertEqual((Zone.objects.count(), Address.objects.count()), (1, 0))
        self.assertEqual(self.events("geography.zone").count(), 1)

    def test_a_bad_outline_is_refused_and_charges_nothing(self):
        for outline in (BOWTIE, ring(501), AREA["outline"][:2], "square", None,
                        [[52, 21], [52, 21.1], [95, 21]]):
            with self.subTest(outline=str(outline)[:40]):
                self.assertEqual(self.save_zone(outline=outline).status_code, 400)
        self.assertEqual(self.save_zone(name="").status_code, 400)
        self.assertFalse(Zone.objects.exists())
        self.assertFalse(self.events().exists())

    def test_five_hundred_corners_are_taken(self):
        self.assertEqual(self.save_zone(outline=ring(500)).status_code, 200)

    def test_retry_402_and_rollback(self):
        key = op()
        with no_funds():
            self.assertEqual(self.save_zone(op=key).status_code, 402)
        with refusing_ledger():
            self.assertEqual(self.save_zone(op=key).status_code, 402)
        self.assertFalse(Zone.objects.exists())
        first = self.save_zone(op=key)
        second = self.save_zone(op=key)
        self.assertEqual(second.json(), {"zone": first.json()["zone"], "charged": False})
        self.assertEqual(self.save_zone(op=key, name="Other").status_code, 409)
        self.assertEqual((Zone.objects.count(), self.events().count()), (1, 1))

    def test_a_change_is_a_note_once_and_nothing_is_free(self):
        row = self.zone()
        client, url = client_of(self.member_user), self.url("zone_detail", row)
        body = {"name": "Meadow", "description": AREA["description"]}
        free = post(client, url, {**body, "op": op()})
        self.assertEqual((free.status_code, free.json()["charged"]), (200, False))
        key = op()
        changed = post(client, url, {**body, "name": "Big meadow", "op": key})
        self.assertTrue(changed.json()["charged"])
        self.assertEqual(changed.json()["zone"]["outline"], AREA["outline"])    # kept
        again = post(client, url, {**body, "name": "Big meadow", "op": key})
        self.assertFalse(again.json()["charged"])
        self.assertEqual(self.events("geography.note").count(), 1)
        with refusing_ledger():
            self.assertEqual(post(client, url, {**body, "name": "X", "op": op()}).status_code,
                             402)
        row.zone.refresh_from_db()
        self.assertEqual(row.zone.name, "Big meadow")
        moved = post(client, url, {**body, "name": "Big meadow", "outline": ring(8),
                                   "op": op()})
        self.assertTrue(moved.json()["charged"])
        self.assertEqual(len(moved.json()["zone"]["outline"]), 8)
        self.assertEqual(post(client, url, {**body, "outline": BOWTIE,
                                            "op": op()}).status_code, 400)

    def test_deleting_takes_the_zone_row(self):
        row = self.zone()
        response = post(client_of(self.member_user), self.url("zone_delete", row), {})
        self.assertEqual(response.json(), {"removed": True})
        self.assertFalse(CommunityZone.objects.exists())
        self.assertFalse(Zone.objects.exists())

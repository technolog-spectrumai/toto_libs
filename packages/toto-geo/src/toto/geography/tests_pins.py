"""Community pins (stage 64, 2026-10-06): a member's pin is one ``Address``
and one ``CommunityPin``, charged ``geography.pin`` once; a change by its
author is charged ``geography.note`` once; a change of nothing and a delete
are free; a refused ledger leaves nothing behind.

    manage.py test toto.geography.tests_pins
"""

from toto.geography.models import Address, CommunityPin, Zone
from toto.geography.testing import client_of, no_funds, op, post, refusing_ledger
from toto.geography.locations_testing import PIN, LocationsCase


class CreateTests(LocationsCase):
    def test_a_pin_is_one_address_and_one_link(self):
        response = self.save_pin()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        data = response.json()
        self.assertTrue(data["charged"])
        self.assertEqual({key: data["pin"][key] for key in PIN}, PIN)
        pin = CommunityPin.objects.get()
        self.assertEqual((pin.community, pin.author, pin.address.name, pin.address.postal_address),
                         (self.guild, self.member_user, "Well", "Rynek 1"))
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (1, 0))
        self.assertEqual(self.events("geography.pin").count(), 1)
        event = self.events().get()
        self.assertEqual(set(event.metadata), {"request"})

    def test_each_refusal_stores_and_charges_nothing(self):
        bad = ({"op": None}, {"op": "not-a-uuid"}, {"lat": 91}, {"lng": "21"}, {"lat": None},
               {"name": ""}, {"name": "x" * 201}, {"note": "x" * 2001}, {"note": 5},
               {"postal_address": "a\x00b"})
        for change in bad:
            with self.subTest(change=change):
                self.assertEqual(self.save_pin(**change).status_code, 400)
        self.assertEqual(client_of(self.member_user).get(
            self.url("pin_create", community=self.guild)).status_code, 405)
        self.assertFalse(CommunityPin.objects.exists())
        self.assertFalse(Address.objects.exists())
        self.assertFalse(self.events().exists())

    def test_no_funds_is_402_before_anything_is_written(self):
        with no_funds():
            response = self.save_pin()
        self.assertEqual(response.status_code, 402)
        self.assertIn("error", response.json())
        self.assertFalse(Address.objects.exists())
        self.assertFalse(self.events().exists())

    def test_a_ledger_refusal_rolls_everything_back(self):
        with refusing_ledger():
            response = self.save_pin()
        self.assertEqual(response.status_code, 402)
        self.assertFalse(CommunityPin.objects.exists())
        self.assertFalse(Address.objects.exists())
        self.assertFalse(self.events().exists())

    def test_the_same_op_and_payload_again_is_the_same_pin_charged_once(self):
        key = op()
        first = self.save_pin(op=key)
        second = self.save_pin(op=key)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), {"pin": first.json()["pin"], "charged": False})
        self.assertEqual(CommunityPin.objects.count(), 1)
        self.assertEqual(self.events().count(), 1)

    def test_the_same_op_with_another_payload_is_409(self):
        key = op()
        self.save_pin(op=key)
        self.assertEqual(self.save_pin(op=key, name="Another").status_code, 409)
        self.assertEqual(self.save_pin(self.head_user, op=key).status_code, 200)   # another member
        self.assertEqual(CommunityPin.objects.count(), 2)

    def test_a_failed_save_then_the_same_op_is_one_charge(self):
        key = op()
        with refusing_ledger():
            self.assertEqual(self.save_pin(op=key).status_code, 402)
        self.assertEqual(self.save_pin(op=key).status_code, 200)
        self.assertEqual(self.events().count(), 1)


class EditAndDeleteTests(LocationsCase):
    def setUp(self):
        super().setUp()
        self.row = self.pin()
        self.edit_url = self.url("pin_detail", self.row)
        self.client_ = client_of(self.member_user)

    def edit(self, **changes):
        body = {"name": "Well", "postal_address": "Rynek 1", "note": "open on Sundays",
                "op": op(), **changes}
        return post(self.client_, self.edit_url, body)

    def test_a_change_is_charged_a_note_once(self):
        key = op()
        first = self.edit(name="Old well", op=key)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["charged"])
        self.assertEqual(first.json()["pin"]["name"], "Old well")
        self.assertEqual((first.json()["pin"]["lat"], first.json()["pin"]["lng"]),
                         (PIN["lat"], PIN["lng"]))                 # it stays where it was
        again = self.edit(name="Old well", op=key)
        self.assertEqual((again.status_code, again.json()["charged"]), (200, False))
        self.assertEqual(self.events("geography.note").count(), 1)
        self.assertEqual(self.events("geography.pin").count(), 1)
        self.assertEqual(Address.objects.count(), 1)

    def test_a_change_of_nothing_is_free(self):
        response = self.edit()
        self.assertEqual((response.status_code, response.json()["charged"]), (200, False))
        self.assertFalse(self.events("geography.note").exists())

    def test_a_ledger_refusal_leaves_the_old_text(self):
        with refusing_ledger():
            self.assertEqual(self.edit(name="New").status_code, 402)
        with no_funds():
            self.assertEqual(self.edit(note="New").status_code, 402)
        self.row.address.refresh_from_db()
        self.assertEqual((self.row.address.name, self.row.address.note),
                         ("Well", "open on Sundays"))
        self.assertFalse(self.events("geography.note").exists())

    def test_a_bad_change_is_400(self):
        for change in ({"name": ""}, {"op": "x"}, {"lat": 95, "lng": 0}):
            self.assertEqual(self.edit(**change).status_code, 400, change)

    def test_deleting_is_free_and_takes_the_address(self):
        response = post(self.client_, self.url("pin_delete", self.row), {})
        self.assertEqual(response.json(), {"removed": True})
        self.assertFalse(CommunityPin.objects.exists())
        self.assertFalse(Address.objects.exists())
        self.assertEqual(self.events().count(), 1)                 # the creation's, no more
        self.assertEqual(post(self.client_, self.url("pin_delete", self.row), {}).status_code,
                         404)

    def test_the_address_goes_with_the_community(self):
        self.guild.delete()
        self.assertFalse(CommunityPin.objects.exists())
        self.assertFalse(Address.objects.exists())

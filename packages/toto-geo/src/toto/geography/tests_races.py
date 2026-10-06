"""Two requests at once (2026-10-06): what a save and a removal do when
another request changes the same owner's rows between its read and its
write.

Every save and every removal takes its owner's row ``FOR UPDATE`` first (the
person, the community) and reads what is saved only then, so on PostgreSQL
the second of two requests waits and decides from what the first one left.
``OneAtATimeTests`` runs that with two real connections; it needs
PostgreSQL and is skipped elsewhere.

The other classes run everywhere, on one connection, and tell a race in the
ORDER it has on a database that takes no row lock (SQLite): the winner's
request runs to its end, then the loser's, whose first read is made to
answer what it would have seen before the winner wrote (the link row as it
was, or none) and whose first check of its op answers "not known yet". They
prove what the writes refuse by themselves: no second link for one owner,
no row put back after it was removed, no second geometry row hung on one
link. The loser is undone and is told what the charged-door rule says: a
replay where the winner was this very request, 409 otherwise, never a 500.

    manage.py test toto.geography.tests_races
"""

import threading
from unittest import mock, skipUnless

from django.apps import apps
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from toto.geography import charging, saves
from toto.geography.models import (Address, CommunityHeadquarters, GeographyUsageEvent,
                                   PersonAddress, Zone)
from toto.geography.testing import SQUARE, client_of, community, fresh_cache, member, op, post

HOME = {"lat": 52.2297, "lng": 21.0122, "name": "Home", "note": "third floor"}
HQ = {"lat": 54.352, "lng": 18.6466, "name": "Harbour house", "note": "ring twice"}
AREA = {"name": "The harbour", "description": "from the pier to the gate", "outline": SQUARE}


def unknown_at_first():
    """``charging.known`` as the loser of a race meets it: "not known yet"
    at its first check, when the winner's event was not there, and the truth
    afterwards."""
    real, calls = charging.known, []

    def known(user, key, body):
        calls.append(key)
        return False if len(calls) == 1 else real(user, key, body)

    return mock.patch("toto.geography.charging.known", side_effect=known)


def read_too_early(name, seen):
    """``saves.<name>`` answering ``seen`` the first time, what a request
    that read before the winner wrote holds, and the truth afterwards."""
    real, calls = getattr(saves, name), []

    def read(owner):
        calls.append(owner)
        return seen if len(calls) == 1 else real(owner)

    return mock.patch.object(saves, name, side_effect=read)


def events(metric=None):
    rows = GeographyUsageEvent.objects.all()
    return rows.filter(metric_code=metric) if metric else rows


class RaceCase(TestCase):
    def setUp(self):
        fresh_cache(self)
        self.ada_user, self.ada = member("ada")
        self.head_user, self.head = member("hugo")
        self.guild = community("Guild", head=self.head)
        self.urls = {name: reverse(f"geography:{name}", kwargs={"slug": self.guild.slug})
                     for name in ("headquarters", "headquarters_clear", "zone", "zone_clear")}
        self.urls["point"] = reverse("geography:my_address")
        self.urls["point_clear"] = reverse("geography:my_address_clear")

    def save_point(self, **changes):
        return post(client_of(self.ada_user), self.urls["point"], {**HOME, "op": op(), **changes})

    def save_hq(self, **changes):
        return post(client_of(self.head_user), self.urls["headquarters"],
                    {**HQ, "op": op(), **changes})

    def save_zone(self, **changes):
        return post(client_of(self.head_user), self.urls["zone"], {**AREA, "op": op(), **changes})

    def assertNoOrphans(self):
        for address in Address.objects.all():
            links = (PersonAddress.objects.filter(address=address).count()
                     + CommunityHeadquarters.objects.filter(address=address).count())
            self.assertEqual(links, 1, f"address {address.pk} has {links} links")
        for zone in Zone.objects.all():
            self.assertEqual(CommunityHeadquarters.objects.filter(zone=zone).count(), 1,
                             f"zone {zone.pk} has no link")

    def assertRefusedAsJson(self, response, status):
        self.assertEqual(response.status_code, status, response.content[:300])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(set(response.json()), {"error"})


class FirstSaveAtOnceTests(RaceCase):
    """Two first saves of one person's point: both read "no point yet"."""

    def test_the_same_request_twice_is_one_point_one_charge_and_a_replay(self):
        key = op()
        self.assertTrue(self.save_point(op=key).json()["charged"])
        with read_too_early("_person_link", None), unknown_at_first(), \
                mock.patch("toto.geography.charging.charge") as charged_again:
            response = self.save_point(op=key)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"address": HOME, "charged": False})
        charged_again.assert_not_called()
        self.assertEqual((Address.objects.count(), PersonAddress.objects.count()), (1, 1))
        self.assertEqual(events().count(), 1)
        self.assertNoOrphans()

    def test_another_body_under_the_same_op_is_409(self):
        key = op()
        self.save_point(op=key)
        with read_too_early("_person_link", None), unknown_at_first():
            response = self.save_point(op=key, name="Elsewhere", lat=50.0)
        self.assertRefusedAsJson(response, 409)
        self.assertIn("already used", response.json()["error"])
        address = Address.objects.get()
        self.assertEqual((address.name, address.point.y), ("Home", 52.2297))
        self.assertEqual(events().count(), 1)
        self.assertNoOrphans()

    def test_another_op_is_409_one_point_and_one_charge(self):
        """Two tabs: the second is told to press again, and that press is a
        change of the point the first one saved."""
        self.save_point()
        with read_too_early("_person_link", None), unknown_at_first():
            response = self.save_point(name="Elsewhere")
        self.assertRefusedAsJson(response, 409)
        self.assertIn("at the same moment", response.json()["error"])
        self.assertEqual((Address.objects.count(), PersonAddress.objects.count()), (1, 1))
        self.assertEqual(Address.objects.get().name, "Home")
        self.assertEqual([event.metric_code for event in events()], ["geography.pin"])
        self.assertNoOrphans()
        self.assertTrue(self.save_point(name="Elsewhere").json()["charged"])
        self.assertEqual([event.metric_code for event in events().order_by("pk")],
                         ["geography.pin", "geography.note"])
        self.assertEqual(Address.objects.count(), 1)


class SaveAgainstRemoveTests(RaceCase):
    """A change and a Remove at once: the change read the point, the removal
    then committed, and the change writes. The removed row is not put back
    (a plain ``save()`` would insert it again under its old id, linked to
    nobody, where no removal and no erasure would ever find it)."""

    def test_a_person_s_point_removed_under_a_change_stays_removed(self):
        self.save_point()
        before = saves._person_link(self.ada)
        self.assertTrue(saves.clear_person_point(self.ada_user, self.ada))
        with read_too_early("_person_link", before), unknown_at_first():
            response = self.save_point(note="fourth floor")
        self.assertRefusedAsJson(response, 409)
        self.assertIn("at the same moment", response.json()["error"])
        self.assertEqual((Address.objects.count(), PersonAddress.objects.count()), (0, 0))
        self.assertEqual([event.metric_code for event in events()], ["geography.pin"],
                         "the change that lost was not charged")
        # Pressed again it is a first save, at the first save's price.
        self.assertTrue(self.save_point(note="fourth floor").json()["charged"])
        self.assertEqual(events("geography.pin").count(), 2)
        self.assertNoOrphans()

    def test_a_headquarters_removed_under_a_change_stays_removed(self):
        self.save_hq()
        self.save_zone()
        before = saves._link_of(self.guild)
        self.assertTrue(saves.clear_headquarters(self.head_user, self.guild))
        with read_too_early("_link_of", before), unknown_at_first():
            response = self.save_hq(note="ring three times")
        self.assertRefusedAsJson(response, 409)
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (0, 1))
        self.assertIsNone(CommunityHeadquarters.objects.get().address_id)
        self.assertFalse(events("geography.note").exists())
        self.assertNoOrphans()

    def test_a_zone_removed_under_a_change_stays_removed(self):
        self.save_zone()
        before = saves._link_of(self.guild)
        self.assertTrue(saves.clear_zone(self.head_user, self.guild))
        with read_too_early("_link_of", before), unknown_at_first():
            response = self.save_zone(description="wider")
        self.assertRefusedAsJson(response, 409)
        self.assertEqual(Zone.objects.count(), 0)
        self.assertFalse(CommunityHeadquarters.objects.exists())
        self.assertFalse(events("geography.note").exists())
        self.assertNoOrphans()

    def test_a_first_point_on_a_link_row_that_went_is_409_and_no_point(self):
        """The link row the save read (it held the zone) was dropped with
        its last geometry; a point hung on it would hang on nothing."""
        self.save_zone()
        before = saves._link_of(self.guild)
        self.assertTrue(saves.clear_zone(self.head_user, self.guild))
        with read_too_early("_link_of", before), unknown_at_first():
            response = self.save_hq()
        self.assertRefusedAsJson(response, 409)
        self.assertFalse(Address.objects.exists() or CommunityHeadquarters.objects.exists())
        self.assertFalse(events("geography.pin").exists())


class HeadquartersAtOnceTests(RaceCase):
    """Two saves for one community, each from what it read before the other
    wrote: the head in two tabs (or the head and an administrator)."""

    def test_two_first_headquarters_on_a_link_with_a_zone_are_one_point_and_one_pin(self):
        self.save_zone()
        before = saves._link_of(self.guild)      # holds the zone, no headquarters yet
        self.assertIsNone(before.address_id)
        self.assertTrue(self.save_hq().json()["charged"])
        with read_too_early("_link_of", before), unknown_at_first():
            response = self.save_hq(name="Other house", lat=54.4)
        self.assertRefusedAsJson(response, 409)
        self.assertEqual(Address.objects.count(), 1, "no second point, linked or not")
        self.assertEqual(CommunityHeadquarters.objects.get().address.name, "Harbour house")
        self.assertEqual(events("geography.pin").count(), 1, "the pin price was taken once")
        self.assertNoOrphans()

    def test_two_first_zones_on_a_link_with_a_headquarters_are_one_zone(self):
        self.save_hq()
        before = saves._link_of(self.guild)
        self.assertIsNone(before.zone_id)
        self.assertTrue(self.save_zone().json()["charged"])
        with read_too_early("_link_of", before), unknown_at_first():
            response = self.save_zone(name="Another area")
        self.assertRefusedAsJson(response, 409)
        self.assertEqual(Zone.objects.count(), 1)
        self.assertEqual(CommunityHeadquarters.objects.get().zone.name, "The harbour")
        self.assertEqual(events("geography.zone").count(), 1)
        self.assertNoOrphans()

    def test_a_first_headquarters_and_a_first_zone_at_once_is_409_then_saves(self):
        """Two different saves that both found no link row: the second is
        told to press again, where it used to end in a 500."""
        self.assertTrue(self.save_hq().json()["charged"])
        with read_too_early("_link_of", None), unknown_at_first():
            response = self.save_zone()
        self.assertRefusedAsJson(response, 409)
        self.assertEqual((CommunityHeadquarters.objects.count(), Zone.objects.count()), (1, 0))
        self.assertTrue(self.save_zone().json()["charged"])
        link = CommunityHeadquarters.objects.get()
        self.assertEqual((link.address.name, link.zone.name), ("Harbour house", "The harbour"))
        self.assertNoOrphans()

    def test_the_same_first_headquarters_twice_is_a_replay(self):
        key = op()
        self.save_hq(op=key)
        with read_too_early("_link_of", None), unknown_at_first(), \
                mock.patch("toto.geography.charging.charge") as charged_again:
            response = self.save_hq(op=key)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"headquarters": HQ, "charged": False})
        charged_again.assert_not_called()
        self.assertEqual((Address.objects.count(), events().count()), (1, 1))
        self.assertNoOrphans()


class RemoveTwiceTests(RaceCase):
    """Remove pressed twice (the button is not held while the first answer
    is on its way): the second finds nothing to remove."""

    def audited(self, action):
        if not apps.is_installed("toto.audit"):
            return None
        from toto.audit.models import AuditRecord

        return AuditRecord.objects.filter(action=action).count()

    def test_a_headquarters_removed_twice_at_once(self):
        self.save_hq()
        before = saves._link_of(self.guild)
        head = client_of(self.head_user)
        self.assertEqual(post(head, self.urls["headquarters_clear"], {}).json(), {"removed": True})
        with read_too_early("_link_of", before):
            response = post(head, self.urls["headquarters_clear"], {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"removed": False})
        self.assertIn(self.audited("GEOGRAPHY.HEADQUARTERS.CLEARED"), (None, 1))

    def test_a_zone_removed_twice_at_once(self):
        self.save_hq()
        self.save_zone()
        before = saves._link_of(self.guild)
        head = client_of(self.head_user)
        self.assertEqual(post(head, self.urls["zone_clear"], {}).json(), {"removed": True})
        with read_too_early("_link_of", before):
            response = post(head, self.urls["zone_clear"], {})
        self.assertEqual(response.json(), {"removed": False})
        self.assertEqual(CommunityHeadquarters.objects.get().address.name, "Harbour house")
        self.assertIn(self.audited("GEOGRAPHY.ZONE.CLEARED"), (None, 1))

    def test_a_person_s_point_removed_twice_at_once(self):
        self.save_point()
        before = saves._person_link(self.ada)
        ada = client_of(self.ada_user)
        self.assertEqual(post(ada, self.urls["point_clear"], {}).json(), {"removed": True})
        with read_too_early("_person_link", before):
            response = post(ada, self.urls["point_clear"], {})
        self.assertEqual(response.json(), {"removed": False})
        self.assertIn(self.audited("GEOGRAPHY.ADDRESS.CLEARED"), (None, 1))


class OwnerRowTests(RaceCase):
    """Each of the six doors takes its owner's row before it reads."""

    def calls(self):
        return (
            ("person", lambda: self.save_point()),
            ("person", lambda: post(client_of(self.ada_user), self.urls["point_clear"], {})),
            ("community", lambda: self.save_hq()),
            ("community", lambda: self.save_zone()),
            ("community", lambda: post(client_of(self.head_user), self.urls["zone_clear"], {})),
            ("community", lambda: post(client_of(self.head_user),
                                       self.urls["headquarters_clear"], {})),
        )

    def test_the_owner_is_held_and_only_then_the_link_is_read(self):
        owners = {"person": self.ada, "community": self.guild}
        for kind, call in self.calls():
            order = []
            hold, person_link, link_of = saves._hold, saves._person_link, saves._link_of

            def held(owner, hold=hold):
                order.append(("hold", owner))
                return hold(owner)

            def person_read(person, person_link=person_link):
                order.append(("read", person))
                return person_link(person)

            def community_read(row, link_of=link_of):
                order.append(("read", row))
                return link_of(row)

            with self.subTest(kind=kind, door=len(order)), \
                    mock.patch.object(saves, "_hold", side_effect=held), \
                    mock.patch.object(saves, "_person_link", side_effect=person_read), \
                    mock.patch.object(saves, "_link_of", side_effect=community_read):
                self.assertEqual(call().status_code, 200)
                self.assertEqual(order[0], ("hold", owners[kind]))
                self.assertEqual([step for step, _owner in order].count("hold"), 1)
                self.assertIn(("read", owners[kind]), order[1:])

    def test_an_owner_who_is_gone_is_404_and_nothing_is_kept(self):
        with mock.patch.object(saves, "_hold", return_value=False):
            for response in (self.save_point(), self.save_hq(), self.save_zone()):
                self.assertRefusedAsJson(response, 404)
            for name, user in (("point_clear", self.ada_user), ("zone_clear", self.head_user),
                               ("headquarters_clear", self.head_user)):
                self.assertEqual(post(client_of(user), self.urls[name], {}).json(),
                                 {"removed": False})
        self.assertFalse(Address.objects.exists() or Zone.objects.exists() or events().exists())

    @skipUnless(connection.vendor == "postgresql", "row locks are PostgreSQL's")
    def test_on_postgresql_the_owner_s_row_is_locked_before_geography_is_read(self):
        tables = {"person": self.ada._meta.db_table, "community": self.guild._meta.db_table}
        for kind, call in self.calls():
            with self.subTest(kind=kind), CaptureQueriesContext(connection) as queries:
                self.assertEqual(call().status_code, 200)
            statements = [query["sql"] for query in queries.captured_queries]
            locks = [index for index, sql in enumerate(statements)
                     if "FOR NO KEY UPDATE" in sql and f'"{tables[kind]}"' in sql]
            reads = [index for index, sql in enumerate(statements) if '"geography_' in sql]
            self.assertEqual(len(locks), 1, kind)
            self.assertTrue(reads, kind)
            self.assertLess(locks[0], reads[0], "the lock comes before the first read")


@skipUnless(connection.vendor == "postgresql",
            "two connections holding row locks: PostgreSQL only (tests/user_tests13.sh, geo63)")
class OneAtATimeTests(TransactionTestCase):
    """Two real requests at once, each on its own connection. A barrier
    inside the afford check holds each request after its read until the
    other has read too: without the owner's lock both would decide from "no
    point yet". With it the second request cannot read until the first has
    committed, so the barrier is never met by both and gives up after its
    two seconds."""

    def setUp(self):
        fresh_cache(self)
        self.head_user, self.head = member("hugo")
        self.ada_user, self.ada = member("ada")
        self.guild = community("Guild", head=self.head)

    def at_once(self, *calls):
        barrier = threading.Barrier(len(calls))
        real = charging.afford

        def afford(user, metric, quantity=1):
            try:
                barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            return real(user, metric, quantity)

        results = [None] * len(calls)

        def run(index, call):
            try:
                results[index] = call()
            except Exception as exc:  # noqa: BLE001 - the test reads it
                results[index] = exc
            finally:
                connections.close_all()

        with mock.patch("toto.geography.charging.afford", side_effect=afford):
            threads = [threading.Thread(target=run, args=(index, call))
                       for index, call in enumerate(calls)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
        return results

    def charged(self, results):
        """Whether each save was charged; a request that ended in an
        exception fails the test with it."""
        for result in results:
            if isinstance(result, Exception):
                raise AssertionError(f"a request ended in {result!r}") from result
        return [charged for _state, charged in results]

    def assertNoOrphans(self):
        for address in Address.objects.all():
            links = (PersonAddress.objects.filter(address=address).count()
                     + CommunityHeadquarters.objects.filter(address=address).count())
            self.assertEqual(links, 1, f"address {address.pk} has {links} links")
        for zone in Zone.objects.all():
            self.assertEqual(CommunityHeadquarters.objects.filter(zone=zone).count(), 1)

    def test_two_first_headquarters_at_once_are_a_pin_and_a_change_on_one_row(self):
        saves.save_zone(self.head_user, self.guild, op=op(), **AREA)
        results = self.at_once(
            lambda: saves.save_headquarters(self.head_user, self.guild, op=op(), **HQ),
            lambda: saves.save_headquarters(self.head_user, self.guild, op=op(),
                                            **{**HQ, "name": "Other house"}))
        self.assertEqual(self.charged(results), [True, True])
        self.assertEqual(Address.objects.count(), 1)
        self.assertEqual(sorted(GeographyUsageEvent.objects.exclude(metric_code="geography.zone")
                                .values_list("metric_code", flat=True)),
                         ["geography.note", "geography.pin"])
        self.assertNoOrphans()

    def test_two_first_saves_of_a_point_at_once_are_one_point(self):
        results = self.at_once(
            lambda: saves.save_person_point(self.ada_user, self.ada, op=op(), **HOME),
            lambda: saves.save_person_point(self.ada_user, self.ada, op=op(),
                                            **{**HOME, "note": "fourth floor"}))
        self.assertEqual(self.charged(results), [True, True])
        self.assertEqual((Address.objects.count(), PersonAddress.objects.count()), (1, 1))
        self.assertEqual(sorted(GeographyUsageEvent.objects
                                .values_list("metric_code", flat=True)),
                         ["geography.note", "geography.pin"])
        self.assertNoOrphans()

    def test_the_same_first_save_twice_at_once_is_one_charge_and_a_replay(self):
        key = op()
        results = self.at_once(
            lambda: saves.save_person_point(self.ada_user, self.ada, op=key, **HOME),
            lambda: saves.save_person_point(self.ada_user, self.ada, op=key, **HOME))
        self.assertEqual(sorted(self.charged(results)), [False, True])
        self.assertEqual([state for state, _charged in results], [HOME, HOME])
        self.assertEqual(GeographyUsageEvent.objects.count(), 1)
        self.assertEqual(Address.objects.count(), 1)

    def test_a_change_and_a_remove_at_once_leave_no_point_without_a_link(self):
        saves.save_person_point(self.ada_user, self.ada, op=op(), **HOME)
        results = self.at_once(
            lambda: saves.save_person_point(self.ada_user, self.ada, op=op(),
                                            **{**HOME, "note": "fourth floor"}),
            lambda: saves.clear_person_point(self.ada_user, self.ada))
        self.assertNotIsInstance(results[0], Exception, results)
        self.assertIs(results[1], True)
        # Whichever went first, every point left has its link.
        self.assertEqual(Address.objects.count(), PersonAddress.objects.count())
        self.assertNoOrphans()

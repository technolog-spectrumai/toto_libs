"""Clearances, more closely (2026-09-29): the model's rules at their edges, the
shared reading rule every app asks (``clearance_access``), the Clearances tab
and the admin's limits for somebody who is not a superuser.

The tab (2026-09-30) is a paginated, read-only list with two doors: New
clearance (``clearance_add`` — a name, a speed per pool and holders, all or
nothing, a refusal re-drawn with the modal open; its holders found through
``clearance_people``) and Delete. Holders and speeds are changed in the admin,
so the doors that edited them in place (``clearance_member``,
``clearance_speeds``) are gone, and their rules are asserted at making.

``clearance_access`` is exercised through a real through table — the map
layer's ``clearance_rows`` (``toto.locations``, same package) — because the rule
is written against "a related name whose rows carry a ``clearance``", and a
fake would test past the lookups that make ``gate`` and ``hidden`` agree.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_more_clearances
"""

import re
from decimal import Decimal
from unittest import mock
from urllib.parse import urljoin

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed
from django.test import Client, RequestFactory, TestCase
from django.urls import NoReverseMatch, reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.locations.models import MapLayer, MapLayerClearance
from toto.people.models import Person
from toto.socialhub import clearance_access
from toto.socialhub.models import MAX_CLEARANCES, Clearance, Community

User = get_user_model()


def person(username, *communities, **flags):
    user = User.objects.create_user(username, f"{username}@example.com", "pw", **flags)
    someone = Person.objects.create(user=user, display_name=username.title())
    someone.communities.add(*communities)
    return someone


def client_for(user):
    client = Client()
    client.force_login(user)
    return client


class ClearanceFixture(TestCase):
    """`devs` is a community; `internal` and `confidential` are clearances. Ada
    is in `devs` and holds `internal`, Bob holds `confidential`, Cy is in
    `devs` only."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.devs = Community.objects.create(name="devs", slug="devs")
        cls.internal = Clearance.objects.create(name="internal", slug="internal")
        cls.confidential = Clearance.objects.create(name="confidential", slug="confidential")
        cls.ada = person("ada", cls.devs)
        cls.ada.clearances.add(cls.internal)
        cls.bob = person("bob")
        cls.bob.clearances.add(cls.confidential)
        cls.cy = person("cy", cls.devs)
        cls.root = User.objects.create_superuser("root", "root@example.com", "pw")


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


class ClearanceRuleTests(ClearanceFixture):
    def test_a_negative_speed_is_refused_on_its_own_field(self):
        self.internal.regen_storage = Decimal("-1")
        with self.assertRaises(ValidationError) as caught:
            self.internal.full_clean()
        self.assertIn("regen_storage", caught.exception.message_dict)

    def test_regen_speeds_names_only_the_pools_a_clearance_sets(self):
        self.internal.regen_security = Decimal("8")
        self.internal.regen_storage = Decimal("0")       # zero is a speed, not "unset"
        self.assertEqual(self.internal.regen_speeds(),
                         {"security": Decimal("8"), "storage": Decimal("0")})
        self.assertEqual(self.confidential.regen_speeds(), {})

    def test_removing_a_clearance_makes_room_for_another(self):
        for n in range(Clearance.objects.count(), MAX_CLEARANCES):
            Clearance.objects.create(name=f"x{n}", slug=f"x{n}")
        with self.assertRaises(ValidationError):
            Clearance.objects.create(name="eighth")
        Clearance.objects.get(slug="x2").delete()
        Clearance.objects.create(name="eighth")
        self.assertEqual(Clearance.objects.count(), MAX_CLEARANCES)

    def test_a_clearance_is_named_once(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Clearance.objects.create(name="internal", slug="internal-2")

    def test_a_second_community_of_the_same_name_gets_its_own_slug(self):
        first = Community.objects.create(name="Weavers Guild")
        second = Community.objects.create(name="Weavers Guild")
        self.assertEqual(first.slug, "weavers-guild")
        self.assertEqual(second.slug, "weavers-guild-1")

    def test_the_two_kinds_are_told_apart_by_their_models(self):
        self.assertEqual(set(Community.objects.all()), {self.devs})
        self.assertEqual(set(Clearance.objects.all()), {self.internal, self.confidential})
        self.assertEqual(set(self.ada.communities.all()), {self.devs})
        self.assertEqual(set(self.ada.clearances.all()), {self.internal})


# ---------------------------------------------------------------------------
# clearance_access — the one reading rule
# ---------------------------------------------------------------------------


class ClearanceAccessTests(ClearanceFixture):
    ROWS = "clearance_rows"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.open_layer = MapLayer.objects.create(name="Open", slug="open")
        cls.internal_layer = MapLayer.objects.create(name="Internal only", slug="internal-only")
        MapLayerClearance.objects.create(layer=cls.internal_layer, clearance=cls.internal)
        cls.shared_layer = MapLayer.objects.create(name="Both clearances", slug="both")
        MapLayerClearance.objects.create(layer=cls.shared_layer, clearance=cls.internal)
        MapLayerClearance.objects.create(layer=cls.shared_layer, clearance=cls.confidential)
        cls.inactive = MapLayer.objects.create(name="Inactive", slug="inactive", is_active=False)

    def readable(self, user, **kwargs):
        return set(clearance_access.gate(user, MapLayer.objects.all(), rows=self.ROWS, **kwargs)
                   .values_list("slug", flat=True))

    def test_person_of_and_clearance_ids_of_know_nobody_without_a_person(self):
        stray = User.objects.create_user("stray", password="pw")
        for user in (None, AnonymousUser(), stray):
            self.assertIsNone(clearance_access.person_of(user))
            self.assertEqual(clearance_access.clearance_ids_of(user), set())

    def test_clearance_ids_of_counts_clearances_and_never_a_community(self):
        self.assertEqual(clearance_access.clearance_ids_of(self.ada.user), {self.internal.pk})
        self.assertEqual(clearance_access.clearance_ids_of(self.cy.user), set())

    def test_a_kept_object_is_read_by_its_clearances_members_only(self):
        everyone = {"open", "inactive"}
        self.assertEqual(self.readable(self.cy.user), everyone)
        self.assertEqual(self.readable(AnonymousUser()), everyone)
        self.assertEqual(self.readable(self.ada.user), everyone | {"internal-only", "both"})
        self.assertEqual(self.readable(self.bob.user), everyone | {"both"})
        self.assertEqual(self.readable(self.root), everyone | {"internal-only", "both"})

    def test_the_apps_own_rule_applies_to_open_objects_only(self):
        """A clearance both keeps and grants: the `open` rule narrows what has no
        clearance, and a member reads a kept object whatever that rule says."""
        MapLayerClearance.objects.create(layer=self.inactive, clearance=self.confidential)
        active = Q(is_active=True)
        self.assertEqual(self.readable(self.cy.user, open=active), {"open"})
        self.assertEqual(self.readable(self.bob.user, open=active), {"open", "both", "inactive"})

    def test_the_owner_reads_a_kept_object_without_holding_its_clearance(self):
        self.internal_layer.owner = self.cy
        self.internal_layer.save()
        owner = Q(owner=self.cy)
        self.assertIn("internal-only", self.readable(self.cy.user, owner=owner))
        # The owner clause is never offered to an anonymous visitor.
        ownerless = Q(owner__isnull=True)
        self.assertIn("both", self.readable(self.cy.user, owner=ownerless))
        self.assertNotIn("both", self.readable(AnonymousUser(), owner=ownerless))

    def test_an_object_in_two_of_my_clearances_is_listed_once(self):
        self.confidential.members.add(self.ada)
        rows = list(clearance_access.gate(self.ada.user, MapLayer.objects.filter(slug="both"),
                                          rows=self.ROWS))
        self.assertEqual(len(rows), 1)

    def test_hidden_is_the_per_object_twin_of_gate(self):
        for user in (self.ada.user, self.bob.user, self.cy.user, AnonymousUser(), self.root):
            readable = self.readable(user)
            for layer in (self.open_layer, self.internal_layer, self.shared_layer):
                with self.subTest(user=getattr(user, "username", "anonymous"), layer=layer.slug):
                    self.assertEqual(not clearance_access.hidden(user, layer, rows=self.ROWS),
                                     layer.slug in readable)

    def test_hidden_answers_missing_for_nothing_and_open_for_the_owner(self):
        self.assertTrue(clearance_access.hidden(self.root, None, rows=self.ROWS))
        self.assertFalse(clearance_access.hidden(self.cy.user, self.internal_layer, rows=self.ROWS,
                                                 is_owner=True))

    def test_kept_says_whether_any_clearance_holds_it(self):
        self.assertTrue(clearance_access.kept(self.internal_layer, rows=self.ROWS))
        self.assertFalse(clearance_access.kept(self.open_layer, rows=self.ROWS))

    def test_clearances_of_lists_by_name(self):
        self.assertEqual(clearance_access.clearances_of(self.shared_layer, rows=self.ROWS),
                         [self.confidential, self.internal])
        self.assertEqual(clearance_access.clearances_of(self.open_layer, rows=self.ROWS), [])

    def test_a_member_shares_with_their_own_clearances_and_the_objects_own(self):
        self.assertEqual(list(clearance_access.shareable_clearances(self.ada.user, self.open_layer,
                                                                    rows=self.ROWS)), [self.internal])
        # Bob does not hold `internal`, but the layer already is: he sees it to keep it.
        self.assertEqual(list(clearance_access.shareable_clearances(self.bob.user, self.internal_layer,
                                                                    rows=self.ROWS)),
                         [self.confidential, self.internal])
        self.assertEqual(list(clearance_access.shareable_clearances(self.cy.user, self.open_layer,
                                                                    rows=self.ROWS)), [])
        self.assertEqual(list(clearance_access.shareable_clearances(self.root, self.open_layer,
                                                                    rows=self.ROWS)),
                         [self.confidential, self.internal])

    def test_a_viewer_who_does_not_manage_sees_only_their_own_clearances_named(self):
        self.assertEqual(clearance_access.visible_clearances_of(self.bob.user, self.shared_layer,
                                                                rows=self.ROWS, manages=False),
                         [self.confidential])
        self.assertEqual(clearance_access.visible_clearances_of(self.bob.user, self.shared_layer,
                                                                rows=self.ROWS, manages=True),
                         [self.confidential, self.internal])


class SetClearancesTests(ClearanceFixture):
    ROWS = "clearance_rows"

    def setUp(self):
        self.layer = MapLayer.objects.create(name="Layer", slug="layer")

    def records(self):
        return AuditRecord.objects.filter(action="LOCATIONS.LAYER_CLEARANCES")

    def set(self, *clearances, **facts):
        return clearance_access.set_clearances(self.layer, clearances, rows=self.ROWS, actor=self.root,
                                               action="layer_clearances", app_label="locations", **facts)

    def test_keeping_changing_and_opening_again_each_leave_one_record(self):
        self.assertEqual(self.set(self.internal, kind="layer"), ([], ["internal"]))
        first = self.records().get()
        self.assertEqual(first.metadata["before"], [])
        self.assertEqual(first.metadata["after"], ["internal"])
        self.assertFalse(first.metadata["open"])
        self.assertEqual(first.metadata["kind"], "layer")
        self.assertEqual(first.actor_user, self.root)

        self.assertEqual(self.set(self.confidential, self.internal),
                         (["internal"], ["confidential", "internal"]))
        self.assertEqual(set(self.layer.clearance_rows.values_list("clearance__slug", flat=True)),
                         {"internal", "confidential"})

        self.assertEqual(self.set(), (["confidential", "internal"], []))
        self.assertFalse(self.layer.clearance_rows.exists())
        self.assertTrue(self.records().order_by("-sequence").first().metadata["open"])
        self.assertEqual(self.records().count(), 3)

    def test_the_same_clearances_again_change_nothing_and_record_nothing(self):
        self.set(self.internal)
        self.assertEqual(self.set(self.internal, self.internal), (["internal"], ["internal"]))
        self.assertEqual(self.layer.clearance_rows.count(), 1)
        self.assertEqual(self.records().count(), 1)

    def test_a_chain_that_cannot_write_never_undoes_the_change(self):
        with mock.patch("toto.audit.services.record", side_effect=RuntimeError("chain down")), \
                self.assertLogs("toto.socialhub", "ERROR"):
            self.set(self.internal)
        self.assertEqual(list(self.layer.clearance_rows.values_list("clearance__slug", flat=True)),
                         ["internal"])


# ---------------------------------------------------------------------------
# The Clearances tab (2026-09-30): a read-only list with two doors — New
# clearance (``clearance_add``, fed by ``clearance_people``) and Delete.
# ---------------------------------------------------------------------------


class ClearancesTabRefusalTests(ClearanceFixture):
    """Every remaining door is a superuser's; the doors that edited a
    clearance in place are gone (holders and speeds change in the admin)."""

    def doors(self):
        """(method, url, data) for every door the tab still has."""
        return (
            ("get", reverse("socialhub:clearances"), {}),
            ("get", reverse("socialhub:clearance_people"), {"q": "ada"}),
            ("post", reverse("socialhub:clearance_add"),
             {"name": "restricted", "regen_security": "99", "person": [self.cy.pk]}),
            ("post", reverse("socialhub:clearance_delete", args=[self.internal.pk]), {}),
        )

    def assert_nothing_changed(self):
        self.assertEqual(set(Clearance.objects.values_list("slug", flat=True)),
                         {"internal", "confidential"})
        self.assertEqual(set(self.internal.members.all()), {self.ada})
        self.assertEqual(set(self.cy.clearances.all()), set())

    def test_every_door_refuses_a_member_and_changes_nothing(self):
        member = client_for(self.ada.user)
        for method, url, data in self.doors():
            with self.subTest(url=url):
                self.assertEqual(getattr(member, method)(url, data).status_code, 403)
        self.assert_nothing_changed()

    def test_every_door_refuses_staff_whatever_rights_they_hold(self):
        clerk = User.objects.create_user("clerk", "clerk@example.com", "pw", is_staff=True)
        clerk.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="socialhub", content_type__model="clearance"))
        staff = client_for(clerk)
        for method, url, data in self.doors():
            with self.subTest(url=url):
                self.assertEqual(getattr(staff, method)(url, data).status_code, 403)
        self.assert_nothing_changed()

    def test_every_door_sends_a_visitor_to_sign_in(self):
        for method, url, data in self.doors():
            with self.subTest(url=url):
                response = getattr(Client(), method)(url, data)
                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response["Location"])
        self.assert_nothing_changed()

    def test_the_people_search_refuses_in_json(self):
        response = client_for(self.ada.user).get(reverse("socialhub:clearance_people"), {"q": "a"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(set(response.json()), {"error"})

    def test_the_doors_answer_the_methods_they_mean(self):
        root = client_for(self.root)
        for name, args in (("clearance_add", []), ("clearance_delete", [self.internal.pk])):
            with self.subTest(door=name):
                self.assertEqual(root.get(reverse(f"socialhub:{name}", args=args)).status_code, 405)
        for name in ("clearances", "clearance_people"):
            with self.subTest(door=name):
                self.assertEqual(root.post(reverse(f"socialhub:{name}")).status_code, 405)
        self.assertTrue(Clearance.objects.filter(pk=self.internal.pk).exists())

    def test_the_doors_that_edited_a_clearance_in_place_are_gone(self):
        for name in ("clearance_member", "clearance_speeds"):
            with self.subTest(door=name), self.assertRaises(NoReverseMatch):
                reverse(f"socialhub:{name}", args=[self.internal.pk])
        # Their old addresses answer nobody, a superuser included.
        root = client_for(self.root)
        base = reverse("socialhub:clearances")
        for path, data in ((f"{base}{self.internal.pk}/members/", {"who": "cy"}),
                           (f"{base}{self.internal.pk}/members/",
                            {"action": "remove", "person": self.ada.pk}),
                           (f"{base}{self.internal.pk}/speeds/", {"regen_security": "99"})):
            with self.subTest(path=path):
                self.assertEqual(root.post(path, data).status_code, 404)
        self.assert_nothing_changed()
        self.internal.refresh_from_db()
        self.assertIsNone(self.internal.regen_security)

    def test_a_clearance_nobody_made_is_a_404_to_delete(self):
        missing = Clearance.objects.order_by("-pk").first().pk + 1
        response = client_for(self.root).post(reverse("socialhub:clearance_delete", args=[missing]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Clearance.objects.count(), 2)


class ClearancePeopleTests(ClearanceFixture):
    """``clearance_people``: the New clearance modal's search, JSON."""

    def search(self, q=None):
        data = {} if q is None else {"q": q}
        response = client_for(self.root).get(reverse("socialhub:clearance_people"), data)
        self.assertEqual(response.status_code, 200)
        return response.json()["people"]

    def names(self, q):
        return [row["name"] for row in self.search(q)]

    def test_no_query_answers_nobody(self):
        for q in (None, "", "   ", "\t\n"):
            with self.subTest(q=q):
                self.assertEqual(self.search(q), [])

    def test_the_shape_of_an_answer(self):
        response = client_for(self.root).get(reverse("socialhub:clearance_people"), {"q": "cy"})
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"people": [
            {"pk": self.cy.pk, "name": "Cy", "username": "cy"}]})

    def test_somebody_is_found_by_name_username_slug_or_email(self):
        found = person("zed")
        found.display_name = "Grace Hopper"
        found.slug = "admiral-g"
        found.save()
        found.user.email = "cobol@navy.example"
        found.user.save()
        for q in ("grace hop", "HOPPER", "zed", "admiral-g", "cobol@navy", "NAVY.EXAMPLE"):
            with self.subTest(q=q):
                self.assertEqual([row["pk"] for row in self.search(q)], [found.pk])

    def test_the_query_is_tidied_before_it_is_asked(self):
        grace = person("grace")
        grace.display_name = "Grace Hopper"
        grace.save()
        self.assertEqual(self.names("  grace \t  hopper "), ["Grace Hopper"])

    def test_only_people_with_an_account_are_offered(self):
        Person.objects.create(display_name="Ghost Ada")               # no login
        self.assertEqual(self.names("ada"), ["Ada"])

    def test_at_most_twenty_ordered_by_name(self):
        for n in range(25):
            person(f"crew{n:02d}")                                    # display names Crew00…
        found = self.names("crew")
        self.assertEqual(len(found), 20)
        self.assertEqual(found, sorted(found))
        self.assertEqual(found[0], "Crew00")
        self.assertEqual(found[-1], "Crew19")

    def test_a_search_names_no_email(self):
        rows = self.search("example.com")                            # found by it, never shown
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row), {"pk", "name", "username"})
            self.assertNotIn("@", str(row))


class ClearancesTabPageTests(ClearanceFixture):
    def page(self, **query):
        response = client_for(self.root).get(reverse("socialhub:clearances"), query)
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_page_lists_each_clearance_with_its_holders_and_the_room_left(self):
        response = self.page()
        rows = {row["clearance"].slug: row for row in response.context["rows"]}
        self.assertEqual(list(rows), ["confidential", "internal"])
        self.assertEqual(rows["internal"]["holders"], [self.ada])
        self.assertEqual(rows["internal"]["more_holders"], 0)
        self.assertEqual(rows["internal"]["kept"], 0)
        self.assertEqual([s["pool"] for s in rows["internal"]["speeds"]],
                         ["security", "compute", "storage"])
        self.assertEqual(response.context["total"], 2)
        self.assertEqual(response.context["max_clearances"], MAX_CLEARANCES)
        self.assertEqual(response.context["room_left"], MAX_CLEARANCES - 2)
        self.assertFalse(response.context["draft"]["open"])
        self.assertNotIn("people", response.context)                 # no datalist any more
        self.assertNotContains(response, 'data-testid="clearances-full"')
        self.assertNotContains(response, 'data-testid="clearances-empty"')

    def test_the_table_and_the_cards_both_carry_each_clearance(self):
        response = self.page()
        self.assertContains(response, 'data-testid="clearances-table"', count=1)
        self.assertContains(response, 'data-testid="clearances-cards"', count=1)
        for slug in ("internal", "confidential"):
            with self.subTest(clearance=slug):
                self.assertContains(response, f'data-testid="clearance-{slug}"', count=1)
                self.assertContains(response, f'data-testid="clearance-card-{slug}"', count=1)
                self.assertContains(response, f'data-testid="holders-{slug}"', count=2)
                self.assertContains(response, f'data-testid="clearance-delete-{slug}"', count=2)

    def test_the_new_clearance_modal_is_offered(self):
        response = self.page()
        for testid in ("clearance-new-open", "clearance-new", "clearance-people-search",
                       "clearance-chosen"):
            with self.subTest(testid=testid):
                self.assertContains(response, f'data-testid="{testid}"', count=1)
        self.assertContains(response, reverse("socialhub:clearance_people"))
        self.assertContains(response, f'action="{reverse("socialhub:clearance_add")}"')
        self.assertContains(response, 'id="clearance-draft"')

    def test_speeds_are_shown_and_a_blank_one_is_the_pool_rate(self):
        self.internal.regen_compute = Decimal("12.5")
        self.internal.save()
        row = next(r for r in self.page().context["rows"] if r["clearance"] == self.internal)
        self.assertEqual({s["pool"]: s["value"] for s in row["speeds"]},
                         {"security": None, "compute": Decimal("12.5"), "storage": None})

    def test_holders_show_four_by_name_then_how_many_more(self):
        crew = [person(f"crew{n}") for n in range(5)]                # Crew0…Crew4
        self.internal.members.add(*crew)                              # with Ada: six
        response = self.page()
        row = next(r for r in response.context["rows"] if r["clearance"] == self.internal)
        self.assertEqual([p.display_name for p in row["holders"]], ["Ada", "Crew0", "Crew1", "Crew2"])
        self.assertEqual(row["more_holders"], 2)
        self.assertContains(response, "+2 more", count=2)             # table and card
        self.assertNotContains(response, "Crew3")
        other = next(r for r in response.context["rows"] if r["clearance"] == self.confidential)
        self.assertEqual((other["holders"], other["more_holders"]), ([self.bob], 0))

    def test_a_clearance_nobody_holds_says_so(self):
        self.internal.members.clear()
        response = self.page()
        row = next(r for r in response.context["rows"] if r["clearance"] == self.internal)
        self.assertEqual((row["holders"], row["more_holders"]), ([], 0))
        self.assertContains(response, "Nobody yet.", count=2)

    def test_pages_of_five(self):
        from toto.socialhub.views.clearances import PER_PAGE

        self.assertEqual(PER_PAGE, 5)
        self.assertFalse(self.page().context["is_paginated"])
        for n in range(4):
            Clearance.objects.create(name=f"p{n}", slug=f"p{n}")      # six in all
        first = self.page()
        self.assertTrue(first.context["is_paginated"])
        self.assertEqual([r["clearance"].slug for r in first.context["rows"]],
                         ["confidential", "internal", "p0", "p1", "p2"])
        self.assertContains(first, 'href="?page=2"')
        self.assertEqual(first.context["total"], 6)                   # all of them, not the page
        second = self.page(page="2")
        self.assertEqual([r["clearance"].slug for r in second.context["rows"]], ["p3"])
        self.assertContains(second, 'data-testid="clearance-p3"')
        self.assertNotContains(second, 'data-testid="clearance-internal"')
        self.assertEqual(second.context["page_obj"].number, 2)

    def test_a_junk_page_number_serves_a_page(self):
        for n in range(4):
            Clearance.objects.create(name=f"p{n}", slug=f"p{n}")
        for junk in ("abc", "", "1.5", "²"):
            with self.subTest(page=junk):
                self.assertEqual(self.page(page=junk).context["page_obj"].number, 1)
        self.assertEqual(self.page(page="99").context["page_obj"].number, 2)   # the last

    def test_a_clearance_that_keeps_something_says_so_and_cannot_be_deleted(self):
        for slug in ("kept-1", "kept-2"):
            layer = MapLayer.objects.create(name=slug, slug=slug)
            MapLayerClearance.objects.create(layer=layer, clearance=self.internal)
        response = self.page()
        rows = {row["clearance"].slug: row for row in response.context["rows"]}
        self.assertEqual((rows["internal"]["kept"], rows["confidential"]["kept"]), (2, 0))
        self.assertContains(response, "2 things", count=2)
        buttons = re.findall(r'<button type="submit" data-testid="clearance-delete-([\w-]+)"([^>]*)>',
                             response.content.decode())
        disabled = {slug: bool(re.search(r"(?<![\w:-])disabled(?![\w:=-])", rest))
                    for slug, rest in buttons}
        self.assertEqual(disabled, {"internal": True, "confidential": False})
        self.assertEqual(len(buttons), 4)                             # table and card each

    def test_no_clearance_at_all(self):
        Clearance.objects.all().delete()
        response = self.page()
        self.assertContains(response, 'data-testid="clearances-empty"')
        self.assertNotContains(response, 'data-testid="clearances-table"')
        self.assertNotContains(response, 'data-testid="clearances-cards"')
        self.assertContains(response, 'data-testid="clearance-new-open"')
        self.assertEqual(response.context["room_left"], MAX_CLEARANCES)

    def test_a_full_platform_offers_no_new_clearance(self):
        for n in range(Clearance.objects.count(), MAX_CLEARANCES):
            Clearance.objects.create(name=f"x{n}", slug=f"x{n}")
        response = self.page(page="2")
        self.assertEqual(response.context["room_left"], 0)
        self.assertContains(response, 'data-testid="clearances-full"')
        self.assertNotContains(response, 'data-testid="clearance-new-open"')
        self.assertNotContains(response, 'data-testid="clearance-new"')


class ClearanceAddTests(ClearanceFixture):
    def add(self, name):
        return client_for(self.root).post(reverse("socialhub:clearance_add"), {"name": name},
                                          follow=True)

    def test_a_blank_name_is_refused(self):
        before = Clearance.objects.count()
        self.assertContains(self.add("   "), "A clearance needs a name.")
        self.assertEqual(Clearance.objects.count(), before)

    def test_a_name_a_clearance_already_has_is_refused_whatever_its_case(self):
        response = self.add("INTERNAL")
        self.assertContains(response, "There is already a clearance called INTERNAL")
        self.assertFalse(Clearance.objects.filter(name="INTERNAL").exists())
        self.add("Confidential")
        self.assertEqual(Clearance.objects.filter(name__iexact="confidential").count(), 1)

    def test_the_name_is_tidied_and_the_clearance_is_made(self):
        self.assertContains(self.add("  Payroll \t  data  "), "Clearance Payroll data made.")
        made = Clearance.objects.get(name="Payroll data")
        self.assertEqual(made.slug, "payroll-data")
        self.assertTrue(AuditRecord.objects.filter(
            action="SOCIALHUB.CLEARANCE_CREATED", object_id=str(made.pk),
            actor_user=self.root).exists())

    def test_a_long_name_is_cut_to_what_the_page_accepts(self):
        self.add("x" * 300)
        self.assertEqual(len(Clearance.objects.get(name__startswith="xxx").name), 120)

    def test_a_clearance_named_in_another_script_keeps_the_page_working(self):
        self.add("Кадры")
        made = Clearance.objects.get(name="Кадры")
        self.assertTrue(made.slug)                                   # the fallback slug
        self.assertEqual(client_for(self.root).get(
            reverse("socialhub:clearances")).status_code, 200)

    def test_a_long_name_still_gets_a_slug_that_fits_its_column(self):
        self.add("x" * 300)
        made = Clearance.objects.get(name__startswith="xxx")
        self.assertLessEqual(len(made.slug), Clearance._meta.get_field("slug").max_length)


class ClearanceAddCase(ClearanceFixture):
    """Posts to ``clearance_add`` as a superuser, following nothing: a
    refusal is re-drawn (200), a success redirects (302)."""

    def setUp(self):
        self.client = client_for(self.root)
        # The fixture's own clearances and holders are on the chain already.
        self.known = set(AuditRecord.objects.values_list("id", flat=True))

    def new_records(self, *actions):
        return AuditRecord.objects.exclude(id__in=self.known).filter(
            action__in=[f"SOCIALHUB.{action}" for action in actions])

    def add(self, name="restricted", *, people=(), **speeds):
        data = {"name": name, "person": [str(p) for p in people], **speeds}
        return self.client.post(reverse("socialhub:clearance_add"), data)

    def made(self, name="restricted"):
        return Clearance.objects.filter(name=name).first()

    def said(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def assert_made(self, response, name="restricted"):
        self.assertRedirects(response, reverse("socialhub:clearances"), fetch_redirect_response=False)
        made = self.made(name)
        self.assertIsNotNone(made)
        return made

    def refused_page(self, response):
        """A refusal is Post/Redirect/Get: back to the list, which draws the
        draft the session carried (and forgets it)."""
        self.assertRedirects(response, reverse("socialhub:clearances"), fetch_redirect_response=False)
        return self.client.get(response["Location"])

    def assert_refused(self, response, said, name="restricted"):
        page = self.refused_page(response)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, said)
        self.assertTrue(page.context["draft"]["open"])
        self.assertIsNone(self.made(name))
        return page


class ClearanceAddSpeedTests(ClearanceAddCase):
    """Speeds are given when a clearance is made (the admin changes them later)."""

    def test_comma_decimals_and_blanks(self):
        made = self.assert_made(self.add(regen_security="0,25", regen_compute=" 12 ", regen_storage=""))
        self.assertEqual(made.regen_security, Decimal("0.25"))
        self.assertEqual(made.regen_compute, Decimal("12"))
        self.assertIsNone(made.regen_storage)                         # the pool's own rate
        self.assertEqual(made.regen_speeds(), {"security": Decimal("0.25"), "compute": Decimal("12")})

    def test_no_speed_at_all_is_every_pool_at_its_own_rate(self):
        made = self.assert_made(self.add())
        self.assertEqual(made.regen_speeds(), {})

    def test_zero_is_a_speed(self):
        made = self.assert_made(self.add(regen_storage="0"))
        self.assertEqual(made.regen_speeds(), {"storage": Decimal("0")})

    def test_a_negative_speed_is_refused(self):
        for value in ("-1", "-0,5"):
            with self.subTest(value=value):
                self.assert_refused(self.add(regen_compute=value), "greater than or equal to 0")

    def test_not_a_number_is_refused(self):
        page = self.assert_refused(self.add(regen_security="fast"), "fast is not a number.")
        self.assertEqual(page.context["draft"]["error"], "fast is not a number.")
        self.assertEqual(self.said(page), [])              # said inside the modal, not flashed

    def test_one_refused_pool_saves_nothing(self):
        self.assert_refused(self.add(regen_security="5", regen_compute="fast"), "is not a number")
        self.assert_refused(self.add(regen_security="5", regen_storage="-0.5"),
                            "greater than or equal to 0")
        self.assertFalse(self.new_records("CLEARANCE_CREATED").exists())

    def test_more_precision_or_size_than_the_column_holds_is_refused(self):
        for value in ("0.00001", "123456789", "NaN", "Infinity", "-Infinity", "sNaN"):
            with self.subTest(value=value):
                response = self.refused_page(self.add(regen_security=value))
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(self.made())
        made = self.assert_made(self.add(regen_security="99999999.9999"))   # the largest it holds
        self.assertEqual(made.regen_security, Decimal("99999999.9999"))

    def test_the_speeds_it_was_made_with_are_on_the_chain(self):
        made = self.assert_made(self.add(regen_compute="3"))
        record = AuditRecord.objects.filter(action="SOCIALHUB.CLEARANCE_CREATED",
                                            object_id=str(made.pk)).get()
        self.assertEqual({pool: Decimal(v) for pool, v in record.metadata["speeds"].items()},
                         {"compute": Decimal("3")})
        self.assertEqual(record.metadata["clearance"], "restricted")
        self.assertEqual(record.object_type, "socialhub.clearance")
        self.assertEqual(record.actor_user, self.root)
        self.assertFalse(AuditRecord.objects.filter(action="SOCIALHUB.CLEARANCE_CHANGED",
                                                    object_id=str(made.pk)).exists())


class ClearanceAddHolderTests(ClearanceAddCase):
    """Holders are given when a clearance is made, by person pk."""

    def test_holders_are_named_by_pk(self):
        response = self.add(people=[self.cy.pk, self.ada.pk])
        made = self.assert_made(response)
        self.assertEqual(set(made.members.all()), {self.ada, self.cy})
        self.assertEqual(self.said(response), ["Clearance restricted made, held by 2."])

    def test_nobody_named_is_a_clearance_nobody_holds(self):
        response = self.add()
        made = self.assert_made(response)
        self.assertFalse(made.members.exists())
        self.assertEqual(self.said(response), ["Clearance restricted made."])

    def test_junk_is_ignored(self):
        junk = ["cy", "", " ", "１２", "-1", f"{self.cy.pk}x", f" {self.cy.pk}", "²", "0",
                "9" * 19, str(Person.objects.order_by("-pk").first().pk + 100)]
        response = self.add(people=junk)
        made = self.assert_made(response)
        self.assertFalse(made.members.exists())
        self.assertEqual(self.said(response), ["Clearance restricted made."])

    def test_the_same_person_twice_holds_it_once(self):
        response = self.add(people=[self.cy.pk, self.cy.pk])
        made = self.assert_made(response)
        self.assertEqual(list(made.members.all()), [self.cy])
        self.assertEqual(self.said(response), ["Clearance restricted made, held by 1."])

    def test_only_people_with_an_account_hold_it(self):
        ghost = Person.objects.create(display_name="Ghost")           # no login
        response = self.add(people=[ghost.pk, self.cy.pk])
        made = self.assert_made(response)
        self.assertEqual(list(made.members.all()), [self.cy])
        self.assertEqual(self.said(response), ["Clearance restricted made, held by 1."])
        self.assertFalse(ghost.clearances.exists())

    def test_a_holder_keeps_their_communities_and_their_other_clearances(self):
        made = self.assert_made(self.add(people=[self.ada.pk]))
        self.assertEqual(set(self.ada.communities.all()), {self.devs})
        self.assertEqual(set(self.ada.clearances.all()), {self.internal, made})

    def test_a_refusal_saves_no_clearance_and_no_membership(self):
        refusals = (
            ({"regen_security": "fast"}, "restricted"),
            ({"regen_compute": "-2"}, "restricted"),
            ({}, "   "),                                                # no name
            ({}, "INTERNAL"),                                           # a name already taken
        )
        for speeds, name in refusals:
            with self.subTest(name=name, speeds=speeds):
                response = self.refused_page(self.add(name, people=[self.cy.pk, self.bob.pk], **speeds))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Clearance.objects.count(), 2)
                self.assertEqual(set(self.cy.clearances.all()), set())
                self.assertEqual(set(self.bob.clearances.all()), {self.confidential})
                self.assertEqual(set(self.internal.members.all()), {self.ada})
        self.assertFalse(self.new_records("CLEARANCE_CREATED", "CLEARANCE_MEMBER_ADDED").exists())

    def test_the_cap_saves_nothing_either(self):
        for n in range(Clearance.objects.count(), MAX_CLEARANCES):
            Clearance.objects.create(name=f"x{n}", slug=f"x{n}")
        response = self.add(people=[self.cy.pk])
        self.assert_refused(response, f"at most {MAX_CLEARANCES} clearances")
        self.assertEqual(Clearance.objects.count(), MAX_CLEARANCES)
        self.assertFalse(self.cy.clearances.exists())

    def test_a_holder_that_cannot_be_added_undoes_the_clearance(self):
        """All or nothing: the clearance and its holders are one transaction."""
        def refuse(sender, action, **kwargs):
            if action == "pre_add":
                raise ValidationError({"members": ["No holder today."]})

        m2m_changed.connect(refuse, sender=Clearance.members.through)
        try:
            response = self.add(people=[self.cy.pk])
        finally:
            m2m_changed.disconnect(refuse, sender=Clearance.members.through)
        self.assert_refused(response, "No holder today.")
        self.assertFalse(self.cy.clearances.exists())
        self.assertFalse(self.new_records("CLEARANCE_CREATED", "CLEARANCE_MEMBER_ADDED").exists())

    def test_the_chain_has_one_made_and_one_given_per_holder(self):
        made = self.assert_made(self.add(people=[self.ada.pk, self.cy.pk], regen_security="4"))
        created = AuditRecord.objects.filter(action="SOCIALHUB.CLEARANCE_CREATED",
                                             object_id=str(made.pk))
        self.assertEqual(created.count(), 1)
        given = AuditRecord.objects.filter(action="SOCIALHUB.CLEARANCE_MEMBER_ADDED",
                                           object_id=str(made.pk))
        self.assertEqual(sorted(r.metadata["person"] for r in given), sorted([self.ada.slug, self.cy.slug]))
        self.assertTrue(all(r.metadata["clearance"] == "restricted" for r in given))
        self.assertEqual({r.actor_user for r in (*created, *given)}, {self.root})
        self.assertFalse(self.new_records("MEMBER_ADDED").exists())       # no community touched


class ClearanceAddDraftTests(ClearanceAddCase):
    """A refused New clearance comes back with the modal open, what was typed
    kept and the reason inside the modal — through the session and a redirect
    to the list (Post/Redirect/Get), so the page's links stay on the list."""

    def test_a_refusal_redraws_the_page_with_the_modal_open(self):
        ghost = Person.objects.create(display_name="Ghost")
        response = self.refused_page(self.add("  Restricted   docs ", people=[self.cy.pk, ghost.pk, "junk"],
                                              regen_security=" 5 ", regen_compute="fast", regen_storage=""))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "socialhub/clearances.html")
        self.assertEqual(response.context["draft"], {
            "open": True, "name": "Restricted docs", "error": "fast is not a number.",
            "speeds": {"security": "5", "compute": "fast", "storage": ""},
            "people": [{"pk": self.cy.pk, "name": "Cy", "username": "cy"}],
            "targets": [],
        })
        self.assertEqual(self.said(response), [])          # inside the modal, not flashed
        self.assertContains(response, 'data-testid="clearance-new-error"')
        self.assertContains(response, "fast is not a number.")
        self.assertContains(response, 'id="clearance-draft"')
        self.assertContains(response, '"open": true')
        self.assertContains(response, 'data-testid="clearance-new"')  # the modal is drawn
        self.assertEqual([r["clearance"].slug for r in response.context["rows"]],
                         ["confidential", "internal"])                # the list beneath it

    def test_the_draft_keeps_its_holders_in_name_order(self):
        response = self.refused_page(self.add("INTERNAL", people=[self.cy.pk, self.bob.pk, self.ada.pk]))
        self.assertEqual([p["name"] for p in response.context["draft"]["people"]], ["Ada", "Bob", "Cy"])
        self.assertTrue(response.context["draft"]["open"])
        self.assertContains(response, "There is already a clearance called INTERNAL")

    def test_a_typed_speed_is_kept_short(self):
        response = self.refused_page(self.add(regen_security="9" * 50))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["draft"]["speeds"]["security"], "9" * 20)

    def test_what_was_typed_comes_back_as_text_never_as_markup(self):
        name = "</script><script>alert(1)</script>"
        response = self.refused_page(self.add(name, regen_security="<b>x</b>"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["draft"]["name"], name)
        page = response.content.decode()
        self.assertNotIn("<script>alert(1)", page)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt; is not a number.", page)

    def test_the_list_after_a_refusal_pages_to_the_list_itself(self):
        """The refused page is the list (Post/Redirect/Get), so the shared
        pagination\'s relative "?page=2" is a GET of the list, never of the
        POST-only add door."""
        for n in range(4):
            Clearance.objects.create(name=f"p{n}", slug=f"p{n}")      # six: two pages
        response = self.refused_page(self.add("internal"))
        self.assertEqual(response.status_code, 200)
        hrefs = re.findall(r'href="([^"]*\?page=\d+[^"]*)"', response.content.decode())
        self.assertTrue(hrefs)
        for href in hrefs:
            with self.subTest(href=href):
                followed = self.client.get(urljoin(response.wsgi_request.path, href))
                self.assertEqual(followed.status_code, 200)

    def test_the_draft_is_drawn_once(self):
        self.refused_page(self.add("INTERNAL"))
        again = self.client.get(reverse("socialhub:clearances"))
        self.assertFalse(again.context["draft"]["open"])

    def test_a_success_redirects_to_the_list_and_closes_the_modal(self):
        response = self.add(people=[self.cy.pk])
        self.assertRedirects(response, reverse("socialhub:clearances"), fetch_redirect_response=False)
        page = self.client.get(response["Location"])                  # the same session: the flash
        self.assertFalse(page.context["draft"]["open"])
        self.assertContains(page, "Clearance restricted made, held by 1.")
        self.assertContains(page, 'data-testid="clearance-restricted"')


class ClearanceDeleteTests(ClearanceFixture):
    def test_a_clearance_that_still_keeps_something_is_not_removed(self):
        """A real PROTECT (a map layer kept to the clearance), not a patched one."""
        layer = MapLayer.objects.create(name="Kept", slug="kept")
        MapLayerClearance.objects.create(layer=layer, clearance=self.internal)
        response = client_for(self.root).post(
            reverse("socialhub:clearance_delete", args=[self.internal.pk]), follow=True)
        self.assertContains(response, "internal still decides who reads something")
        self.assertTrue(Clearance.objects.filter(pk=self.internal.pk).exists())
        self.assertIn(self.internal, self.ada.clearances.all())

    def test_removing_a_clearance_takes_nobody_out_of_anything_else(self):
        pk = self.internal.pk
        response = client_for(self.root).post(reverse("socialhub:clearance_delete", args=[pk]),
                                              follow=True)
        self.assertContains(response, "Clearance internal removed.")
        self.assertEqual(set(self.ada.communities.all()), {self.devs})
        self.assertEqual(set(self.ada.clearances.all()), set())
        self.assertTrue(AuditRecord.objects.filter(action="SOCIALHUB.CLEARANCE_DELETED",
                                                   object_id=str(pk)).exists())

    def test_removing_a_clearance_records_everybody_who_held_it(self):
        pk = self.internal.pk
        client_for(self.root).post(reverse("socialhub:clearance_delete", args=[pk]))
        self.assertTrue(AuditRecord.objects.filter(
            action="SOCIALHUB.CLEARANCE_MEMBER_REMOVED", object_id=str(pk),
            metadata__person=self.ada.slug).exists())


# ---------------------------------------------------------------------------
# The admin, for somebody who is not a superuser
# ---------------------------------------------------------------------------


class ClearanceAdminLimitsTests(ClearanceFixture):
    """The clearance admin is a superuser's door whatever model permissions a
    staff account holds; the community admin is untouched by it."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.clerk = User.objects.create_user("clerk", "clerk@example.com", "pw", is_staff=True)
        cls.clerk.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="socialhub",
            content_type__model__in=("community", "clearance")))

    def model_admin(self, model):
        from django.contrib import admin

        return admin.site._registry[model]

    def request(self, user):
        request = RequestFactory().post("/")
        request.user = user
        return request

    def test_staff_may_change_and_delete_a_community_but_never_a_clearance(self):
        communities = self.model_admin(Community)
        clearances = self.model_admin(Clearance)
        request = self.request(self.clerk)
        self.assertTrue(communities.has_change_permission(request, self.devs))
        self.assertTrue(communities.has_delete_permission(request, self.devs))
        for door in ("has_module_permission", "has_view_permission", "has_add_permission"):
            with self.subTest(door=door):
                self.assertFalse(getattr(clearances, door)(request))
        self.assertFalse(clearances.has_change_permission(request, self.internal))
        self.assertFalse(clearances.has_delete_permission(request, self.internal))
        self.assertTrue(clearances.has_delete_permission(self.request(self.root), self.internal))

    def test_staff_cannot_delete_a_clearance_through_the_admin(self):
        client = client_for(self.clerk)
        response = client.post(reverse("admin:socialhub_clearance_delete", args=[self.internal.pk]),
                               {"post": "yes"})
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(Clearance.objects.filter(pk=self.internal.pk).exists())

    def test_staff_cannot_make_a_clearance_through_the_admin(self):
        client = client_for(self.clerk)
        response = client.post(reverse("admin:socialhub_clearance_add"), {
            "name": "restricted", "slug": "restricted", "regen_compute": "9"})
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(Clearance.objects.filter(slug="restricted").exists())
        self.assertNotContains(client.get(reverse("admin:index")), "Clearances")

    def test_the_community_admin_carries_no_speed(self):
        form = self.model_admin(Community).get_form(self.request(self.root))()
        for name in ("regen_security", "regen_compute", "regen_storage", "is_clearance"):
            self.assertNotIn(name, form.fields)

    def test_a_superuser_makes_a_clearance_with_speeds_in_the_admin(self):
        response = client_for(self.root).post(reverse("admin:socialhub_clearance_add"), {
            "name": "restricted", "slug": "restricted",
            "regen_security": "", "regen_compute": "", "regen_storage": "2.5"})
        self.assertEqual(response.status_code, 302)
        restricted = Clearance.objects.get(slug="restricted")
        self.assertEqual(restricted.regen_speeds(), {"storage": Decimal("2.5")})

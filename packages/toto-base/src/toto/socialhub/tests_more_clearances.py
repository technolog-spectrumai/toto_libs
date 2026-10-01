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

import io
import re
from decimal import Decimal
from unittest import mock
from urllib.parse import urljoin

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed
from django.test import Client, RequestFactory, TestCase
from django.urls import NoReverseMatch, reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from django.db.models import OuterRef

from toto.locations.models import MapDomain, MapDomainClearance, MapLayer, MapLayerInDomain
from toto.people.models import Person
from toto.socialhub import clearance_access
from toto.socialhub.models import MAX_CLEARANCES, Clearance, Community
from toto.socialhub.plugins import clearance_plugins
from toto.socialhub.plugins.clearance_plugins import SEARCH_LIMIT, ClearanceTargetPlugin

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


class OnThePlan:
    """The Clearances tab asks for the Superuser plan as well (2026-10-01,
    the review of stage 37c): `bootstrap_plans` puts `root` on it, and
    `bare_root`, made after, is a superuser without it. Only the classes that
    open the tab take it — it makes a community of its own, which the others
    count."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())
            cls.root = User.objects.get(pk=cls.root.pk)
        cls.bare_root = User.objects.create_superuser("bareroot", "bare@example.com", "pw")


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
    """The group rule (2026-09-30): clearances go on groups — here map domains,
    the socialhub's own fixture — and an item is read by the rule of its groups,
    pessimistically: a clearance of EVERY kept group it is in."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        def domain(name, *clearances):
            made = MapDomain.objects.create(name=name)
            for clearance in clearances:
                MapDomainClearance.objects.create(domain=made, clearance=clearance)
            return made

        def layer(slug, *domains, **extra):
            made = MapLayer.objects.create(name=slug, slug=slug, **extra)
            for group in domains:
                MapLayerInDomain.objects.create(domain=group, map_layer=made)
            return made

        cls.d_internal = domain("Internal", cls.internal)
        cls.d_confidential = domain("Confidential", cls.confidential)
        cls.d_either = domain("Either", cls.internal, cls.confidential)
        cls.d_open = domain("Open domain")
        cls.open_layer = layer("open")
        cls.in_open_domain = layer("in-open-domain", cls.d_open)
        cls.internal_layer = layer("internal-only", cls.d_internal)
        cls.either_layer = layer("either", cls.d_either)
        cls.two_domains = layer("two-domains", cls.d_internal, cls.d_confidential)
        cls.half_kept = layer("half-kept", cls.d_internal, cls.d_open)
        cls.inactive = layer("inactive", is_active=False)

    @staticmethod
    def groups():
        return MapDomain.objects.filter(map_layer_rows__map_layer=OuterRef("pk"))

    def readable(self, user, **kwargs):
        return set(clearance_access.group_gate(user, MapLayer.objects.all(), groups=self.groups(),
                                               **kwargs).values_list("slug", flat=True))

    def hidden(self, user, layer):
        return clearance_access.group_hidden(user, MapDomain.objects.filter(map_layer_rows__map_layer=layer))

    OPEN = {"open", "in-open-domain", "inactive"}

    def test_person_of_and_clearance_ids_of_know_nobody_without_a_person(self):
        stray = User.objects.create_user("stray", password="pw")
        for user in (None, AnonymousUser(), stray):
            self.assertIsNone(clearance_access.person_of(user))
            self.assertEqual(clearance_access.clearance_ids_of(user), set())

    def test_clearance_ids_of_counts_clearances_and_never_a_community(self):
        self.assertEqual(clearance_access.clearance_ids_of(self.ada.user), {self.internal.pk})
        self.assertEqual(clearance_access.clearance_ids_of(self.cy.user), set())

    def test_an_item_in_no_kept_group_is_everybodys(self):
        self.assertEqual(self.readable(self.cy.user), self.OPEN)
        self.assertEqual(self.readable(AnonymousUser()), self.OPEN)

    def test_one_clearance_of_each_kept_group_opens_it(self):
        # Ada holds internal: the internal domain, and the domain either clearance opens.
        self.assertEqual(self.readable(self.ada.user),
                         self.OPEN | {"internal-only", "either", "half-kept"})
        # Bob holds confidential: only the domain either clearance opens.
        self.assertEqual(self.readable(self.bob.user), self.OPEN | {"either"})

    def test_an_item_in_two_kept_groups_needs_a_clearance_of_each(self):
        self.assertNotIn("two-domains", self.readable(self.ada.user))
        self.confidential.members.add(self.ada)
        self.assertIn("two-domains", self.readable(self.ada.user))

    def test_a_group_without_clearances_does_not_constrain(self):
        """half-kept is in the internal domain and an open one: the kept one decides."""
        self.assertIn("half-kept", self.readable(self.ada.user))
        self.assertNotIn("half-kept", self.readable(self.cy.user))

    def test_a_superuser_reads_everything(self):
        self.assertEqual(self.readable(self.root), set(MapLayer.objects.values_list("slug", flat=True)))

    def test_the_owner_is_no_reader_of_a_kept_item(self):
        self.internal_layer.owner = self.cy
        self.internal_layer.save()
        self.assertNotIn("internal-only", self.readable(self.cy.user))
        self.assertTrue(self.hidden(self.cy.user, self.internal_layer))

    def test_the_apps_own_rule_applies_to_items_in_no_kept_group_only(self):
        """A clearance both keeps and grants: ``open`` narrows what is not kept,
        and a holder reads a kept item whatever that rule says."""
        MapLayerInDomain.objects.create(domain=self.d_confidential, map_layer=self.inactive)
        active = Q(is_active=True)
        self.assertEqual(self.readable(self.cy.user, open=active), {"open", "in-open-domain"})
        self.assertIn("inactive", self.readable(self.bob.user, open=active))

    def test_an_item_is_listed_once_however_many_groups_open_it(self):
        self.confidential.members.add(self.ada)
        rows = list(clearance_access.group_gate(self.ada.user, MapLayer.objects.filter(slug="two-domains"),
                                                groups=self.groups()))
        self.assertEqual(len(rows), 1)

    def test_hidden_is_the_per_object_twin_of_the_gate(self):
        for user in (self.ada.user, self.bob.user, self.cy.user, AnonymousUser(), self.root):
            readable = self.readable(user)
            for layer in MapLayer.objects.all():
                with self.subTest(user=getattr(user, "username", "anonymous"), layer=layer.slug):
                    self.assertEqual(not self.hidden(user, layer), layer.slug in readable)

    def test_item_groups_kept_says_whether_any_of_its_groups_is_kept(self):
        kept = clearance_access.item_groups_kept
        self.assertTrue(kept(MapDomain.objects.filter(map_layer_rows__map_layer=self.half_kept)))
        self.assertFalse(kept(MapDomain.objects.filter(map_layer_rows__map_layer=self.in_open_domain)))
        self.assertFalse(kept(MapDomain.objects.filter(map_layer_rows__map_layer=self.open_layer)))

    def test_clearances_of_a_group_by_name(self):
        self.assertEqual(clearance_access.clearances_of(self.d_either, rows="clearance_rows"),
                         [self.confidential, self.internal])
        self.assertEqual(clearance_access.clearances_of(self.d_open, rows="clearance_rows"), [])


class SetClearancesTests(ClearanceFixture):
    ROWS = "clearance_rows"

    def setUp(self):
        self.layer = MapDomain.objects.create(name="Layer")       # any group with clearance_rows

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


class ClearancesTabRefusalTests(OnThePlan, ClearanceFixture):
    """Every remaining door is a superuser's; the doors that edited a
    clearance in place are gone (holders and speeds change in the admin)."""

    def doors(self):
        """(method, url, data) for every door the tab still has."""
        return (
            ("get", reverse("socialhub:clearances"), {}),
            ("get", reverse("socialhub:clearance_people"), {"q": "ada"}),
            ("get", reverse("socialhub:clearance_graph"), {"holders": "1"}),
            ("get", reverse("socialhub:clearance_targets"), {"kind": "locations.domain", "q": "a"}),
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

    def test_every_door_refuses_a_superuser_without_the_plan_and_changes_nothing(self):
        """Superuser functionality asks for the Superuser plan too (2026-10-01,
        the review of stage 37c): a bucket's and a wiki topic's own doors did,
        and this tab set both through them with the superuser bit alone."""
        if not apps.is_installed("toto.subscriptions"):
            self.skipTest("a host that sells no plan: the superuser bit is the rule")
        bare = client_for(self.bare_root)
        for method, url, data in self.doors():
            with self.subTest(url=url):
                response = getattr(bare, method)(url, data)
                self.assertEqual(response.status_code, 403)
                self.assertIn("This needs a superuser on the Superuser plan.",
                              response.content.decode())
        self.assert_nothing_changed()

    def test_a_superuser_on_the_plan_opens_every_door(self):
        root = client_for(self.root)
        for method, url, data in self.doors()[:4]:
            with self.subTest(url=url):
                self.assertEqual(getattr(root, method)(url, data).status_code, 200)

    def test_the_tab_shows_only_where_its_doors_open(self):
        """The strip asks the plan too (2026-10-01, 37c.32): a superuser off
        the plan saw the tab on Profiles and Communities, and every click on
        it was a 403 page."""
        tab = 'data-testid="tab-clearances"'
        for url in (reverse("socialhub:profile_list"), reverse("socialhub:community_list")):
            with self.subTest(url=url):
                self.assertContains(client_for(self.root).get(url), tab)
                self.assertNotContains(client_for(self.ada.user).get(url), tab)
                if apps.is_installed("toto.subscriptions"):
                    page = client_for(self.bare_root).get(url)
                    self.assertEqual(page.status_code, 200)
                    self.assertNotContains(page, tab)

    def test_the_filter_is_the_doors_rule(self):
        from toto.socialhub.templatetags.socialhub_flags import may_manage_clearances
        from toto.socialhub.views.clearances import may_manage

        for user in (self.root, self.bare_root, self.ada.user, AnonymousUser()):
            with self.subTest(user=str(user)):
                self.assertEqual(may_manage_clearances(user), may_manage(user))
        self.assertTrue(may_manage_clearances(self.root))
        self.assertFalse(may_manage_clearances(self.ada.user))
        self.assertFalse(may_manage_clearances(AnonymousUser()))

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


class ClearancePeopleTests(OnThePlan, ClearanceFixture):
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


class ClearancesTabPageTests(OnThePlan, ClearanceFixture):
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
            domain = MapDomain.objects.create(name=slug)
            MapDomainClearance.objects.create(domain=domain, clearance=self.internal)
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


class ClearanceAddTests(OnThePlan, ClearanceFixture):
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


class ClearanceAddCase(OnThePlan, ClearanceFixture):
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


class ClearanceDeleteTests(OnThePlan, ClearanceFixture):
    def test_a_clearance_that_still_keeps_something_is_not_removed(self):
        """A real PROTECT (a map domain kept to the clearance), not a patched one."""
        domain = MapDomain.objects.create(name="Kept")
        MapDomainClearance.objects.create(domain=domain, clearance=self.internal)
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


# ---------------------------------------------------------------------------
# What a clearance clears (2026-09-30): the ClearanceTargetPlugin point and
# the New clearance modal's "What it clears". Every test here registers FAKE
# kinds only (the registry patched, cleared, for the test), so nothing
# depends on which apps are installed.
# ---------------------------------------------------------------------------


class Thing:
    """Something a fake kind keeps — just a pk, a label and a detail."""

    def __init__(self, pk, label, detail=""):
        self.pk, self.label, self.detail = pk, label, detail

    def __str__(self):
        return self.label

    def __repr__(self):
        return f"Thing({self.pk})"


class FakeThings(ClearanceTargetPlugin):
    """A kind that remembers what it was asked; ``keep`` also notes where it
    ran (inside which transaction, after which holders)."""

    key = "fake.thing"
    title = "Things"
    icon = "cube"
    order = 50

    def __init__(self, *things):
        self.things = {thing.pk: thing for thing in things}
        self.searches, self.resolved, self.kept = [], [], []

    def search(self, q, limit=SEARCH_LIMIT):
        self.searches.append((q, limit))
        return [self.row(thing) for thing in self.things.values()
                if q.lower() in thing.label.lower()][:limit]

    def resolve(self, pks):
        pks = set(pks)
        self.resolved.append(pks)
        return [self.things[pk] for pk in sorted(pks) if pk in self.things]

    def keep(self, objects, clearance, *, actor):
        self.kept.append({
            "objects": list(objects), "clearance": clearance, "actor": actor,
            "in_atomic_block": connection.in_atomic_block,
            "depth": len(connection.atomic_blocks),
            "saved": Clearance.objects.filter(pk=clearance.pk).exists(),
            "holders": set(clearance.members.all()),
        })
        return len(objects)

    def detail(self, obj):
        return obj.detail


class FakeDecks(FakeThings):
    key = "fake.deck"
    title = "Decks"
    icon = "person-chalkboard"
    order = 10


class RefusingThings(FakeThings):
    """A kind whose door refuses — the app's own ValidationError."""

    key = "fake.refusing"
    title = "Refusing things"
    order = 90

    def keep(self, objects, clearance, *, actor):
        super().keep(objects, clearance, actor=actor)
        raise ValidationError("This thing is kept elsewhere.")


class FakeLayers(ClearanceTargetPlugin):
    """A kind that really writes: map domains (a real group) kept through their
    clearance table, so an undone clearance can be seen undoing an app's rows
    too."""

    key = "fake.layer"
    title = "Layers"
    icon = "layer-group"
    order = 30

    def search(self, q, limit=SEARCH_LIMIT):
        return [self.row(domain) for domain in MapDomain.objects.filter(name__icontains=q)[:limit]]

    def resolve(self, pks):
        return list(MapDomain.objects.filter(pk__in=pks).order_by("pk"))

    def keep(self, objects, clearance, *, actor):
        for domain in objects:
            MapDomainClearance.objects.create(domain=domain, clearance=clearance)
        return len(objects)


def registered(test, *plugins):
    """Only ``plugins`` are kinds for the rest of ``test``."""
    patcher = mock.patch.dict(ClearanceTargetPlugin.registry,
                              {plugin.get_key(): plugin for plugin in plugins}, clear=True)
    patcher.start()
    test.addCleanup(patcher.stop)


class ClearanceTargetRegistryTests(ClearanceFixture):
    """The plugin point itself: its own registry, its order, ``add_to``."""

    def test_the_kinds_have_a_registry_of_their_own(self):
        from toto.core.plugin import BasePlugin

        self.assertIsNot(ClearanceTargetPlugin.registry, BasePlugin.registry)
        self.assertTrue(all(isinstance(plugin, ClearanceTargetPlugin)
                            for plugin in ClearanceTargetPlugin.registry.values()))

    def test_kinds_are_in_their_order_whatever_order_they_came_in(self):
        things, decks, layers = FakeThings(), FakeDecks(), FakeLayers()
        registered(self, things, layers, decks)
        self.assertEqual(clearance_plugins.kinds(), [decks, layers, things])

    def test_no_kind_at_all(self):
        registered(self)
        self.assertEqual(clearance_plugins.kinds(), [])
        self.assertIsNone(clearance_plugins.kind("fake.thing"))

    def test_a_kind_by_its_key(self):
        things, decks = FakeThings(), FakeDecks()
        registered(self, things, decks)
        self.assertIs(clearance_plugins.kind("fake.thing"), things)
        self.assertIs(clearance_plugins.kind("fake.deck"), decks)
        for key in ("", "fake", "FAKE.THING", "fake.thing ", "nope"):
            with self.subTest(key=key):
                self.assertIsNone(clearance_plugins.kind(key))

    def test_a_kind_registered_through_the_decorator_lands_in_this_registry(self):
        registered(self)

        @ClearanceTargetPlugin.plugin(key="fake.decorated", title="Decorated", order=1)
        class Decorated(FakeThings):
            pass

        self.assertIsInstance(clearance_plugins.kind("fake.decorated"), Decorated)
        self.assertEqual([p.get_key() for p in clearance_plugins.kinds()], ["fake.decorated"])
        with self.assertRaises(ValueError):                           # a key is registered once
            ClearanceTargetPlugin.register(Decorated)

    def test_the_patched_registry_is_restored(self):
        before = dict(ClearanceTargetPlugin.registry)
        with mock.patch.dict(ClearanceTargetPlugin.registry, {"fake.thing": FakeThings()}, clear=True):
            self.assertEqual(list(ClearanceTargetPlugin.registry), ["fake.thing"])
        self.assertEqual(ClearanceTargetPlugin.registry, before)

    def test_a_row_is_pk_label_and_detail(self):
        self.assertEqual(FakeThings().row(Thing(7, "Budget", "2026")),
                         {"pk": 7, "label": "Budget", "detail": "2026"})
        self.assertEqual(ClearanceTargetPlugin().row(Thing(7, "Budget", "2026")),
                         {"pk": 7, "label": "Budget", "detail": ""})   # the defaults: str(), ""

    def test_the_interface_is_left_to_each_kind(self):
        bare = ClearanceTargetPlugin()
        for call in (lambda: bare.search("x"), lambda: bare.resolve([1]),
                     lambda: bare.keep([], self.internal, actor=self.root)):
            with self.subTest(call=call), self.assertRaises(NotImplementedError):
                call()

    def test_add_to_adds_the_clearance_and_keeps_the_others(self):
        payroll = Clearance.objects.create(name="payroll", slug="payroll")
        self.assertEqual(clearance_plugins.add_to([self.internal], payroll), [self.internal, payroll])
        self.assertEqual(clearance_plugins.add_to([], payroll), [payroll])
        self.assertEqual(clearance_plugins.add_to(
            Clearance.objects.filter(pk__in=[self.internal.pk, self.confidential.pk]).order_by("name"),
            payroll), [self.confidential, self.internal, payroll])

    def test_add_to_adds_once(self):
        current = [self.internal, self.confidential]
        again = Clearance.objects.get(pk=self.internal.pk)          # the same clearance, another object
        self.assertEqual(clearance_plugins.add_to(current, again), [self.internal, self.confidential])
        self.assertEqual(clearance_plugins.add_to(iter(current), self.confidential),
                         [self.internal, self.confidential])
        self.assertEqual(current, [self.internal, self.confidential])  # the caller's list untouched


class ClearanceTargetsPageTests(OnThePlan, ClearanceFixture):
    """The page offers exactly the registered kinds in the modal's "What it
    clears", and no such section when there is none."""

    def page(self):
        response = client_for(self.root).get(reverse("socialhub:clearances"))
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_page_offers_exactly_the_registered_kinds_in_order(self):
        registered(self, FakeThings(), FakeDecks())
        response = self.page()
        self.assertEqual(response.context["target_kinds"], [
            {"key": "fake.deck", "title": "Decks", "icon": "person-chalkboard"},
            {"key": "fake.thing", "title": "Things", "icon": "cube"},
        ])
        for testid in ("clearance-targets", "clearance-target-kind", "clearance-target-search",
                       "clearance-targets-chosen"):
            with self.subTest(testid=testid):
                self.assertContains(response, f'data-testid="{testid}"', count=1)
        page = response.content.decode()
        self.assertEqual(re.findall(r'<option value="([^"]*)" data-icon="([^"]*)">([^<]*)</option>', page),
                         [("fake.deck", "person-chalkboard", "Decks"), ("fake.thing", "cube", "Things")])
        self.assertIn(reverse("socialhub:clearance_targets"), page)     # where the modal searches
        self.assertIn("'fake.deck')", page)                             # the first kind, chosen
        self.assertContains(response, 'name="target"')

    def test_no_kind_is_no_what_it_clears(self):
        registered(self)
        response = self.page()
        self.assertEqual(response.context["target_kinds"], [])
        for testid in ("clearance-targets", "clearance-target-kind", "clearance-target-search"):
            with self.subTest(testid=testid):
                self.assertNotContains(response, f'data-testid="{testid}"')
        self.assertNotContains(response, 'name="target"')
        self.assertContains(response, 'data-testid="clearance-new"')    # the modal itself stays

    def test_a_closed_page_draws_no_targets(self):
        registered(self, FakeThings())
        response = self.page()
        self.assertEqual(response.context["draft"]["targets"], [])
        self.assertFalse(response.context["draft"]["open"])


class ClearanceTargetSearchTests(OnThePlan, ClearanceFixture):
    """``clearance_targets``: the modal's search of one kind, JSON."""

    def setUp(self):
        self.things = FakeThings(Thing(1, "Budget 2026", "finance"), Thing(2, "Budget 2027", "finance"),
                                 Thing(3, "Roadmap", ""))
        registered(self, self.things, FakeDecks())
        self.url = reverse("socialhub:clearance_targets")

    def ask(self, client=None, **query):
        return (client or client_for(self.root)).get(self.url, query)

    def test_a_member_and_staff_are_refused_in_json(self):
        clerk = User.objects.create_user("clerk", "clerk@example.com", "pw", is_staff=True)
        clerk.user_permissions.add(*Permission.objects.filter(content_type__app_label="socialhub"))
        for user in (self.ada.user, clerk):
            with self.subTest(user=user.username):
                response = self.ask(client_for(user), kind="fake.thing", q="budget")
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response["Content-Type"], "application/json")
                self.assertEqual(set(response.json()), {"error"})
                # Refused before the kind is even looked at.
                self.assertEqual(self.ask(client_for(user), kind="nope", q="x").status_code, 403)
        self.assertEqual(self.things.searches, [])

    def test_a_visitor_is_sent_to_sign_in(self):
        response = self.ask(Client(), kind="fake.thing", q="budget")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
        self.assertEqual(self.things.searches, [])

    def test_get_only(self):
        root = client_for(self.root)
        for method in ("post", "put", "delete"):
            with self.subTest(method=method):
                response = getattr(root, method)(f"{self.url}?kind=fake.thing&q=budget")
                self.assertEqual(response.status_code, 405)
        self.assertEqual(self.things.searches, [])

    def test_an_unknown_kind_is_a_404(self):
        for query in ({"kind": "nope", "q": "budget"}, {"q": "budget"}, {"kind": "", "q": "budget"},
                      {"kind": "FAKE.THING", "q": "budget"}):
            with self.subTest(query=query):
                response = self.ask(**query)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response["Content-Type"], "application/json")
                self.assertEqual(set(response.json()), {"error"})

    def test_no_query_answers_nothing_and_asks_nobody(self):
        for q in (None, "", "   ", "\t\n"):
            with self.subTest(q=q):
                response = self.ask(kind="fake.thing", **({} if q is None else {"q": q}))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"results": []})
        self.assertEqual(self.things.searches, [])

    def test_the_query_is_tidied_and_the_limit_passed(self):
        self.ask(kind="fake.thing", q="  budget \t  2026 ")
        self.ask(kind="fake.thing", q="x" * 200)
        self.assertEqual(self.things.searches, [("budget 2026", SEARCH_LIMIT), ("x" * 80, SEARCH_LIMIT)])
        self.assertEqual(SEARCH_LIMIT, 20)

    def test_the_shape_of_an_answer(self):
        response = self.ask(kind="fake.thing", q="budget")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"results": [
            {"pk": 1, "label": "Budget 2026", "detail": "finance"},
            {"pk": 2, "label": "Budget 2027", "detail": "finance"},
        ]})

    def test_only_the_asked_kind_is_searched(self):
        decks = clearance_plugins.kind("fake.deck")
        self.assertEqual(self.ask(kind="fake.deck", q="budget").json(), {"results": []})
        self.assertEqual(decks.searches, [("budget", SEARCH_LIMIT)])
        self.assertEqual(self.things.searches, [])


class ClearanceAddTargetsTests(ClearanceAddCase):
    """``clearance_add`` with ``target=<kind>:<pk>``: each kind keeps what it
    was given, in the same transaction as the clearance and its holders."""

    def setUp(self):
        super().setUp()
        self.things = FakeThings(Thing(1, "Budget", "finance"), Thing(2, "Roadmap"), Thing(3, "Payslips"))
        self.decks = FakeDecks(Thing(1, "Board deck"), Thing(8, "Kickoff"))
        self.refusing = RefusingThings(Thing(5, "Locked"))
        self.layers = FakeLayers()
        registered(self, self.things, self.decks, self.refusing, self.layers)

    def post(self, name="restricted", *, people=(), targets=(), **speeds):
        data = {"name": name, "person": [str(p) for p in people], "target": list(targets), **speeds}
        return self.client.post(reverse("socialhub:clearance_add"), data)

    def test_each_kind_keeps_its_things_once_with_the_clearance_and_the_actor(self):
        depth = len(connection.atomic_blocks)
        response = self.post(people=[self.cy.pk],
                             targets=["fake.thing:2", "fake.deck:8", "fake.thing:1", "fake.deck:1"])
        made = self.assert_made(response)
        self.assertEqual(len(self.things.kept), 1)
        self.assertEqual(len(self.decks.kept), 1)
        self.assertEqual(self.refusing.kept, [])
        for plugin, objects in ((self.things, [1, 2]), (self.decks, [1, 8])):
            with self.subTest(kind=plugin.get_key()):
                call = plugin.kept[0]
                self.assertEqual([obj.pk for obj in call["objects"]], objects)
                self.assertEqual(call["clearance"], made)
                self.assertEqual(call["actor"].pk, self.root.pk)
                # Inside the view's own transaction, after the clearance and its holders.
                self.assertTrue(call["in_atomic_block"])
                self.assertGreater(call["depth"], depth)
                self.assertTrue(call["saved"])
                self.assertEqual(call["holders"], {self.cy})

    def test_the_things_kept_are_counted_in_a_message(self):
        response = self.post(people=[self.cy.pk], targets=["fake.thing:1", "fake.thing:3", "fake.deck:8"])
        self.assert_made(response)
        said = self.said(response)
        self.assertEqual(said[0], "Clearance restricted made, held by 1.")
        self.assertEqual(said[1:], ["It keeps 3 groups: what is in them is read only by its holders "
                                    "and superusers now."])

    def test_no_target_is_no_keep_and_no_message(self):
        response = self.post(people=[self.cy.pk])
        self.assert_made(response)
        self.assertEqual(self.said(response), ["Clearance restricted made, held by 1."])
        for plugin in (self.things, self.decks, self.refusing):
            self.assertEqual(plugin.kept, [])
            self.assertEqual(plugin.resolved, [])

    def test_junk_targets_are_ignored(self):
        junk = ["fake.thing", "fake.thing:", ":1", "1", "", " ", "fake.thing:x", "fake.thing:-1",
                "fake.thing:１", "fake.thing: 1", "fake.thing:1 ", " fake.thing:1", "nope:1",
                "FAKE.THING:1", "fake.thing:1:2", "fake.thing:" + "9" * 19, "fake.thing:²"]
        response = self.post(targets=junk)
        self.assert_made(response)
        self.assertEqual(self.things.kept, [])
        self.assertEqual(self.things.resolved, [])                    # never even looked up
        self.assertEqual(self.said(response), ["Clearance restricted made."])

    def test_a_pk_that_does_not_exist_is_ignored(self):
        response = self.post(targets=["fake.thing:99", "fake.thing:2", "fake.deck:99"])
        self.assert_made(response)
        self.assertEqual([obj.pk for obj in self.things.kept[0]["objects"]], [2])
        self.assertEqual(self.decks.resolved, [{99}])
        self.assertEqual(self.decks.kept, [])                         # nothing of its kind: not asked
        self.assertIn("It keeps 1 group", self.said(response)[-1])

    def test_only_missing_pks_is_nothing_kept_and_no_message(self):
        response = self.post(targets=["fake.thing:99", "fake.deck:0"])
        self.assert_made(response)
        self.assertEqual((self.things.kept, self.decks.kept), ([], []))
        self.assertEqual(self.said(response), ["Clearance restricted made."])

    def test_the_same_target_twice_is_kept_and_counted_once(self):
        response = self.post(targets=["fake.thing:1", "fake.thing:1", "fake.thing:01"])
        self.assert_made(response)
        self.assertEqual(self.things.resolved, [{1}])
        self.assertEqual([obj.pk for obj in self.things.kept[0]["objects"]], [1])
        self.assertIn("It keeps 1 group", self.said(response)[-1])

    def test_a_kind_that_writes_keeps_through_its_own_rows(self):
        layer = MapDomain.objects.create(name="Pipes")
        made = self.assert_made(self.post(targets=[f"fake.layer:{layer.pk}"]))
        self.assertEqual(list(MapDomainClearance.objects.filter(clearance=made).values_list("domain", flat=True)),
                         [layer.pk])

    def test_a_refused_keep_undoes_the_whole_clearance(self):
        layer = MapDomain.objects.create(name="Pipes")
        response = self.post(people=[self.cy.pk, self.bob.pk],
                             targets=[f"fake.layer:{layer.pk}", "fake.thing:1", "fake.refusing:5"])
        # The kinds before it did keep (inside the transaction)...
        self.assertEqual(len(self.things.kept), 1)
        self.assertEqual(len(self.refusing.kept), 1)
        page = self.assert_refused(response, "This thing is kept elsewhere.")
        # ...and all of it is undone: no clearance, no holder, no app row, no record.
        self.assertEqual(Clearance.objects.count(), 2)
        self.assertFalse(self.cy.clearances.exists())
        self.assertEqual(set(self.bob.clearances.all()), {self.confidential})
        self.assertFalse(MapDomainClearance.objects.exists())
        self.assertFalse(self.new_records("CLEARANCE_CREATED", "CLEARANCE_MEMBER_ADDED").exists())
        self.assertFalse(AuditRecord.objects.exclude(id__in=self.known).exists())
        self.assertEqual(page.context["draft"]["error"], "This thing is kept elsewhere.")
        self.assertEqual(self.said(page), [])                          # inside the modal, not flashed

    def test_the_refusal_draft_carries_the_targets(self):
        layer = MapDomain.objects.create(name="Pipes")
        page = self.assert_refused(
            self.post(people=[self.cy.pk],
                      targets=["fake.thing:2", "fake.refusing:5", f"fake.layer:{layer.pk}", "nope:1",
                               "fake.thing:99"]),
            "This thing is kept elsewhere.")
        draft = page.context["draft"]
        self.assertTrue(draft["open"])
        self.assertEqual(draft["people"], [{"pk": self.cy.pk, "name": "Cy", "username": "cy"}])
        self.assertEqual(draft["targets"], [
            {"key": "fake.thing", "kind": "Things", "icon": "cube", "pk": 2, "label": "Roadmap", "detail": ""},
            {"key": "fake.refusing", "kind": "Refusing things", "icon": "cube", "pk": 5, "label": "Locked",
             "detail": ""},
            {"key": "fake.layer", "kind": "Layers", "icon": "layer-group", "pk": layer.pk, "label": "Pipes",
             "detail": ""},
        ])
        self.assertContains(page, '"key": "fake.refusing"')           # in the draft the modal reads

    def test_any_other_refusal_keeps_nothing_and_carries_the_targets(self):
        for name, speeds, said in (("restricted", {"regen_security": "fast"}, "fast is not a number."),
                                   ("INTERNAL", {}, "There is already a clearance called INTERNAL"),
                                   ("   ", {}, "A clearance needs a name.")):
            with self.subTest(name=name):
                page = self.refused_page(self.post(name, targets=["fake.thing:1"], **speeds))
                self.assertContains(page, said)
                self.assertEqual([(t["key"], t["pk"]) for t in page.context["draft"]["targets"]],
                                 [("fake.thing", 1)])
        self.assertEqual(self.things.kept, [])
        self.assertEqual(Clearance.objects.count(), 2)

    def test_the_cap_keeps_nothing(self):
        for n in range(Clearance.objects.count(), MAX_CLEARANCES):
            Clearance.objects.create(name=f"x{n}", slug=f"x{n}")
        self.assert_refused(self.post(targets=["fake.thing:1"]), f"at most {MAX_CLEARANCES} clearances")
        self.assertEqual(self.things.kept, [])

    def test_a_member_cannot_make_a_clearance_that_keeps_things(self):
        response = client_for(self.ada.user).post(reverse("socialhub:clearance_add"),
                                                  {"name": "restricted", "target": ["fake.thing:1"]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual((self.things.resolved, self.things.kept), ([], []))
        self.assertIsNone(self.made())

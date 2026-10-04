"""The Domains tab (2026-09-30): superusers group map items into map domains
and keep the domains to clearances. Superusers only, everywhere; a table on a
wide screen and cards on a narrow one; New domain, Items, Clearances and
Delete, each a modal posting to its own door; a refused New domain comes back
through the session (Post/Redirect/Get); every change on the audit chain.
What the domains then hide is `tests_more_clearances`.
"""

import io
from unittest import skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.locations import access, domain_views, urls as locations_urls
from toto.locations.models import (
    HAS_GIS, Address, MapDomain, MapDomainClearance, MapLayer, Route, RouteInDomain, Territory,
    Zone,
)
from toto.locations.plugins.domain_plugins import MapDomainKind
from toto.people.models import Person
from toto.socialhub.models import Clearance

User = get_user_model()


class DomainsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.root = User.objects.create_superuser("root", "r@example.org", "x")
        cls.staff = User.objects.create_user("staff", password="x", is_staff=True)
        cls.member = User.objects.create_user("member", password="x")
        cls.board = Clearance.objects.create(name="internal", slug="internal")
        cls.seniors = Clearance.objects.create(name="confidential", slug="confidential")
        Person.objects.create(user=cls.member, display_name="Member").clearances.add(cls.board)
        cls.address = Address.objects.create(street="Długa", building="1", locality_name="Gdańsk",
                                             latitude=54.35, longitude=18.65)
        cls.layer = MapLayer.objects.create(name="Rainfall", slug="rainfall")
        if HAS_GIS:
            from django.contrib.gis.geos import LineString, MultiLineString

            cls.route = Route.objects.create(
                name="Coast road", geometry=MultiLineString(LineString((18.6, 54.3), (21.0, 52.2))))
            cls.territory = Territory.objects.create(name="Pomerania",
                                                     geometry="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
            cls.zone = Zone.objects.create(name="Harbour",
                                           geometry="MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))")
        # The tab is Superuser-plan functionality (2026-10-02, PlanTests):
        # `bootstrap_plans` puts `root` on the plan, and `bare_root`, made
        # after, is a superuser without it.
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())
            cls.root = User.objects.get(pk=cls.root.pk)
        cls.bare_root = User.objects.create_superuser("bareroot", "b@example.org", "x")

    def setUp(self):
        self.client.force_login(self.root)

    def actions(self, name):
        return list(AuditRecord.objects.filter(action=f"LOCATIONS.DOMAIN.{name}")
                    .order_by("sequence"))

    def make(self, name="Coastal", *clearances):
        domain = MapDomain.objects.create(name=name)
        for clearance in clearances:
            MapDomainClearance.objects.create(domain=domain, clearance=clearance)
        return domain


def off_the_plan_is_refused() -> bool:
    """Whether this host sells a plan for admins, so that a superuser off it
    is refused superuser functionality."""
    if not apps.is_installed("toto.subscriptions"):
        return False
    from toto.subscriptions.plans import admin_plan

    return admin_plan() is not None


class PlanTests(DomainsTestCase):
    """Map domains and their clearances are Superuser-plan functionality
    (2026-10-02, crown 41), as a bucket's clearances are:
    the superuser bit alone opened every door of the tab, so a superuser off
    the plan kept any map item to any clearance."""

    def test_may_manage_domains_is_a_superuser_on_the_plan(self):
        self.assertTrue(access.may_manage_domains(self.root))
        if off_the_plan_is_refused():
            self.assertFalse(access.may_manage_domains(self.bare_root))
        inactive = User.objects.create_superuser("gone", "g@example.org", "x", is_active=False)
        self.assertFalse(access.may_manage_domains(inactive))

    def test_a_superuser_off_the_plan_is_refused_every_door_and_told_why(self):
        if not off_the_plan_is_refused():
            self.skipTest("a host that sells no plan for admins: the superuser bit is the rule")
        domain = self.make()
        doors = [
            ("get", reverse("locations:domains"), {}),
            ("get", reverse("locations:domain_item_search"), {"kind": "address", "q": "Dł"}),
            ("post", reverse("locations:domain_add"), {"name": "Mine"}),
            ("post", reverse("locations:domain_items", args=[domain.pk]),
             {"add": [f"address:{self.address.pk}"]}),
            ("post", reverse("locations:domain_clearances", args=[domain.pk]),
             {"clearance": [self.board.pk]}),
            ("post", reverse("locations:domain_delete", args=[domain.pk]), {}),
        ]
        self.client.force_login(self.bare_root)
        for method, url, data in doors:
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, data)
                self.assertEqual(response.status_code, 403)
                self.assertIn("This needs a superuser on the Superuser plan.",
                              response.content.decode())
        self.assertEqual(list(MapDomain.objects.values_list("name", flat=True)), ["Coastal"])
        self.assertFalse(domain.clearance_rows.exists())
        self.assertFalse(domain.address_rows.exists())

    def test_anybody_else_still_hears_the_tab_is_superusers(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("locations:domains"))
        self.assertContains(response, "Map domains are managed by superusers.", status_code=403)


class SuperusersOnlyTests(DomainsTestCase):
    def test_everyone_else_is_refused_every_door(self):
        domain = self.make()
        doors = [
            ("get", reverse("locations:domains"), {}),
            ("get", reverse("locations:domain_item_search"), {"kind": "address", "q": "Dł"}),
            ("post", reverse("locations:domain_add"), {"name": "Mine"}),
            ("post", reverse("locations:domain_items", args=[domain.pk]),
             {"add": [f"address:{self.address.pk}"]}),
            ("post", reverse("locations:domain_clearances", args=[domain.pk]),
             {"clearance": [self.board.pk]}),
            ("post", reverse("locations:domain_delete", args=[domain.pk]), {}),
        ]
        for user in (self.staff, self.member):
            self.client.force_login(user)
            for method, url, data in doors:
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(getattr(self.client, method)(url, data).status_code, 403)
        self.client.logout()
        for method, url, data in doors:
            with self.subTest(user="anonymous", url=url):
                response = getattr(self.client, method)(url, data)
                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response["Location"])
        self.assertEqual(list(MapDomain.objects.values_list("name", flat=True)), ["Coastal"])
        self.assertFalse(domain.clearance_rows.exists())
        self.assertFalse(domain.address_rows.exists())
        self.assertFalse(AuditRecord.objects.filter(action__startswith="LOCATIONS.DOMAIN").exists())

    def test_the_search_refuses_in_json(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("locations:domain_item_search"), {"kind": "address"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())

    def test_the_doors_are_post_only(self):
        domain = self.make()
        for url in (reverse("locations:domain_add"),
                    reverse("locations:domain_items", args=[domain.pk]),
                    reverse("locations:domain_clearances", args=[domain.pk]),
                    reverse("locations:domain_delete", args=[domain.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_a_missing_domain_is_404(self):
        for name in ("domain_items", "domain_clearances", "domain_delete"):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(f"locations:{name}", args=[999999])).status_code,
                                 404)

    def test_the_tab_is_in_the_strip_for_superusers_only(self):
        url = reverse("locations:domains")
        response = self.client.get(url)
        self.assertContains(response, 'data-testid="locations-tab-domains"')
        self.client.force_login(self.staff)
        # Another page of the Locations frame (the People tab 404s on a GIS-off build).
        if HAS_GIS:
            other = reverse("locations:people")
        elif apps.is_installed("toto.places"):
            other = reverse("places:index")
        else:
            self.skipTest("no other Locations page renders on this build")
        response = self.client.get(other)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="locations-tab-domains"')
        self.assertNotContains(response, url)

    def test_the_tab_shows_a_superuser_off_the_plan_nothing(self):
        if not off_the_plan_is_refused():
            self.skipTest("a host that sells no plan for admins: the superuser bit is the rule")
        self.client.force_login(self.bare_root)
        response = self.client.get(reverse("locations:people") if HAS_GIS
                                   else reverse("places:index"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="locations-tab-domains"')

    def test_the_tab_works_without_gis(self):
        """Nothing here draws: every Domains door is exempt from the GIS-off 404."""
        names = {p.name for p in locations_urls.urlpatterns if p.name and p.name.startswith("domain")}
        self.assertEqual(names, {"domains", "domain_add", "domain_item_search", "domain_items",
                                 "domain_clearances", "domain_delete"})
        self.assertTrue(names <= locations_urls.GIS_FREE)


class ListTests(DomainsTestCase):
    def test_the_list_is_a_table_and_cards(self):
        domain = self.make("Coastal", self.board, self.seniors)
        domain.description = "Along the sea"
        domain.save()
        domain.address_rows.create(address=self.address)
        domain.map_layer_rows.create(map_layer=self.layer)
        response = self.client.get(reverse("locations:domains"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="domains-table"')
        self.assertContains(response, 'data-testid="domains-cards"')
        self.assertContains(response, 'data-testid="domain-coastal"')
        self.assertContains(response, 'data-testid="domain-card-coastal"')
        self.assertContains(response, "Along the sea")
        row = response.context["rows"][0]
        self.assertEqual([c.name for c in row["clearances"]], ["confidential", "internal"])
        self.assertEqual({c["key"]: c["count"] for c in row["counts"]}, {"address": 1, "map_layer": 1})
        self.assertEqual(row["total"], 2)
        data = response.context["domain_data"][str(domain.pk)]
        self.assertEqual(data["items"]["address"][0]["pk"], self.address.pk)
        self.assertEqual(sorted(data["clearances"]), sorted([str(self.board.pk), str(self.seniors.pk)]))
        self.assertEqual(data["items_url"], reverse("locations:domain_items", args=[domain.pk]))

    def test_an_open_domain_says_so_and_an_empty_list_too(self):
        response = self.client.get(reverse("locations:domains"))
        self.assertContains(response, 'data-testid="domains-empty"')
        self.make("Loose")
        response = self.client.get(reverse("locations:domains"))
        self.assertContains(response, "None: open to every member.")

    def test_the_list_is_paginated(self):
        for n in range(domain_views.PER_PAGE + 2):
            self.make(f"Domain {n:02d}")
        first = self.client.get(reverse("locations:domains"))
        self.assertTrue(first.context["is_paginated"])
        self.assertEqual(len(first.context["rows"]), domain_views.PER_PAGE)
        self.assertEqual(first.context["total"], domain_views.PER_PAGE + 2)
        second = self.client.get(reverse("locations:domains"), {"page": 2})
        self.assertEqual([r["domain"].name for r in second.context["rows"]], ["Domain 10", "Domain 11"])
        self.assertContains(second, "2 / 2")

    def test_the_kinds_are_offered_in_order_with_the_hosts_places(self):
        keys = [kind["key"] for kind in self.client.get(reverse("locations:domains")).context["kinds"]]
        expected = ["route", "map_layer", "place", "address", "zone", "territory"]
        if not apps.is_installed("toto.places"):
            expected.remove("place")
        self.assertEqual(keys, expected)
        self.assertEqual([k.get_key() for k in MapDomainKind.all()], expected)


class NewDomainTests(DomainsTestCase):
    def test_a_domain_is_made_with_its_clearances_and_first_items_and_audited(self):
        response = self.client.post(reverse("locations:domain_add"), {
            "name": "  Coastal   waters ", "description": "Sea things",
            "clearance": [self.board.pk],
            "item": [f"address:{self.address.pk}", f"map_layer:{self.layer.pk}",
                     "planet:1", "address:abc", f"address:{self.address.pk}"],
        })
        self.assertRedirects(response, reverse("locations:domains"), fetch_redirect_response=False)
        domain = MapDomain.objects.get()
        self.assertEqual((domain.name, domain.slug, domain.description),
                         ("Coastal waters", "coastal-waters", "Sea things"))
        self.assertEqual(list(domain.clearance_rows.values_list("clearance__name", flat=True)), ["internal"])
        self.assertEqual(list(domain.address_rows.values_list("address_id", flat=True)), [self.address.pk])
        self.assertEqual(list(domain.map_layer_rows.values_list("map_layer_id", flat=True)), [self.layer.pk])
        created, = self.actions("CREATED")
        self.assertEqual((created.metadata["domain"], created.actor_user_id), ("Coastal waters", self.root.pk))
        changed, = self.actions("CLEARANCES_CHANGED")
        self.assertEqual(changed.metadata["after"], ["internal"])
        added = self.actions("ITEM_ADDED")
        self.assertEqual({(r.metadata["kind"], r.metadata["item"]) for r in added},
                         {("address", self.address.pk), ("map_layer", self.layer.pk)})
        self.assertFalse(access.may_read(self.staff, self.address))
        self.assertTrue(access.may_read(self.member, self.address))

    def test_a_domain_with_no_clearances_is_not_audited_as_a_change(self):
        self.client.post(reverse("locations:domain_add"), {"name": "Loose"})
        self.assertEqual(len(self.actions("CREATED")), 1)
        self.assertEqual(self.actions("CLEARANCES_CHANGED"), [])

    def test_a_refusal_comes_back_as_a_draft_once(self):
        self.make("Coastal")
        response = self.client.post(reverse("locations:domain_add"), {
            "name": "coastal", "description": "Again", "clearance": [self.seniors.pk],
            "item": [f"address:{self.address.pk}"]})
        self.assertRedirects(response, reverse("locations:domains"), fetch_redirect_response=False)
        self.assertEqual(MapDomain.objects.count(), 1)
        page = self.client.get(reverse("locations:domains"))
        draft = page.context["draft"]
        self.assertTrue(draft["open"])
        self.assertEqual((draft["name"], draft["description"]), ("coastal", "Again"))
        self.assertEqual(draft["clearances"], [str(self.seniors.pk)])
        self.assertEqual([(i["kind"], i["pk"]) for i in draft["items"]], [("address", self.address.pk)])
        self.assertIn("There is already a domain called coastal.", draft["error"])
        self.assertContains(page, "There is already a domain called coastal.")
        self.assertFalse(self.client.get(reverse("locations:domains")).context["draft"]["open"])
        self.assertEqual(self.actions("CREATED"), [])

    def test_a_name_is_needed_and_bounded(self):
        for name, error in (("   ", "A domain needs a name."),
                            ("x" * 121, "A domain's name is at most 120 characters.")):
            with self.subTest(name=name[:5]):
                self.client.post(reverse("locations:domain_add"), {"name": name})
                self.assertEqual(self.client.session[domain_views.DRAFT_KEY]["error"], error)
                self.client.get(reverse("locations:domains"))
        self.assertFalse(MapDomain.objects.exists())


class ItemsTests(DomainsTestCase):
    def post(self, domain, **data):
        return self.client.post(reverse("locations:domain_items", args=[domain.pk]), data)

    def test_items_are_added_and_taken_out_and_audited(self):
        domain = self.make("Coastal", self.board)
        response = self.post(domain, add=[f"address:{self.address.pk}", f"map_layer:{self.layer.pk}"],
                             page="1")
        self.assertRedirects(response, reverse("locations:domains") + "?page=1",
                             fetch_redirect_response=False)
        self.assertFalse(access.may_read(self.staff, self.layer))
        self.post(domain, remove=[f"map_layer:{self.layer.pk}"],
                  add=[f"address:{self.address.pk}"])                        # already in: nothing
        self.assertTrue(access.may_read(self.staff, self.layer))
        self.assertEqual(domain.address_rows.count(), 1)
        added = [(r.metadata["kind"], r.metadata["item"]) for r in self.actions("ITEM_ADDED")]
        self.assertEqual(sorted(added), sorted([("address", self.address.pk), ("map_layer", self.layer.pk)]))
        removed, = self.actions("ITEM_REMOVED")
        self.assertEqual((removed.metadata["kind"], removed.metadata["item"], removed.metadata["label"]),
                         ("map_layer", self.layer.pk, "Rainfall"))

    def test_junk_and_unknown_kinds_change_nothing(self):
        domain = self.make()
        response = self.post(domain, add=["planet:1", "address:", "address:-1", "address:999999",
                                          "nonsense", f"address:{self.address.pk}.0"],
                             remove=["zone:abc"], page="javascript:")
        self.assertRedirects(response, reverse("locations:domains"), fetch_redirect_response=False)
        self.assertFalse(domain.address_rows.exists())
        self.assertEqual(self.actions("ITEM_ADDED"), [])

    @skipUnless(HAS_GIS, "routes, zones and territories carry geometry")
    def test_every_kind_goes_in(self):
        domain = self.make()
        self.post(domain, add=[f"route:{self.route.pk}", f"zone:{self.zone.pk}",
                               f"territory:{self.territory.pk}"])
        self.assertEqual((domain.route_rows.count(), domain.zone_rows.count(),
                          domain.territory_rows.count()), (1, 1, 1))
        self.assertEqual(RouteInDomain.objects.get().route, self.route)


class ClearancesTests(DomainsTestCase):
    def post(self, domain, *clearances):
        return self.client.post(reverse("locations:domain_clearances", args=[domain.pk]),
                                {"clearance": [c.pk for c in clearances]})

    def test_the_clearances_are_set_and_the_change_audited(self):
        domain = self.make()
        domain.address_rows.create(address=self.address)
        self.post(domain, self.board, self.seniors)
        self.assertEqual(sorted(domain.clearance_rows.values_list("clearance__name", flat=True)),
                         ["confidential", "internal"])
        self.assertTrue(access.may_read(self.member, self.address))          # either one of them
        self.post(domain, self.board, self.seniors)                          # the same: not audited
        self.post(domain)
        self.assertFalse(domain.clearance_rows.exists())
        records = self.actions("CLEARANCES_CHANGED")
        self.assertEqual([r.metadata["after"] for r in records], [["confidential", "internal"], []])
        self.assertTrue(records[-1].metadata["open"])
        self.assertEqual(records[-1].metadata["domain"], "Coastal")

    def test_junk_clearance_values_are_ignored(self):
        domain = self.make()
        self.client.post(reverse("locations:domain_clearances", args=[domain.pk]),
                         {"clearance": ["abc", "-1", "１", "9" * 30, "999999"]})
        self.assertFalse(domain.clearance_rows.exists())


class DeleteTests(DomainsTestCase):
    def test_the_domain_goes_its_items_stay_and_it_is_audited(self):
        domain = self.make("Coastal", self.board)
        domain.address_rows.create(address=self.address)
        self.assertFalse(access.may_read(self.staff, self.address))
        response = self.client.post(reverse("locations:domain_delete", args=[domain.pk]))
        self.assertRedirects(response, reverse("locations:domains"), fetch_redirect_response=False)
        self.assertFalse(MapDomain.objects.exists())
        self.assertTrue(Address.objects.filter(pk=self.address.pk).exists())
        self.assertTrue(access.may_read(self.staff, self.address))
        deleted, = self.actions("DELETED")
        self.assertEqual(deleted.metadata["clearances"], ["internal"])
        self.assertEqual(deleted.metadata["items"]["address"], 1)
        self.board.delete()                                  # no longer PROTECTED by the domain

    def test_the_confirm_modal_is_on_the_page(self):
        self.make()
        response = self.client.get(reverse("locations:domains"))
        self.assertContains(response, 'data-testid="domain-delete"')
        self.assertContains(response, 'data-testid="domain-delete-coastal"')


class SearchTests(DomainsTestCase):
    def search(self, kind, q):
        return self.client.get(reverse("locations:domain_item_search"), {"kind": kind, "q": q})

    def test_items_of_a_kind_are_found_by_name(self):
        items = self.search("address", "gdań").json()["items"]
        self.assertEqual([(i["pk"], i["label"]) for i in items], [(self.address.pk, str(self.address))])
        items = self.search("map_layer", "rain").json()["items"]
        self.assertEqual(items, [{"pk": self.layer.pk, "label": "Rainfall", "detail": "rainfall"}])

    @skipUnless(HAS_GIS, "routes carry geometry")
    def test_a_route_zone_and_territory_are_found(self):
        self.assertEqual(self.search("route", "coast").json()["items"][0]["pk"], self.route.pk)
        self.assertEqual(self.search("zone", "harb").json()["items"][0]["pk"], self.zone.pk)
        self.assertEqual(self.search("territory", "pomer").json()["items"][0]["pk"], self.territory.pk)

    def test_an_empty_query_finds_nothing_and_an_unknown_kind_is_refused(self):
        self.assertEqual(self.search("address", "  ").json()["items"], [])
        response = self.search("planet", "x")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["items"], [])

    def test_the_answer_is_capped(self):
        for n in range(domain_views.SEARCH_LIMIT + 3):
            Address.objects.create(street=f"Street {n}", locality_name="Gdynia")
        self.assertEqual(len(self.search("address", "Gdynia").json()["items"]), domain_views.SEARCH_LIMIT)


# ---------------------------------------------------------------------------
# The New clearance modal's kind for map domains (2026-09-30): socialhub's
# ClearanceTargetPlugin, provided here, adding through this app's own door.
# ---------------------------------------------------------------------------


class DomainClearanceKindTests(TestCase):
    KEY = "locations.domain"

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        from toto.core.models import Platform
        from toto.socialhub.models import Clearance

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.root = get_user_model().objects.create_superuser("kindroot", "k@example.com", "pw")
        cls.internal = Clearance.objects.create(name="internal", slug="internal")
        cls.payroll = Clearance.objects.create(name="payroll", slug="payroll")
        from toto.locations.models import MapDomain

        cls.first = MapDomain.objects.create(name="payroll sites")
        MapDomain.objects.create(name="Payroll archive")
        MapDomain.objects.create(name="Harbours")

    def plugin(self):
        from toto.socialhub.plugins import clearance_plugins

        return clearance_plugins.kind(self.KEY)

    def test_the_kind_is_registered_with_a_title_and_an_icon(self):
        plugin = self.plugin()
        self.assertIsNotNone(plugin)
        self.assertEqual(str(plugin.title), "Map domains")
        self.assertEqual(plugin.icon, "draw-polygon")

    def test_search_finds_by_name_case_blind_ordered_and_capped(self):
        rows = self.plugin().search("PAYROLL", 20)
        self.assertEqual([r["label"] for r in rows], ["Payroll archive", "payroll sites"])
        self.assertEqual(set(rows[0]), {"pk", "label", "detail"})
        self.assertEqual(len(self.plugin().search("PAYROLL", 1)), 1)
        self.assertEqual(self.plugin().search("nothing-like-it", 20), [])

    def test_resolve_ignores_what_does_not_exist(self):
        self.assertEqual(self.plugin().resolve({self.first.pk, 10 ** 9}), [self.first])

    def test_keep_adds_the_clearance_and_keeps_the_others(self):
        from toto.audit.models import AuditRecord

        from toto.locations.domain_views import set_domain_clearances

        set_domain_clearances(self.first, [self.internal], actor=self.root)
        self.assertEqual(self.plugin().keep([self.first], self.payroll, actor=self.root), 1)
        self.assertEqual(set(self.first.clearance_rows.values_list("clearance__name", flat=True)),
                         {"internal", "payroll"})
        record = AuditRecord.objects.filter(action="LOCATIONS.DOMAIN.CLEARANCES_CHANGED").order_by("-sequence").first()
        self.assertEqual(record.actor_user, self.root)

    def test_keeping_twice_changes_nothing(self):
        from toto.audit.models import AuditRecord

        self.plugin().keep([self.first], self.payroll, actor=self.root)
        before = AuditRecord.objects.filter(action="LOCATIONS.DOMAIN.CLEARANCES_CHANGED").count()
        self.plugin().keep([self.first], self.payroll, actor=self.root)
        self.assertEqual(self.first.clearance_rows.count(), 1)
        self.assertEqual(AuditRecord.objects.filter(action="LOCATIONS.DOMAIN.CLEARANCES_CHANGED").count(), before)

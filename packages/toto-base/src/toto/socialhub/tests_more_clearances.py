"""Clearances, more closely (2026-09-29): the model's rules at their edges, the
shared reading rule every app asks (``clearance_access``), the Clearances tab's
refusals and the admin's limits for somebody who is not a superuser.

``clearance_access`` is exercised through a real through table — the map
layer's ``clearance_rows`` (``toto.locations``, same package) — because the rule
is written against "a related name whose rows carry a ``clearance``", and a
fake would test past the lookups that make ``gate`` and ``hidden`` agree.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_more_clearances
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

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
# The Clearances tab
# ---------------------------------------------------------------------------


class ClearancesTabRefusalTests(ClearanceFixture):
    def test_every_door_refuses_a_member_and_changes_nothing(self):
        member = client_for(self.ada.user)
        doors = (
            (reverse("socialhub:clearance_add"), {"name": "restricted"}),
            (reverse("socialhub:clearance_member", args=[self.internal.pk]), {"who": "cy"}),
            (reverse("socialhub:clearance_member", args=[self.internal.pk]),
             {"action": "remove", "person": self.ada.pk}),
            (reverse("socialhub:clearance_speeds", args=[self.internal.pk]), {"regen_security": "99"}),
            (reverse("socialhub:clearance_delete", args=[self.internal.pk])),
        )
        for door in doors:
            url, data = door if isinstance(door, tuple) else (door, {})
            with self.subTest(url=url):
                self.assertEqual(member.post(url, data).status_code, 403)
        self.assertFalse(Clearance.objects.filter(name="restricted").exists())
        self.assertEqual(set(self.internal.members.all()), {self.ada})
        self.internal.refresh_from_db()
        self.assertIsNone(self.internal.regen_security)

    def test_the_doors_answer_posts_only(self):
        root = client_for(self.root)
        for name, args in (("clearance_add", []), ("clearance_member", [self.internal.pk]),
                           ("clearance_speeds", [self.internal.pk]), ("clearance_delete", [self.internal.pk])):
            with self.subTest(door=name):
                self.assertEqual(root.get(reverse(f"socialhub:{name}", args=args)).status_code, 405)
        self.assertEqual(root.post(reverse("socialhub:clearances")).status_code, 405)

    def test_a_clearance_nobody_made_is_a_404_to_every_door(self):
        root = client_for(self.root)
        missing = Clearance.objects.order_by("-pk").first().pk + 1
        for name, data in (("clearance_member", {"who": "bob"}),
                           ("clearance_speeds", {"regen_security": "5"}),
                           ("clearance_delete", {})):
            with self.subTest(door=name):
                response = root.post(reverse(f"socialhub:{name}", args=[missing]), data)
                self.assertEqual(response.status_code, 404)
        self.assertEqual(set(self.bob.clearances.all()), {self.confidential})


class ClearancesTabPageTests(ClearanceFixture):
    def test_the_page_lists_each_clearance_with_its_members_and_the_room_left(self):
        response = client_for(self.root).get(reverse("socialhub:clearances"))
        rows = {row["clearance"].slug: row for row in response.context["rows"]}
        self.assertEqual(list(rows), ["confidential", "internal"])
        self.assertEqual(rows["internal"]["members"], [self.ada])
        self.assertEqual([s["pool"] for s in rows["internal"]["speeds"]],
                         ["security", "compute", "storage"])
        self.assertEqual(response.context["room_left"], MAX_CLEARANCES - 2)
        self.assertNotContains(response, 'data-testid="clearances-full"')

    def test_the_people_offered_are_the_ones_with_an_account(self):
        Person.objects.create(display_name="Ghost")           # no login
        response = client_for(self.root).get(reverse("socialhub:clearances"))
        self.assertNotIn("Ghost", {p.display_name for p in response.context["people"]})
        self.assertIn(self.cy, list(response.context["people"]))


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


class ClearanceMemberTests(ClearanceFixture):
    def post(self, data):
        return client_for(self.root).post(
            reverse("socialhub:clearance_member", args=[self.confidential.pk]), data, follow=True)

    def test_somebody_is_found_by_slug_or_by_username(self):
        self.assertContains(self.post({"who": f"  {self.cy.slug}  "}), "is in confidential")
        self.assertIn(self.confidential, self.cy.clearances.all())
        self.post({"who": "ada"})
        self.assertEqual(set(self.confidential.members.all()), {self.bob, self.cy, self.ada})

    def test_a_blank_name_finds_nobody(self):
        self.assertContains(self.post({"who": "   "}), "There is nobody called")
        self.assertEqual(set(self.confidential.members.all()), {self.bob})

    def test_taking_somebody_out_names_them_by_pk_only(self):
        for junk in ("bob", "", "１２", "-1", f"{self.bob.pk}x"):
            with self.subTest(person=junk):
                self.post({"action": "remove", "person": junk})
                self.assertIn(self.bob, self.confidential.members.all())
        response = self.post({"action": "remove", "person": str(self.bob.pk)})
        self.assertContains(response, "Bob left confidential.")
        self.assertNotIn(self.bob, self.confidential.members.all())

    def test_taking_out_somebody_who_is_not_in_it_says_nothing_and_records_nothing(self):
        before = AuditRecord.objects.filter(action="SOCIALHUB.CLEARANCE_MEMBER_REMOVED").count()
        self.post({"action": "remove", "person": str(self.cy.pk)})
        self.assertEqual(AuditRecord.objects.filter(action="SOCIALHUB.CLEARANCE_MEMBER_REMOVED").count(),
                         before)
        self.assertEqual(set(self.cy.clearances.all()), set())

    def test_putting_somebody_in_a_clearance_leaves_their_communities_alone(self):
        self.post({"who": "cy"})
        self.assertEqual(set(self.cy.communities.all()), {self.devs})
        self.assertEqual(set(self.cy.clearances.all()), {self.confidential})


class ClearanceSpeedTests(ClearanceFixture):
    def post(self, **data):
        return client_for(self.root).post(
            reverse("socialhub:clearance_speeds", args=[self.internal.pk]), data, follow=True)

    def test_comma_decimals_and_blanks(self):
        response = self.post(regen_security="0,25", regen_compute=" 12 ", regen_storage="")
        self.assertContains(response, "Speeds saved for internal")
        self.internal.refresh_from_db()
        self.assertEqual(self.internal.regen_security, Decimal("0.25"))
        self.assertEqual(self.internal.regen_compute, Decimal("12"))
        self.assertIsNone(self.internal.regen_storage)

    def test_a_blank_clears_a_speed_that_was_set(self):
        self.post(regen_compute="7")
        self.post(regen_compute="")
        self.internal.refresh_from_db()
        self.assertEqual(self.internal.regen_speeds(), {})

    def test_one_refused_pool_saves_none_of_them(self):
        self.assertContains(self.post(regen_security="5", regen_compute="fast"), "is not a number")
        self.internal.refresh_from_db()
        self.assertIsNone(self.internal.regen_security)
        self.post(regen_security="5", regen_storage="-0.5")
        self.internal.refresh_from_db()
        self.assertIsNone(self.internal.regen_security)

    def test_more_precision_or_size_than_the_column_holds_is_refused(self):
        for value in ("0.00001", "123456789", "NaN", "Infinity"):
            with self.subTest(value=value):
                self.post(regen_security=value)
                self.internal.refresh_from_db()
                self.assertIsNone(self.internal.regen_security)
        self.post(regen_security="99999999.9999")                 # the largest it holds
        self.internal.refresh_from_db()
        self.assertEqual(self.internal.regen_security, Decimal("99999999.9999"))

    def test_a_saved_speed_is_on_the_chain_with_before_and_after(self):
        self.post(regen_compute="3")
        record = AuditRecord.objects.filter(action="SOCIALHUB.CLEARANCE_CHANGED",
                                            object_id=str(self.internal.pk)).get()
        self.assertEqual(record.metadata["changed"], ["regen_compute"])
        self.assertIsNone(record.metadata["before"]["regen_compute"])
        self.assertEqual(Decimal(record.metadata["after"]["regen_compute"]), Decimal("3"))
        self.assertEqual(record.metadata["clearance"], "internal")
        self.assertEqual(record.object_type, "socialhub.clearance")


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

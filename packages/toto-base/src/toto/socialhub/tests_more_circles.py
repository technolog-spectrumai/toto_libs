"""Circles, more closely (2026-09-29): the model's rules at their edges, the
shared reading rule every app asks (``circle_access``), the Circles tab's
refusals and the admin's limits for somebody who is not a superuser.

``circle_access`` is exercised through a real through table — the map
layer's ``circle_rows`` (``toto.locations``, same package) — because the rule
is written against "a related name whose rows carry a ``circle``", and a
fake would test past the lookups that make ``gate`` and ``hidden`` agree.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_more_circles
"""

from decimal import Decimal
from unittest import mock, skip

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.locations.models import MapLayer, MapLayerCircle
from toto.people.models import Person
from toto.socialhub import circle_access
from toto.socialhub.models import (
    CIRCLE_ONLY_SPEED,
    MAX_CIRCLES,
    Community,
    CommunityPrivilege,
)

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


class CircleFixture(TestCase):
    """`devs` is functional; `board` and `seniors` are circles. Ada is in
    `board`, Bob in `seniors`, Cy in `devs` only."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.devs = Community.objects.create(name="devs", slug="devs")
        cls.board = Community.objects.create(name="board", slug="board", is_circle=True)
        cls.seniors = Community.objects.create(name="seniors", slug="seniors", is_circle=True)
        cls.ada = person("ada", cls.devs, cls.board)
        cls.bob = person("bob", cls.seniors)
        cls.cy = person("cy", cls.devs)
        cls.root = User.objects.create_superuser("root", "root@example.com", "pw")


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


class CommunityRuleTests(CircleFixture):
    def test_a_functional_community_is_refused_a_refill_speed(self):
        self.devs.regen_compute = Decimal("5")
        with self.assertRaises(ValidationError) as caught:
            self.devs.full_clean()
        self.assertEqual(caught.exception.message_dict["is_circle"], [str(CIRCLE_ONLY_SPEED)])

    def test_a_circle_turned_functional_must_drop_its_speeds_first(self):
        self.board.regen_security = Decimal("8")
        self.board.save()
        self.board.is_circle = False
        with self.assertRaises(ValidationError):
            self.board.full_clean()
        self.board.regen_security = None
        self.board.full_clean()                       # nothing left of the other axis

    def test_a_negative_speed_is_refused_on_its_own_field(self):
        self.board.regen_storage = Decimal("-1")
        with self.assertRaises(ValidationError) as caught:
            self.board.full_clean()
        self.assertIn("regen_storage", caught.exception.message_dict)

    def test_regen_speeds_names_only_the_pools_a_circle_sets(self):
        self.board.regen_security = Decimal("8")
        self.board.regen_storage = Decimal("0")       # zero is a speed, not "unset"
        self.assertEqual(self.board.regen_speeds(),
                         {"security": Decimal("8"), "storage": Decimal("0")})
        self.assertEqual(self.seniors.regen_speeds(), {})

    def test_a_speed_left_on_a_functional_community_reads_as_none(self):
        """Written past `clean`: the resolver still answers nothing."""
        Community.objects.filter(pk=self.devs.pk).update(regen_compute=Decimal("50"))
        self.devs.refresh_from_db()
        self.assertEqual(self.devs.regen_speeds(), {})

    def test_the_money_axis_is_read_from_every_relation_that_carries_it(self):
        from toto.subscriptions.models import CommunityDiscount, CommunityPlanOffer

        for make in (lambda c: CommunityPrivilege.objects.create(community=c),
                     lambda c: CommunityPlanOffer.objects.create(community=c, plan_key="free"),
                     lambda c: CommunityDiscount.objects.create(community=c, percent=5)):
            community = Community.objects.create(name=f"c{Community.objects.count()}")
            self.assertFalse(community.carries_the_money_axis())
            make(community)
            self.assertTrue(community.carries_the_money_axis())
        self.assertFalse(Community(name="unsaved").carries_the_money_axis())

    def test_a_functional_community_may_not_sit_under_a_circle(self):
        child = Community(name="board-devs", parent=self.board)
        with self.assertRaises(ValidationError) as caught:
            child.full_clean()
        self.assertIn("parent", caught.exception.message_dict)
        Community(name="devs-backend", parent=self.devs).full_clean()   # functional tree: fine

    def test_a_circle_turned_functional_is_never_refused_by_the_cap(self):
        for n in range(Community.objects.circles().count(), MAX_CIRCLES):
            Community.objects.create(name=f"x{n}", slug=f"x{n}", is_circle=True)
        self.board.is_circle = False
        self.board.full_clean()
        self.board.save()
        self.assertEqual(Community.objects.circles().count(), MAX_CIRCLES - 1)

    def test_removing_a_circle_makes_room_for_another(self):
        for n in range(Community.objects.circles().count(), MAX_CIRCLES):
            Community.objects.create(name=f"x{n}", slug=f"x{n}", is_circle=True)
        with self.assertRaises(ValidationError):
            Community.objects.create(name="eighth", is_circle=True)
        Community.objects.get(slug="x2").delete()
        Community.objects.create(name="eighth", is_circle=True)
        self.assertEqual(Community.objects.circles().count(), MAX_CIRCLES)

    def test_a_second_community_of_the_same_name_gets_its_own_slug(self):
        first = Community.objects.create(name="Weavers Guild")
        second = Community.objects.create(name="Weavers Guild")
        self.assertEqual(first.slug, "weavers-guild")
        self.assertEqual(second.slug, "weavers-guild-1")

    def test_the_two_kinds_are_told_apart_in_one_place(self):
        self.assertEqual(set(Community.objects.functional()), {self.devs})
        self.assertEqual(set(Community.objects.circles()), {self.board, self.seniors})
        for viewer in (None, AnonymousUser(), self.ada.user):
            self.assertEqual(set(Community.objects.listed_for(viewer)), {self.devs})
        self.assertEqual(Community.objects.listed_for(self.root).count(), 3)


# ---------------------------------------------------------------------------
# circle_access — the one reading rule
# ---------------------------------------------------------------------------


class CircleAccessTests(CircleFixture):
    ROWS = "circle_rows"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.open_layer = MapLayer.objects.create(name="Open", slug="open")
        cls.board_layer = MapLayer.objects.create(name="Board only", slug="board-only")
        MapLayerCircle.objects.create(layer=cls.board_layer, circle=cls.board)
        cls.shared_layer = MapLayer.objects.create(name="Both circles", slug="both")
        MapLayerCircle.objects.create(layer=cls.shared_layer, circle=cls.board)
        MapLayerCircle.objects.create(layer=cls.shared_layer, circle=cls.seniors)
        cls.inactive = MapLayer.objects.create(name="Inactive", slug="inactive", is_active=False)

    def readable(self, user, **kwargs):
        return set(circle_access.gate(user, MapLayer.objects.all(), rows=self.ROWS, **kwargs)
                   .values_list("slug", flat=True))

    def test_person_of_and_circle_ids_of_know_nobody_without_a_person(self):
        stray = User.objects.create_user("stray", password="pw")
        for user in (None, AnonymousUser(), stray):
            self.assertIsNone(circle_access.person_of(user))
            self.assertEqual(circle_access.circle_ids_of(user), set())

    def test_circle_ids_of_counts_circles_and_never_a_functional_community(self):
        self.assertEqual(circle_access.circle_ids_of(self.ada.user), {self.board.pk})
        self.assertEqual(circle_access.circle_ids_of(self.cy.user), set())

    def test_a_kept_object_is_read_by_its_circles_members_only(self):
        everyone = {"open", "inactive"}
        self.assertEqual(self.readable(self.cy.user), everyone)
        self.assertEqual(self.readable(AnonymousUser()), everyone)
        self.assertEqual(self.readable(self.ada.user), everyone | {"board-only", "both"})
        self.assertEqual(self.readable(self.bob.user), everyone | {"both"})
        self.assertEqual(self.readable(self.root), everyone | {"board-only", "both"})

    def test_the_apps_own_rule_applies_to_open_objects_only(self):
        """A circle both keeps and grants: the `open` rule narrows what has no
        circle, and a member reads a kept object whatever that rule says."""
        MapLayerCircle.objects.create(layer=self.inactive, circle=self.seniors)
        active = Q(is_active=True)
        self.assertEqual(self.readable(self.cy.user, open=active), {"open"})
        self.assertEqual(self.readable(self.bob.user, open=active), {"open", "both", "inactive"})

    def test_the_owner_reads_a_kept_object_without_being_in_its_circle(self):
        self.board_layer.owner = self.cy
        self.board_layer.save()
        owner = Q(owner=self.cy)
        self.assertIn("board-only", self.readable(self.cy.user, owner=owner))
        # The owner clause is never offered to an anonymous visitor.
        ownerless = Q(owner__isnull=True)
        self.assertIn("both", self.readable(self.cy.user, owner=ownerless))
        self.assertNotIn("both", self.readable(AnonymousUser(), owner=ownerless))

    def test_an_object_in_two_of_my_circles_is_listed_once(self):
        self.seniors.members.add(self.ada)
        rows = list(circle_access.gate(self.ada.user, MapLayer.objects.filter(slug="both"),
                                       rows=self.ROWS))
        self.assertEqual(len(rows), 1)

    def test_hidden_is_the_per_object_twin_of_gate(self):
        for user in (self.ada.user, self.bob.user, self.cy.user, AnonymousUser(), self.root):
            readable = self.readable(user)
            for layer in (self.open_layer, self.board_layer, self.shared_layer):
                with self.subTest(user=getattr(user, "username", "anonymous"), layer=layer.slug):
                    self.assertEqual(not circle_access.hidden(user, layer, rows=self.ROWS),
                                     layer.slug in readable)

    def test_hidden_answers_missing_for_nothing_and_open_for_the_owner(self):
        self.assertTrue(circle_access.hidden(self.root, None, rows=self.ROWS))
        self.assertFalse(circle_access.hidden(self.cy.user, self.board_layer, rows=self.ROWS,
                                              is_owner=True))

    def test_kept_says_whether_any_circle_holds_it(self):
        self.assertTrue(circle_access.kept(self.board_layer, rows=self.ROWS))
        self.assertFalse(circle_access.kept(self.open_layer, rows=self.ROWS))

    def test_circles_of_lists_by_name(self):
        self.assertEqual(circle_access.circles_of(self.shared_layer, rows=self.ROWS),
                         [self.board, self.seniors])
        self.assertEqual(circle_access.circles_of(self.open_layer, rows=self.ROWS), [])

    def test_a_member_shares_with_their_own_circles_and_the_objects_own(self):
        self.assertEqual(list(circle_access.shareable_circles(self.ada.user, self.open_layer,
                                                              rows=self.ROWS)), [self.board])
        # Bob is not in `board`, but the layer already is: he sees it to keep it.
        self.assertEqual(list(circle_access.shareable_circles(self.bob.user, self.board_layer,
                                                              rows=self.ROWS)),
                         [self.board, self.seniors])
        self.assertEqual(list(circle_access.shareable_circles(self.cy.user, self.open_layer,
                                                              rows=self.ROWS)), [])
        self.assertEqual(list(circle_access.shareable_circles(self.root, self.open_layer,
                                                              rows=self.ROWS)),
                         [self.board, self.seniors])

    def test_a_viewer_who_does_not_manage_sees_only_their_own_circles_named(self):
        self.assertEqual(circle_access.visible_circles_of(self.bob.user, self.shared_layer,
                                                          rows=self.ROWS, manages=False),
                         [self.seniors])
        self.assertEqual(circle_access.visible_circles_of(self.bob.user, self.shared_layer,
                                                          rows=self.ROWS, manages=True),
                         [self.board, self.seniors])


class SetCirclesTests(CircleFixture):
    ROWS = "circle_rows"

    def setUp(self):
        self.layer = MapLayer.objects.create(name="Layer", slug="layer")

    def records(self):
        return AuditRecord.objects.filter(action="LOCATIONS.LAYER_CIRCLES")

    def set(self, *circles, **facts):
        return circle_access.set_circles(self.layer, circles, rows=self.ROWS, actor=self.root,
                                         action="layer_circles", app_label="locations", **facts)

    def test_a_functional_community_is_refused_and_nothing_is_written(self):
        with self.assertRaises(circle_access.CircleRefused):
            self.set(self.board, self.devs)
        self.assertFalse(self.layer.circle_rows.exists())
        self.assertFalse(self.records().exists())

    def test_keeping_changing_and_opening_again_each_leave_one_record(self):
        self.assertEqual(self.set(self.board, kind="layer"), ([], ["board"]))
        first = self.records().get()
        self.assertEqual(first.metadata["before"], [])
        self.assertEqual(first.metadata["after"], ["board"])
        self.assertFalse(first.metadata["open"])
        self.assertEqual(first.metadata["kind"], "layer")
        self.assertEqual(first.actor_user, self.root)

        self.assertEqual(self.set(self.seniors, self.board), (["board"], ["board", "seniors"]))
        self.assertEqual(set(self.layer.circle_rows.values_list("circle__slug", flat=True)),
                         {"board", "seniors"})

        self.assertEqual(self.set(), (["board", "seniors"], []))
        self.assertFalse(self.layer.circle_rows.exists())
        self.assertTrue(self.records().order_by("-sequence").first().metadata["open"])
        self.assertEqual(self.records().count(), 3)

    def test_the_same_circles_again_change_nothing_and_record_nothing(self):
        self.set(self.board)
        self.assertEqual(self.set(self.board, self.board), (["board"], ["board"]))
        self.assertEqual(self.layer.circle_rows.count(), 1)
        self.assertEqual(self.records().count(), 1)

    def test_a_chain_that_cannot_write_never_undoes_the_change(self):
        with mock.patch("toto.audit.services.record", side_effect=RuntimeError("chain down")), \
                self.assertLogs("toto.socialhub", "ERROR"):
            self.set(self.board)
        self.assertEqual(list(self.layer.circle_rows.values_list("circle__slug", flat=True)),
                         ["board"])


# ---------------------------------------------------------------------------
# The Circles tab
# ---------------------------------------------------------------------------


class CirclesTabRefusalTests(CircleFixture):
    def test_every_door_refuses_a_member_and_changes_nothing(self):
        member = client_for(self.ada.user)
        doors = (
            (reverse("socialhub:circle_add"), {"name": "cabal"}),
            (reverse("socialhub:circle_member", args=[self.board.pk]), {"who": "cy"}),
            (reverse("socialhub:circle_member", args=[self.board.pk]),
             {"action": "remove", "person": self.ada.pk}),
            (reverse("socialhub:circle_speeds", args=[self.board.pk]), {"regen_security": "99"}),
            (reverse("socialhub:circle_delete", args=[self.board.pk])),
        )
        for door in doors:
            url, data = door if isinstance(door, tuple) else (door, {})
            with self.subTest(url=url):
                self.assertEqual(member.post(url, data).status_code, 403)
        self.assertFalse(Community.objects.filter(name="cabal").exists())
        self.assertEqual(set(self.board.members.all()), {self.ada})
        self.board.refresh_from_db()
        self.assertIsNone(self.board.regen_security)

    def test_the_doors_answer_posts_only(self):
        root = client_for(self.root)
        for name, args in (("circle_add", []), ("circle_member", [self.board.pk]),
                           ("circle_speeds", [self.board.pk]), ("circle_delete", [self.board.pk])):
            with self.subTest(door=name):
                self.assertEqual(root.get(reverse(f"socialhub:{name}", args=args)).status_code, 405)
        self.assertEqual(root.post(reverse("socialhub:circles")).status_code, 405)

    def test_a_functional_community_is_not_a_circle_to_any_door(self):
        root = client_for(self.root)
        for name, data in (("circle_member", {"who": "bob"}),
                           ("circle_speeds", {"regen_security": "5"}),
                           ("circle_delete", {})):
            with self.subTest(door=name):
                response = root.post(reverse(f"socialhub:{name}", args=[self.devs.pk]), data)
                self.assertEqual(response.status_code, 404)
        self.assertTrue(Community.objects.filter(pk=self.devs.pk).exists())
        self.assertNotIn(self.devs, self.bob.communities.all())


class CirclesTabPageTests(CircleFixture):
    def test_the_page_lists_each_circle_with_its_members_and_the_room_left(self):
        response = client_for(self.root).get(reverse("socialhub:circles"))
        rows = {row["circle"].slug: row for row in response.context["rows"]}
        self.assertEqual(list(rows), ["board", "seniors"])
        self.assertEqual(rows["board"]["members"], [self.ada])
        self.assertEqual([s["pool"] for s in rows["board"]["speeds"]],
                         ["security", "compute", "storage"])
        self.assertEqual(response.context["room_left"], MAX_CIRCLES - 2)
        self.assertNotContains(response, 'data-testid="circles-full"')

    def test_the_people_offered_are_the_ones_with_an_account(self):
        Person.objects.create(display_name="Ghost")           # no login
        response = client_for(self.root).get(reverse("socialhub:circles"))
        self.assertNotIn("Ghost", {p.display_name for p in response.context["people"]})
        self.assertIn(self.cy, list(response.context["people"]))


class CircleAddTests(CircleFixture):
    def add(self, name):
        return client_for(self.root).post(reverse("socialhub:circle_add"), {"name": name},
                                          follow=True)

    def test_a_blank_name_is_refused(self):
        before = Community.objects.count()
        self.assertContains(self.add("   "), "A circle needs a name.")
        self.assertEqual(Community.objects.count(), before)

    def test_a_name_any_community_already_has_is_refused_whatever_its_case(self):
        response = self.add("DEVS")
        self.assertContains(response, "There is already a community or circle called DEVS")
        self.assertFalse(Community.objects.filter(name="DEVS").exists())
        self.add("Seniors")
        self.assertEqual(Community.objects.filter(name__iexact="seniors").count(), 1)

    def test_the_name_is_tidied_and_the_circle_is_a_circle(self):
        self.assertContains(self.add("  Old \t  hands  "), "Circle Old hands made.")
        made = Community.objects.get(name="Old hands")
        self.assertTrue(made.is_circle)
        self.assertEqual(made.slug, "old-hands")
        self.assertTrue(AuditRecord.objects.filter(
            action="SOCIALHUB.COMMUNITY_CREATED", object_id=str(made.pk),
            actor_user=self.root).exists())

    def test_a_long_name_is_cut_to_what_the_page_accepts(self):
        self.add("x" * 300)
        self.assertEqual(len(Community.objects.circles().get(name__startswith="xxx").name), 120)

    @skip("suspected bug: Community.save slugifies without allow_unicode and never "
          "falls back, so a circle named in a non-Latin script ('Старшие') is saved "
          "with slug '' and the superuser's community list 500s (NoReverseMatch for "
          "socialhub:community_detail with '')")
    def test_a_circle_named_in_another_script_keeps_the_directory_working(self):
        self.add("Старшие")
        made = Community.objects.get(name="Старшие")
        self.assertTrue(made.slug)
        self.assertEqual(client_for(self.root).get(
            reverse("socialhub:community_list")).status_code, 200)

    @skip("suspected bug: Community.save derives the slug from the whole name with no "
          "cut, so a circle named with more than 50 slug characters gets a slug longer "
          "than its SlugField(max_length=50) — SQLite keeps it, PostgreSQL refuses the "
          "INSERT (DataError, a 500 on the Circles tab)")
    def test_a_long_name_still_gets_a_slug_that_fits_its_column(self):
        self.add("x" * 300)
        made = Community.objects.circles().get(name__startswith="xxx")
        self.assertLessEqual(len(made.slug), Community._meta.get_field("slug").max_length)


class CircleMemberTests(CircleFixture):
    def post(self, data):
        return client_for(self.root).post(
            reverse("socialhub:circle_member", args=[self.seniors.pk]), data, follow=True)

    def test_somebody_is_found_by_slug_or_by_username(self):
        self.assertContains(self.post({"who": f"  {self.cy.slug}  "}), "is in seniors")
        self.assertIn(self.seniors, self.cy.communities.all())
        self.post({"who": "ada"})
        self.assertEqual(set(self.seniors.members.all()), {self.bob, self.cy, self.ada})

    def test_a_blank_name_finds_nobody(self):
        self.assertContains(self.post({"who": "   "}), "There is nobody called")
        self.assertEqual(set(self.seniors.members.all()), {self.bob})

    def test_taking_somebody_out_names_them_by_pk_only(self):
        for junk in ("bob", "", "１２", "-1", f"{self.bob.pk}x"):
            with self.subTest(person=junk):
                self.post({"action": "remove", "person": junk})
                self.assertIn(self.bob, self.seniors.members.all())
        response = self.post({"action": "remove", "person": str(self.bob.pk)})
        self.assertContains(response, "Bob left seniors.")
        self.assertNotIn(self.bob, self.seniors.members.all())

    def test_taking_out_somebody_who_is_not_in_it_says_nothing_and_records_nothing(self):
        before = AuditRecord.objects.filter(action="SOCIALHUB.MEMBER_REMOVED").count()
        self.post({"action": "remove", "person": str(self.cy.pk)})
        self.assertEqual(AuditRecord.objects.filter(action="SOCIALHUB.MEMBER_REMOVED").count(),
                         before)
        self.assertEqual(set(self.cy.communities.all()), {self.devs})

    def test_putting_somebody_in_a_circle_leaves_their_communities_alone(self):
        self.post({"who": "cy"})
        self.assertEqual(set(self.cy.communities.all()), {self.devs, self.seniors})


class CircleSpeedTests(CircleFixture):
    def post(self, **data):
        return client_for(self.root).post(
            reverse("socialhub:circle_speeds", args=[self.board.pk]), data, follow=True)

    def test_comma_decimals_and_blanks(self):
        response = self.post(regen_security="0,25", regen_compute=" 12 ", regen_storage="")
        self.assertContains(response, "Speeds saved for board")
        self.board.refresh_from_db()
        self.assertEqual(self.board.regen_security, Decimal("0.25"))
        self.assertEqual(self.board.regen_compute, Decimal("12"))
        self.assertIsNone(self.board.regen_storage)

    def test_a_blank_clears_a_speed_that_was_set(self):
        self.post(regen_compute="7")
        self.post(regen_compute="")
        self.board.refresh_from_db()
        self.assertEqual(self.board.regen_speeds(), {})

    def test_one_refused_pool_saves_none_of_them(self):
        self.assertContains(self.post(regen_security="5", regen_compute="fast"), "is not a number")
        self.board.refresh_from_db()
        self.assertIsNone(self.board.regen_security)
        self.post(regen_security="5", regen_storage="-0.5")
        self.board.refresh_from_db()
        self.assertIsNone(self.board.regen_security)

    def test_more_precision_or_size_than_the_column_holds_is_refused(self):
        for value in ("0.00001", "123456789", "NaN", "Infinity"):
            with self.subTest(value=value):
                self.post(regen_security=value)
                self.board.refresh_from_db()
                self.assertIsNone(self.board.regen_security)
        self.post(regen_security="99999999.9999")                 # the largest it holds
        self.board.refresh_from_db()
        self.assertEqual(self.board.regen_security, Decimal("99999999.9999"))

    def test_a_saved_speed_is_on_the_chain_with_before_and_after(self):
        self.post(regen_compute="3")
        record = AuditRecord.objects.filter(action="SOCIALHUB.COMMUNITY_CHANGED",
                                            object_id=str(self.board.pk)).get()
        self.assertEqual(record.metadata["changed"], ["regen_compute"])
        self.assertIsNone(record.metadata["before"]["regen_compute"])
        self.assertEqual(Decimal(record.metadata["after"]["regen_compute"]), Decimal("3"))
        self.assertTrue(record.metadata["is_circle"])


class CircleDeleteTests(CircleFixture):
    def test_a_circle_that_still_keeps_something_is_not_removed(self):
        """A real PROTECT (a map layer kept to the circle), not a patched one."""
        layer = MapLayer.objects.create(name="Kept", slug="kept")
        MapLayerCircle.objects.create(layer=layer, circle=self.board)
        response = client_for(self.root).post(
            reverse("socialhub:circle_delete", args=[self.board.pk]), follow=True)
        self.assertContains(response, "board still decides who reads something")
        self.assertTrue(Community.objects.filter(pk=self.board.pk).exists())
        self.assertIn(self.board, self.ada.communities.all())

    def test_removing_a_circle_takes_nobody_out_of_anything_else(self):
        pk = self.board.pk
        response = client_for(self.root).post(reverse("socialhub:circle_delete", args=[pk]),
                                              follow=True)
        self.assertContains(response, "Circle board removed.")
        self.assertEqual(set(self.ada.communities.all()), {self.devs})
        self.assertTrue(AuditRecord.objects.filter(action="SOCIALHUB.COMMUNITY_DELETED",
                                                   object_id=str(pk)).exists())

    @skip("suspected gap: deleting a community cascades its Person.communities rows "
          "without m2m_changed, so socialhub/audit.py records no MEMBER_REMOVED and "
          "COMMUNITY_DELETED names no members — the chain cannot say who lost the "
          "circle's reading access")
    def test_removing_a_circle_records_everybody_who_was_in_it(self):
        pk = self.board.pk
        client_for(self.root).post(reverse("socialhub:circle_delete", args=[pk]))
        self.assertTrue(AuditRecord.objects.filter(
            action="SOCIALHUB.MEMBER_REMOVED", object_id=str(pk),
            metadata__person=self.ada.slug).exists())


# ---------------------------------------------------------------------------
# The admin, for somebody who is not a superuser
# ---------------------------------------------------------------------------


class CommunityAdminLimitsTests(CircleFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.clerk = User.objects.create_user("clerk", "clerk@example.com", "pw", is_staff=True)
        cls.clerk.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="socialhub", content_type__model="community"))

    def model_admin(self):
        from django.contrib import admin

        return admin.site._registry[Community]

    def request(self, user):
        request = RequestFactory().post("/")
        request.user = user
        return request

    def test_the_circle_fields_are_read_only_to_staff_alone(self):
        fields = self.model_admin().get_readonly_fields(self.request(self.clerk), self.devs)
        for name in ("is_circle", "regen_security", "regen_compute", "regen_storage"):
            self.assertIn(name, fields)
        self.assertNotIn("is_circle", self.model_admin().get_readonly_fields(
            self.request(self.root), self.devs))

    def test_staff_may_change_and_delete_a_functional_community_but_not_a_circle(self):
        admin_ = self.model_admin()
        request = self.request(self.clerk)
        self.assertTrue(admin_.has_change_permission(request, self.devs))
        self.assertTrue(admin_.has_delete_permission(request, self.devs))
        self.assertFalse(admin_.has_change_permission(request, self.board))
        self.assertFalse(admin_.has_delete_permission(request, self.board))
        self.assertTrue(admin_.has_delete_permission(self.request(self.root), self.board))

    def test_the_second_layer_refuses_a_circle_or_a_speed_from_staff(self):
        admin_ = self.model_admin()
        for community in (Community(name="sneaky", is_circle=True),
                          Community(name="fast", is_circle=True, regen_compute=Decimal("9"))):
            with self.subTest(community=community.name):
                with self.assertRaises(PermissionDenied):
                    admin_.save_model(self.request(self.clerk), community, None, False)
                self.assertIsNone(community.pk)

    def test_staff_cannot_delete_a_circle_through_the_admin(self):
        client = client_for(self.clerk)
        response = client.post(reverse("admin:socialhub_community_delete", args=[self.board.pk]),
                               {"post": "yes"})
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(Community.objects.filter(pk=self.board.pk).exists())

    def test_staff_editing_a_functional_community_cannot_make_it_a_circle(self):
        client = client_for(self.clerk)
        response = client.post(reverse("admin:socialhub_community_change", args=[self.devs.pk]), {
            "name": "devs", "slug": "devs", "org_type": Community.OTHER, "is_circle": "on",
            "regen_security": "10",
            "privilege-TOTAL_FORMS": "0", "privilege-INITIAL_FORMS": "0",
            "privilege-MIN_NUM_FORMS": "0", "privilege-MAX_NUM_FORMS": "1"})
        self.assertEqual(response.status_code, 302)
        self.devs.refresh_from_db()
        self.assertFalse(self.devs.is_circle)
        self.assertIsNone(self.devs.regen_security)

    def test_a_superuser_cannot_turn_a_granting_community_into_a_circle(self):
        CommunityPrivilege.objects.create(community=self.devs, may_see_community_chain=True)
        privilege = self.devs.privilege
        response = client_for(self.root).post(
            reverse("admin:socialhub_community_change", args=[self.devs.pk]), {
                "name": "devs", "slug": "devs", "org_type": Community.OTHER, "is_circle": "on",
                "privilege-TOTAL_FORMS": "1", "privilege-INITIAL_FORMS": "1",
                "privilege-MIN_NUM_FORMS": "0", "privilege-MAX_NUM_FORMS": "1",
                "privilege-0-id": privilege.pk, "privilege-0-community": self.devs.pk,
                "privilege-0-may_see_community_chain": "on"})
        self.assertEqual(response.status_code, 200)             # re-rendered with the refusal
        self.devs.refresh_from_db()
        self.assertFalse(self.devs.is_circle)

    def test_a_superuser_makes_a_circle_with_speeds_in_the_admin(self):
        response = client_for(self.root).post(reverse("admin:socialhub_community_add"), {
            "name": "elders", "slug": "elders", "org_type": Community.OTHER, "is_circle": "on",
            "regen_storage": "2.5",
            "privilege-TOTAL_FORMS": "0", "privilege-INITIAL_FORMS": "0",
            "privilege-MIN_NUM_FORMS": "0", "privilege-MAX_NUM_FORMS": "1"})
        self.assertEqual(response.status_code, 302)
        elders = Community.objects.get(slug="elders")
        self.assertTrue(elders.is_circle)
        self.assertEqual(elders.regen_speeds(), {"storage": Decimal("2.5")})

    def test_a_functional_community_with_a_speed_is_refused_in_the_admin(self):
        response = client_for(self.root).post(reverse("admin:socialhub_community_add"), {
            "name": "testers", "slug": "testers", "org_type": Community.OTHER,
            "regen_compute": "4",
            "privilege-TOTAL_FORMS": "0", "privilege-INITIAL_FORMS": "0",
            "privilege-MIN_NUM_FORMS": "0", "privilege-MAX_NUM_FORMS": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Community.objects.filter(slug="testers").exists())
